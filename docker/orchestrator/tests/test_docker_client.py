from unittest.mock import MagicMock, patch

from docker.errors import NotFound

from app.docker_client import DockerOrchestratorClient, ORCH_LABEL, ServiceSpec


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
        {
            "Protocol": "tcp",
            "PublishedPort": 32000,
            "TargetPort": 10022,
            "PublishMode": "host",
        }
    ]
