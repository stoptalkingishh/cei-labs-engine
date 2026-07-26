"""Regression guard for the "stable access endpoint" P1 fix.

The original bug was never actually in this code -- Config.BASE_DOMAIN was
already environment-driven, never a hardcoded IP -- it was that a
point-in-time station IP (192.168.1.131) got copied into player-facing
launch instructions outside this repo and went stale. See
docs/network-prerequisites.md's "Stable access endpoint" section and
docs/P1-FIX-LOG-2026-07-23.md for the full writeup. This test just makes
sure nobody "fixes" a future symptom by baking a LAN IP in as the default
here, which would reintroduce the exact failure mode from scratch.
"""
import importlib
import re

import pytest

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def test_base_domain_default_is_not_a_bare_ip_literal(monkeypatch):
    monkeypatch.delenv("BASE_DOMAIN", raising=False)
    from app import config as config_module
    importlib.reload(config_module)

    assert not _IPV4_RE.match(config_module.Config.BASE_DOMAIN), (
        f"Config.BASE_DOMAIN default {config_module.Config.BASE_DOMAIN!r} looks like a "
        "bare LAN IP -- it must stay a DNS-style default (e.g. 'ctf.local') "
        "so a stale station IP can never ship as the fallback. Configure the "
        "real, current value via the BASE_DOMAIN env var / docker/.env instead."
    )


def test_base_domain_is_still_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BASE_DOMAIN", "ctf.example.org")
    from app import config as config_module
    importlib.reload(config_module)

    assert config_module.Config.BASE_DOMAIN == "ctf.example.org"

    monkeypatch.delenv("BASE_DOMAIN", raising=False)
    importlib.reload(config_module)


def test_offline_mode_defaults_false(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_HOST", raising=False)
    from app import config as config_module
    importlib.reload(config_module)

    assert config_module.Config.OFFLINE_MODE is False
    assert config_module.Config.OFFLINE_HOST == ""


def test_offline_mode_accepts_common_truthy_strings(monkeypatch):
    from app import config as config_module
    monkeypatch.setenv("ORCHESTRATOR_OFFLINE_HOST", "192.0.2.10")

    for truthy in ("true", "True", "1", "yes", "YES"):
        monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", truthy)
        importlib.reload(config_module)
        assert config_module.Config.OFFLINE_MODE is True, f"{truthy!r} should parse as True"

    monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", "false")
    importlib.reload(config_module)
    assert config_module.Config.OFFLINE_MODE is False

    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_HOST", raising=False)
    importlib.reload(config_module)


def test_offline_mode_without_offline_host_fails_fast(monkeypatch):
    # A hostname-shaped or hardcoded default for OFFLINE_HOST would silently
    # reintroduce the exact DNS dependency OFFLINE_MODE exists to remove --
    # this must be a loud startup failure, not a quiet fallback.
    from app import config as config_module
    monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", "true")
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_HOST", raising=False)

    try:
        with pytest.raises(RuntimeError, match="ORCHESTRATOR_OFFLINE_HOST"):
            importlib.reload(config_module)
    finally:
        # Leave the module in a re-importable state for later tests.
        monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", "false")
        importlib.reload(config_module)
        monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
