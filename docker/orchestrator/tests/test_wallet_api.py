"""HTTP-level tests for the hint-wallet endpoints (POST /wallet/sync,
POST /wallet/deduct, GET /wallet/balance/<owner_id>, POST /wallet/credit).

Covers the fail-closed contract from docs/P0-FIX-LOG-2026-07-23.md: HMAC
signature auth (including secret rotation overlap), the revision/digest
state machine, all-or-nothing three-track schema/track/economic validation,
and that a rejected sync never disturbs the previously accepted catalog.
Also covers deduct/balance/credit and a concurrency test proving double-
spend is impossible under gunicorn's multi-worker model, matching the rigor
of tests/test_store_concurrency.py for InstanceStore.
"""
import hashlib
import hmac
import json
import os
import tempfile
import threading

import pytest

from app.config import Config
from app.main import create_app

from .fakes import FakeDockerOrchestratorClient

SECRET = "a" * 32
PREVIOUS_SECRET = "b" * 32


class FakeConfig(Config):
    BASE_DOMAIN = "ctf.local"
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
    HINT_WALLET_SYNC_SECRET = SECRET
    HINT_WALLET_SYNC_SECRET_PREVIOUS = ""


PLUGIN_HEADERS = {"X-Orchestrator-Auth": "test-plugin-secret"}


