"""docker/orchestrator/app/wallet.py

Validation for the signed three-track hint-wallet catalog bundle posted to
POST /wallet/sync by the Wargames deploy job's `sync_hint_wallet_bundle()`
(see docker/orchestrator/docs/P0-FIX-LOG-2026-07-23.md for the full
contract, and CEI-Labs-Wargames/docs/hint-wallet-sync-deployment.md and
CEI-Labs-Wargames/deploy.sh for the sender side).

Kept separate from main.py so the schema/economic rules -- independent of
HTTP, HMAC, or revision/digest state -- can be unit tested directly and so
the mapping from "what's wrong with this payload" to "which fail-closed
status code" stays in one place.

Cost model (see cei-labs-event#7): each tier's "cost" is a cumulative
PERCENTAGE of the challenge's own point value (1-99), not a flat/absolute
number of points spent from a shared team currency. There is no team wallet
balance anywhere in this system -- opening a tier just records that this
owner peeked at it for that specific challenge; the percent is applied as a
reduction of that challenge's own award at solve time (see
docker/ctfd/plugins/hint-wallet/solve_hook.py), never debited from a shared
pool.
"""
import hashlib
import json

REQUIRED_TRACKS = frozenset({"bandit", "krypton", "natas"})


class WalletSchemaError(Exception):
    """Structural/type problem -> 400 invalid_schema."""


class WalletIncompleteTracksError(Exception):
    """Bundle doesn't contain exactly the three required tracks -> 400 incomplete_tracks."""


class WalletValidationError(Exception):
    """Well-formed but violates catalog invariants (costs, tamper digest) -> 422 catalog_validation_failed."""


def manifest_digest(manifest: dict) -> str:
    """Same computation as deploy.sh's signing script: sha256 hex over the
    canonical JSON of just the schema_version/track/entries triple (i.e.
    excluding the "digest" key itself, which is added afterward)."""
    canonical = {k: manifest[k] for k in ("schema_version", "track", "entries")}
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WalletSchemaError(message)


def validate_bundle(bundle) -> list:
    """Validate the full signed bundle. Returns the normalized list of
    manifest dicts on success. Raises WalletSchemaError,
    WalletIncompleteTracksError, or WalletValidationError -- callers map
    each to its HTTP status per the contract in the P0 fix log.
    """
    _require(isinstance(bundle, dict), "bundle must be a JSON object")
    _require(set(bundle.keys()) == {"schema_version", "revision", "manifests"}, "unexpected top-level keys")
    _require(bundle.get("schema_version") == 1, "schema_version must be 1")
    revision = bundle.get("revision")
    _require(isinstance(revision, int) and not isinstance(revision, bool) and revision > 0, "revision must be a positive integer")
    manifests = bundle.get("manifests")
    _require(isinstance(manifests, list), "manifests must be a list")

    for manifest in manifests:
        _require(isinstance(manifest, dict), "each manifest must be a JSON object")
        _require(
            set(manifest.keys()) == {"schema_version", "track", "entries", "digest"},
            "unexpected manifest keys",
        )
        _require(manifest.get("schema_version") == 1, "manifest schema_version must be 1")
        _require(isinstance(manifest.get("track"), str) and manifest["track"], "manifest track must be a non-empty string")
        entries = manifest.get("entries")
        _require(isinstance(entries, list) and len(entries) > 0, "manifest entries must be a non-empty list")
        for entry in entries:
            _require(isinstance(entry, dict), "each entry must be a JSON object")
            _require(set(entry.keys()) == {"name", "tiers"}, "unexpected entry keys")
            _require(isinstance(entry.get("name"), str) and entry["name"], "entry name must be a non-empty string")
            tiers = entry.get("tiers")
            _require(isinstance(tiers, list) and len(tiers) == 3, "entry must have exactly 3 tiers")
            for tier_obj in tiers:
                _require(isinstance(tier_obj, dict), "each tier must be a JSON object")
                _require(set(tier_obj.keys()) == {"tier", "cost", "content"}, "unexpected tier keys")
                _require(
                    isinstance(tier_obj.get("tier"), int) and not isinstance(tier_obj.get("tier"), bool),
                    "tier number must be an integer",
                )
                _require(
                    isinstance(tier_obj.get("cost"), int)
                    and not isinstance(tier_obj.get("cost"), bool)
                    and 0 < tier_obj["cost"] < 100,
                    "tier cost must be an integer percent of the challenge's value, strictly between 0 and 100",
                )
                _require(isinstance(tier_obj.get("content"), str) and tier_obj["content"], "tier content must be a non-empty string")
        _require(isinstance(manifest.get("digest"), str) and manifest["digest"], "manifest digest must be a non-empty string")

    # Track completeness: exactly the three required tracks, no duplicates, no extras.
    tracks = [manifest["track"] for manifest in manifests]
    if len(manifests) != 3 or set(tracks) != REQUIRED_TRACKS or len(set(tracks)) != len(tracks):
        raise WalletIncompleteTracksError(
            f"bundle must contain exactly the tracks {sorted(REQUIRED_TRACKS)} once each, got {sorted(tracks)}"
        )

    # Economic / tamper validation.
    for manifest in manifests:
        expected_digest = manifest_digest(manifest)
        if manifest["digest"] != expected_digest:
            raise WalletValidationError(f"manifest digest mismatch for track {manifest['track']!r}")
        for entry in manifest["entries"]:
            tiers = sorted(entry["tiers"], key=lambda t: t["tier"])
            tier_numbers = [t["tier"] for t in tiers]
            if tier_numbers != [1, 2, 3]:
                raise WalletValidationError(f"entry {entry['name']!r} must have tiers numbered 1, 2, 3 exactly")
            costs = [t["cost"] for t in tiers]
            if not (costs[0] < costs[1] < costs[2]):
                raise WalletValidationError(f"entry {entry['name']!r} tier costs must be strictly increasing")

    return manifests


def find_hint_cost(manifests: list, track: str, entry_name: str, tier: int) -> "int | None":
    """Look up a specific hint tier's cost in an already-accepted catalog's
    manifest list (as returned by WalletStore.get_catalog()["manifests"]).
    Returns None if the (track, entry, tier) doesn't exist."""
    for manifest in manifests:
        if manifest.get("track") != track:
            continue
        for entry in manifest.get("entries", []):
            if entry.get("name") != entry_name:
                continue
            for tier_obj in entry.get("tiers", []):
                if tier_obj.get("tier") == tier:
                    return tier_obj.get("cost")
    return None


def find_hint_content(manifests: list, track: str, entry_name: str, tier: int) -> "str | None":
    for manifest in manifests:
        if manifest.get("track") != track:
            continue
        for entry in manifest.get("entries", []):
            if entry.get("name") != entry_name:
                continue
            for tier_obj in entry.get("tiers", []):
                if tier_obj.get("tier") == tier:
                    return tier_obj.get("content")
    return None
