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


def test_offline_mode_setting_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_HOST", raising=False)
    from app import config as config_module
    importlib.reload(config_module)

    assert config_module.Config.OFFLINE_MODE_SETTING == "auto"
    assert config_module.Config.OFFLINE_HOST == ""


def test_offline_mode_setting_accepts_common_truthy_and_falsy_strings(monkeypatch):
    from app import config as config_module

    for truthy in ("true", "True", "1", "yes", "YES"):
        monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", truthy)
        importlib.reload(config_module)
        assert config_module.Config.OFFLINE_MODE_SETTING is True, f"{truthy!r} should parse as True"

    for falsy in ("false", "False", "0", "no", "NO"):
        monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", falsy)
        importlib.reload(config_module)
        assert config_module.Config.OFFLINE_MODE_SETTING is False, f"{falsy!r} should parse as False"

    for auto_spelling in ("auto", "Auto", ""):
        monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", auto_spelling)
        importlib.reload(config_module)
        assert config_module.Config.OFFLINE_MODE_SETTING == "auto"

    monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
    importlib.reload(config_module)


def test_offline_mode_setting_rejects_garbage_at_import_time(monkeypatch):
    # A typo here must fail the container loudly at startup, not silently
    # fall back to "auto" and mask what the operator actually intended.
    from app import config as config_module
    monkeypatch.setenv("ORCHESTRATOR_OFFLINE_MODE", "yolo")

    try:
        with pytest.raises(RuntimeError, match="ORCHESTRATOR_OFFLINE_MODE"):
            importlib.reload(config_module)
    finally:
        monkeypatch.delenv("ORCHESTRATOR_OFFLINE_MODE", raising=False)
        importlib.reload(config_module)


class _FakeCfg:
    BASE_DOMAIN = "ctf.local"

    def __init__(self, setting, offline_host=""):
        self.OFFLINE_MODE_SETTING = setting
        self.OFFLINE_HOST = offline_host


def test_resolve_offline_mode_explicit_true_skips_the_probe():
    from app.config import resolve_offline_mode

    def probe_should_not_be_called(hostname):
        raise AssertionError("explicit True must not consult the DNS probe")

    cfg = _FakeCfg(setting=True, offline_host="192.0.2.10")
    assert resolve_offline_mode(cfg, probe_dns=probe_should_not_be_called) is True


def test_resolve_offline_mode_explicit_false_skips_the_probe():
    from app.config import resolve_offline_mode

    def probe_should_not_be_called(hostname):
        raise AssertionError("explicit False must not consult the DNS probe")

    cfg = _FakeCfg(setting=False)
    assert resolve_offline_mode(cfg, probe_dns=probe_should_not_be_called) is False


def test_resolve_offline_mode_auto_uses_probe_result():
    from app.config import resolve_offline_mode

    cfg = _FakeCfg(setting="auto", offline_host="192.0.2.10")
    assert resolve_offline_mode(cfg, probe_dns=lambda hostname: True) is False
    assert resolve_offline_mode(cfg, probe_dns=lambda hostname: False) is True


def test_resolve_offline_mode_fails_fast_without_offline_host():
    # No safe default for OFFLINE_HOST, whether offline mode was reached
    # via an explicit "true" or via "auto" detecting no DNS.
    from app.config import resolve_offline_mode

    with pytest.raises(RuntimeError, match="ORCHESTRATOR_OFFLINE_HOST"):
        resolve_offline_mode(_FakeCfg(setting=True, offline_host=""))

    with pytest.raises(RuntimeError, match="ORCHESTRATOR_OFFLINE_HOST"):
        resolve_offline_mode(_FakeCfg(setting="auto"), probe_dns=lambda hostname: False)

    # Auto + DNS resolves + no OFFLINE_HOST is fine -- offline mode never activates.
    assert resolve_offline_mode(_FakeCfg(setting="auto"), probe_dns=lambda hostname: True) is False
