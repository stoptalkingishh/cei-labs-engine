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

from cryptography.fernet import Fernet

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
