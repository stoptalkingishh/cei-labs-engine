"""docker/orchestrator/app/config.py

Environment-driven configuration. Secrets are read from Docker Swarm secret
files (mounted under /run/secrets/<name>) rather than plain env vars.
"""
import os


def _read_secret(name: str, default: str = "") -> str:
    path = f"/run/secrets/{name}"
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return default


class Config:
    # Network Traefik shares with orchestrator-created public-facing
    # containers (Juice Shop, attacker noVNC). Must match the actual deployed
    # name of docker/stack.yml's `challenge-edge` network (stack-name-prefixed).
    CHALLENGE_NETWORK = os.environ.get("CHALLENGE_NETWORK", "cei-labs_challenge-edge")

    # Domain used to build per-team access URLs, e.g. <owner>-<key>.apps.<BASE_DOMAIN>
    BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "ctf.local")

    # Hard cap on total concurrent instances (mirrors MultiJuicer's maxInstances).
    MAX_INSTANCES = int(os.environ.get("ORCHESTRATOR_MAX_INSTANCES", "30"))

    # A single participant must not be able to consume the global pool. This
    # counts in-flight reservations as well as running instances, so parallel
    # launches cannot race past the limit.
    MAX_INSTANCES_PER_OWNER = int(os.environ.get("ORCHESTRATOR_MAX_INSTANCES_PER_OWNER", "3"))

    # Resource ceilings applied to every participant-controlled workload
    # service. Gateways have their own smaller hard-coded limits.
    WORKLOAD_MEMORY_LIMIT_BYTES = int(os.environ.get("ORCHESTRATOR_WORKLOAD_MEMORY_LIMIT_MB", "512")) * 1024 * 1024
    WORKLOAD_MEMORY_RESERVATION_BYTES = (
        int(os.environ.get("ORCHESTRATOR_WORKLOAD_MEMORY_RESERVATION_MB", "128")) * 1024 * 1024
    )
    WORKLOAD_CPU_LIMIT_NANOS = int(float(os.environ.get("ORCHESTRATOR_WORKLOAD_CPU_LIMIT", "1.0")) * 1_000_000_000)

    # Idle instances are torn down after this many minutes without a touch.
    IDLE_GRACE_MINUTES = int(os.environ.get("ORCHESTRATOR_IDLE_GRACE_MINUTES", "120"))

    # Absolute lifetime cap, independent of touches and solve countdowns.
    MAX_INSTANCE_LIFETIME_MINUTES = int(
        os.environ.get("ORCHESTRATOR_MAX_INSTANCE_LIFETIME_MINUTES", "240")
    )

    # Reservations older than this represent a worker that died mid-create.
    # Releasing them lets the orphan sweep remove any Docker resources that
    # were created before the worker disappeared.
    RESERVATION_TIMEOUT_SECONDS = int(os.environ.get("ORCHESTRATOR_RESERVATION_TIMEOUT_SECONDS", "300"))

    # How often the reaper thread sweeps for idle instances.
    REAP_INTERVAL_SECONDS = int(os.environ.get("ORCHESTRATOR_REAP_INTERVAL_SECONDS", "60"))

    # Shared secret the CTFd plugin must present (X-Orchestrator-Auth header).
    # Participants never see this — the plugin calls the orchestrator server-to-server.
    PLUGIN_SHARED_SECRET = _read_secret("plugin_shared_secret", os.environ.get("PLUGIN_SHARED_SECRET", ""))

    # Protects the /admin/* dashboard endpoints.
    ADMIN_PASSWORD = _read_secret("orchestrator_admin_password", os.environ.get("ORCHESTRATOR_ADMIN_PASSWORD", ""))

    # AEAD key store.py encrypts persisted credentials (plan_json -- VNC/
    # SSH passwords, per-team flag secrets) with before they reach SQLite.
    # See app/crypto.py. Deliberately outside the database (a Docker secret,
    # same mechanism as every other secret above), and unset here defaults
    # to "" so CredentialCipher.from_key_material can log its own loud
    # warning and fall back to an ephemeral key rather than this file
    # silently deciding what "unconfigured" means.
    CREDENTIAL_ENCRYPTION_KEY = _read_secret(
        "credential_encryption_key", os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
    )

    DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "unix://var/run/docker.sock")

    # SQLite-backed store path. Must be a real file (not ":memory:") in
    # production so multiple gunicorn worker processes share one store --
    # see store.py's module docstring for why this matters. Ephemeral by
    # design, same as the in-memory dict it replaced: a fresh file each
    # container start is correct, since `last_accessed`/`shutdown_at` are
    # allowed to reset on restart (only affects idle-reap/countdown timing).
    STORE_DB_PATH = os.environ.get("ORCHESTRATOR_STORE_DB_PATH", "/tmp/orchestrator-store.db")

    # Directly-published port range for `single-target` instances (SSH etc.).
    # Deliberately disjoint from spawn-workspaces.sh's bulk-provisioning
    # range (default starts at 30001) to avoid collisions between the two
    # provisioning paths — see docker/.env.example.
    SSH_PORT_RANGE_START = int(os.environ.get("ORCHESTRATOR_SSH_PORT_RANGE_START", "32000"))
    SSH_PORT_RANGE_END = int(os.environ.get("ORCHESTRATOR_SSH_PORT_RANGE_END", "32767"))

    # Post-solve auto-shutdown countdown (see controller.schedule_shutdown).
    SHUTDOWN_DELAY_SECONDS = int(os.environ.get("ORCHESTRATOR_SHUTDOWN_DELAY_SECONDS", "30"))
    SHUTDOWN_EXTEND_SECONDS = int(os.environ.get("ORCHESTRATOR_SHUTDOWN_EXTEND_SECONDS", "300"))
    SHUTDOWN_MAX_EXTENSIONS = int(os.environ.get("ORCHESTRATOR_SHUTDOWN_MAX_EXTENSIONS", "3"))
