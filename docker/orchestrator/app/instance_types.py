"""docker/orchestrator/app/instance_types.py

Pure planning logic: given a request body, decide what Docker services (and
networks) are needed. No Docker API calls happen here, which is what makes
this module unit-testable without a daemon.

Three instance types, per the migration plan:
  - web-app:          one container, HTTP-routed through Traefik on the
                       shared `challenge-edge` network. Covers Juice Shop.
  - single-target:    one container on its OWN dedicated, airgapped
                       (Docker `internal: true`) network, reachable only via
                       a directly published port (SSH or whatever the
                       challenge needs) — no Traefik involved at all.
  - target-attacker:  a per-team "range": one shared attacker (browser
                       noVNC, Traefik-routed) plus N targets, all on one
                       persistent, airgapped network scoped to the owner
                       (not the individual challenge) — see RangePlan below.
                       A target is only ever reachable from its range's own
                       attacker, never from Traefik, other teams, or CTFd.
"""
import json
import os
import secrets
import string
from dataclasses import dataclass, field

from .docker_client import ServiceSpec
from . import naming

WEB_APP = "web-app"
SINGLE_TARGET = "single-target"
TARGET_ATTACKER = "target-attacker"

VALID_TYPES = (WEB_APP, SINGLE_TARGET, TARGET_ATTACKER)

GATEWAY_IMAGE = os.environ.get(
    "ORCHESTRATOR_GATEWAY_IMAGE",
    "ghcr.io/stoptalkingishh/cei-labs-engine/tcp-gateway:latest",
)
GATEWAY_HTTP_PORT = 18080
GATEWAY_SSH_PORT = 10022
GATEWAY_NOVNC_PORT = 16080

# Phase 6 hardening: cap_drop=["ALL"] plus back only what's actually
# needed, confirmed by live-testing each image's real lessons rather than
# guessed. Baseline (targets: Bandit/Krypton SSH boxes, the Natas LAMP
# target) covers sshd/su/chpasswd-style per-connection privilege drops,
# MPM-ITK's per-vhost setuid/setgid, and cron. SYS_CHROOT is required --
# confirmed by testing, not assumed: without it, sshd's privilege-
# separation preauth child fails outright with "chroot(\"/run/sshd\"):
# Operation not permitted", breaking every SSH connection before
# authentication even starts. NET_RAW/MKNOD from Docker's own default set
# are still left out -- neither is needed by these lessons.
_TARGET_CAPS = [
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL",
    "SETGID", "SETUID", "SETFCAP", "SETPCAP",
    "NET_BIND_SERVICE", "AUDIT_WRITE", "SYS_CHROOT",
]
# The Natas/analyst attacker workstation additionally needs raw-socket
# access for nmap/tcpdump (NET_RAW, NET_ADMIN for promiscuous capture).
_ATTACKER_CAPS = _TARGET_CAPS + ["NET_RAW", "NET_ADMIN"]


@dataclass(frozen=True)
class WorkloadQuota:
    """Per-service resource ceiling for participant-controlled workloads."""
    memory_limit_bytes: int = 512 * 1024 * 1024
    memory_reservation_bytes: int = 128 * 1024 * 1024
    cpu_limit_nanos: int = 1_000_000_000

    def __post_init__(self) -> None:
        if min(self.memory_limit_bytes, self.memory_reservation_bytes, self.cpu_limit_nanos) <= 0:
            raise ValueError("workload quota values must be positive")
        if self.memory_reservation_bytes > self.memory_limit_bytes:
            raise ValueError("workload memory reservation cannot exceed its limit")

    def service_kwargs(self) -> dict:
        return {
            "mem_limit_bytes": self.memory_limit_bytes,
            "mem_reservation_bytes": self.memory_reservation_bytes,
            "cpu_limit_nanos": self.cpu_limit_nanos,
        }


DEFAULT_WORKLOAD_QUOTA = WorkloadQuota()


class InvalidInstanceRequestError(ValueError):
    pass


