"""Tests orchestrator_client.py in isolation, without importing the plugin
package's __init__.py (which pulls in CTFd, not installed in this test env).
The plugin directory is named with a hyphen (matching CTFd's own plugin
folder convention) so it isn't a valid Python package name anyway — CTFd
loads plugins by walking the filesystem, not via `import`, so loading this
one file directly by path mirrors how it's actually used in production.
"""
import importlib.util
import os
import sys
from unittest.mock import Mock, patch

import pytest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "orchestrator_client.py")
spec = importlib.util.spec_from_file_location("orchestrator_client", MODULE_PATH)
orchestrator_client = importlib.util.module_from_spec(spec)
sys.modules["orchestrator_client"] = orchestrator_client
spec.loader.exec_module(orchestrator_client)

OrchestratorClient = orchestrator_client.OrchestratorClient
OrchestratorError = orchestrator_client.OrchestratorError


def make_response(status_code, json_body=None):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = str(json_body)
    return resp


def test_create_or_get_sends_expected_payload_and_headers():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(201, {"status": "created", "access": {"url": "https://x"}})
        result = client.create_or_get("web-app", "team-1", "juice", {"image": "img"})

    args, kwargs = mock_post.call_args
    assert args[0] == "http://orchestrator:8080/instances"
    assert kwargs["headers"]["X-Orchestrator-Auth"] == "s3cr3t"
    assert kwargs["json"] == {
        "type": "web-app", "owner_id": "team-1", "instance_key": "juice", "spec": {"image": "img"}, "relaunch": False,
    }
    assert result["access"]["url"] == "https://x"


def test_create_or_get_relaunch_flag_is_forwarded():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(201, {"status": "created", "access": {}})
        client.create_or_get("web-app", "team-1", "juice", {"image": "img"}, relaunch=True)
    assert mock_post.call_args.kwargs["json"]["relaunch"] is True


def test_reboot_sends_expected_request():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(200, {"status": "rebooting"})
        result = client.reboot("team-1", "juice")
    assert mock_post.call_args[0][0] == "http://orchestrator:8080/instances/team-1/juice/reboot"
    assert result["status"] == "rebooting"


def test_schedule_shutdown_sends_delay():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(200, {"status": "scheduled", "shutdown_at": 123.0})
        client.schedule_shutdown("team-1", "juice", delay_seconds=30)
    assert mock_post.call_args.kwargs["json"] == {"delay_seconds": 30}


def test_extend_shutdown_success():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(200, {"status": "extended", "shutdown_at": 456.0})
        result = client.extend_shutdown("team-1", "juice", extend_seconds=300)
    assert result["shutdown_at"] == 456.0


def test_extend_shutdown_conflict_raises():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(409, {"error": "maximum of 3 extensions already used"})
        with pytest.raises(OrchestratorError, match="maximum of 3 extensions"):
            client.extend_shutdown("team-1", "juice")


def test_create_or_get_raises_on_error_status():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.post") as mock_post:
        mock_post.return_value = make_response(503, {"error": "at capacity"})
        with pytest.raises(OrchestratorError, match="at capacity"):
            client.create_or_get("web-app", "team-1", "juice", {"image": "img"})


def test_get_returns_none_on_404():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.get") as mock_get:
        mock_get.return_value = make_response(404)
        assert client.get("team-1", "juice") is None


def test_delete_treats_404_as_success():
    client = OrchestratorClient(base_url="http://orchestrator:8080", shared_secret="s3cr3t")
    with patch("orchestrator_client.requests.delete") as mock_delete:
        mock_delete.return_value = make_response(404)
        assert client.delete("team-1", "juice") is True


def test_base_url_trailing_slash_is_stripped():
    client = OrchestratorClient(base_url="http://orchestrator:8080/", shared_secret="s")
    with patch("orchestrator_client.requests.get") as mock_get:
        mock_get.return_value = make_response(200, {"type": "web-app", "access": {}})
        client.get("team-1", "juice")
    assert mock_get.call_args[0][0] == "http://orchestrator:8080/instances/team-1/juice"


def test_read_secret_falls_back_to_env_when_no_secret_file(monkeypatch):
    monkeypatch.setenv("PLUGIN_SHARED_SECRET", "from-env")
    assert orchestrator_client.read_secret("plugin_shared_secret") == "from-env"
