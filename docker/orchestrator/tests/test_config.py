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