def generate_track_secrets(level_keys: list, nbytes: int = 18) -> dict:
    """One random per-team value per level key -- the fix for every wargame
    level's flag/password previously being an identical string baked into
    the shared image at build time (see docs/security-audit-status.md).
    Mirrors the VNC_PASSWORD pattern below exactly: generated fresh only
    when an instance is first created, since plan_* functions are only
    ever called from controller.py's create_or_get on that "existing is
    None" path (a plain reuse returns the cached InstancePlan without
    calling back into this module at all) -- so this never regenerates
    values out from under an already-running team.

    `level_keys` is generic across every track (this module has no
    built-in notion of "Bandit" vs "Krypton") -- it comes from the
    request spec's "secret_keys", itself sourced CTFd-side from each
    per_team_dynamic Flags row's `data` column (see routes.py's
    _spec_with_secret_keys)."""
    return {key: secrets.token_urlsafe(nbytes) for key in level_keys}


def generate_alpha_track_secrets(level_keys: list, length: int = 10) -> dict:
    """Same purpose as generate_track_secrets(), but restricted to
    uppercase letters only -- for levels where the secret gets embedded
    inside a classical-cipher passage (Caesar/substitution/Vigenere) that
    only transforms alphabetic characters and passes everything else
    through unchanged. A regular token_urlsafe() value contains digits/
    -/_ that would pass through such a cipher UNENCRYPTED, visibly
    standing out against the rest of the encrypted passage and giving
    away the flag's location without needing the taught cryptanalysis
    technique at all. Kept as a separate function (not a flag on
    generate_track_secrets) so the two call sites stay obviously
    distinct -- this one is the exception, not the default."""
    alphabet = string.ascii_uppercase
    return {key: "".join(secrets.choice(alphabet) for _ in range(length)) for key in level_keys}


def generate_fixed_length_track_secrets(level_keys: list, length: int = 32) -> dict:
    """Same purpose again, but a fixed-length alphanumeric string (default
    32 characters, matching OverTheWire's own real flag format and this
    project's original hardcoded flags exactly). Needed wherever a level's
    own description/puzzle mechanics are calibrated to an EXACT byte count
    or character length (e.g. "find the file that is exactly 1033 bytes",
    a fixed insertion position in a large generated file) -- those
    descriptions are static CTFd text, shared by every team, so the
    per-team secret's length has to stay exactly what the description
    already promises or the puzzle breaks. token_urlsafe()'s length
    varies with its input byte count in a way that's awkward to pin
    exactly; a direct fixed-length alphanumeric choice is simpler and
    guaranteed exact."""
    alphabet = string.ascii_letters + string.digits
    return {key: "".join(secrets.choice(alphabet) for _ in range(length)) for key in level_keys}


@dataclass
class InstancePlan:
    """What one (owner_id, instance_key) launch actually owns. Tearing down
    or rebooting an instance only ever affects `services` (and `network`, if
    set) — never a range's shared attacker/network."""
    type: str
    owner_id: str
    instance_key: str
    services: list[ServiceSpec]
    access: dict[str, str]
    network: "str | None" = field(default=None)  # owned + airgapped; None for target-attacker (shared range network instead)
    range_owner_id: "str | None" = field(default=None)  # set for target-attacker: which range this target belongs to


@dataclass
class RangePlan:
    """The shared, persistent-per-team half of a target-attacker range."""
    owner_id: str
    network: str
    attacker_service: ServiceSpec
    access: dict[str, str]
    gateway_service: "ServiceSpec | None" = None


def _require_str(spec: dict, key: str) -> str:
    value = spec.get(key)
    if not value or not isinstance(value, str):
        raise InvalidInstanceRequestError(f"'{key}' is required and must be a non-empty string")
    return value


def _traefik_labels(router_name: str, hostname: str, port: int, challenge_network: str) -> dict[str, str]:
    return {
        "traefik.enable": "true",
        "traefik.docker.network": challenge_network,
        f"traefik.http.routers.{router_name}.rule": f"Host(`{hostname}`)",
        f"traefik.http.routers.{router_name}.entrypoints": "web,websecure",
        f"traefik.http.routers.{router_name}.tls": "true",
        f"traefik.http.services.{router_name}.loadbalancer.server.port": str(port),
    }


def _gateway_service(name: str, networks: list[str], forwards: list[dict], **kwargs) -> ServiceSpec:
    return ServiceSpec(
        name=name,
        image=GATEWAY_IMAGE,
        networks=networks,
        env={"TCP_FORWARDS": json.dumps(forwards, separators=(",", ":"))},
        cap_drop=["ALL"],
        read_only=True,
        mem_limit_bytes=64 * 1024 * 1024,
        mem_reservation_bytes=16 * 1024 * 1024,
        cpu_limit_nanos=250_000_000,
        sysctls={
            "net.ipv4.ip_forward": "0",
            "net.ipv6.conf.all.forwarding": "0",
        },
        **kwargs,
    )


