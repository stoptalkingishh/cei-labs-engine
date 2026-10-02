import time

import pytest
from cryptography.fernet import Fernet

from app.config import Config
from app.main import MAX_SHUTDOWN_SECONDS, create_app

from .fakes import FakeDockerOrchestratorClient


class FakeConfig(Config):
    BASE_DOMAIN = "ctf.local"
    # Explicit, not "auto" -- tests must never trigger resolve_offline_mode()'s
    # real DNS probe (slow, non-deterministic, and BASE_DOMAIN="ctf.local"
    # won't resolve in a sandboxed test environment anyway, which would
    # otherwise make every test here hit the OFFLINE_HOST-required error).
    OFFLINE_MODE_SETTING = False
    OFFLINE_HOST = ""
    CHALLENGE_NETWORK = "cei-labs_challenge-edge"
    MAX_INSTANCES = 30
    MAX_INSTANCES_PER_OWNER = 3
    WORKLOAD_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
    WORKLOAD_MEMORY_RESERVATION_BYTES = 128 * 1024 * 1024
    WORKLOAD_CPU_LIMIT_NANOS = 1_000_000_000
    IDLE_GRACE_MINUTES = 120
    MAX_INSTANCE_LIFETIME_MINUTES = 240
    RESERVATION_TIMEOUT_SECONDS = 300
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


def test_launch_is_never_reported_as_created_when_its_reservation_vanished(client):
    """store.finalize() used to be a bare UPDATE with no rowcount check, so
    a reservation deleted underneath an in-progress creation (the reaper's
    stale-reservation release, or a concurrent teardown) made finalizing a
    silent no-op and the participant got a 201 with a URL for an instance
    the store has no record of. A lost reservation has to be an error."""
    controller = client.application.config["controller"]
    store = client.application.config["store"]
    create_service = controller.docker.create_service

    def create_then_lose_the_reservation(spec):
        result = create_service(spec)
        # The reaper winning the race against a creation slower than its
        # own timeout, compressed to a single call.
        store.release_stale_reservations(0)
        return result

    controller.docker.create_service = create_then_lose_the_reservation

    resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)

    assert resp.status_code != 201
    assert resp.status_code == 503
    assert store.get("team-1", "juice") is None


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


def test_reboot_of_a_recorded_but_docker_less_instance_is_not_404(client):
    """The store row is the instance; the container is just where it runs.
    Answering 404 here told the participant's launcher to treat the
    environment as gone, and the only thing that "fixes" that is a
    relaunch -- which destroys the progress they're trying to keep. A
    missing container is Docker's problem to report (502), not a claim that
    the instance never existed."""
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    docker = client.application.config["controller"].docker
    docker.services.clear()

    resp = client.post("/instances/team-1/juice/reboot", headers=PLUGIN_HEADERS)

    assert resp.status_code != 404
    assert resp.status_code == 502
    body = resp.get_json()
    assert "relaunch" in body["error"]
    assert body["relaunch_required"] is True
    # And the record is untouched -- nothing about this answer should cost
    # the participant their credentials.
    assert client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).status_code == 200


def test_reboot_range_attacker_of_a_recorded_but_docker_less_range_is_not_404(client):
    payload = {"type": "target-attacker", "owner_id": "team-1", "instance_key": "otw", "spec": {"target_image": "t", "attacker_image": "k"}}
    client.post("/instances", json=payload, headers=PLUGIN_HEADERS)
    client.application.config["controller"].docker.services.clear()

    resp = client.post("/ranges/team-1/attacker/reboot", headers=PLUGIN_HEADERS)

    assert resp.status_code == 502
    assert "relaunch" in resp.get_json()["error"]
    # Still a 404 for a range that genuinely has no record at all.
    assert client.post("/ranges/nobody/attacker/reboot", headers=PLUGIN_HEADERS).status_code == 404


# ── idle pause / resume: the credential-lifecycle fix, end-to-end over HTTP ────
# These simulate the real bug report: a learner leaves an environment idle,
# the orchestrator's own reaper sweeps it (exactly as the background thread
# would), and the learner comes back later. The credentials/flags they were
# given at launch must still work -- and the API must say "resumed", not
# silently reissue new ones under an "exists"/"created" label.

