import pytest

from app.config import Config
from app.main import create_app

from .fakes import FakeDockerOrchestratorClient


class FakeConfig(Config):
    BASE_DOMAIN = "ctf.local"
    CHALLENGE_NETWORK = "cei-labs_challenge-edge"
    MAX_INSTANCES = 30
    IDLE_GRACE_MINUTES = 120
    REAP_INTERVAL_SECONDS = 9999
    PLUGIN_SHARED_SECRET = "test-plugin-secret"
    ADMIN_PASSWORD = "test-admin-secret"


@pytest.fixture
def client():
    app = create_app(config=FakeConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    return app.test_client()


PLUGIN_HEADERS = {"X-Orchestrator-Auth": "test-plugin-secret"}
ADMIN_HEADERS = {"X-Admin-Auth": "test-admin-secret"}


def test_healthz_needs_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_create_instance_without_auth_is_rejected(client):
    resp = client.post("/instances", json={"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}})
    assert resp.status_code == 401


def test_create_instance_with_wrong_secret_is_rejected(client):
    resp = client.post(
        "/instances",
        json={"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}},
        headers={"X-Orchestrator-Auth": "wrong"},
    )
    assert resp.status_code == 401


def test_create_instance_success(client):
    resp = client.post(
        "/instances",
        json={"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "created"
    assert body["access"]["url"] == "https://team-1-juice.apps.ctf.local"


def test_create_instance_twice_returns_200_exists(client):
    payload = {"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}}
    client.post("/instances", json=payload, headers=PLUGIN_HEADERS)
    resp = client.post("/instances", json=payload, headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "exists"


def test_create_instance_bad_type_rejected(client):
    resp = client.post(
        "/instances",
        json={"type": "not-real", "owner_id": "team-1", "instance_key": "juice", "spec": {}},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 400


def test_get_missing_instance_404(client):
    resp = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


def test_delete_missing_instance_404(client):
    resp = client.delete("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


def test_full_lifecycle_create_get_delete(client):
    payload = {"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}}
    client.post("/instances", json=payload, headers=PLUGIN_HEADERS)

    resp = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200

    resp = client.delete("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200

    resp = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


def test_admin_routes_require_admin_secret_not_plugin_secret(client):
    resp = client.get("/admin/instances", headers=PLUGIN_HEADERS)
    assert resp.status_code == 401

    resp = client.get("/admin/instances", headers=ADMIN_HEADERS)
    assert resp.status_code == 200


def test_admin_list_reflects_created_instances(client):
    payload = {"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}}
    client.post("/instances", json=payload, headers=PLUGIN_HEADERS)

    resp = client.get("/admin/instances", headers=ADMIN_HEADERS)
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]["owner_id"] == "team-1"


def test_auth_rejected_when_no_secret_configured():
    class NoSecretConfig(FakeConfig):
        PLUGIN_SHARED_SECRET = ""

    app = create_app(config=NoSecretConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    c = app.test_client()
    # Even an empty-string header must not authenticate against an empty secret.
    resp = c.post("/instances", json={}, headers={"X-Orchestrator-Auth": ""})
    assert resp.status_code == 401
