"""Unit tests for app/wallet.py's schema/economic validation of the signed
three-track hint-wallet bundle, independent of HTTP/HMAC/revision-state
concerns (those are covered in test_wallet_api.py). Mirrors deploy.sh's
`sync_hint_wallet_bundle()` payload shape exactly -- see
docs/P0-FIX-LOG-2026-07-23.md.
"""
import hashlib
import json

import pytest

from app.wallet import (
    WalletIncompleteTracksError,
    WalletSchemaError,
    WalletValidationError,
    find_hint_content,
    find_hint_cost,
    manifest_digest,
    validate_bundle,
)


def _manifest(track: str, entries=None) -> dict:
    entries = entries if entries is not None else [
        {
            "name": f"{track} challenge 1",
            "tiers": [
                {"tier": 1, "cost": 10, "content": "nudge"},
                {"tier": 2, "cost": 20, "content": "bigger nudge"},
                {"tier": 3, "cost": 30, "content": "answer"},
            ],
        }
    ]
    body = {"schema_version": 1, "track": track, "entries": entries}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    body["digest"] = hashlib.sha256(raw).hexdigest()
    return body


def _bundle(revision=1, tracks=("bandit", "krypton", "natas"), overrides=None):
    manifests = [_manifest(t) for t in tracks]
    if overrides:
        for i, patch in overrides.items():
            manifests[i].update(patch)
    return {"schema_version": 1, "revision": revision, "manifests": manifests}


# ── happy path ──────────────────────────────────────────────────────────────

def test_valid_bundle_round_trips():
    bundle = _bundle()
    manifests = validate_bundle(bundle)
    assert {m["track"] for m in manifests} == {"bandit", "krypton", "natas"}


def test_manifest_digest_matches_deploy_sh_computation():
    manifest = _manifest("bandit")
    assert manifest_digest(manifest) == manifest["digest"]


def test_find_hint_cost_and_content():
    bundle = _bundle()
    manifests = validate_bundle(bundle)
    assert find_hint_cost(manifests, "bandit", "bandit challenge 1", 2) == 20
    assert find_hint_content(manifests, "bandit", "bandit challenge 1", 2) == "bigger nudge"
    assert find_hint_cost(manifests, "bandit", "does not exist", 1) is None
    assert find_hint_cost(manifests, "bandit", "bandit challenge 1", 9) is None


# ── schema errors -> 400 invalid_schema ─────────────────────────────────────

@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("revision"),
    lambda b: b.__setitem__("revision", 0),
    lambda b: b.__setitem__("revision", -1),
    lambda b: b.__setitem__("revision", "1"),
    lambda b: b.__setitem__("revision", True),
    lambda b: b.__setitem__("schema_version", 2),
    lambda b: b.__setitem__("extra_key", "nope"),
    lambda b: b.__setitem__("manifests", "not-a-list"),
])
def test_top_level_schema_violations_raise_schema_error(mutate):
    bundle = _bundle()
    mutate(bundle)
    with pytest.raises(WalletSchemaError):
        validate_bundle(bundle)


def test_manifest_missing_digest_is_schema_error():
    bundle = _bundle()
    del bundle["manifests"][0]["digest"]
    with pytest.raises(WalletSchemaError):
        validate_bundle(bundle)


def test_entry_with_wrong_tier_count_is_schema_error():
    bad_entries = [{
        "name": "x",
        "tiers": [
            {"tier": 1, "cost": 10, "content": "a"},
            {"tier": 2, "cost": 20, "content": "b"},
        ],
    }]
    bundle = _bundle(overrides={0: {"entries": bad_entries}})
    # overrides mutates before digest recompute, so refresh the digest to
    # isolate this test to the tier-count schema violation, not a digest
    # mismatch (covered separately).
    m = bundle["manifests"][0]
    raw = json.dumps(
        {k: m[k] for k in ("schema_version", "track", "entries")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    m["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(WalletSchemaError):
        validate_bundle(bundle)


def test_negative_or_zero_cost_is_schema_error():
    entries = [{
        "name": "x",
        "tiers": [
            {"tier": 1, "cost": 0, "content": "a"},
            {"tier": 2, "cost": 20, "content": "b"},
            {"tier": 3, "cost": 30, "content": "c"},
        ],
    }]
    bundle = _bundle(overrides={0: {"entries": entries}})
    m = bundle["manifests"][0]
    raw = json.dumps(
        {k: m[k] for k in ("schema_version", "track", "entries")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    m["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(WalletSchemaError):
        validate_bundle(bundle)


# ── track completeness -> 400 incomplete_tracks ─────────────────────────────

def test_missing_track_is_incomplete_tracks():
    bundle = _bundle(tracks=("bandit", "krypton"))
    with pytest.raises(WalletIncompleteTracksError):
        validate_bundle(bundle)


def test_duplicate_track_is_incomplete_tracks():
    bundle = _bundle(tracks=("bandit", "bandit", "natas"))
    with pytest.raises(WalletIncompleteTracksError):
        validate_bundle(bundle)


def test_extra_unknown_track_is_incomplete_tracks():
    bundle = _bundle(tracks=("bandit", "krypton", "natas", "extra"))
    with pytest.raises(WalletIncompleteTracksError):
        validate_bundle(bundle)


# ── economic/tamper validation -> 422 catalog_validation_failed ────────────

def test_tampered_manifest_digest_is_validation_error():
    bundle = _bundle()
    bundle["manifests"][0]["digest"] = "0" * 64
    with pytest.raises(WalletValidationError):
        validate_bundle(bundle)


def test_non_increasing_tier_costs_is_validation_error():
    entries = [{
        "name": "x",
        "tiers": [
            {"tier": 1, "cost": 30, "content": "a"},
            {"tier": 2, "cost": 20, "content": "b"},
            {"tier": 3, "cost": 40, "content": "c"},
        ],
    }]
    bundle = _bundle(overrides={0: {"entries": entries}})
    m = bundle["manifests"][0]
    raw = json.dumps(
        {k: m[k] for k in ("schema_version", "track", "entries")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    m["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(WalletValidationError):
        validate_bundle(bundle)


def test_wrong_tier_numbering_is_validation_error():
    entries = [{
        "name": "x",
        "tiers": [
            {"tier": 1, "cost": 10, "content": "a"},
            {"tier": 2, "cost": 20, "content": "b"},
            {"tier": 4, "cost": 30, "content": "c"},
        ],
    }]
    bundle = _bundle(overrides={0: {"entries": entries}})
    m = bundle["manifests"][0]
    raw = json.dumps(
        {k: m[k] for k in ("schema_version", "track", "entries")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    m["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(WalletValidationError):
        validate_bundle(bundle)
