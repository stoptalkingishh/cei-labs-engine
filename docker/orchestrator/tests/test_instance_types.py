import json

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
    assert svc.published_ports == [(32000, 22)]
    assert plan.access["connect_port"] == 32000
    assert plan.access["connect_host"] == BASE_DOMAIN
    assert plan.access["protocol"] == "ssh"


def test_single_target_custom_target_port():
    plan = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "target_port": 2222}, allocated_port=32001, base_domain=BASE_DOMAIN
    )
    assert plan.services[0].published_ports == [(32001, 2222)]


def test_single_target_requires_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_single_target("team-1", "otw", {}, allocated_port=32000, base_domain=BASE_DOMAIN)


def test_single_target_no_secret_keys_means_no_level_secrets_env():
    plan = it.plan_single_target("team-1", "otw", {"image": "some/target"}, allocated_port=32000, base_domain=BASE_DOMAIN)
    assert "LEVEL_SECRETS" not in plan.services[0].env


def test_single_target_generates_per_team_level_secrets():
    plan = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "secret_keys": ["krypton1", "krypton2"]},
        allocated_port=32000, base_domain=BASE_DOMAIN,
    )
    svc = plan.services[0]
    assert "LEVEL_SECRETS" in svc.env
    secrets_blob = json.loads(svc.env["LEVEL_SECRETS"])
    assert set(secrets_blob.keys()) == {"krypton1", "krypton2"}
    assert secrets_blob["krypton1"] != secrets_blob["krypton2"]
    # Also surfaced in access (transport back to CTFd -- routes.py is
    # responsible for scrubbing these before a player ever sees them).
    assert plan.access["krypton1"] == secrets_blob["krypton1"]
    assert plan.access["krypton2"] == secrets_blob["krypton2"]


def test_single_target_secrets_are_random_per_call():
    plan_a = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "secret_keys": ["krypton2"]}, allocated_port=32000, base_domain=BASE_DOMAIN
    )
    plan_b = it.plan_single_target(
        "team-2", "otw", {"image": "some/target", "secret_keys": ["krypton2"]}, allocated_port=32001, base_domain=BASE_DOMAIN
    )
    assert plan_a.access["krypton2"] != plan_b.access["krypton2"]


def test_generate_alpha_track_secrets_is_letters_only():
    result = it.generate_alpha_track_secrets(["krypton3", "krypton4"], length=12)
    assert set(result.keys()) == {"krypton3", "krypton4"}
    for value in result.values():
        assert len(value) == 12
        assert value.isalpha()
        assert value.isupper()
    assert result["krypton3"] != result["krypton4"]


def test_single_target_alpha_secret_keys_produce_letters_only():
    plan = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "alpha_secret_keys": ["krypton3"]}, allocated_port=32000, base_domain=BASE_DOMAIN
    )
    assert plan.access["krypton3"].isalpha()
    assert plan.access["krypton3"].isupper()


def test_generate_fixed_length_track_secrets_is_exact_length_alphanumeric():
    result = it.generate_fixed_length_track_secrets(["bandit1", "bandit2"], length=32)
    assert set(result.keys()) == {"bandit1", "bandit2"}
    for value in result.values():
        assert len(value) == 32
        assert value.isalnum()
    assert result["bandit1"] != result["bandit2"]


def test_single_target_fixed_secret_keys_produce_exact_length():
    plan = it.plan_single_target(
        "team-1", "otw", {"image": "some/target", "fixed_secret_keys": ["bandit5"]}, allocated_port=32000, base_domain=BASE_DOMAIN
    )
    assert len(plan.access["bandit5"]) == 32
    assert plan.access["bandit5"].isalnum()


def test_single_target_mixes_normal_and_alpha_secret_keys():
    plan = it.plan_single_target(
        "team-1", "otw",
        {"image": "some/target", "secret_keys": ["krypton2"], "alpha_secret_keys": ["krypton3"]},
        allocated_port=32000, base_domain=BASE_DOMAIN,
    )
    assert "krypton2" in plan.access
    assert "krypton3" in plan.access
    assert plan.access["krypton3"].isalpha()