def plan_web_app(
    owner_id: str,
    instance_key: str,
    spec: dict,
    base_domain: str,
    challenge_network: str,
    workload_quota: WorkloadQuota = DEFAULT_WORKLOAD_QUOTA,
) -> InstancePlan:
    image = _require_str(spec, "image")
    port = int(spec.get("port", 3000))
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise InvalidInstanceRequestError("'env' must be an object of string -> string")

    svc_name = naming.service_name(owner_id, instance_key)
    hostname = naming.access_hostname(owner_id, instance_key, base_domain)

    net_name = naming.network_name(owner_id, instance_key)
    service = ServiceSpec(
        name=svc_name,
        image=image,
        networks=[net_name],
        env={str(k): str(v) for k, v in env.items()},
        **workload_quota.service_kwargs(),
    )
    gateway_name = naming.gateway_service_name(owner_id, instance_key)
    gateway = _gateway_service(
        gateway_name,
        [net_name, challenge_network],
        [{"listen": GATEWAY_HTTP_PORT, "host": svc_name, "port": port}],
        labels=_traefik_labels(gateway_name, hostname, GATEWAY_HTTP_PORT, challenge_network),
    )
    return InstancePlan(
        type=WEB_APP,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[service, gateway],
        access={"url": f"https://{hostname}"},
        network=net_name,
    )


def plan_single_target(
    owner_id: str,
    instance_key: str,
    spec: dict,
    allocated_port: int,
    base_domain: str,
    workload_quota: WorkloadQuota = DEFAULT_WORKLOAD_QUOTA,
    offline_mode: bool = False,
    offline_host: "str | None" = None,
) -> InstancePlan:
    """No Traefik involvement — a dedicated airgapped network plus a directly
    published port (e.g. for SSH). `allocated_port` is acquired by the
    caller (controller.py) from a PortAllocator, since that's inherently
    stateful and doesn't belong in this otherwise-pure planning module.

    `connect_host` below is just as DNS-dependent as the attacker links
    plan_range_attacker() fixes -- `base_domain` (e.g. "ctf.local") only
    resolves for players if a real DNS server (or cei-labs-net's own)
    actually answers for it. `offline_mode` (the resolved value from config.resolve_offline_mode()) swaps in
    `offline_host` (Config.OFFLINE_HOST, the venue's current bare LAN IP)
    instead, for the same reason and via the same flag as the attacker
    links -- one venue-level "we have no DNS" switch, not two."""
    image = _require_str(spec, "image")
    target_port = int(spec.get("target_port", 22))
    protocol = spec.get("protocol", "ssh")
    env = spec.get("env") or {}
    if not isinstance(env, dict):
        raise InvalidInstanceRequestError("'env' must be an object of string -> string")

    # Per-team level secrets (see generate_track_secrets docstring): the
    # image's entrypoint reads LEVEL_SECRETS (one JSON blob covering every
    # level this box hosts), applies each level's own value at container
    # START, and never anything baked in at build time. Surfaced in
    # `access` too, under each level's own key, purely as the transport
    # back to CTFd -- routes.py's _persist_and_scrub_secrets MUST strip
    # these back out of `access` before a player ever sees it (access is
    # otherwise displayed verbatim as connect info).
    secret_keys = spec.get("secret_keys") or []
    alpha_secret_keys = spec.get("alpha_secret_keys") or []
    fixed_secret_keys = spec.get("fixed_secret_keys") or []
    track_secrets = generate_track_secrets(secret_keys) if secret_keys else {}
    # See generate_alpha_track_secrets' docstring: levels whose secret gets
    # embedded inside a classical-cipher passage need letters only.
    track_secrets.update(generate_alpha_track_secrets(alpha_secret_keys) if alpha_secret_keys else {})
    # See generate_fixed_length_track_secrets' docstring: levels whose
    # static description text is calibrated to an exact byte count/length.
    track_secrets.update(generate_fixed_length_track_secrets(fixed_secret_keys) if fixed_secret_keys else {})
    if track_secrets:
        env = {**env, "LEVEL_SECRETS": json.dumps(track_secrets)}

    svc_name = naming.service_name(owner_id, instance_key)
    net_name = naming.network_name(owner_id, instance_key)

    service = ServiceSpec(
        name=svc_name,
        image=image,
        networks=[net_name],
        env={str(k): str(v) for k, v in env.items()},
        cap_drop=["ALL"],
        cap_add=list(_TARGET_CAPS),
        **workload_quota.service_kwargs(),
    )
    gateway = _gateway_service(
        naming.gateway_service_name(owner_id, instance_key),
        [net_name],
        [{"listen": GATEWAY_SSH_PORT, "host": svc_name, "port": target_port}],
        published_ports=[(allocated_port, GATEWAY_SSH_PORT)],
    )
    return InstancePlan(
        type=SINGLE_TARGET,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[service, gateway],
        network=net_name,
        access={
            "connect_host": offline_host if offline_mode else base_domain,
            "connect_port": allocated_port,
            "protocol": protocol,
            "note": (
                "Connects via any swarm node — this IP routes to whichever node the target actually landed on."
                if offline_mode else
                "Connects via any swarm node — this hostname routes to whichever node the target actually landed on."
            ),
            **track_secrets,
        },
    )


