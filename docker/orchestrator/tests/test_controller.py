import os
import tempfile
import threading
import time

import pytest

from app import instance_types as it
from app.controller import (
    CapacityError,
    ExtensionsExhaustedError,
    InstanceController,
    NotFoundError,
    ShutdownNotPendingError,
)
from app.ports import PortAllocator
from app.store import InstanceStore, RangeStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_controller(max_instances=30, max_extensions=3, max_instances_per_owner=None, workload_quota=None):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(
        docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET,
        max_instances, max_extensions, max_instances_per_owner, workload_quota
    )
    return controller, docker, store, range_store


# ── web-app ───────────────────────────────────────────────────────────────────

def test_create_web_app_creates_target_and_gateway():
    controller, docker, store, _ = make_controller()
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is True
    assert len(docker.create_calls) == 2
    assert store.count() == 1


def test_create_is_idempotent_for_same_owner_and_key():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is False
    assert len(docker.create_calls) == 2
    assert store.count() == 1


def test_capacity_error_when_at_max():
    controller, docker, store, _ = make_controller(max_instances=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    with pytest.raises(CapacityError):
        controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})


def test_owner_quota_blocks_only_that_owner_and_releases_reservation():
    controller, _, store, _ = make_controller(max_instances_per_owner=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})

    with pytest.raises(CapacityError, match="owner at quota"):
        controller.create_or_get(it.WEB_APP, "team-1", "second", {"image": "img"})

    assert store.count_for_owner("team-1") == 1
    controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})
    assert store.count_for_owner("team-2") == 1


def test_owner_quota_allows_idempotent_reuse_and_relaunch():
    controller, _, store, _ = make_controller(max_instances_per_owner=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})

    _, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    _, relaunched = controller.create_or_get(
        it.WEB_APP, "team-1", "juice", {"image": "img"}, force_relaunch=True
    )

    assert created is False
    assert relaunched is True
    assert store.count_for_owner("team-1") == 1


def test_controller_applies_configured_workload_quota():
    quota = it.WorkloadQuota(256 * 1024 * 1024, 64 * 1024 * 1024, 500_000_000)
    controller, _, _, _ = make_controller(workload_quota=quota)

    plan, _ = controller.create_or_get(it.SINGLE_TARGET, "team-1", "otw", {"image": "img"})
    workload, gateway = plan.services

    assert workload.mem_limit_bytes == quota.memory_limit_bytes
    assert workload.mem_reservation_bytes == quota.memory_reservation_bytes
    assert workload.cpu_limit_nanos == quota.cpu_limit_nanos
    assert gateway.mem_limit_bytes == 64 * 1024 * 1024


# ── single-target ─────────────────────────────────────────────────────────────

def test_single_target_allocates_a_port_and_creates_an_internal_network():
    controller, docker, store, _ = make_controller()
    plan, created = controller.create_or_get(it.SINGLE_TARGET, "team-1", "otw", {"image": "img"})
    assert created is True
    assert docker.networks[plan.network] is True  # internal=True
    assert plan.access["connect_port"] >= 32000


def test_single_target_teardown_releases_its_port():
    controller, docker, store, _ = make_controller()
    plan, _ = controller.create_or_get(it.SINGLE_TARGET, "team-1", "otw", {"image": "img"})
    port = plan.access["connect_port"]

    controller.teardown("team-1", "otw")

    # Port should be reusable immediately after teardown.
    plan2, _ = controller.create_or_get(it.SINGLE_TARGET, "team-2", "otw", {"image": "img"})
    assert plan2.access["connect_port"] == port


def test_single_target_create_failure_removes_network_and_releases_port():
    class FailingCreateDocker(FakeDockerOrchestratorClient):
        def create_service(self, spec):
            raise RuntimeError("synthetic Docker failure")

    docker = FailingCreateDocker()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(
        docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3
    )

    with pytest.raises(RuntimeError, match="synthetic Docker failure"):
        controller.create_or_get(it.SINGLE_TARGET, "team-1", "otw", {"image": "img"})

    assert docker.networks == {}
    assert len(docker.remove_network_calls) == 1
    assert ports.allocate() == 32000


