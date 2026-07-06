import pytest

from app import naming


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
