import pytest

from app import instance_types as it
from app.controller import CapacityError, InstanceController
from app.store import InstanceStore

from .fakes import FakeDockerOrchestratorClient

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def make_controller(max_instances=30):
    docker = FakeDockerOrchestratorClient()
    store = InstanceStore()
    controller = InstanceController(docker, store, BASE_DOMAIN, CHALLENGE_NET, max_instances)
    return controller, docker, store


def test_create_web_app_creates_one_service():
    controller, docker, store = make_controller()
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is True
    assert len(docker.create_calls) == 1
    assert store.count() == 1


def test_create_is_idempotent_for_same_owner_and_key():
    controller, docker, store = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is False
    assert len(docker.create_calls) == 1  # no second service spun up
    assert store.count() == 1


def test_second_request_touches_existing_record():
    controller, docker, store = make_controller()
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    record = store.get("team-1", "juice")
    first_touch = record.last_accessed
    record.last_accessed -= 1000  # simulate time passing
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert store.get("team-1", "juice").last_accessed > first_touch - 1000


def test_target_attacker_creates_network_and_two_services():
    controller, docker, store = make_controller()
    controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    assert len(docker.create_calls) == 2
    assert len(docker.networks) == 1


def test_teardown_removes_services_and_team_network():
    controller, docker, store = make_controller()
    controller.create_or_get(
        it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "t", "attacker_image": "k"}
    )
    removed = controller.teardown("team-1", "otw")
    assert removed is True
    assert docker.services == {}
    assert docker.networks == set()
    assert store.count() == 0


def test_teardown_missing_instance_returns_false():
    controller, docker, store = make_controller()
    assert controller.teardown("nobody", "nothing") is False


def test_capacity_error_when_at_max():
    controller, docker, store = make_controller(max_instances=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    with pytest.raises(CapacityError):
        controller.create_or_get(it.WEB_APP, "team-2", "juice", {"image": "img"})


def test_capacity_check_does_not_block_reuse_of_existing_instance():
    controller, docker, store = make_controller(max_instances=1)
    controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    # Same owner/key again should hit the idempotent path, not the capacity check.
    plan, created = controller.create_or_get(it.WEB_APP, "team-1", "juice", {"image": "img"})
    assert created is False


def test_invalid_spec_rolls_back_partial_creation():
    controller, docker, store = make_controller()
    with pytest.raises(Exception):
        controller.create_or_get(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "only-one"})
    assert store.count() == 0
