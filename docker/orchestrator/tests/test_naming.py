import re

import pytest

from app import naming

# A DNS label is at most 63 bytes, and instance_id() is embedded verbatim in
# a Traefik Host() rule (instance_types._traefik_labels) -- an over-long label
# produces a URL that can never resolve. 63 lowercase/digit/dash chars is 63
# bytes; nothing here may emit non-ASCII.
LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# The exact pair from issue #65: identical for the first 40 characters after
# normalization, differing only past the old `slug[:40]` cut.
ISSUE_65_OWNER_A = "team-" + "a" * 40
ISSUE_65_OWNER_B = "team-" + "a" * 39 + "b"


def test_slugify_lowercases_and_strips_invalid_chars():
    assert naming.slugify("Team One!") == "team-one"


def test_slugify_collapses_repeated_dashes():
    assert naming.slugify("team___one") == "team-one"


def test_slugify_rejects_empty_result():
    with pytest.raises(naming.InvalidIdentifierError):
        naming.slugify("!!!")


def test_slugify_rejects_empty_input():
    with pytest.raises(naming.InvalidIdentifierError):
        naming.slugify("   ")


def test_service_name_stable_and_dns_safe():
    name = naming.service_name("Team One", "Juice Shop Level 1")
    assert name == "chinst-team-one-juice-shop-level-1"


def test_service_name_with_role_suffix():
    assert naming.service_name("team-1", "otw", "target") == "chinst-team-1-otw-target"
    assert naming.service_name("team-1", "otw", "attacker") == "chinst-team-1-otw-attacker"


def test_network_name_distinct_from_service_name():
    svc = naming.service_name("team-1", "otw")
    net = naming.network_name("team-1", "otw")
    assert svc != net
    assert net == "chnet-team-1-otw"


def test_access_hostname():
    host = naming.access_hostname("team-1", "juice-shop", "ctf.local")
    assert host == "team-1-juice-shop.apps.ctf.local"


def test_two_owners_never_collide():
    a = naming.instance_id("team-1", "juice")
    b = naming.instance_id("team-2", "juice")
    assert a != b


# ── issue #65: truncation must not alias two tenants onto one Docker object ──

# Every name a (owner_id, instance_key) pair can produce, keyed by the function
# that produces it. The collision test is parametrized over exactly this
# mapping, so a fix applied to only *some* composing functions (e.g. hashing
# in service_name but not in range_attacker_service_name) still fails: a
# shared name in any single entry is a cross-tenant aliasing bug, because
# create_service() is idempotent (docker_client.py) and the store keys on the
# raw owner_id, so nothing downstream would notice.
def _derived_names(owner_id: str, instance_key: str = "juice") -> dict:
    return {
        "slugify": naming.slugify(owner_id),
        "instance_id": naming.instance_id(owner_id, instance_key),
        "service_name": naming.service_name(owner_id, instance_key),
        "service_name_target_role": naming.service_name(owner_id, instance_key, "target"),
        "gateway_service_name": naming.gateway_service_name(owner_id, instance_key),
        "network_name": naming.network_name(owner_id, instance_key),
        "access_hostname": naming.access_hostname(owner_id, instance_key, "ctf.local"),
        "range_network_name": naming.range_network_name(owner_id),
        "range_attacker_service_name": naming.range_attacker_service_name(owner_id),
        "range_gateway_service_name": naming.range_gateway_service_name(owner_id),
        "range_target_service_name": naming.range_target_service_name(owner_id, instance_key),
        "range_attacker_hostname": naming.range_attacker_hostname(owner_id, "ctf.local"),
    }


@pytest.mark.parametrize("derived_name", sorted(_derived_names("probe")))
def test_truncation_does_not_alias_two_owners_onto_one_name(derived_name):
    """The verified #65 collision: both ids are byte-identical over the old
    `slug[:40]` cut, so truncation alone mapped them onto the same name."""
    assert ISSUE_65_OWNER_A != ISSUE_65_OWNER_B  # sanity: the inputs really do differ
    assert ISSUE_65_OWNER_A[:40] == ISSUE_65_OWNER_B[:40]
    names_a = _derived_names(ISSUE_65_OWNER_A)
    names_b = _derived_names(ISSUE_65_OWNER_B)
    assert names_a[derived_name] != names_b[derived_name], (
        f"{derived_name} collides across tenants: {names_a[derived_name]!r}"
    )


