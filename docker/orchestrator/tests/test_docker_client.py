from unittest.mock import MagicMock, patch

import pytest
from docker.errors import APIError, NotFound

from app.docker_client import (
    _SERVICE_CREATE_ATTEMPTS,
    DockerOrchestratorClient,
    ORCH_LABEL,
    ServiceSpec,
)


def test_ensure_network_retries_until_new_overlay_is_inspectable():
    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.api.create_network.return_value = {"Id": "network-id"}
    docker_client.api.inspect_network.side_effect = [NotFound("not ready"), {"Id": "network-id"}]

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client), patch(
        "app.docker_client.time.sleep"
    ) as sleep:
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        client.ensure_network("challenge-network", internal=True)

    docker_client.api.create_network.assert_called_once_with(
        "challenge-network",
        driver="overlay",
        attachable=True,
        internal=True,
        labels={ORCH_LABEL: "true"},
    )
    assert docker_client.api.inspect_network.call_count == 2
    sleep.assert_called_once()


def test_create_gateway_service_passes_hardening_and_published_port():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        client.create_service(ServiceSpec(
            name="gateway",
            image="gateway:sha",
            networks=["private"],
            published_ports=[(32000, 10022)],
            cap_drop=["ALL"],
            read_only=True,
            sysctls={"net.ipv4.ip_forward": "0"},
        ))

    kwargs = docker_client.services.create.call_args.kwargs
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["sysctls"] == {"net.ipv4.ip_forward": "0"}
    assert kwargs["endpoint_spec"]["Ports"] == [
        {"Protocol": "tcp", "PublishedPort": 32000, "TargetPort": 10022}
    ]


# ── resolve_image_digest ─────────────────────────────────────────────────────
#
# See docker_client.py's resolve_image_digest docstring and
# docs/P1-FIX-LOG-2026-07-23.md for why this exists: these images are built
# and loaded locally (offline/air-gapped install path) rather than reliably
# served by a reachable registry, so pinning to whatever the local image
# store's RepoDigests already say -- not a registry lookup -- is what
# actually protects a fresh Swarm task from the stuck-`Preparing` failure
# mode and from silent tag-mutation drift.

def test_resolve_image_digest_pins_to_local_repo_digest():
    docker_client = MagicMock()
    image = MagicMock()
    image.attrs = {
        "RepoDigests": [
            "ghcr.io/stoptalkingishh/cei-labs-engine/ctf-kali-novnc@sha256:" + "a" * 64,
        ]
    }
    docker_client.images.get.return_value = image

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        resolved = client.resolve_image_digest(
            "ghcr.io/stoptalkingishh/cei-labs-engine/ctf-kali-novnc:latest"
        )

    docker_client.images.get.assert_called_once_with(
        "ghcr.io/stoptalkingishh/cei-labs-engine/ctf-kali-novnc:latest"
    )
    assert resolved == "ghcr.io/stoptalkingishh/cei-labs-engine/ctf-kali-novnc@sha256:" + "a" * 64


def test_resolve_image_digest_ignores_repo_digest_for_a_different_repo():
    docker_client = MagicMock()
    image = MagicMock()
    # e.g. an image that was retagged from another repo and still carries
    # that other repo's RepoDigests entry -- must not be matched.
    image.attrs = {"RepoDigests": ["ghcr.io/some-other/repo@sha256:" + "b" * 64]}
    docker_client.images.get.return_value = image

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        resolved = client.resolve_image_digest("myimage:latest")

    assert resolved == "myimage:latest"


def test_resolve_image_digest_falls_back_when_no_repo_digests_recorded():
    docker_client = MagicMock()
    image = MagicMock()
    image.attrs = {"RepoDigests": []}
    docker_client.images.get.return_value = image

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        resolved = client.resolve_image_digest("myimage:latest")

    assert resolved == "myimage:latest"


def test_resolve_image_digest_falls_back_when_image_not_present_locally():
    docker_client = MagicMock()
    docker_client.images.get.side_effect = NotFound("missing")

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        resolved = client.resolve_image_digest("myimage:latest")

    assert resolved == "myimage:latest"


