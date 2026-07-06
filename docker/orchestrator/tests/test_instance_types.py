import pytest

from app import instance_types as it

BASE_DOMAIN = "ctf.local"
CHALLENGE_NET = "cei-labs_challenge-edge"


def test_web_app_plan_single_service_on_challenge_network():
    plan = it.plan(it.WEB_APP, "team-1", "juice-shop", {"image": "bkimminich/juice-shop:v17.1.1"}, BASE_DOMAIN, CHALLENGE_NET)

    assert plan.type == it.WEB_APP
    assert len(plan.services) == 1
    svc = plan.services[0]
    assert svc.image == "bkimminich/juice-shop:v17.1.1"
    assert svc.networks == [CHALLENGE_NET]
    assert plan.team_network is None
    assert plan.access["url"] == "https://team-1-juice-shop.apps.ctf.local"


def test_web_app_plan_sets_traefik_labels_for_custom_port():
    plan = it.plan(it.WEB_APP, "team-1", "app", {"image": "some/app", "port": 8080}, BASE_DOMAIN, CHALLENGE_NET)
    svc = plan.services[0]
    router = svc.name
    assert svc.labels["traefik.enable"] == "true"
    assert svc.labels[f"traefik.http.services.{router}.loadbalancer.server.port"] == "8080"
    assert svc.labels["traefik.docker.network"] == CHALLENGE_NET


def test_web_app_plan_requires_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan(it.WEB_APP, "team-1", "app", {}, BASE_DOMAIN, CHALLENGE_NET)


def test_single_target_behaves_like_web_app():
    plan = it.plan(it.SINGLE_TARGET, "team-1", "otw", {"image": "some/target"}, BASE_DOMAIN, CHALLENGE_NET)
    assert plan.type == it.SINGLE_TARGET
    assert len(plan.services) == 1
    assert plan.team_network is None


def test_target_attacker_plan_creates_two_services_and_a_team_network():
    plan = it.plan(
        it.TARGET_ATTACKER,
        "team-1",
        "otw-range",
        {"target_image": "cei/target-linux:latest", "attacker_image": "cei/kali-novnc:latest"},
        BASE_DOMAIN,
        CHALLENGE_NET,
    )

    assert plan.type == it.TARGET_ATTACKER
    assert plan.team_network == "chnet-team-1-otw-range"
    assert len(plan.services) == 2

    target = next(s for s in plan.services if s.name.endswith("-target"))
    attacker = next(s for s in plan.services if s.name.endswith("-attacker"))

    # Target must NEVER join the public challenge network — only its own
    # private team network. This is the isolation guarantee the plan promises.
    assert target.networks == [plan.team_network]
    assert CHALLENGE_NET not in target.networks
    assert not target.labels  # no traefik exposure at all

    # Attacker joins both: the private team network (to reach its target) and
    # the public challenge network (so Traefik can route to its noVNC port).
    assert set(attacker.networks) == {plan.team_network, CHALLENGE_NET}
    assert attacker.labels["traefik.enable"] == "true"

    assert plan.access["attacker_url"] == "https://team-1-otw-range.apps.ctf.local"


def test_target_attacker_requires_both_images():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan(it.TARGET_ATTACKER, "team-1", "otw", {"target_image": "only-target"}, BASE_DOMAIN, CHALLENGE_NET)


def test_two_teams_never_share_a_network_or_service_name():
    plan_a = it.plan(
        it.TARGET_ATTACKER, "team-a", "otw", {"target_image": "t", "attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET
    )
    plan_b = it.plan(
        it.TARGET_ATTACKER, "team-b", "otw", {"target_image": "t", "attacker_image": "k"}, BASE_DOMAIN, CHALLENGE_NET
    )
    assert plan_a.team_network != plan_b.team_network
    names_a = {s.name for s in plan_a.services}
    names_b = {s.name for s in plan_b.services}
    assert names_a.isdisjoint(names_b)


def test_unknown_type_rejected():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan("not-a-real-type", "team-1", "x", {}, BASE_DOMAIN, CHALLENGE_NET)