@pytest.mark.parametrize("derived_name", sorted(_derived_names("probe")))
def test_naming_is_stable_for_the_same_identifier(derived_name):
    """Relaunch/reboot idempotency depends on this: a relaunch re-derives the
    names in order to tear the *existing* containers down, so an unstable
    name would orphan live containers instead of replacing them. The digest
    must be a pure function of the identifier (no salt, no randomness) --
    hence a value-identical but object-distinct string, as a rebuild-from-
    request would supply."""
    first = _derived_names(ISSUE_65_OWNER_A)[derived_name]
    assert _derived_names(str(ISSUE_65_OWNER_A))[derived_name] == first
    # Re-deriving again must land on the same value -- no per-call state.
    assert _derived_names(ISSUE_65_OWNER_A)[derived_name] == first


LENGTH_CASES = {
    "short": ("1", "juice"),
    "one_long": ("team-" + "a" * 200, "juice"),
    "both_long": ("team-" + "a" * 200, "level-" + "b" * 200),
    "huge": ("o" * 200, "k" * 200),
}


@pytest.mark.parametrize("case", sorted(LENGTH_CASES))
def test_instance_id_fits_a_dns_label(case):
    """instance_id() is embedded verbatim in Traefik's Host() rule, so an
    over-63-byte label is a URL that can never resolve -- the cap has to be
    on the *joined* owner+key string, not on each half independently."""
    owner_id, instance_key = LENGTH_CASES[case]
    iid = naming.instance_id(owner_id, instance_key)
    assert len(iid) <= naming.MAX_LABEL_LEN, f"{case}: instance_id is {len(iid)} chars"
    assert len(iid.encode("utf-8")) <= 63
    # The cap must hold for the label that actually reaches Traefik, not just
    # for the standalone id: access_hostname's first label IS the instance_id.
    host = naming.access_hostname(owner_id, instance_key, "ctf.local")
    label = host.split(".")[0]
    assert label == iid
    assert len(label.encode("utf-8")) <= 63


def test_short_ordinary_ids_stay_readable():
    """Guard against over-hashing. Every real deployment passes
    owner_id = str(user.account_id) -- a short integer -- so the digest suffix
    should essentially never appear in practice. If it starts appearing on
    ordinary ids, the hash is churning names for no reason (and needlessly
    breaking existing deployments)."""
    assert naming.slugify("team-1") == "team-1"
    assert naming.instance_id("1", "juice") == "1-juice"
    assert naming.service_name("1", "juice") == "chinst-1-juice"
    assert naming.service_name("Team One", "Juice Shop Level 1") == "chinst-team-one-juice-shop-level-1"
    assert "team-1" in naming.service_name("team-1", "juice")


@pytest.mark.parametrize("case", sorted(LENGTH_CASES))
def test_hashed_output_is_dns_label_safe(case):
    """The digest is hex and the prefix comes from the slug alphabet, but the
    *join* can still leave a dash in an illegal position (leading, trailing, or
    doubled) where the truncation cut landed -- assert it rather than assume."""
    owner_id, instance_key = LENGTH_CASES[case]
    for value in (
        naming.slugify(owner_id),
        naming.instance_id(owner_id, instance_key),
        naming.access_hostname(owner_id, instance_key, "ctf.local").split(".")[0],
        naming.range_attacker_hostname(owner_id, "ctf.local").split(".")[0],
    ):
        assert LABEL_RE.match(value), f"{case}: {value!r} is not a valid DNS label"
        assert "--" not in value, f"{case}: {value!r} has a doubled dash"
        assert value.isascii(), f"{case}: {value!r} is non-ASCII (byte length != char length)"