def plan_range_attacker(
    owner_id: str,
    spec: dict,
    allocated_port: int,
    allocated_novnc_port: int,
    base_domain: str,
    challenge_network: str,
    workload_quota: WorkloadQuota = DEFAULT_WORKLOAD_QUOTA,
    offline_mode: bool = False,
    offline_host: "str | None" = None,
) -> RangePlan:
    """Called once per team, the first time they launch any target-attacker
    challenge. Subsequent challenges reuse this range (see plan_range_target).

    `allocated_port` publishes the attacker's SSH port directly (same
    PortAllocator pool `single-target` already draws from — see
    controller.py._create_range_target) alongside the existing Traefik/
    noVNC label-based route; ServiceSpec/docker_client.create_service()
    already support both `labels` and `published_ports` on one service, so
    this doesn't touch docker_client.py at all. Without this, the
    attacker's SSH server (present in the image) was reachable from
    nowhere outside the container — confirmed by testing, not assumed.

    `allocated_novnc_port` does the same for noVNC itself: a second directly
    published port onto the attacker's existing noVNC listener, alongside
    (not instead of) the Traefik/DNS route above. Traefik's route depends on
    `*.apps.<base_domain>` actually resolving (cei-labs-net's DNS, or some
    other wildcard DNS aimed at the swarm) — with no wildcard DNS available
    at all, that route is simply unreachable, and unlike single-target
    (which only ever used a bare published port to begin with) there was no
    fallback. This mirrors the SSH fix exactly, for the same reason.

    `offline_mode` (the resolved value from config.resolve_offline_mode()) collapses this to a single link:
    when true, the hostname-based route isn't emitted at all and the
    direct-IP noVNC address becomes access["attacker_url"] outright,
    instead of a novnc_url fallback sitting alongside a permanently
    unreachable primary link. That direct-IP address, and the SSH
    connect_host below, both use `offline_host` (Config.OFFLINE_HOST, the
    venue's current bare LAN IP) instead of `base_domain` when offline_mode
    is on -- base_domain itself is exactly as DNS-dependent as the
    hostname route being removed, so leaving it in either of those two
    fields would silently reintroduce the same unreachable-link problem
    this flag exists to fix."""
    attacker_image = _require_str(spec, "attacker_image")
    attacker_port = int(spec.get("attacker_port", 6080))
    # Separate, TLS-only websockify listener (operator/kali-novnc/Dockerfile's
    # /start.sh runs both) that the no-DNS noVNC fallback below is forwarded
    # to instead of the plain `attacker_port`. attacker_port stays plaintext
    # and keeps serving the Traefik-fronted primary route unchanged (that hop
    # is edge-TLS-terminated by Traefik and never leaves the private overlay
    # network); attacker_novnc_tls_port exists because the fallback -- unlike
    # the Traefik route -- goes straight from the player's browser, through
    # tcp-gateway (which does zero TLS of its own), to this container, so it
    # needs its own encrypted listener rather than reusing the plaintext one.
    attacker_novnc_tls_port = int(spec.get("attacker_novnc_tls_port", 6443))
    attacker_ssh_port = int(spec.get("attacker_ssh_port", 22))
    attacker_env = dict(spec.get("attacker_env") or {})

    # Security: generate random per-range credentials here, at instance-
    # creation time, rather than trusting anything baked into the image.
    # operator/kali-novnc/Dockerfile and operator/analyst/Dockerfile both
    # apply whichever of these env vars they care about at container START
    # (not build) time -- setting both is harmless since analyst (SSH-only,
    # no VNC) simply never reads VNC_PASSWORD. Only generated the first
    # time a team's range is created (this function isn't called again for
    # that owner until a full teardown/relaunch — see
    # controller.py._create_range_target and reboot_range_attacker), so
    # the credentials are stable across a team's whole session and only
    # rotate on a real Relaunch.
    #
    # These are deliberately TWO DIFFERENT credentials, not one shared
    # string, even though they both authenticate the same "operator"
    # account. TigerVNC's protocol silently truncates its password to 8
    # characters -- `vncpasswd -f` doesn't error or warn, it just discards
    # everything past the 8th byte. Previously a single long
    # secrets.token_urlsafe(18) string (20+ chars) was stuffed into both
    # VNC_PASSWORD and OPERATOR_PASSWORD: SSH (chpasswd, no such limit)
    # honored the full string, but VNC only ever actually accepted its
    # first 8 characters -- whatever the UI displayed as "the" password
    # would silently fail to connect over noVNC. ssh_password stays long
    # or high-entropy since chpasswd/SSH has no length ceiling to work
    # around; novnc_password is generated at exactly 8 characters so
    # there's no truncation gap between what's generated and what VNC will
    # actually honor.
    ssh_password = secrets.token_urlsafe(18)
    novnc_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    attacker_env["OPERATOR_PASSWORD"] = ssh_password
    attacker_env["VNC_PASSWORD"] = novnc_password

    range_network = naming.range_network_name(owner_id)
    attacker_name = naming.range_attacker_service_name(owner_id)
    hostname = naming.range_attacker_hostname(owner_id, base_domain)

    attacker_service = ServiceSpec(
        name=attacker_name,
        image=attacker_image,
        networks=[range_network],
        env={str(k): str(v) for k, v in attacker_env.items()},
        cap_drop=["ALL"],
        cap_add=list(_ATTACKER_CAPS),
        **workload_quota.service_kwargs(),
    )
    gateway_name = naming.range_gateway_service_name(owner_id)
    gateway_service = _gateway_service(
        gateway_name,
        [range_network, challenge_network],
        [
            {"listen": GATEWAY_SSH_PORT, "host": attacker_name, "port": attacker_ssh_port},
            {"listen": GATEWAY_NOVNC_PORT, "host": attacker_name, "port": attacker_novnc_tls_port},
            {"listen": GATEWAY_HTTP_PORT, "host": attacker_name, "port": attacker_port},
        ],
        labels=_traefik_labels(gateway_name, hostname, GATEWAY_HTTP_PORT, challenge_network),
        published_ports=[(allocated_port, GATEWAY_SSH_PORT), (allocated_novnc_port, GATEWAY_NOVNC_PORT)],
    )
    # https, not http: this URL reaches operator/tcp-gateway (a zero-TLS
    # byte-forwarding proxy) straight through to kali-novnc's dedicated TLS
    # websockify listener (attacker_novnc_tls_port above), with no Traefik
    # hop in between -- it's the whole reason this fallback exists (works
    # with no wildcard DNS). websockify terminates TLS itself there with a
    # self-signed cert generated fresh per container at boot (see
    # operator/kali-novnc/Dockerfile's /start.sh), so this path is
    # encrypted instead of silently carrying the VNC password and full
    # desktop session in cleartext, the way it would over the plain
    # listener the Traefik-fronted primary route still uses.
    # base_domain itself is exactly as DNS-dependent as the hostname route
    # this fallback exists to route around -- offline_host (a bare LAN IP)
    # replaces it here whenever offline_mode is on.
    novnc_host = offline_host if offline_mode else base_domain
    novnc_url = f"https://{novnc_host}:{allocated_novnc_port}/vnc.html"
    novnc_cert_warning = (
        "This connects over TLS with a self-signed certificate (unique to "
        "your instance), so your browser will show a certificate-warning "
        "page first — click through it (e.g. \"Advanced\" -> \"Proceed\"). "
        "That warning is expected here, not a sign of a problem."
    )
    if offline_mode:
        # No DNS at this venue at all -- don't even emit the hostname link
        # for the frontend to demote to a labeled fallback. attacker_url
        # IS the direct-IP noVNC address, so challenge-launch.js's existing
        # "no novnc_url present" branch renders exactly one button, and it
        # always works.
        attacker_url_fields = {
            "attacker_url": novnc_url,
            "novnc_note": novnc_cert_warning,
        }
    else:
        attacker_url_fields = {
            "attacker_url": f"https://{hostname}",
            "novnc_url": novnc_url,
            "novnc_note": (
                "Direct noVNC access, no DNS required — use this if the link "
                "above doesn't resolve. " + novnc_cert_warning
            ),
        }
    return RangePlan(
        owner_id=owner_id,
        network=range_network,
        attacker_service=attacker_service,
        gateway_service=gateway_service,
        access={
            **attacker_url_fields,
            "attacker_username": "operator",
            # Two distinct fields, deliberately -- never a single shared
            # "password" -- since ssh_password and novnc_password are two
            # different generated values (see the truncation comment
            # above). Any API consumer or launch panel must label these
            # separately rather than presenting one credential for both.
            "ssh_password": ssh_password,
            "novnc_password": novnc_password,
            "connect_host": offline_host if offline_mode else base_domain,
            "connect_port": allocated_port,
            "protocol": "ssh",
            "note": (
                "SSH connects via any swarm node — this IP routes to whichever node the attacker actually landed on."
                if offline_mode else
                "SSH connects via any swarm node — this hostname routes to whichever node the attacker actually landed on."
            ),
            "novnc_port": allocated_novnc_port,
        },
    )


