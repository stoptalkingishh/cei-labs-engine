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


def make_controller(max_instances=30, max_extensions=3):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(
        docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, max_instances, max_extensions
    )
    return controller, docker, store, range_store


# ── web-app ───────────────────────────────────────────────────────────────────

def test_create_web_app_creates_one_service():
    controller, docker, store, _ = make_controller()
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is True
    assert len(docker.create_calls) == 1
    assert store.count() == 1


def test_create_is_idempotent_for_same_owner_and_key():
    controller, docker, store, _ = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is False
    assert len(docker.create_calls) == 1
    assert store.count() == 1


def test_capacity_error_when_at_max():
    controller, docker, store, _ = make_controller(max_instances=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    with pytest.raises(CapacityError):
        controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})


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


# ── target-attacker (range) ───────────────────────────────────────────────────

def test_first_range_target_creates_attacker_and_network():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})

    assert range_store.get("team-1") is not None
    assert len(docker.create_calls) == 2  # attacker + target
    assert range_store.get("team-1").target_keys == {"otw-1"}


def test_second_challenge_same_team_reuses_attacker_and_network():
    controller, docker, store, range_store = make_controller()
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})

    # 1 attacker + 2 targets = 3 services total, not 4 (no second attacker created)
    assert len(docker.create_calls) == 3
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
    assert len(docker.restart_calls) == 1


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
    assert len(docker.create_calls) == 2
    assert docker.create_calls[1].name == first_service_name  # same identity, fresh container


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