def test_create_service_passes_the_resolved_digest_to_the_docker_api():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    image = MagicMock()
    image.attrs = {"RepoDigests": ["myimage@sha256:" + "c" * 64]}
    docker_client.images.get.return_value = image

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        client.create_service(ServiceSpec(name="svc", image="myimage:latest", networks=["net"]))

    kwargs = docker_client.services.create.call_args.kwargs
    assert kwargs["image"] == "myimage@sha256:" + "c" * 64


def test_create_service_still_works_when_image_has_no_local_digest():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    docker_client.images.get.side_effect = NotFound("missing")

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        client.create_service(ServiceSpec(name="svc", image="myimage:latest", networks=["net"]))

    kwargs = docker_client.services.create.call_args.kwargs
    assert kwargs["image"] == "myimage:latest"


# ── Network-vanished-mid-create retry ────────────────────────────────────────
# ensure_network() returns early when the network already exists and cannot
# tell a healthy network from one a concurrent teardown is midway through
# deleting. A player relaunching the instant their box is torn down lands in
# that window, and the create fails against a network that no longer exists.
# See DockerOrchestratorClient._managed_missing_network's docstring.

def _api_error(message):
    return APIError(message, explanation=message)


def test_create_service_recreates_a_managed_network_that_vanished_mid_create():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.api.create_network.return_value = {"Id": "network-id"}
    docker_client.api.inspect_network.return_value = {"Id": "network-id"}
    docker_client.services.create.side_effect = [
        _api_error('500 Server Error: Internal Server Error ("network chnet-21-group-bandit not found")'),
        "created-service",
    ]

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        result = client.create_service(ServiceSpec(
            name="chinst-21-group-bandit", image="bandit:sha", networks=["chnet-21-group-bandit"],
        ))

    assert result == "created-service"
    assert docker_client.services.create.call_count == 2
    # The vanished network was rebuilt before the retry.
    docker_client.api.create_network.assert_called_once()


def test_create_service_gives_up_after_the_attempt_ceiling():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    docker_client.networks.get.return_value = MagicMock()
    docker_client.services.create.side_effect = _api_error(
        '500 Server Error: Internal Server Error ("network chnet-21-group-bandit not found")'
    )

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        with pytest.raises(APIError):
            client.create_service(ServiceSpec(
                name="chinst-21-group-bandit", image="bandit:sha", networks=["chnet-21-group-bandit"],
            ))

    assert docker_client.services.create.call_count == _SERVICE_CREATE_ATTEMPTS


def test_create_service_does_not_retry_unrelated_api_errors():
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    docker_client.services.create.side_effect = _api_error("500 Server Error: no suitable node")

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        with pytest.raises(APIError):
            client.create_service(ServiceSpec(
                name="chinst-21-group-bandit", image="bandit:sha", networks=["chnet-21-group-bandit"],
            ))

    assert docker_client.services.create.call_count == 1


def test_create_service_never_recreates_a_stack_owned_network():
    """`cei-labs_challenge-edge` is provisioned by docker/stack.yml, not by
    this orchestrator. Re-creating it here would produce a same-named network
    carrying orchestrator labels and no stack ownership, so a missing one must
    surface as a hard failure instead."""
    docker_client = MagicMock()
    docker_client.services.get.side_effect = NotFound("missing")
    docker_client.services.create.side_effect = _api_error(
        '500 Server Error: Internal Server Error ("network cei-labs_challenge-edge not found")'
    )

    with patch("app.docker_client.docker.DockerClient", return_value=docker_client):
        client = DockerOrchestratorClient("unix:///var/run/docker.sock")
        with pytest.raises(APIError):
            client.create_service(ServiceSpec(
                name="chinst-21-group-bandit", image="gw:sha",
                networks=["cei-labs_challenge-edge"],
            ))

    assert docker_client.services.create.call_count == 1
    docker_client.api.create_network.assert_not_called()
