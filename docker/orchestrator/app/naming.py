"""docker/orchestrator/app/naming.py

Turns arbitrary owner/instance identifiers into names that are simultaneously
valid Docker service/network names AND valid DNS labels (since they end up in
Traefik Host() rules as <slug>.apps.<base_domain>).
"""
import re

_INVALID = re.compile(r"[^a-z0-9-]+")
_DASHES = re.compile(r"-{2,}")
MAX_SLUG_LEN = 40


class InvalidIdentifierError(ValueError):
    pass


def slugify(value: str) -> str:
    if not value or not value.strip():
        raise InvalidIdentifierError("identifier must not be empty")
    slug = value.strip().lower()
    slug = _INVALID.sub("-", slug)
    slug = _DASHES.sub("-", slug).strip("-")
    if not slug:
        raise InvalidIdentifierError(f"identifier {value!r} has no valid characters")
    return slug[:MAX_SLUG_LEN]


def instance_id(owner_id: str, instance_key: str) -> str:
    """Stable, DNS-safe identifier for one team's one challenge instance."""
    return f"{slugify(owner_id)}-{slugify(instance_key)}"


def service_name(owner_id: str, instance_key: str, role: str | None = None) -> str:
    base = f"chinst-{instance_id(owner_id, instance_key)}"
    return f"{base}-{role}" if role else base


def network_name(owner_id: str, instance_key: str) -> str:
    return f"chnet-{instance_id(owner_id, instance_key)}"


def access_hostname(owner_id: str, instance_key: str, base_domain: str) -> str:
    return f"{instance_id(owner_id, instance_key)}.apps.{base_domain}"


# ── Range-level (shared attacker + isolated network per team) ────────────────
# Unlike a single instance, a range is scoped to owner_id ALONE — every
# target-attacker challenge a team launches shares the same attacker and
# network, so these names must never include instance_key.

def range_network_name(owner_id: str) -> str:
    return f"chrange-{slugify(owner_id)}"


def range_attacker_service_name(owner_id: str) -> str:
    return f"chrange-{slugify(owner_id)}-attacker"


def range_target_service_name(owner_id: str, instance_key: str) -> str:
    return f"chrange-{instance_id(owner_id, instance_key)}-target"


def range_attacker_hostname(owner_id: str, base_domain: str) -> str:
    return f"{slugify(owner_id)}-attacker.apps.{base_domain}"
