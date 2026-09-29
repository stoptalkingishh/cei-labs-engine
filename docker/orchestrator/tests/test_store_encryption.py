"""Regression tests for store.py's at-rest encryption of plan_json (see
crypto.py and store.py's module docstring): every InstanceStore/RangeStore
write must land on disk as Fernet ciphertext, never as the plaintext JSON
blob that carries VNC/SSH passwords and per-team flag secrets -- and a
pre-encryption row (plain JSON) must still be readable so an in-place
upgrade doesn't strand already-running instances.
"""
import os
import sqlite3
import tempfile

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app import instance_types as it
from app.crypto import CredentialCipher
from app.docker_client import ServiceSpec
from app.instance_types import RangePlan
from app.store import InstanceStore, RangeStore

SECRET_MARKER = "VERY-SECRET-VNC-PASSWORD-DO-NOT-LEAK"


def _make_plan_with_marker():
    plan = it.plan_web_app("team-1", "juice", {"image": "img"}, "ctf.local", "challenge-net")
    plan.access["vnc_password"] = SECRET_MARKER
    return plan


def _raw_plan_json_column(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT plan_json FROM instances WHERE owner_id = 'team-1'").fetchone()
    finally:
        conn.close()
    return row[0]


def test_persisted_plan_json_is_not_plaintext_on_disk():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        cipher = CredentialCipher(Fernet.generate_key())
        store = InstanceStore(db_path=db_path, cipher=cipher)

        assert store.reserve("team-1", "juice") is True
        store.finalize("team-1", "juice", _make_plan_with_marker())
        store.close()

        raw = _raw_plan_json_column(db_path)
        assert SECRET_MARKER not in raw
        assert not raw.startswith("{"), "plan_json was stored as plaintext JSON, not ciphertext"


def test_encrypted_plan_round_trips_through_get():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        cipher = CredentialCipher(Fernet.generate_key())
        store = InstanceStore(db_path=db_path, cipher=cipher)

        store.reserve("team-1", "juice")
        store.finalize("team-1", "juice", _make_plan_with_marker())

        record = store.get("team-1", "juice")
        assert record.plan.access["vnc_password"] == SECRET_MARKER
        store.close()


def test_a_row_written_under_one_key_is_unreadable_under_a_different_key():
    """Simulates losing/rotating the credential_encryption_key secret without
    a migration: an old ciphertext row can no longer be decrypted, and the
    module deliberately does NOT silently treat wrong-key ciphertext as
    plaintext (that fallback exists only for genuinely pre-encryption rows,
    which are valid JSON, not Fernet tokens under a different key)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        store_a = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        store_a.reserve("team-1", "juice")
        store_a.finalize("team-1", "juice", _make_plan_with_marker())
        store_a.close()

        store_b = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        try:
            store_b.get("team-1", "juice")
        except Exception:
            pass  # acceptable: surfacing an error is fine
        else:
            record = store_b.get("team-1", "juice")
            # If it didn't raise, it must NOT have silently produced the
            # real secret from garbage ciphertext.
            assert record is None or SECRET_MARKER not in str(record.plan.access)
        store_b.close()


def test_pre_encryption_plaintext_row_is_still_readable_after_upgrade():
    """A row written before this module started encrypting (plain JSON, as
    every row was pre-P0-fix) must still be readable post-upgrade -- an
    in-place deploy shouldn't strand already-running instances."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")

        # Write a plaintext row directly, bypassing the cipher entirely --
        # exactly what every pre-upgrade row on disk looks like.
        plan = _make_plan_with_marker()
        unencrypted_store = InstanceStore(db_path=db_path)  # ephemeral cipher, irrelevant to this write
        unencrypted_store.reserve("team-1", "juice")
        conn = sqlite3.connect(db_path)
        from app.store import _plan_to_json
        conn.execute(
            "UPDATE instances SET plan_json = ? WHERE owner_id = 'team-1' AND instance_key = 'juice'",
            (_plan_to_json(plan),),
        )
        conn.commit()
        conn.close()
        unencrypted_store.close()

        # A real deployment's store (with the real configured cipher) must
        # still be able to read this legacy plaintext row.
        store = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        record = store.get("team-1", "juice")
        assert record.plan.access["vnc_password"] == SECRET_MARKER
        store.close()


def test_range_store_also_encrypts_plan_json_at_rest():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "ranges.db")
        cipher = CredentialCipher(Fernet.generate_key())
        store = RangeStore(db_path=db_path, cipher=cipher)

        plan = RangePlan(
            owner_id="team-1",
            network="cei-labs_range-team-1",
            attacker_service=ServiceSpec(name="attacker-team-1", image="k", networks=["cei-labs_range-team-1"]),
            access={"attacker_password": SECRET_MARKER},
        )
        store.reserve("team-1")
        store.finalize("team-1", plan)

        conn = sqlite3.connect(db_path)
        raw = conn.execute("SELECT plan_json FROM ranges WHERE owner_id = 'team-1'").fetchone()[0]
        conn.close()
        assert SECRET_MARKER not in raw

        record = store.get("team-1")
        assert record.plan.access["attacker_password"] == SECRET_MARKER
        store.close()