def test_single_target_pause_resume_keeps_same_port_and_secrets():
    """The core credential-lifecycle fix, at the single-target level: a
    non-destructive pause (idle/shutdown), followed by a resume through the
    normal create_or_get() path, must hand back the exact same connect
    port and per-team level secrets -- not a freshly generated set."""
    controller, docker, store, _ = make_controller()
    plan, _ = controller.create_or_get(
        it.SINGLE_TARGET, "team-1", "otw", {"image": "img", "secret_keys": ["level1"]}
    )
    original_access = dict(plan.access)
    original_port = plan.access["connect_port"]

    assert controller.pause("team-1", "otw") is True
    paused = store.get("team-1", "otw")
    assert paused.stopped is True
    assert paused.plan.access == original_access  # secrets/port still on the record
    assert docker.services == {}
    assert docker.networks == {}
    # Pausing does NOT release the port -- a different instance must not be
    # able to grab it out from under a soon-to-resume one.
    assert controller.port_allocator.allocate() != original_port

    resumed_plan, created = controller.create_or_get(
        it.SINGLE_TARGET, "team-1", "otw", {"image": "img", "secret_keys": ["level1"]}
    )

    assert created is False
    assert resumed_plan.access == original_access
    assert resumed_plan.access["connect_port"] == original_port
    assert store.get("team-1", "otw").stopped is False
    assert len(docker.services) == 2  # target + gateway recreated


def test_pause_is_a_noop_for_a_missing_or_already_paused_instance():
    controller, docker, store, _ = make_controller()
    assert controller.pause("nobody", "nothing") is False

    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert controller.pause("team-1", "juice") is True
    assert controller.pause("team-1", "juice") is False  # already paused


def test_pause_clears_a_pending_shutdown_countdown():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)

    controller.pause("team-1", "juice")

    record = store.get("team-1", "juice")
    assert record.shutdown_at is None
    assert record.extensions_used == 0


# ── target-attacker (range) ───────────────────────────────────────────────────

def test_first_range_target_creates_attacker_and_network():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})

    assert range_store.get("team-1") is not None
    assert len(docker.create_calls) == 3  # attacker + gateway + target
    assert range_store.get("team-1").target_keys == {"otw-1"}


def test_second_challenge_same_team_reuses_attacker_and_network():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})

    # 1 attacker + 1 gateway + 2 targets = 4; no second shared pair.
    assert len(docker.create_calls) == 4
    assert range_store.get("team-1").target_keys == {"otw-1", "otw-2"}


def test_tearing_down_one_target_leaves_attacker_and_other_targets_alone():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})

    controller.teardown("team-1", "otw-1")

    assert store.get("team-1", "otw-1") is None
    assert store.get("team-1", "otw-2") is not None
    assert range_store.get("team-1") is not None
    assert range_store.get("team-1").target_keys == {"otw-2"}


def test_teardown_range_removes_attacker_network_and_all_targets():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})

    removed = controller.teardown_range("team-1")

    assert removed is True
    assert range_store.get("team-1") is None
    assert store.get("team-1", "otw-1") is None
    assert store.get("team-1", "otw-2") is None
    assert docker.services == {}
    assert docker.networks == {}


def test_pause_range_keeps_network_and_ports_for_resume():
    controller, docker, store, range_store = make_controller()
    plan, _ = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    original_password = range_store.get("team-1").plan.access["ssh_password"]
    network_name = range_store.get("team-1").plan.network

    assert controller.pause_range("team-1") is True
    assert range_store.get("team-1").stopped is True
    # Attacker+gateway gone (pause_range only touches those two), but the
    # shared network -- the still-live target stays attached to it
    # independently -- and its published ports are untouched.
    attacker_name = range_store.get("team-1").plan.attacker_service.name
    gateway_name = range_store.get("team-1").plan.gateway_service.name
    assert attacker_name not in docker.services
    assert gateway_name not in docker.services
    assert network_name in docker.networks

    resumed_plan, created = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    assert created is False
    assert range_store.get("team-1").stopped is False
    assert resumed_plan.access["ssh_password"] == original_password


def test_pause_range_is_a_noop_for_a_missing_or_already_paused_range():
    controller, docker, store, range_store = make_controller()
    assert controller.pause_range("nobody") is False

    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    assert controller.pause_range("team-1") is True
    assert controller.pause_range("team-1") is False


def test_reboot_range_attacker_resumes_it_when_paused():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    original_password = range_store.get("team-1").plan.access["ssh_password"]
    controller.pause_range("team-1")

    ok = controller.reboot_range_attacker("team-1")

    assert ok is True
    assert range_store.get("team-1").stopped is False
    assert range_store.get("team-1").plan.access["ssh_password"] == original_password


def test_two_teams_get_independent_ranges():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-a", "otw", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-b", "otw", {"target_image": "t", "attacker_image": "k"})

    controller.teardown_range("team-a")

    assert range_store.get("team-a") is None
    assert range_store.get("team-b") is not None


# ── reboot ────────────────────────────────────────────────────────────────────

def test_reboot_restarts_the_instance_service():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    ok = controller.reboot("team-1", "juice")
    assert ok is True
    assert len(docker.restart_calls) == 2


