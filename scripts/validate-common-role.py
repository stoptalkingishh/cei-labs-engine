#!/usr/bin/env python3
"""Validate cross-platform invariants in the Ansible common role."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = REPO_ROOT / "ansible" / "roles" / "common" / "defaults" / "main.yml"
ENV_EXAMPLE = REPO_ROOT / "docker" / ".env.example"
REQUIRED_BASE_PACKAGES = {"btop", "fail2ban", "tmux"}
REQUIRED_OS_PACKAGES = {
    "Debian": {"ufw"},
    "RedHat": {"firewalld", "python3-firewall"},
}
REQUIRED_FIXED_FIREWALL_PORTS = {
    "22/tcp",
    "80/tcp",
    "443/tcp",
    "2377/tcp",
    "4789/udp",
    "7946/tcp",
    "7946/udp",
}


def normalized_ufw_ports(rules: list[dict[str, str]]) -> set[str]:
    """Return UFW's colon-delimited ranges in firewalld notation."""
    return {f"{rule['port'].replace(':', '-')}/{rule['proto']}" for rule in rules}


def read_env_defaults() -> dict[str, str]:
    """Read non-secret port defaults from docker/.env.example."""
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main() -> None:
    config = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    env_defaults = read_env_defaults()

    supported = set(config["common_supported_os_families"])
    if supported != set(REQUIRED_OS_PACKAGES):
        raise SystemExit(f"unexpected supported OS families: {sorted(supported)}")

    base_packages = set(config["common_base_packages"])
    missing_base = REQUIRED_BASE_PACKAGES - base_packages
    if missing_base:
        raise SystemExit(
            f"missing common monitoring/security packages: {sorted(missing_base)}"
        )

    for family, required in REQUIRED_OS_PACKAGES.items():
        actual = set(config["common_os_packages"].get(family, []))
        missing = required - actual
        if missing:
            raise SystemExit(f"{family} is missing packages: {sorted(missing)}")

    ufw_ports = normalized_ufw_ports(config["common_ufw_rules"])
    firewalld_ports = {rule["port"] for rule in config["common_firewalld_rules"]}
    if ufw_ports != firewalld_ports:
        raise SystemExit(
            "firewall backends differ: "
            f"UFW-only={sorted(ufw_ports - firewalld_ports)}, "
            f"firewalld-only={sorted(firewalld_ports - ufw_ports)}"
        )

    workspace_start = int(env_defaults["ANALYST_BASE_PORT"])
    gateway_start = int(env_defaults["ORCHESTRATOR_SSH_PORT_RANGE_START"])
    gateway_end = int(env_defaults["ORCHESTRATOR_SSH_PORT_RANGE_END"])
    required_ports = REQUIRED_FIXED_FIREWALL_PORTS | {
        f"{workspace_start}-{gateway_start - 1}/tcp",
        f"{gateway_start}-{gateway_end}/tcp",
    }
    missing_ports = required_ports - ufw_ports
    if missing_ports:
        raise SystemExit(f"missing required firewall ports: {sorted(missing_ports)}")
    if "30000-32767/tcp" in ufw_ports:
        raise SystemExit("legacy 30000-32767 participant range must not be opened")

    print(
        "Common-role validation passed: Debian and RedHat package sets, "
        f"{len(ufw_ports)} equivalent firewall rules, and btop provisioning."
    )


if __name__ == "__main__":
    main()