# ── flag_secret_keys survives persistence ──────────────────────────────────
#
# main.py can only keep per-team flag values out of an API response if it
# still knows which `access` keys they are after a restart -- the request
# spec that named them (secret_keys/alpha_secret_keys/fixed_secret_keys) is
# long gone by then. InstancePlan.flag_secret_keys is persisted inside the
# same encrypted plan_json for exactly that reason.

def test_flag_secret_keys_round_trip_through_the_store():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        key = Fernet.generate_key()
        plan = it.plan_single_target(
            "team-1", "krypton", {"image": "img", "secret_keys": ["krypton1", "krypton2"]},
            allocated_port=32000, base_domain="ctf.local",
        )
        assert plan.flag_secret_keys == ["krypton1", "krypton2"]

        store = InstanceStore(db_path=db_path, cipher=CredentialCipher(key))
        store.reserve("team-1", "krypton")
        store.finalize("team-1", "krypton", plan)
        store.close()

        # A brand-new store built from the same key material -- i.e. exactly
        # what a restarted orchestrator process sees.
        reloaded = InstanceStore(db_path=db_path, cipher=CredentialCipher(key))
        record = reloaded.get("team-1", "krypton")
        assert record.plan.flag_secret_keys == ["krypton1", "krypton2"]
        # Only the generated per-level values, never ordinary connect info --
        # getting this list wrong is what would drop connect_port from a
        # player's launch panel.
        assert "connect_port" not in record.plan.flag_secret_keys
        reloaded.close()


def test_a_web_app_plan_records_no_flag_secret_keys():
    plan = it.plan_web_app("team-1", "juice", {"image": "img"}, "ctf.local", "challenge-net")
    assert plan.flag_secret_keys == []


# ── the legacy-plaintext fallback is a shape test, not a "decrypt threw" test ──
#
# _decrypt_plan used to return the raw stored value on ANY InvalidToken /
# ValueError, so the one-way pre-upgrade migration path also silently
# promoted every undecryptable row -- wrong key, truncated write, tampering,
# plain garbage -- to authoritative-plan status. These tests pin the narrowed
# contract: a value that actually looks like the pre-upgrade row is honoured,
# and everything else has to decrypt or raise.

def _write_raw_plan_json(db_path: str, owner_id: str, instance_key: str, raw: str) -> None:
    """Put an arbitrary value into plan_json the way a pre-upgrade row (or
    anything else writing to the orchestrator_data volume) would."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO instances "
        "(owner_id, instance_key, plan_json, created_at, last_accessed, extensions_used) "
        "VALUES (?, ?, ?, 0, 0, 0)",
        (owner_id, instance_key, raw),
    )
    conn.commit()
    conn.close()


def test_a_non_json_corrupt_row_is_rejected_rather_than_accepted_as_the_plan():
    """The regression this guards: under the old fallback this returned the
    corrupt string verbatim from _decrypt_plan as though it were the
    decrypted plan."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        store = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        _write_raw_plan_json(db_path, "team-1", "juice", "this-is-not-a-plan-and-not-ciphertext")

        with pytest.raises(Exception):
            store.get("team-1", "juice")
        store.close()


def test_a_corrupt_row_does_not_silently_pass_through_the_teardown_path_either():
    """teardown()/relaunch reach the plan through claim_for_replacement(),
    which goes through the same _decrypt_plan -- so a corrupt row has to fail
    there too rather than being handed to _plan_from_json as a 'plan'. Under
    the old swallow this was an unexplained 500 two layers downstream of the
    actual cause; here the point is only that it raises, and never returns a
    record built out of the corrupt value."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        store = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        _write_raw_plan_json(db_path, "team-1", "juice", "truncated-ciphertext")

        with pytest.raises(Exception):
            store.claim_for_replacement("team-1", "juice")
        store.close()


def test_ciphertext_written_under_a_different_key_raises_rather_than_being_returned():
    """Same failure mode, reached the realistic way: a row persisted under a
    credential_encryption_key this process no longer has. Its stored value is
    genuine Fernet ciphertext and does not start with '{', so it must never
    take the legacy-plaintext path."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = os.path.join(tmp_dir, "instances.db")
        store = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        store.reserve("team-1", "juice")
        store.finalize("team-1", "juice", _make_plan_with_marker())
        ciphertext = _raw_plan_json_column(db_path)
        store.close()
        assert not ciphertext.startswith("{")

        store_b = InstanceStore(db_path=db_path, cipher=CredentialCipher(Fernet.generate_key()))
        with pytest.raises(InvalidToken):
            store_b.get("team-1", "juice")
        store_b.close()
