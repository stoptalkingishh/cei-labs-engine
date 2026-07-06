import pytest

from app import instance_types as it

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


# ── web-app ───────────────────────────────────────────────────────────────────

def test_web_app_plan_single_service_on_challenge_network():
    plan = it.plan_web_app("team-1", "juice-shop", {"image": "bkimminich/juice-shop:v17.1.1"}, BASE_DOMAIN, CHALLENGE_NET)

    assert plan.type == it.WEB_APP
    assert len(plan.services) == 1
    svc = plan.services[0]
    assert svc.image == "bkimminich/juice-shop:v17.1.1"
    assert svc.networks == [CHALLENGE_NET]
    assert plan.network is None
    assert plan.range_owner_id is None
    assert plan.access["url"] == "https://team-1-juice-shop.apps.ctf.local"


def test_web_app_plan_sets_traefik_labels_for_custom_port():
    plan = it.plan_web_app("team-1", "app", {"image": "some/app", "port": 8080}, BASE_DOMAIN, CHALLENGE_NET)
    svc = plan.services[0]
    router = svc.name
    assert svc.labels["traefik.enable"] == "true"
    assert svc.labels[f"traefik.http.services.{router}.loadbalancer.server.port"] == "8080"
    assert svc.labels["traefik.docker.network"] == CHALLENGE_NET


def test_web_app_plan_requires_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_web_app("team-1", "app", {}, BASE_DOMAIN, CHALLENGE_NET)


# ── single-target ─────────────────────────────────────────────────────────────

def test_single_target_gets_its_own_airgapped_network_and_published_port():
    plan = it.plan_single_target("team-1", "otw", {"image": "some/target"}, allocated_port=32000, base_domain=BASE_DOMAIN)

    assert plan.type == it.SINGLE_TARGET
    assert len(plan.services) == 1
    svc = plan.services[0]
    assert plan.network is not None
    assert svc.networks == [plan.network]
    assert svc.published_port == (32000, 22)
    assert plan.access["connect_port"] == 32000
    assert plan.access["connect_host"] == BASE_DOMAIN
    assert plan.access["protocol"] == "ssh"


def test_single_target_custom_target_port():
    plan = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "target_port": 2222}, allocated_port=32001, base_domain=BASE_DOMAIN
    )
    assert plan.services[0].published_port == (32001, 2222)


def test_single_target_requires_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_single_target("team-1", "otw", {}, allocated_port=32000, base_domain=BASE_DOMAIN)


# ── target-attacker (range model) ─────────────────────────────────────────────

def test_range_attacker_plan_joins_range_network_and_challenge_network():
    plan = it.plan_range_attacker("team-1", {"attacker_image": "cei/kali-novnc:latest"}, BASE_DOMAIN, CHALLENGE_NET)

    assert plan.owner_id == "team-1"
    assert set(plan.attacker_service.networks) == {plan.network, CHALLENGE_NET}
    assert plan.attacker_service.labels["traefik.enable"] == "true"
    assert plan.access["attacker_url"] == "https://team-1-attacker.apps.ctf.local"


def test_range_attacker_hostname_has_no_instance_key_component():
    # Two different challenges for the same team must resolve to the SAME
    # attacker hostname/network — that's the whole point of the range model.
    plan_a = it.plan_range_attacker("team-1", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    plan_b = it.plan_range_attacker("team-1", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    assert plan_a.network == plan_b.network
    assert plan_a.attacker_service.name == plan_b.attacker_service.name
    assert plan_a.access == plan_b.access


def test_range_attacker_requires_attacker_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_range_attacker("team-1", {}, BASE_DOMAIN, CHALLENGE_NET)


def test_range_target_joins_only_the_range_network():
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    target_plan = it.plan_range_target("team-1", "otw-1", {"target_image": "t"}, range_plan.network, range_plan.access)

    assert target_plan.type == it.TARGET_ATTACKER
    assert target_plan.range_owner_id == "team-1"
    target_svc = target_plan.services[0]
    assert target_svc.networks == [range_plan.network]
    assert CHALLENGE_NET not in target_svc.networks
    assert not target_svc.labels  # never exposed via Traefik

    assert target_plan.access["attacker_url"] == range_plan.access["attacker_url"]
    assert target_plan.access["target_hostname"] == target_svc.name


def test_two_targets_same_team_share_network_but_have_distinct_services():
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    target_a = it.plan_range_target("team-1", "challenge-a", {"target_image": "t"}, range_plan.network, range_plan.access)
    target_b = it.plan_range_target("team-1", "challenge-b", {"target_image": "t"}, range_plan.network, range_plan.access)

    assert target_a.services[0].networks == target_b.services[0].networks  # same shared range network
    assert target_a.services[0].name != target_b.services[0].name  # distinct targets


def test_two_teams_never_share_a_range_network_or_service_name():
    plan_a = it.plan_range_attacker("team-a", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    plan_b = it.plan_range_attacker("team-b", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    assert plan_a.network != plan_b.network
    assert plan_a.attacker_service.name != plan_b.attacker_service.name


def test_range_target_requires_target_image():
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET)
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_range_target("team-1", "otw", {}, range_plan.network, range_plan.access)