def test_idle_then_resume_preserves_credentials_over_http(client):
    create_resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    assert create_resp.get_json()["status"] == "created"
    original_access = create_resp.get_json()["access"]

    controller = client.application.config["controller"]
    store = client.application.config["store"]
    reaper = client.application.config["reaper"]

    # Simulate having been idle past the grace period, then run exactly the
    # sweep the background reaper thread would run.
    record = store.get("team-1", "juice")
    record.last_accessed -= 999999
    store.update(record)
    assert reaper.sweep() >= 1
    assert store.get("team-1", "juice").stopped is True

    # A learner reopening the challenge in CTFd hits this same endpoint --
    # no `relaunch` flag, exactly like the idempotent "exists" case.
    resume_resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    assert resume_resp.status_code == 200
    body = resume_resp.get_json()
    # Must clearly signal "resumed a paused environment", not be
    # indistinguishable from "was already running" or "freshly created".
    assert body["status"] == "resumed"
    assert body["access"] == original_access  # exact same credentials
    assert store.get("team-1", "juice").stopped is False

    # And the instance is genuinely usable again, not just recorded as such.
    status = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).get_json()
    assert status["stopped"] is False


def test_reboot_of_a_paused_instance_resumes_it_with_same_credentials(client):
    create_resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    original_access = create_resp.get_json()["access"]

    store = client.application.config["store"]
    reaper = client.application.config["reaper"]
    record = store.get("team-1", "juice")
    record.last_accessed -= 999999
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    resp = client.post("/instances/team-1/juice/reboot", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "resumed"
    assert store.get("team-1", "juice").stopped is False

    status = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).get_json()
    assert status["access"] == original_access


def test_explicit_relaunch_after_pause_reports_rotated_credentials(client):
    """The one case where credentials SHOULD change, and the API must say
    so clearly -- not silently swap them under a resume-shaped response."""
    create_resp = client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    original_access = create_resp.get_json()["access"]

    store = client.application.config["store"]
    reaper = client.application.config["reaper"]
    record = store.get("team-1", "juice")
    record.last_accessed -= 999999
    store.update(record)
    reaper.sweep()
    assert store.get("team-1", "juice").stopped is True

    resp = client.post("/instances", json={**WEB_APP_PAYLOAD, "relaunch": True}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 201
    body = resp.get_json()
    # Matches the pre-existing convention (see test_relaunch_flag_recreates_
    # the_instance above): an explicit relaunch always reports the same
    # `status="created"` a first-ever create does, on a 201 -- the signal
    # that credentials/flags changed is the 201 (only ever returned for a
    # freshly (re)built environment) together with a different `access`
    # payload, never the ambiguous 200 "exists"/"resumed" a caller could
    # mistake for their already-known values still being valid.
    assert body["status"] == "created"
    assert store.get("team-1", "juice").stopped is False
    # (web-app's own creds live in caller-supplied `env`/CTFd metadata, not
    # generated access fields, so we only assert the lifecycle signaling
    # here -- instance_types-level credential rotation on relaunch is
    # covered directly in tests/test_controller.py.)


def test_instance_response_reports_idle_pause_and_expiry_countdowns(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    status = client.get("/instances/team-1/juice", headers=PLUGIN_HEADERS).get_json()
    assert status["stopped"] is False
    assert "idle_pause_at" in status
    assert "expires_at" in status
    assert status["expires_at"] > status["idle_pause_at"]  # FakeConfig: 240min > 120min ceilings


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


# ── shutdown countdown input validation ───────────────────────────────────────
# `int(body.get("delay_seconds", ...))` used to sit outside the route's
# try block, so anything JSON can express that isn't already an int --
# "abc", null, a bool, a float -- raised straight out of the handler and
# surfaced as an unhandled 500 with a stack trace instead of something the
# caller could act on.

@pytest.mark.parametrize(
    "route,field",
    [
        ("schedule-shutdown", "delay_seconds"),
        ("extend-shutdown", "extend_seconds"),
    ],
)
@pytest.mark.parametrize("bad_value", ["abc", True, 30.5, [30], {"seconds": 30}])
def test_non_integer_countdown_values_are_400_not_500(client, route, field, bad_value):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    client.post("/instances/team-1/juice/schedule-shutdown", json={}, headers=PLUGIN_HEADERS)

    resp = client.post(
        f"/instances/team-1/juice/{route}", json={field: bad_value}, headers=PLUGIN_HEADERS
    )

    assert resp.status_code == 400
    assert field in resp.get_json()["error"]


def test_explicit_null_countdown_falls_back_to_the_configured_default(client):
    # CTFd's plugin omits the key rather than sending null when it wants the
    # configured value (see orchestrator_client.schedule_shutdown), so an
    # explicit null means the same thing -- it must not be a 500.
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)

    resp = client.post(
        "/instances/team-1/juice/schedule-shutdown", json={"delay_seconds": None}, headers=PLUGIN_HEADERS
    )

    assert resp.status_code == 200
    assert resp.get_json()["shutdown_at"] > time.time() + FakeConfig.SHUTDOWN_DELAY_SECONDS - 5


def test_negative_countdown_is_clamped_instead_of_due_immediately(client):
    # {"delay_seconds": -99999} used to set shutdown_at about a day in the
    # PAST, so the very next reaper sweep saw the countdown as already due
    # and destroyed an instance the participant was still using.
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    before = time.time()

    resp = client.post(
        "/instances/team-1/juice/schedule-shutdown", json={"delay_seconds": -99999}, headers=PLUGIN_HEADERS
    )

    assert resp.status_code == 200
    assert resp.get_json()["shutdown_at"] >= before


def test_absurdly_large_countdown_is_clamped(client):
    client.post("/instances", json=WEB_APP_PAYLOAD, headers=PLUGIN_HEADERS)
    before = time.time()

    resp = client.post(
        "/instances/team-1/juice/schedule-shutdown", json={"delay_seconds": 10**12}, headers=PLUGIN_HEADERS
    )

    assert resp.status_code == 200
    assert resp.get_json()["shutdown_at"] <= before + MAX_SHUTDOWN_SECONDS + 5


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


# ── production credential_encryption_key gate ───────────────────────────────
#
# create_app() distinguishes "production" from "test" the same way as every
# other call site in this file: production (app/wsgi.py) always calls
# create_app() with no `config` at all, letting it default to a real
# Config() reading from env/secrets; every test above passes its own
# FakeConfig explicitly. These tests exercise that no-config path directly
# (with a fake docker_client so no real Docker daemon is required).
#
# Config.CREDENTIAL_ENCRYPTION_KEY (like every other Config attribute) is
# computed once at class-definition/import time via _read_secret, so
# setting the CREDENTIAL_ENCRYPTION_KEY env var *after* import (i.e. from
# inside a test) has no effect on it -- monkeypatch.setattr on the class
# attribute directly instead, which is the accurate way to simulate "this
# deployment does/doesn't have the secret mounted."

def test_create_app_refuses_to_start_in_production_without_a_credential_encryption_key(monkeypatch):
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="credential_encryption_key"):
        create_app(docker_client=FakeDockerOrchestratorClient(), start_reaper=False)


