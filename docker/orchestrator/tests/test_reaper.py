from app import instance_types as it
from app.controller import InstanceController
from app.ports import PortAllocator
from app.reaper import Reaper
from app.store import InstanceStore, RangeStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_reaper(grace_minutes=120):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    range_store = RangeStore()
    ports = PortAllocator(32000, 32767)
    controller = InstanceController(docker, store, range_store, ports, BASE_DOMAIN, CHALLENGE_NET, 30, 3)
    reaper = Reaper(controller, store, range_store, grace_minutes, interval_seconds=9999)
    return reaper, controller, docker, store, range_store


def test_sweep_leaves_fresh_instances_alone():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert reaper.sweep() == 0
    assert store.count() == 1


def test_sweep_removes_idle_instance_past_grace_period():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    store.get("team-1", "juice").last_accessed -= 120  # 2 minutes idle, grace is 1

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.count() == 0
    assert docker.services == {}


def test_sweep_only_reaps_the_idle_one():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})
    store.get("team-1", "juice").last_accessed -= 120

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.get("team-1", "juice") is None
    assert store.get("team-2", "juice") is not None


def test_sweep_tears_down_range_target_and_its_shared_network():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    store.get("team-1", "otw").last_accessed -= 120
    range_store.get("team-1").last_accessed -= 120  # range itself also idle

    reaper.sweep()

    assert docker.networks == {}
    assert docker.services == {}
    assert range_store.get("team-1") is None


def test_sweep_reaps_idle_range_even_with_no_remaining_targets():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"})
    controller.teardown("team-1", "otw")  # target gone, attacker/network remain
    range_store.get("team-1").last_accessed -= 120

    reaper.sweep()

    assert range_store.get("team-1") is None
    assert docker.services == {}
    assert docker.networks == {}


def test_sweep_leaves_active_range_alone_even_if_a_target_was_reaped():
    reaper, controller, docker, store, range_store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-1", {"target_image": "t", "attacker_image": "k"})
    controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw-2", {"target_image": "t", "attacker_image": "k"})
    store.get("team-1", "otw-1").last_accessed -= 120  # only this target is idle
    # range_store last_accessed stays fresh (touched by otw-2's creation)

    reaper.sweep()

    assert store.get("team-1", "otw-1") is None
    assert store.get("team-1", "otw-2") is not None
    assert range_store.get("team-1") is not None


# ── shutdown countdown sweeping ───────────────────────────────────────────────

def test_sweep_tears_down_instance_whose_shutdown_deadline_passed():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=30)
    store.get("team-1", "juice").shutdown_at -= 60  # force it into the past

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.get("team-1", "juice") is None


def test_sweep_does_not_touch_instance_with_future_shutdown_deadline():
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None


def test_pending_shutdown_instance_is_not_also_idle_reaped():
    # An instance on a shutdown countdown shouldn't ALSO get caught by the
    # separate idle-grace sweep even if it happens to look idle too.
    reaper, controller, docker, store, _ = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    record.last_accessed -= 120  # looks idle
    controller.schedule_shutdown("team-1", "juice", delay_seconds=9999)  # but shutdown isn't due yet

    reaped = reaper.sweep()

    assert reaped == 0
    assert store.get("team-1", "juice") is not None