# ── target-attacker (range model) ─────────────────────────────────────────────

def test_range_attacker_plan_joins_range_network_and_challenge_network():
    plan = it.plan_range_attacker("team-1", {"attacker_image": "cei/kali-novnc:latest"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)

    assert plan.owner_id == "team-1"
    assert set(plan.attacker_service.networks) == {plan.network, CHALLENGE_NET}
    assert plan.attacker_service.labels["traefik.enable"] == "true"
    assert plan.access["attacker_url"] == "https://team-1-attacker.apps.ctf.local"


def test_range_attacker_hostname_has_no_instance_key_component():
    # Two different challenges for the same team must resolve to the SAME
    # attacker hostname/network — that's the whole point of the range model.
    plan_a = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    plan_b = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    assert plan_a.network == plan_b.network
    assert plan_a.attacker_service.name == plan_b.attacker_service.name
    assert plan_a.access["attacker_url"] == plan_b.access["attacker_url"]
    assert plan_a.access["connect_host"] == plan_b.access["connect_host"]
    # attacker_password is randomized per call by design (see
    # test_range_attacker_password_is_random_and_never_baked_in below) --
    # controller.py only calls this once per team per range lifetime, so
    # this per-call randomness doesn't mean the credential changes on every
    # challenge launch in practice.


def test_range_attacker_password_is_random_and_never_baked_in():
    # Security fix: the attacker workstation's login used to be a single
    # value baked into the image at build time (recoverable via `docker
    # inspect`/`docker history` by anyone who could pull the image) and
    # shared by every team. Each range must now get its own random,
    # server-generated credential, injected only into that specific
    # service's runtime environment.
    plan_a = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    plan_b = it.plan_range_attacker("team-2", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)

    assert plan_a.access["attacker_password"]
    assert plan_a.access["attacker_password"] != plan_b.access["attacker_password"]
    assert len(plan_a.access["attacker_password"]) >= 20  # secrets.token_urlsafe(18)

    # Must reach the container via its runtime environment, not be assumed
    # present in the image -- both env var names are set since either a
    # kali-novnc (VNC_PASSWORD) or analyst (OPERATOR_PASSWORD) image could
    # be configured as the attacker_image.
    env = plan_a.attacker_service.env
    assert env["VNC_PASSWORD"] == plan_a.access["attacker_password"]
    assert env["OPERATOR_PASSWORD"] == plan_a.access["attacker_password"]
    assert plan_a.access["attacker_username"] == "operator"


def test_range_attacker_requires_attacker_image():
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_range_attacker("team-1", {}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)


def test_range_target_joins_only_the_range_network():
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
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
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    target_a = it.plan_range_target("team-1", "challenge-a", {"target_image": "t"}, range_plan.network, range_plan.access)
    target_b = it.plan_range_target("team-1", "challenge-b", {"target_image": "t"}, range_plan.network, range_plan.access)

    assert target_a.services[0].networks == target_b.services[0].networks  # same shared range network
    assert target_a.services[0].name != target_b.services[0].name  # distinct targets


def test_two_teams_never_share_a_range_network_or_service_name():
    plan_a = it.plan_range_attacker("team-a", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    plan_b = it.plan_range_attacker("team-b", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    assert plan_a.network != plan_b.network
    assert plan_a.attacker_service.name != plan_b.attacker_service.name


def test_range_target_requires_target_image():
    range_plan = it.plan_range_attacker("team-1", {"attacker_image": "k"}, 32000, 32100, BASE_DOMAIN, CHALLENGE_NET)
    with pytest.raises(it.InvalidInstanceRequestError):
        it.plan_range_target("team-1", "otw", {}, range_plan.network, range_plan.access)
