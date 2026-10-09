"""docker/orchestrator/app/naming.py

Turns arbitrary owner/instance identifiers into names that are simultaneously
valid Docker service/network names AND valid DNS labels (since they end up in
Traefik Host() rules as <slug>.apps.<base_domain>).

Truncation is collision-resistant (issue #65)
-------------------------------------------
Every name here is derived by *truncating* a normalized identifier, because
owner ids and instance keys are attacker-influenced strings of unbounded
length and a DNS label is capped at 63 bytes. A bare `slug[:40]` truncation
silently maps two different identifiers onto one name, and that is a
cross-tenant isolation bug, not a cosmetic one: docker_client.create_service()
is idempotent (`if existing is not None: return existing`), so on a collision
team B's launch adopts team A's already-running service, network and
hostname, while B's own store row records that plan as B's -- and the store
keys on the *raw* owner_id, so nothing detects it. Nothing detects it later
either: B can then read/plant in A's challenge through the shared container.

So truncation now always carries a short SHA-256 digest of the FULL
pre-truncation slug: `f"{prefix}-{sha256(slug)[:8]}"`. Properties this buys,
in order of importance:

  * Deterministic. The same identifier always produces the same name, which
    is what relaunch/reboot idempotency depends on (a relaunch has to
    re-derive the identical Docker names to tear the old ones down).
  * Applied only when truncation is actually needed. Short ids -- which is
    every id a real deployment produces today, since CTFd passes
    `owner_id = str(user.account_id)`, a short integer -- are returned
    byte-for-byte unchanged. `slugify("team-1") == "team-1"`. The blast
    radius is confined to the (already broken) long-identifier case; this is
    NOT cosmetic churn, and it should not be read as such.
  * The digest covers the whole slug, not just the discarded tail, so two
    identifiers that share a 31-char prefix cannot collide.

Combined names are capped as a whole, not per half. `instance_id` used to be
`slug[:40] + "-" + slug[:40]` = 81 bytes, and it is embedded verbatim in the
Traefik `Host()` rule (see instance_types._traefik_labels) -- a label over 63
bytes can never resolve, so such a URL was dead on arrival. instance_id() is
therefore capped at MAX_LABEL_LEN over the joined string, keeping the
owner-side prefix readable and appending a digest of the full pair.

BREAKING CHANGE — an operator action is required
------------------------------------------------
Any deployment whose identifiers were long enough to truncate will, after
this change, map those identifiers to *different* service/network/hostname
names than it used to. That is intended: the old names were not unique, so
"preserving" them would preserve the aliasing being removed.

There is deliberately NO migration that renames live Docker objects. Doing
so would mean writing the very aliasing this fix removes (rewriting B's name
onto A's existing container is exactly the cross-tenant adoption bug), and a
half-migrated deployment is strictly worse than either pure state: some
instances resolvable under their new name, some under their old, and the
store's plan rows no longer matching either. Instead:

    1. Let in-flight work finish.
    2. Drain the affected instances through the existing teardown/relaunch
       path (reaper sweep or an explicit teardown of the long-identifier
       instances).
    3. Let them relaunch. Fresh names are derived, and from then on the
       mapping is collision-free.

**This change needs a CHANGELOG.md entry** (operator-visible breaking
change: existing long-identifier instances must be drained and relaunched).
"""
import hashlib
import re

_INVALID = re.compile(r"[^a-z0-9-]+")
_DASHES = re.compile(r"-{2,}")
# Readable-prefix budget for one normalized identifier. Kept at 40 so
# composed names built from two slugs plus a separator still fit under
# MAX_LABEL_LEN in the common case.
MAX_SLUG_LEN = 40
# A DNS label is at most 63 bytes, and instance_id() is embedded verbatim in
# a Traefik Host() rule -- see the module docstring.
MAX_LABEL_LEN = 63
# Hex characters of SHA-256 kept as the disambiguating suffix. 8 hex chars =
# 32 bits: with a handful of tenants per deployment that is a ~1-in-4-billion
# chance of a collision per pair, which is well below the rate at which
# anything else in the stack (port allocation, the SQLite PK) fails.
_HASH_LEN = 8


class InvalidIdentifierError(ValueError):
    pass


