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
    SSH_PORT_RANGE_START = 32000
    SSH_PORT_RANGE_END = 32767
    SHUTDOWN_DELAY_SECONDS = 30
    SHUTDOWN_EXTEND_SECONDS = 300
    SHUTDOWN_MAX_EXTENSIONS = 3
    STORE_DB_PATH = ":memory:"


@pytest.fixture
def client():
    app = create_app(config=FakeConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    return app.test_client()


PLUGIN_HEADERS = {"X-Orchestrator-Auth": "test-plugin-secret"}
ADMIN_HEADERS = {"X-Admin-Auth": "test-admin-secret"}
WEB_APP_PAYLOAD = {"type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}}


def test_healthz_needs_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_create_instance_without_auth_is_rejected(client):
    resp = client.post("/instances", json=WEB_APP_PAYLOAD)
    assert resp.status_code == 401


def test_create_instance_with_wrong_secret_is_rejected(client):
    resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers={"X-Orchestrator-Auth": "wrong"})
    assert resp.status_code == 401


def test_create_instance_success(client):
    resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "created"
    assert body["access"]["url"] == "https://team-1-juice.apps.ctf.local"


def test_create_instance_twice_returns_200_exists(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "exists"


def test_relaunch_flag_recreates_the_instance(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    resp = client.post("/instances", json={**WEB_APP_PAYLOAD, "relaunch": True}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 201
    assert resp.get_json()["status"] == "created"


def test_create_instance_bad_type_rejected(client):
    resp = client.post(
        "/instances", json={"type": "not-real", "owner_id": "team-1", "instance_key": "juice", "spec": {}}, headers=PLUGIN_HEADERS
    )
    assert resp.status_code == 400


def test_get_missing_instance_404(client):
    resp = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


def test_delete_missing_instance_404(client):
    resp = client.delete("/instances/team-1/juice", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


def test_full_lifecycle_create_get_delete(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    assert client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).status_code == 200
    assert client.delete("/instances/team-1/juice", headers=PLUGIN_HEADERS).status_code == 200
    assert client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).status_code == 404


def test_reboot_instance(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    resp = client.post("/instances/team-1/juice/reboot", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rebooting"


def test_reboot_missing_instance_404(client):
    resp = client.post("/instances/team-1/juice/reboot", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


# ── shutdown countdown ────────────────────────────────────────────────────────

def test_schedule_and_extend_shutdown(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)

    resp = client.post("/instances/team-1/juice/schedule-shutdown", json={"delay_seconds": 30}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    first_deadline = resp.get_json()["shutdown_at"]

    resp = client.post("/instances/team-1/juice/extend-shutdown", json={"extend_seconds": 300}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["shutdown_at"] > first_deadline

    status = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).get_json()
    assert status["extensions_used"] == 1


def test_extend_without_pending_shutdown_is_conflict(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    resp = client.post("/instances/team-1/juice/extend-shutdown", json={}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 409


def test_extend_exhausted_after_max_extensions(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    client.post("/instances/team-1/juice/schedule-shutdown", json={}, headers=PLUGIN_HEADERS)
    for _ in range(3):
        client.post("/instances/team-1/juice/extend-shutdown", json={}, headers=PLUGIN_HEADERS)
    resp = client.post("/instances/team-1/juice/extend-shutdown", json={}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 409


# ── ranges ────────────────────────────────────────────────────────────────────

def test_range_lifecycle(client):
    payload = {"type": "target-attacker", "owner_id": "team-1", "instance_key": "otw", "spec": {"target_image": "t", "attacker_image": "k"}}
    resp = client.post("/instances", json=payload, headers=PLUGIN_HEADERS)
    assert resp.status_code == 201
    assert "attacker_url" in resp.get_json()["access"]

    resp = client.post("/ranges/team-1/attacker/reboot", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200

    resp = client.delete("/ranges/team-1", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200

    resp = client.delete("/ranges/team-1", headers=PLUGIN_HEADERS)
    assert resp.status_code == 404


# ── admin ─────────────────────────────────────────────────────────────────────

def test_admin_routes_require_admin_secret_not_plugin_secret(client):
    assert client.get("/admin/instances", headers=PLUGIN_HEADERS).status_code == 401
    assert client.get("/admin/instances", headers=ADMIN_HEADERS).status_code == 200


def test_admin_list_reflects_created_instances(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    body = client.get("/admin/instances", headers=ADMIN_HEADERS).get_json()
    assert len(body) == 1
    assert body[0]["owner_id"] == "team-1"


def test_admin_list_ranges(client):
    payload = {"type": "target-attacker", "owner_id": "team-1", "instance_key": "otw", "spec": {"target_image": "t", "attacker_image": "k"}}
    client.post("/instances", json=payload, headers=PLUGIN_HEADERS)
    body = client.get("/admin/ranges", headers=ADMIN_HEADERS).get_json()
    assert len(body) == 1
    assert body[0]["owner_id"] == "team-1"
    assert body[0]["target_keys"] == ["otw"]


def test_auth_rejected_when_no_secret_configured():
    class NoSecretConfig(FakeConfig):
        PLUGIN_SHARED_SECRET = ""

    app = create_app(config=NoSecretConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    c = app.test_client()
    resp = c.post("/instances", json={}, headers={"X-Orchestrator-Auth": ""})
    assert resp.status_code == 401
