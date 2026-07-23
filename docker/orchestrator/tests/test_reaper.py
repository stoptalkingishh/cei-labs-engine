from app import instance_types as it
from app.controller import InstanceController
from app.docker_client import ServiceSpec
from app.ports import PortAllocator
from app.reaper import Reaper
from app.store import InstanceStore, RangeStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_reaper(grace_minutes=120, max_lifetime_minutes=None, reservation_timeout_seconds=60):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3)
    reaper = Reaper(
        controller, store, range_store, grace_minutes, interval_seconds=9999,
        max_lifetime_minutes=max_lifetime_minutes,
        reservation_timeout_seconds=reservation_timeout_seconds,
    )
    return reaper, controller, docker, store, range_store


def test_sweep_leaves_fresh_instances_alone():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert reaper.sweep() == 0
    assert store.count() == 1


# ── idle pausing (non-destructive: credentials/flags must survive) ─────────────

def test_sweep_pauses_idle_instance_past_grace_period_without_deleting_it():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # 2 minutes idle, grace is 1
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    # The record survives a pause -- only the running Docker resources go
    # away. This is the crux of the credential-lifecycle fix: idle timeout
    # must not delete the row that holds the generated credentials/env.
    assert store.count() == 1
    paused = store.get("team-1", "juice")
    assert paused.stopped is True
    assert paused.plan.services[0].env == original_env
    assert docker.services == {}
    assert docker.networks == {}


def test_sweep_only_pauses_the_idle_one():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.get("team-1", "juice").stopped is True
    assert store.get("team-2", "juice").stopped is False


def test_sweep_does_not_repause_an_already_paused_instance():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)

    assert reaper.sweep() == 1
    assert reaper.sweep() == 0  # already stopped -- nothing left to do


def test_paused_instance_resumes_with_identical_credentials():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    original_access = dict(plan.access)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    resumed_plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})

    assert created is False
    assert resumed_plan.services[0].env == original_env
    assert resumed_plan.access == original_access
    assert store.get("team-1", "juice").stopped is False
    assert len(docker.services) == 2  # target + gateway recreated


def test_relaunch_after_pause_does_rotate_credentials():
    """The one path that's SUPPOSED to change credentials: an explicit
    relaunch/reset, even against a currently-paused instance."""
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    record = store.get("team-1", "juice")
    record.last_accessed -= 120
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    relaunched_plan, created = controller.create_or_get(
        it.WEB_APP, "team-1", "juice", {"image": "img", "env": {"MARKER": "rotated"}}, force_relaunch=True
    )

    assert created is True
    assert store.get("team-1", "juice").stopped is False
    assert relaunched_plan.services[0].env != original_env
    assert relaunched_plan.services[0].env.get("MARKER") == "rotated"


def test_sweep_pauses_range_attacker_but_keeps_shared_network_for_resume():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    record = store.get("team-1", "otw")
    record.last_accessed -= 120
    store.update(record)
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120  # range itself also idle
    range_store.update(range_record)

    reaper.sweep()

    # Both the target and the shared attacker/gateway are stopped...
    assert docker.services == {}
    assert store.get("team-1", "otw").stopped is True
    assert range_store.get("team-1").stopped is True
    # ...but the range's shared overlay network is NOT torn down on a
    # pause (only on a real teardown_range()) -- targets/attacker resume
    # back onto it later.
    assert "chrange-team-1" in docker.networks


def test_sweep_pauses_idle_range_even_with_no_remaining_targets():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    controller.teardown("team-1", "otw")  # target explicitly deleted, attacker/network remain
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120
    range_store.update(range_record)

    reaper.sweep()

    paused = range_store.get("team-1")
    assert paused is not None
    assert paused.stopped is True
    assert docker.services == {}


def test_sweep_leaves_active_range_alone_even_if_a_target_was_paused():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})
    record = store.get("team-1", "otw-1")
    record.last_accessed -= 120  # only this target is idle
    store.update(record)
    # range_store last_accessed stays fresh (touched by otw-2's creation)

    reaper.sweep()

    assert store.get("team-1", "otw-1").stopped is True
    assert store.get("team-1", "otw-2").stopped is False
    assert range_store.get("team-1").stopped is False