def plan_range_target(
    owner_id: str,
    instance_key: str,
    spec: dict,
    range_network: str,
    range_access: dict,
    workload_quota: WorkloadQuota = DEFAULT_WORKLOAD_QUOTA,
) -> InstancePlan:
    """A single challenge's target within an existing (or about-to-exist)
    range. Joins ONLY the range's network — never challenge_network, never
    Traefik — so it is unreachable from anywhere except its range's own
    attacker.

    Per-team level secrets: same mechanism as plan_single_target (see its
    docstring) -- generated here and never before, since this function
    previously ignored spec["secret_keys"]/alpha/fixed entirely. That
    silently broke every per_team_dynamic flag on every target-attacker
    track (confirmed live: Natas's natas0 page served the literal
    unsubstituted "__NATAS1_SECRET__" placeholder to every team, since
    LEVEL_SECRETS was never set) -- CTFd's routes.py._spec_with_secret_keys
    already aggregates every sibling challenge's flag data across the
    whole instance_group correctly; this function just never consumed
    what it was handed."""
    target_image = _require_str(spec, "target_image")
    target_env = spec.get("target_env") or {}

    secret_keys = spec.get("secret_keys") or []
    alpha_secret_keys = spec.get("alpha_secret_keys") or []
    fixed_secret_keys = spec.get("fixed_secret_keys") or []
    track_secrets = generate_track_secrets(secret_keys) if secret_keys else {}
    track_secrets.update(generate_alpha_track_secrets(alpha_secret_keys) if alpha_secret_keys else {})
    track_secrets.update(generate_fixed_length_track_secrets(fixed_secret_keys) if fixed_secret_keys else {})
    if track_secrets:
        target_env = {**target_env, "LEVEL_SECRETS": json.dumps(track_secrets)}

    target_name = naming.range_target_service_name(owner_id, instance_key)
    target_service = ServiceSpec(
        name=target_name,
        image=target_image,
        networks=[range_network],
        env={str(k): str(v) for k, v in target_env.items()},
        cap_drop=["ALL"],
        cap_add=list(_TARGET_CAPS),
        **workload_quota.service_kwargs(),
    )
    return InstancePlan(
        type=TARGET_ATTACKER,
        owner_id=owner_id,
        instance_key=instance_key,
        services=[target_service],
        range_owner_id=owner_id,
        access={
            **range_access,
            "target_hostname": target_name,
            "target_note": "Target is reachable only from your attacker workstation, at the hostname above.",
            **track_secrets,
        },
    )