def test_create_app_refuses_to_start_in_production_with_an_invalid_credential_encryption_key(monkeypatch):
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "not-a-valid-fernet-key")

    with pytest.raises(RuntimeError, match="credential_encryption_key"):
        create_app(docker_client=FakeDockerOrchestratorClient(), start_reaper=False)


def test_create_app_starts_fine_in_production_with_a_valid_credential_encryption_key(monkeypatch):
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    # This test's real Config has OFFLINE_MODE_SETTING="auto" by default,
    # which would otherwise make create_app() perform a real DNS probe
    # against BASE_DOMAIN ("ctf.local") -- unrelated to what this test
    # actually checks (the credential-key startup gate), and would fail
    # in any sandboxed/CI network where that domain doesn't resolve.
    monkeypatch.setattr(Config, "OFFLINE_MODE_SETTING", False)

    app = create_app(docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True

    assert app.test_client().get("/healthz").status_code == 200


def test_create_app_with_an_explicit_config_is_never_subject_to_the_production_gate(monkeypatch):
    # Passing `config=` explicitly -- what every other test in this file
    # does, and the only thing that distinguishes them from app/wsgi.py's
    # bare create_app() -- must keep working even with no key configured
    # anywhere, since CredentialCipher.from_key_material()'s ephemeral-key
    # fallback is exactly what local dev/test convenience relies on.
    monkeypatch.setattr(Config, "CREDENTIAL_ENCRYPTION_KEY", "")

    class NoKeyConfig(FakeConfig):
        CREDENTIAL_ENCRYPTION_KEY = ""

    app = create_app(config=NoKeyConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True

    assert app.test_client().get("/healthz").status_code == 200