def test_paused_range_attacker_resumes_with_same_password():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    plan, _ = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    original_password = range_store.get("team-1").plan.access["ssh_password"]
    range_record = range_store.get("team-1")
    range_record.last_accessed -= 120
    range_store.update(range_record)
    reaper.sweep()
    assert range_store.get("team-1").stopped is True

    resumed_plan, created = controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )

    assert created is False
    assert range_store.get("team-1").stopped is False
    assert resumed_plan.access["ssh_password"] == original_password


# ── shutdown countdown sweeping (also non-destructive) ──────────────────────

def test_sweep_pauses_instance_whose_shutdown_deadline_passed_without_deleting_it():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    plan, _ = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    original_env = dict(plan.services[0].env)
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    record = store.get("team-1", "juice")
    record.shutdown_at -= 60  # force it into the past
    store.update(record)

    reaped = reaper.sweep()

    assert reaped == 1
    paused = store.get("team-1", "juice")
    assert paused is not None
    assert paused.stopped is True
    assert paused.shutdown_at is None  # countdown cleared, doesn't fire again on resume
    assert paused.plan.services[0].env == original_env


def test_sweep_does_not_touch_instance_with_future_shutdown_deadline():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None
    assert store.get("team-1", "juice").stopped is False


def test_pending_shutdown_instance_is_not_also_idle_reaped():
    # An instance on a shutdown countdown shouldn't ALSO get caught by the
    # separate idle-grace sweep even if it happens to look idle too.
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # looks idle
    store.update(record)
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)  # but shutdown isn't due yet

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None


# ── absolute lifetime (still real, destructive expiration) ─────────────────

def test_sweep_enforces_absolute_lifetime_even_when_instance_is_active():
    reaper, controller, docker, store, _ = make_reaper(
        grace_minutes=120, max_lifetime_minutes=1
    )
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.created_at -= 120
    store.put(record)

    assert reaper.sweep() == 1
    assert store.get("team-1", "juice") is None
    assert docker.services == {}


def test_sweep_absolute_lifetime_also_destroys_an_already_paused_instance():
    """Absolute lifetime is a real, one-way expiration -- it must still fire
    (and actually delete the row/credentials) even for an instance that's
    currently paused, so credentials don't become permanently sticky."""
    reaper, controller, docker, store, _ = make_reaper(
        grace_minutes=1, max_lifetime_minutes=2
    )
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # idle -> gets paused first
    store.update(record)
    assert reaper.sweep() == 1
    assert store.get("team-1", "juice").stopped is True

    record = store.get("team-1", "juice")
    record.created_at -= 180  # now also past the absolute lifetime ceiling
    store.put(record)

    assert reaper.sweep() == 1
    assert store.get("team-1", "juice") is None


def test_sweep_releases_stale_reservation_and_removes_its_orphans():
    reaper, controller, docker, store, _ = make_reaper(reservation_timeout_seconds=10)
    assert store.reserve("team-1", "crashed")
    store._conn().execute(
        "UPDATE instances SET created_at = created_at - 60 WHERE owner_id = ? AND instance_key = ?",
        ("team-1", "crashed"),
    )
    port = controller.port_allocator.allocate()
    orphan = ServiceSpec(
        name="orphan-service",
        image="img",
        networks=["orphan-network"],
        published_ports=[(port, 22)],
    )
    docker.services[orphan.name] = orphan
    docker.networks["orphan-network"] = True

    assert reaper.sweep() == 3
    assert store.pending_count() == 0
    assert docker.services == {}
    assert docker.networks == {}
    assert controller.port_allocator.allocate() == port


def test_sweep_does_not_reconcile_orphans_during_live_reservation():
    reaper, _, docker, store, _ = make_reaper(reservation_timeout_seconds=60)
    assert store.reserve("team-1", "creating")
    in_flight = ServiceSpec(name="in-flight-service", image="img", networks=["in-flight-network"])
    docker.services[in_flight.name] = in_flight
    docker.networks["in-flight-network"] = True

    assert reaper.sweep() == 0
    assert in_flight.name in docker.services
    assert "in-flight-network" in docker.networks
