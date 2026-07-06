from app import instance_types as it
from app.controller import InstanceController
from app.reaper import Reaper
from app.store import InstanceStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_reaper(grace_minutes=120):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    controller = InstanceController(docker, store, BASE_DOMAIN, CHALLENGE_NET, max_instances=30)
    reaper = Reaper(controller, store, grace_minutes, interval_seconds=9999)
    return reaper, controller, docker, store


def test_sweep_leaves_fresh_instances_alone():
    reaper, controller, docker, store = make_reaper(grace_minutes=120)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    reaped = reaper.sweep()
    assert reaped == 0
    assert store.count() == 1


def test_sweep_removes_idle_instance_past_grace_period():
    reaper, controller, docker, store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    store.get("team-1", "juice").last_accessed -= 120  # 2 minutes idle, grace is 1

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.count() == 0
    assert docker.services == {}


def test_sweep_only_reaps_the_idle_one():
    reaper, controller, docker, store = make_reaper(grace_minutes=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})
    store.get("team-1", "juice").last_accessed -= 120

    reaped = reaper.sweep()

    assert reaped == 1
    assert store.get("team-1", "juice") is None
    assert store.get("team-2", "juice") is not None


def test_sweep_reaps_target_attacker_network_too():
    reaper, controller, docker, store = make_reaper(grace_minutes=1)
    controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    store.get("team-1", "otw").last_accessed -= 120

    reaper.sweep()

    assert docker.networks == set()
    assert docker.services == {}