@pytest.fixture
def client():
    app = create_app(config=FakeConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    return app.test_client()


def _manifest(track: str, entry_costs=(10, 20, 30), entry_name=None) -> dict:
    entry_name = entry_name or f"{track} challenge 1"
    body = {
        "schema_version": 1,
        "track": track,
        "entries": [
            {
                "name": entry_name,
                "tiers": [
                    {"tier": 1, "cost": entry_costs[0], "content": "nudge"},
                    {"tier": 2, "cost": entry_costs[1], "content": "bigger nudge"},
                    {"tier": 3, "cost": entry_costs[2], "content": "answer"},
                ],
            }
        ],
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    body["digest"] = hashlib.sha256(raw).hexdigest()
    return body


def _signed_bundle(secret: str, revision: int, manifests=None):
    manifests = manifests if manifests is not None else [_manifest("bandit"), _manifest("krypton"), _manifest("natas")]
    bundle = {"schema_version": 1, "revision": revision, "manifests": manifests}
    raw = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, signature


def _sync(client, secret, revision, manifests=None):
    raw, signature = _signed_bundle(secret, revision, manifests)
    return client.post(
        "/wallet/sync",
        data=raw,
        headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"},
    )


# ── auth / signature ────────────────────────────────────────────────────────

def test_sync_needs_no_x_orchestrator_auth_header(client):
    # Different trust boundary -- the CTFd-plugin secret must not work here.
    raw, signature = _signed_bundle(SECRET, 1)
    resp = client.post(
        "/wallet/sync",
        data=raw,
        headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_sync_without_signature_header_is_401(client):
    raw, _ = _signed_bundle(SECRET, 1)
    resp = client.post("/wallet/sync", data=raw, headers={"Content-Type": "application/json"})
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_signature"


def test_sync_with_wrong_signature_is_401(client):
    raw, _ = _signed_bundle(SECRET, 1)
    resp = client.post(
        "/wallet/sync",
        data=raw,
        headers={"X-Hint-Wallet-Signature": "0" * 64, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_sync_signed_with_ctfd_plugin_secret_is_401(client):
    raw, signature = _signed_bundle("test-plugin-secret", 1)
    resp = client.post(
        "/wallet/sync",
        data=raw,
        headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_sync_rejected_when_no_secret_configured():
    class NoSecretConfig(FakeConfig):
        HINT_WALLET_SYNC_SECRET = ""
        HINT_WALLET_SYNC_SECRET_PREVIOUS = ""

    app = create_app(config=NoSecretConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    c = app.test_client()
    raw, signature = _signed_bundle(SECRET, 1)
    resp = c.post(
        "/wallet/sync",
        data=raw,
        headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "secret_or_database_unavailable"


def test_rotation_accepts_both_old_and_new_secret():
    class RotatingConfig(FakeConfig):
        HINT_WALLET_SYNC_SECRET = SECRET
        HINT_WALLET_SYNC_SECRET_PREVIOUS = PREVIOUS_SECRET

    app = create_app(config=RotatingConfig(), docker_client=FakeDockerOrchestratorClient(), start_reaper=False)
    app.testing = True
    c = app.test_client()

    resp = _sync(c, SECRET, 1)
    assert resp.status_code == 200

    resp = _sync(c, PREVIOUS_SECRET, 2)
    assert resp.status_code == 200


def test_old_secret_rejected_once_retired(client):
    # FakeConfig has no HINT_WALLET_SYNC_SECRET_PREVIOUS -- simulates the old
    # secret having been retired after the rotation overlap window.
    resp = _sync(client, PREVIOUS_SECRET, 1)
    assert resp.status_code == 401


# ── schema / track / economic validation ────────────────────────────────────

def test_unparseable_body_is_400_invalid_schema(client):
    raw = b"not json"
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    resp = client.post(
        "/wallet/sync", data=raw, headers={"X-Hint-Wallet-Signature": signature, "Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_schema"


def test_missing_track_is_400_incomplete_tracks(client):
    resp = _sync(client, SECRET, 1, manifests=[_manifest("bandit"), _manifest("krypton")])
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "incomplete_tracks"


def test_bad_tier_costs_is_422_catalog_validation_failed(client):
    bad = _manifest("bandit")
    bad["entries"][0]["tiers"][1]["cost"] = 5  # tier 2 cheaper than tier 1
    # digest now stale on purpose -- but recompute so this fails validation
    # (cost ordering), not schema (digest mismatch), to isolate the case.
    raw = json.dumps(
        {k: bad[k] for k in ("schema_version", "track", "entries")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    bad["digest"] = hashlib.sha256(raw).hexdigest()
    resp = _sync(client, SECRET, 1, manifests=[bad, _manifest("krypton"), _manifest("natas")])
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "catalog_validation_failed"


def test_tampered_digest_is_422(client):
    bad = _manifest("bandit")
    bad["digest"] = "f" * 64
    resp = _sync(client, SECRET, 1, manifests=[bad, _manifest("krypton"), _manifest("natas")])
    assert resp.status_code == 422


def test_rejected_sync_leaves_previous_catalog_active(client):
    assert _sync(client, SECRET, 1).status_code == 200
    bad_resp = _sync(client, SECRET, 2, manifests=[_manifest("bandit"), _manifest("krypton")])
    assert bad_resp.status_code == 400

    # Deduct still resolves against the revision-1 catalog, proving the
    # rejected revision-2 sync never touched stored state.
    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "bandit challenge 1", "tier": 1},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 402  # insufficient balance, but hint_not_found would mean catalog was lost
    assert resp.get_json()["error"] == "insufficient_balance"


# ── revision / digest state machine ─────────────────────────────────────────

def test_first_sync_any_positive_revision_is_accepted(client):
    resp = _sync(client, SECRET, 5)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_higher_revision_accepted(client):
    _sync(client, SECRET, 1)
    resp = _sync(client, SECRET, 2)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_same_revision_same_digest_is_idempotent(client):
    _sync(client, SECRET, 1)
    resp = _sync(client, SECRET, 1)  # identical bundle -> identical raw bytes -> identical digest
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "idempotent"


def test_same_revision_different_digest_is_409_conflict(client):
    _sync(client, SECRET, 1)
    resp = _sync(client, SECRET, 1, manifests=[_manifest("bandit", entry_costs=(1, 2, 3)), _manifest("krypton"), _manifest("natas")])
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "revision_digest_conflict"


def test_lower_revision_is_409_stale(client):
    _sync(client, SECRET, 5)
    resp = _sync(client, SECRET, 3)
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "stale_revision"


def test_no_rollback_via_same_revision_lower_content(client):
    _sync(client, SECRET, 5)
    stale = _sync(client, SECRET, 4)
    assert stale.status_code == 409
    # A correction must ship as a new higher revision, not a resend of an
    # old one -- confirm a genuinely higher revision still works afterward.
    resp = _sync(client, SECRET, 6)
    assert resp.status_code == 200


# ── deduct / balance / credit ────────────────────────────────────────────────

def test_deduct_without_catalog_is_409(client):
    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "x", "tier": 1},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_active_catalog"


def test_deduct_unknown_hint_is_404(client):
    _sync(client, SECRET, 1)
    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "does not exist", "tier": 1},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 404


def test_deduct_requires_orchestrator_auth(client):
    _sync(client, SECRET, 1)
    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "bandit challenge 1", "tier": 1},
    )
    assert resp.status_code == 401


def test_credit_then_deduct_then_balance(client):
    _sync(client, SECRET, 1)
    resp = client.post("/wallet/credit", json={"owner_id": "team-1", "amount": 100}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["balance"] == 100

    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "bandit challenge 1", "tier": 1},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "unlocked"
    assert body["balance"] == 90
    assert body["content"] == "nudge"

    resp = client.get("/wallet/balance/team-1", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["balance"] == 90


def test_deduct_is_idempotent_on_retry(client):
    _sync(client, SECRET, 1)
    client.post("/wallet/credit", json={"owner_id": "team-1", "amount": 100}, headers=PLUGIN_HEADERS)
    payload = {"owner_id": "team-1", "track": "bandit", "entry_name": "bandit challenge 1", "tier": 1}
    first = client.post("/wallet/deduct", json=payload, headers=PLUGIN_HEADERS)
    second = client.post("/wallet/deduct", json=payload, headers=PLUGIN_HEADERS)
    assert first.get_json()["balance"] == 90
    assert second.status_code == 200
    assert second.get_json()["status"] == "already_unlocked"
    assert second.get_json()["balance"] == 90  # not double-charged


def test_deduct_insufficient_balance(client):
    _sync(client, SECRET, 1)
    resp = client.post(
        "/wallet/deduct",
        json={"owner_id": "team-1", "track": "bandit", "entry_name": "bandit challenge 1", "tier": 3},
        headers=PLUGIN_HEADERS,
    )
    assert resp.status_code == 402
    assert resp.get_json()["error"] == "insufficient_balance"


def test_balance_for_unknown_team_defaults_to_zero(client):
    resp = client.get("/wallet/balance/never-seen", headers=PLUGIN_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["balance"] == 0


def test_credit_rejects_non_positive_amount(client):
    resp = client.post("/wallet/credit", json={"owner_id": "team-1", "amount": 0}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 400
    resp = client.post("/wallet/credit", json={"owner_id": "team-1", "amount": -5}, headers=PLUGIN_HEADERS)
    assert resp.status_code == 400


# ── concurrency: no double-spend across independent WalletStore objects ────

def test_deduct_double_spend_is_impossible_across_workers():
    """Same shape as test_store_concurrency.py: many independent WalletStore
    objects sharing one on-disk file (standing in for gunicorn worker
    processes) all racing unlock_hint() for the same team/hint at once.
    Exactly one may spend the balance; the rest must see "already_unlocked"
    once the balance-holder wins, with the final balance debited exactly
    once."""
    from app.store import WalletStore

    n_workers = 20
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "wallet.db")
        stores = [WalletStore(db_path=db_path) for _ in range(n_workers)]
        stores[0].credit("team-1", 100)
        results = [None] * n_workers
        barrier = threading.Barrier(n_workers)

        def race(i):
            barrier.wait()
            results[i] = stores[i].unlock_hint("team-1", "bandit", "challenge-1", 1, cost=10)

        threads = [threading.Thread(target=race, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        statuses = [status for status, _ in results]
        assert statuses.count("unlocked") == 1
        assert statuses.count("already_unlocked") == n_workers - 1
        assert stores[0].get_balance("team-1") == 90

        for s in stores:
            s.close()