def _digest(value: str) -> str:
    """Short, stable, lowercase-hex disambiguator for a normalized slug."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_HASH_LEN]


def _cap(value: str, limit: int) -> str:
    """Cap `value` to `limit` bytes, keeping a readable prefix and appending
    a digest of the whole value so distinct inputs stay distinct.

    Only ever shortens: a value already within `limit` is returned unchanged,
    which is what keeps ordinary short identifiers (a CTFd numeric account
    id) human-readable and byte-identical to the pre-#65 names.
    """
    if len(value) <= limit:
        return value
    # rstrip so the truncation point can never produce a doubled or trailing
    # dash; the slug alphabet plus '-' is then the only thing emitted, so the
    # result is always a syntactically valid DNS label.
    prefix = value[: limit - _HASH_LEN - 1].rstrip("-")
    return f"{prefix}-{_digest(value)}"


def slugify(value: str) -> str:
    if not value or not value.strip():
        raise InvalidIdentifierError("identifier must not be empty")
    slug = value.strip().lower()
    slug = _INVALID.sub("-", slug)
    slug = _DASHES.sub("-", slug).strip("-")
    if not slug:
        raise InvalidIdentifierError(f"identifier {value!r} has no valid characters")
    return _cap(slug, MAX_SLUG_LEN)


def instance_id(owner_id: str, instance_key: str) -> str:
    """Stable, DNS-safe identifier for one team's one challenge instance.

    Capped at MAX_LABEL_LEN over the *joined* value, not at MAX_SLUG_LEN per
    half: the result goes straight into `Host(`<instance_id>.apps.<domain>`)`
    (instance_types._traefik_labels), and a 63-byte cap on each half still
    allowed an 81-byte label, i.e. a hostname that could never resolve.
    """
    return _cap(f"{slugify(owner_id)}-{slugify(instance_key)}", MAX_LABEL_LEN)


def service_name(owner_id: str, instance_key: str, role: str | None = None) -> str:
    base = f"chinst-{instance_id(owner_id, instance_key)}"
    return f"{base}-{role}" if role else base


def gateway_service_name(owner_id: str, instance_key: str) -> str:
    return service_name(owner_id, instance_key, "gateway")


def network_name(owner_id: str, instance_key: str) -> str:
    return f"chnet-{instance_id(owner_id, instance_key)}"


def access_hostname(owner_id: str, instance_key: str, base_domain: str) -> str:
    return f"{instance_id(owner_id, instance_key)}.apps.{base_domain}"


# ── Range-level (shared attacker + isolated network per team) ────────────────
# Unlike a single instance, a range is scoped to owner_id ALONE — every
# target-attacker challenge a team launches shares the same attacker and
# network, so these names must never include instance_key.
# These compose slugify(owner_id) directly rather than instance_id(), so they
# inherit its collision resistance without any of the 63-byte label cap --
# a Docker service/network name is not DNS-label-limited, only the Host()
# label (range_attacker_hostname) is, and that one is a bare owner slug of at
# most MAX_SLUG_LEN.

def range_network_name(owner_id: str) -> str:
    return f"chrange-{slugify(owner_id)}"


def range_attacker_service_name(owner_id: str) -> str:
    return f"chrange-{slugify(owner_id)}-attacker"


def range_gateway_service_name(owner_id: str) -> str:
    return f"chrange-{slugify(owner_id)}-gateway"


def range_target_service_name(owner_id: str, instance_key: str) -> str:
    return f"chrange-{instance_id(owner_id, instance_key)}-target"


def range_attacker_hostname(owner_id: str, base_domain: str) -> str:
    return f"{slugify(owner_id)}-attacker.apps.{base_domain}"


# ── In-flight creation footprints ─────────────────────────────────────────────
# The reaper has to answer "is this pending reservation a crashed worker, or
# a live creation that's just slow?" before it may abandon it, and the only
# evidence available is which managed Docker resources already exist. The
# instance type isn't persisted until finalize(), so a row that is still a
# reservation could be any of the three; each of them is fully determined by
# (owner_id, instance_key) alone, so the candidate set is just their union.
# A resource whose name isn't in here can't have been created by that row's
# in-flight creation, which is what makes "leave it alone" precise rather
# than a blanket skip-everything timeout.

def reservation_resource_names(owner_id: str, instance_key: str) -> set:
    return {
        service_name(owner_id, instance_key),              # web-app / single-target workload, or range target
        gateway_service_name(owner_id, instance_key),     # web-app / single-target gateway
        network_name(owner_id, instance_key),             # web-app / single-target network
        range_target_service_name(owner_id, instance_key),  # target-attacker target
    }


def range_reservation_resource_names(owner_id: str) -> set:
    return {
        range_attacker_service_name(owner_id),
        range_gateway_service_name(owner_id),
        range_network_name(owner_id),
    }