def test_reboot_of_a_paused_instance_resumes_it_instead_of_force_updating():
    controller, docker, store, _ = make_controller()
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_access = dict(plan.access)
    controller.pause("team-1", "juice")
    assert docker.services == {}

    ok = controller.reboot("team-1", "juice")

    assert ok is True
    assert docker.restart_calls == []  # nothing live to force_update -- it was recreated instead
    assert len(docker.services) == 2
    assert store.get("team-1", "juice").stopped is False
    assert store.get("team-1", "juice").plan.access == original_access


def test_reboot_missing_instance_returns_false():
    controller, docker, store, _ = make_controller()
    assert controller.reboot("nobody", "nothing") is False


def test_reboot_range_attacker():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    ok = controller.reboot_range_attacker("team-1")
    assert ok is True
    assert docker.restart_calls == [range_store.get("team-1").plan.attacker_service.name]


# ── relaunch ──────────────────────────────────────────────────────────────────

def test_relaunch_tears_down_and_recreates():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    first_service_name = docker.create_calls[0].name

    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"}, force_relaunch=True)

    assert created is True
    assert len(docker.create_calls) == 4
    assert docker.create_calls[2].name == first_service_name  # same identity, fresh container


def test_parallel_relaunches_converge_on_one_replacement():
    class BlockingRemovalDocker(FakeDockerOrchestratorClient):
        def __init__(self):
            super().__init__()
            self.removal_started = threading.Event()
            self.allow_removal = threading.Event()

        def remove_service(self, name: str) -> None:
            self.removal_started.set()
            assert self.allow_removal.wait(timeout=5)
            super().remove_service(name)

    worker_count = 20
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "controller-race.db")
        docker = BlockingRemovalDocker()
        stores = [InstanceStore(db_path=db_path) for _ in range(worker_count)]
        range_stores = [RangeStore(db_path=db_path) for _ in range(worker_count)]
        allocators = [PortAllocator(32000, 32767, db_path=db_path) for _ in range(worker_count)]
        controllers = [
            InstanceController(
                docker,
                stores[i],
                range_stores[i],
                allocators[i],
                BASE_DOMAIN,
                CHALLENGE_NET,
                30,
                3,
            )
            for i in range(worker_count)
        ]
        controllers[0].create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})

        barrier = threading.Barrier(worker_count)
        results = [None] * worker_count

        def race(i):
            barrier.wait()
            results[i] = controllers[i].create_or_get(
                it.WEB_APP,
                "team-1",
                "juice",
                {"image": "img"},
                force_relaunch=True,
            )

        threads = [threading.Thread(target=race, args=(i,)) for i in range(worker_count)]
        for thread in threads:
            thread.start()
        assert docker.removal_started.wait(timeout=5)
        # Keep the winning request inside teardown long enough for every
        # sibling to observe the pending SQLite reservation.
        time.sleep(0.25)
        docker.allow_removal.set()
        for thread in threads:
            thread.join(timeout=12)

        assert all(not thread.is_alive() for thread in threads)
        assert len(docker.create_calls) == 4  # target+gateway initial and exactly one replacement pair
        assert sum(created for _plan, created in results) == 1
        assert stores[0].get("team-1", "juice") is not None
        for resource in [*stores, *range_stores, *allocators]:
            resource.close()


# ── post-solve shutdown countdown ────────────────────────────────────────────

def test_schedule_shutdown_sets_a_future_deadline():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    before = time.time()
    shutdown_at = controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    assert shutdown_at >= before + 30


def test_schedule_shutdown_missing_instance_raises():
    controller, docker, store, _ = make_controller()
    with pytest.raises(NotFoundError):
        controller.schedule_shutdown("nobody", "nothing", delay_seconds=30)


def test_extend_shutdown_pushes_deadline_further_out():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    first_deadline = controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    new_deadline = controller.extend_shutdown("team-1", "juice", extend_seconds=300)
    assert new_deadline > first_deadline


def test_extend_shutdown_without_pending_shutdown_raises():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    with pytest.raises(ShutdownNotPendingError):
        controller.extend_shutdown("team-1", "juice", extend_seconds=300)


def test_extend_shutdown_capped_at_max_extensions():
    controller, docker, store, _ = make_controller(max_extensions=2)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)

    controller.extend_shutdown("team-1", "juice", extend_seconds=300)
    controller.extend_shutdown("team-1", "juice", extend_seconds=300)
    with pytest.raises(ExtensionsExhaustedError):
        controller.extend_shutdown("team-1", "juice", extend_seconds=300)


def test_cancel_shutdown_clears_the_deadline():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    controller.cancel_shutdown("team-1", "juice")
    assert store.get("team-1", "juice").shutdown_at is None
