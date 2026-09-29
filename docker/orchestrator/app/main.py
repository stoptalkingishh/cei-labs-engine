"""docker/orchestrator/app/main.py

Internal-only HTTP API. Never exposed via Traefik/the public internet — only
reachable from CTFd's instance-launcher plugin over the orchestrator-internal
overlay network (see docker/stack.yml). All /instances* and /ranges* routes
require the X-Orchestrator-Auth header to match the shared secret both sides
mount from the same Docker secret. /admin/* routes use X-Admin-Auth instead.

/wallet/sync is the one exception to the X-Orchestrator-Auth rule: it's
called by the Wargames release pipeline, not the CTFd plugin, and
authenticates itself with an HMAC-SHA256 body signature (X-Hint-Wallet-
Signature) against a dedicated secret instead -- see
docs/P0-FIX-LOG-2026-07-23.md for the full contract. /wallet/unlock and
/wallet/unlocked/<owner_id>/<track>/<entry_name> are CTFd-plugin-facing and
use the normal X-Orchestrator-Auth check like /instances*.

Hint-wallet cost model (cei-labs-event#7): tiers are priced as a percentage
of the challenge's own point value, and unlocking a tier is pure
record-keeping (no shared team-currency balance exists anywhere in this
service -- see app/store.py's WalletStore docstring). The percentage is
applied as a reduction of that challenge's own score award at solve time,
CTFd-side (docker/ctfd/plugins/hint-wallet/solve_hook.py), which reads back
the highest tier a team opened via /wallet/unlocked/....

What each audience gets back (see _access_for_response for the full rule): a
plan's `access` dict mixes the player's own connect credentials with that
team's generated per-team flag values and marks neither, so it is served
differently per route. The `/admin/*` listings carry no `access` at all --
one static admin header must not be able to read every team's passwords and
flags at once. `POST /instances` serves `access` only on the `201` (the one
response where an environment was really built) and never with flag values.
`GET /instances/<owner_id>/<instance_key>` is the single exception: it is
what the CTFd plugin's _persist_and_scrub_secrets feeds, and that scrub is
the only thing that ever populates TeamChallengeSecret, which in turn is the
only thing flags.PerTeamDynamicFlag.compare() validates against -- so it
still carries them, deliberately.
"""
import hashlib
import hmac
import json
import logging
import time

from flask import Flask, jsonify, request

from . import instance_types
from . import wallet
from .config import Config, resolve_offline_mode
from .controller import (
    CapacityError,
    ExtensionsExhaustedError,
    InstanceController,
    InstanceInitializingError,
    NotFoundError,
    ShutdownNotPendingError,
)
from .crypto import CredentialCipher, is_valid_key_material
from .docker_client import DockerOrchestratorClient
from .instance_types import InvalidInstanceRequestError, VALID_TYPES
from .naming import InvalidIdentifierError
from .ports import PortAllocator, PortsExhaustedError
from .reaper import Reaper
from .store import InstanceStore, RangeStore, WalletStore
from .wallet import WalletIncompleteTracksError, WalletSchemaError, WalletValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Hard ceiling on any request body, enforced by Werkzeug before a route reads
# the stream. Sized for the biggest legitimate body this service accepts: the
# hint catalog POST /wallet/sync receives (three tracks x every challenge x
# three tiers of hint prose -- a few hundred KB on a real deployment), with a
# large margin on top of that.
#
# This exists specifically because /wallet/sync is the one route _enforce_auth
# lets through unauthenticated (it carries its own HMAC body signature) while
# its first statement is `raw_body = request.get_data()`. Without a ceiling,
# the signature check sits downstream of a full buffer of the attacker's
# body: anyone who can reach the orchestrator-internal network can POST a
# huge body and have gunicorn buffer all of it before anything is rejected.
# The orchestrator is capped at `memory: 256M` (docker/stack.yml), so a
# couple of concurrent oversized uploads OOM-kills the container and takes
# every team's live environments with it -- repeatable, pre-auth, cheap.
MAX_CONTENT_LENGTH_BYTES = 1024 * 1024

# Ceiling on how many per-level keys a single create request may ask for.
# generate_track_secrets()/generate_alpha_track_secrets()/
# generate_fixed_length_track_secrets() (instance_types.py) each generate one
# random value per key and merge the result into BOTH the container's
# LEVEL_SECRETS env blob and the plan's `access` dict, so an unbounded list
# turns one small authorized request into an arbitrarily large allocation --
# and an arbitrarily large encrypted plan_json row, re-decrypted in full on
# every status check. A real deployment's level count is a handful per
# challenge group, not thousands.
MAX_SECRET_KEYS_PER_SPEC = 64


def _authorized(provided: "str | None", expected: str) -> bool:
    if not expected:
        # Refuse to run wide open if the deployment forgot to set a secret.
        return False
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _access_for_response(plan, include_flag_secrets: bool = False) -> dict:
    """The `access` dict as it may be sent to THIS caller, with per-team flag
    material (instance_types.InstancePlan.flag_secret_keys) stripped unless
    the caller is the one response that legitimately needs it.

    Why this is per-caller and not "always strip": `access` is the transport
    CTFd uses to learn a team's flag values at all. The plugin's
    _persist_and_scrub_secrets pops each level key out of `access` and
    upserts it into TeamChallengeSecret, and that row is the ONLY thing
    flags.PerTeamDynamicFlag.compare() validates a submission against -- no
    other path writes it, and no orchestrator round-trip happens at solve
    time. So the one response feeding that scrub (GET
    /instances/<owner>/<key>) has to carry the values, and every other
    response can drop them: a caller that never receives them cannot leak
    them, and a flag value that nothing is persisting anywhere is not a
    credential any downstream consumer needs.

    Genuine credentials (ssh_password/novnc_password, connect_host/port, the
    URLs) are never touched here -- the plugin needs them to show the player
    their own console, and they are the player's to know."""
    if include_flag_secrets:
        return dict(plan.access)
    flag_keys = set(plan.flag_secret_keys or ())
    return {key: value for key, value in plan.access.items() if key not in flag_keys}


def _instance_response(record, cfg: "Config | None" = None, include_flag_secrets: bool = False) -> dict:
    body = {
        "type": record.plan.type,
        "access": _access_for_response(record.plan, include_flag_secrets),
        "idle_seconds": record.idle_seconds(),
        # Non-destructive pause state (idle timeout / post-solve countdown
        # already fired, container stopped) vs. live -- NOT the same thing
        # as "gone": a stopped instance's credentials/flags are unchanged
        # and a create_or_get()/reboot() call resumes it with the same
        # values. See controller.py's "Pause / resume" section.
        "stopped": record.stopped,
    }
    if record.shutdown_pending():
        body["shutdown_at"] = record.shutdown_at
        body["extensions_used"] = record.extensions_used
    if cfg is not None:
        # Countdown/warning info for callers, per
        # docs/P0-FIX-LOG-2026-07-23.md's expiration-behavior notes: idle
        # pausing is non-destructive (credentials survive), but the
        # absolute lifetime ceiling below is a real, one-way expiration --
        # once it fires the record itself is deleted and any later access
        # is a brand-new environment with brand-new credentials.
        if not record.stopped:
            body["idle_pause_at"] = record.last_accessed + cfg.IDLE_GRACE_MINUTES * 60
        if cfg.MAX_INSTANCE_LIFETIME_MINUTES:
            body["expires_at"] = record.created_at + cfg.MAX_INSTANCE_LIFETIME_MINUTES * 60
    return body


def create_app(config: "Config | None" = None, docker_client=None, start_reaper: bool = True) -> Flask:
    """`docker_client`/`start_reaper` are injectable so tests can run without
    a real Docker daemon or background thread — production (wsgi/CLI) always
    calls this with defaults.

    `config` follows the same convention: production (app/wsgi.py) always
    calls create_app() with no config, letting it default to a real
    Config() reading from env/secrets; every test constructs its own
    FakeConfig (see tests/test_api.py) and passes it explicitly. That is
    also this method's signal for whether it is safe to run with an
    ephemeral, non-persistent encryption key -- see the
    CREDENTIAL_ENCRYPTION_KEY check below."""
    production = config is None
    cfg = config or Config()
    app = Flask(__name__)

    if production and not is_valid_key_material(cfg.CREDENTIAL_ENCRYPTION_KEY):
        # Fail loudly and refuse to start, matching this codebase's other
        # secret-gated components: operator/kali-novnc/Dockerfile's
        # /start.sh exit 1's without VNC_PASSWORD/OPERATOR_PASSWORD, and
        # POST /wallet/sync 503s without hint_wallet_sync_secret.
        #
        # CredentialCipher.from_key_material() itself never refuses -- it
        # silently falls back to an ephemeral in-process key, which is
        # useful for local dev/testing (see its docstring and
        # tests/test_crypto.py) but is exactly the wrong thing for a real
        # deployment: store.py raises on any read of data encrypted under a
        # different key than currently configured (see
        # test_a_row_written_under_one_key_is_unreadable_under_a_different_key
        # in tests/test_store_encryption.py), so an orchestrator that
        # silently started with a fresh random key on this restart would
        # crash on its very next store.get()/store.all() call -- including
        # the reaper's every sweep -- instead of failing at startup where
        # the cause is obvious.
        raise RuntimeError(
            "credential_encryption_key is not configured (or is not a valid Fernet "
            "key) -- refusing to start. Set the `credential_encryption_key` Docker "
            "secret before starting the orchestrator in production (see "
            "docker/secrets.example/ and app/crypto.py), e.g.: "
            'python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )

    # Resolved exactly once here at startup, not per-request -- "auto"
    # means a real DNS probe against cfg.BASE_DOMAIN (see
    # config.resolve_offline_mode's docstring), which is deliberately not
    # something every request or every test run should trigger.
    offline_mode = resolve_offline_mode(cfg)
    logger.info(
        "offline_mode resolved to %s (setting=%r, base_domain=%r)",
        offline_mode, cfg.OFFLINE_MODE_SETTING, cfg.BASE_DOMAIN,
    )

    docker_client = docker_client or DockerOrchestratorClient(cfg.DOCKER_SOCKET)
    # Shared between both stores so a single deployment's persisted
    # instances and ranges are encrypted (and decryptable) under the same
    # key -- see crypto.py and config.py's CREDENTIAL_ENCRYPTION_KEY.
    cipher = CredentialCipher.from_key_material(cfg.CREDENTIAL_ENCRYPTION_KEY)
    store = InstanceStore(db_path=cfg.STORE_DB_PATH, cipher=cipher)
    range_store = RangeStore(db_path=cfg.STORE_DB_PATH, cipher=cipher)
    # WalletStore holds catalog/balance data only, never credentials, so it
    # does not take the cipher -- see docs/P0-FIX-LOG-2026-07-23.md.
    wallet_store = WalletStore(db_path=cfg.STORE_DB_PATH)
    port_allocator = PortAllocator(
        cfg.SSH_PORT_RANGE_START,
        cfg.SSH_PORT_RANGE_END,
        db_path=cfg.STORE_DB_PATH,
    )
    controller = InstanceController(
        docker_client=docker_client,
        store=store,
        range_store=range_store,
        port_allocator=port_allocator,
        base_domain=cfg.BASE_DOMAIN,
        challenge_network=cfg.CHALLENGE_NETWORK,
        max_instances=cfg.MAX_INSTANCES,
        shutdown_max_extensions=cfg.SHUTDOWN_MAX_EXTENSIONS,
        max_instances_per_owner=cfg.MAX_INSTANCES_PER_OWNER,
        workload_quota=instance_types.WorkloadQuota(
            memory_limit_bytes=cfg.WORKLOAD_MEMORY_LIMIT_BYTES,
            memory_reservation_bytes=cfg.WORKLOAD_MEMORY_RESERVATION_BYTES,
            cpu_limit_nanos=cfg.WORKLOAD_CPU_LIMIT_NANOS,
        ),
        offline_mode=offline_mode,
        offline_host=cfg.OFFLINE_HOST,
    )
    reaper = Reaper(
        controller,
        store,
        range_store,
        cfg.IDLE_GRACE_MINUTES,
        cfg.REAP_INTERVAL_SECONDS,
        cfg.MAX_INSTANCE_LIFETIME_MINUTES,
        cfg.RESERVATION_TIMEOUT_SECONDS,
    )
    if start_reaper:
        reaper.start()

    app.config.update(
        cfg=cfg, controller=controller, store=store, range_store=range_store, wallet_store=wallet_store, reaper=reaper,
        # See MAX_CONTENT_LENGTH_BYTES above: without this, the pre-auth
        # /wallet/sync route buffers an arbitrarily large body into a
        # memory-capped container before its HMAC check ever runs.
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH_BYTES,
    )

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok"), 200

    @app.before_request
    def _enforce_auth():
        if request.path == "/healthz":
            return None
        if request.path.startswith("/admin/"):
            provided = request.headers.get("X-Admin-Auth")
            if not _authorized(provided, cfg.ADMIN_PASSWORD):
                return jsonify(error="unauthorized"), 401
            return None
        if request.path == "/wallet/sync":
            # Authenticated inside the route itself via HMAC body signature
            # against a dedicated secret -- a different trust boundary than
            # X-Orchestrator-Auth (release pipeline, not the CTFd plugin).
            return None
        provided = request.headers.get("X-Orchestrator-Auth")
        if not _authorized(provided, cfg.PLUGIN_SHARED_SECRET):
            return jsonify(error="unauthorized"), 401
        return None

    # ── Instances ─────────────────────────────────────────────────────────────
    @app.post("/instances")
    def create_instance():
        body = request.get_json(silent=True) or {}
        instance_type = body.get("type")
        owner_id = body.get("owner_id")
        instance_key = body.get("instance_key")
        spec = body.get("spec") or {}
        force_relaunch = bool(body.get("relaunch", False))

        if instance_type not in VALID_TYPES:
            return jsonify(error=f"'type' must be one of {list(VALID_TYPES)}"), 400
        if not owner_id or not instance_key:
            return jsonify(error="'owner_id' and 'instance_key' are required"), 400

        # Rejected here, before instance_types.py ever iterates them -- see
        # MAX_SECRET_KEYS_PER_SPEC. Also pins the shape: these are consumed as
        # an iterable of level keys, so a caller sending a dict or a string
        # would otherwise have it silently iterated one character at a time.
        for keys_field in ("secret_keys", "alpha_secret_keys", "fixed_secret_keys"):
            keys = spec.get(keys_field)
            if keys is None:
                continue
            if not isinstance(keys, list) or len(keys) > MAX_SECRET_KEYS_PER_SPEC:
                return jsonify(
                    error=f"'{keys_field}' must be a list of at most {MAX_SECRET_KEYS_PER_SPEC} level keys"
                ), 400

        # Recorded before the call: create_or_get() clears `stopped` itself
        # when it resumes a paused record, so this is the only place left to
        # tell "resumed a paused environment" apart from "already running,
        # untouched" -- both report created=False from create_or_get().
        was_stopped = False
        if not force_relaunch:
            existing_before = store.get(owner_id, instance_key)
            was_stopped = existing_before.stopped if existing_before is not None else False

        try:
            plan, created = controller.create_or_get(instance_type, owner_id, instance_key, spec, force_relaunch)
        except (InvalidInstanceRequestError, InvalidIdentifierError) as exc:
            return jsonify(error=str(exc)), 400
        except (CapacityError, PortsExhaustedError, InstanceInitializingError) as exc:
            return jsonify(error=str(exc)), 503
        except Exception:
            logger.exception("failed to create instance owner=%s key=%s", owner_id, instance_key)
            return jsonify(error="internal error creating instance"), 500

        # "relaunched" is the only status where credentials/flags actually
        # changed -- it's the sole caller-visible signal that an explicit
        # reset happened, vs. "resumed" (same credentials, container was
        # paused) or "exists" (same credentials, was already running).
        if created:
            status = "created"
        elif force_relaunch:
            status = "relaunched"
        elif was_stopped:
            status = "resumed"
        else:
            status = "exists"

        response = {"status": status, "type": plan.type}
        if created:
            # 201 is returned for exactly one thing: an environment that was
            # (re)built, i.e. the only case where the connect info below is
            # news to the caller. It is also the only place `access` appears
            # on this route -- on the 200 exists/resumed paths nothing was
            # created and the plugin already holds these exact credentials
            # (it just fetched them for this same owner/key moments earlier),
            # so re-serving them only widens how many responses carry every
            # team's passwords around. Checked against the plugin: routes.py's
            # _run_action discards create_or_get()'s return value entirely
            # and re-reads state through GET /instances/<owner>/<key>, so
            # nothing downstream depends on this body. Flag values are dropped
            # even here -- see _access_for_response.
            response["access"] = _access_for_response(plan)
        return jsonify(**response), 201 if created else 200

    @app.get("/instances/<owner_id>/<instance_key>")
    def get_instance(owner_id: str, instance_key: str):
        record = store.get(owner_id, instance_key)
        if record is None:
            return jsonify(error="not found"), 404
        record.touch()
        store.touch(owner_id, instance_key)
        # include_flag_secrets=True, and deliberately so -- this is the one
        # response the CTFd plugin's _persist_and_scrub_secrets feeds, and
        # that scrub is the only thing that ever populates
        # TeamChallengeSecret, which is in turn the only thing
        # flags.PerTeamDynamicFlag.compare() validates a submission against.
        # Strip the flag values here and every per_team_dynamic flag in the
        # deployment fails closed forever, silently, since compare() just
        # returns False when the row is missing. The plugin pops each of them
        # out of `access` and upserts it CTFd-side before anything is rendered
        # to or returned to a player, which is the boundary that actually
        # matters; every other response in this file is held to the stricter
        # rule they can live with (see _access_for_response).
        return jsonify(**_instance_response(record, cfg, include_flag_secrets=True))

    @app.delete("/instances/<owner_id>/<instance_key>")
    def delete_instance(owner_id: str, instance_key: str):
        removed = controller.teardown(owner_id, instance_key)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    @app.post("/instances/<owner_id>/<instance_key>/reboot")
    def reboot_instance(owner_id: str, instance_key: str):
        # Same credentials/flags either way -- reboot() never generates a
        # fresh plan (only force_relaunch=True does). This only tells the
        # caller which path was taken (in-place restart vs. resuming a
        # paused instance), not that anything about the environment itself
        # changed.
        record = store.get(owner_id, instance_key)
        was_stopped = record.stopped if record is not None else False
        ok = controller.reboot(owner_id, instance_key)
        if not ok:
            return jsonify(error="not found"), 404
        return jsonify(status="resumed" if was_stopped else "rebooting"), 200

    # ── Post-solve shutdown countdown ────────────────────────────────────────
    @app.post("/instances/<owner_id>/<instance_key>/schedule-shutdown")
    def schedule_shutdown(owner_id: str, instance_key: str):
        body = request.get_json(silent=True) or {}
        delay_seconds = int(body.get("delay_seconds", cfg.SHUTDOWN_DELAY_SECONDS))
        try:
            shutdown_at = controller.schedule_shutdown(owner_id, instance_key, delay_seconds)
        except NotFoundError as exc:
            return jsonify(error=str(exc)), 404
        return jsonify(status="scheduled", shutdown_at=shutdown_at), 200

    @app.post("/instances/<owner_id>/<instance_key>/extend-shutdown")
    def extend_shutdown(owner_id: str, instance_key: str):
        body = request.get_json(silent=True) or {}
        extend_seconds = int(body.get("extend_seconds", cfg.SHUTDOWN_EXTEND_SECONDS))
        try:
            shutdown_at = controller.extend_shutdown(owner_id, instance_key, extend_seconds)
        except NotFoundError as exc:
            return jsonify(error=str(exc)), 404
        except ShutdownNotPendingError as exc:
            return jsonify(error=str(exc)), 409
        except ExtensionsExhaustedError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(status="extended", shutdown_at=shutdown_at), 200

    # ── Ranges (shared target-attacker attacker + network) ──────────────────
    @app.post("/ranges/<owner_id>/attacker/reboot")
    def reboot_range_attacker(owner_id: str):
        range_record = range_store.get(owner_id)
        was_stopped = range_record.stopped if range_record is not None else False
        ok = controller.reboot_range_attacker(owner_id)
        if not ok:
            return jsonify(error="not found"), 404
        return jsonify(status="resumed" if was_stopped else "rebooting"), 200

    @app.delete("/ranges/<owner_id>")
    def delete_range(owner_id: str):
        removed = controller.teardown_range(owner_id)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    # ── Hint wallet ──────────────────────────────────────────────────────────
    @app.post("/wallet/sync")
    def wallet_sync():
        raw_body = request.get_data()  # exact bytes, before any JSON re-encoding
        secret_ids = {
            "current": cfg.HINT_WALLET_SYNC_SECRET,
            "previous": cfg.HINT_WALLET_SYNC_SECRET_PREVIOUS,
        }
        secret_ids = {sid: value for sid, value in secret_ids.items() if value}
        if not secret_ids:
            logger.error("wallet sync rejected: no hint_wallet_sync_secret configured")
            return jsonify(error="secret_or_database_unavailable"), 503

        signature = request.headers.get("X-Hint-Wallet-Signature", "")
        matched_secret_id = None
        if signature:
            for secret_id, secret_value in secret_ids.items():
                expected = hmac.new(secret_value.encode(), raw_body, hashlib.sha256).hexdigest()
                if hmac.compare_digest(signature, expected):
                    matched_secret_id = secret_id
                    break
        if matched_secret_id is None:
            logger.warning("wallet sync rejected: invalid_signature")
            return jsonify(error="invalid_signature"), 401

        try:
            bundle = json.loads(raw_body)
        except (ValueError, TypeError):
            logger.warning("wallet sync rejected: invalid_schema (unparseable JSON) secret_id=%s", matched_secret_id)
            return jsonify(error="invalid_schema"), 400

        try:
            manifests = wallet.validate_bundle(bundle)
        except WalletSchemaError as exc:
            logger.warning("wallet sync rejected: invalid_schema (%s) secret_id=%s", exc, matched_secret_id)
            return jsonify(error="invalid_schema"), 400
        except WalletIncompleteTracksError as exc:
            logger.warning("wallet sync rejected: incomplete_tracks (%s) secret_id=%s", exc, matched_secret_id)
            return jsonify(error="incomplete_tracks"), 400
        except WalletValidationError as exc:
            logger.warning("wallet sync rejected: catalog_validation_failed (%s) secret_id=%s", exc, matched_secret_id)
            return jsonify(error="catalog_validation_failed"), 422

        revision = bundle["revision"]
        catalog_digest = hashlib.sha256(raw_body).hexdigest()

        try:
            result = wallet_store.try_accept_catalog(revision, catalog_digest, manifests, matched_secret_id)
        except Exception:
            logger.exception("wallet sync failed: database error secret_id=%s revision=%s", matched_secret_id, revision)
            return jsonify(error="secret_or_database_unavailable"), 503

        logger.info(
            "wallet sync result=%s secret_id=%s revision=%s digest=%s at=%s",
            result, matched_secret_id, revision, catalog_digest, time.time(),
        )
        if result == "stale":
            return jsonify(error="stale_revision"), 409
        if result == "conflict":
            return jsonify(error="revision_digest_conflict"), 409
        # "accepted" or "idempotent" -- both are success from the caller's
        # point of view (idempotent retry of an already-applied revision).
        return jsonify(status=result, revision=revision, digest=catalog_digest), 200

    @app.post("/wallet/unlock")
    def wallet_unlock():
        body = request.get_json(silent=True) or {}
        owner_id = body.get("owner_id")
        track = body.get("track")
        entry_name = body.get("entry_name")
        tier = body.get("tier")
        if not owner_id or not track or not entry_name or not isinstance(tier, int) or isinstance(tier, bool):
            return jsonify(error="'owner_id', 'track', 'entry_name', and integer 'tier' are required"), 400

        catalog = wallet_store.get_catalog()
        if catalog is None:
            return jsonify(error="no_active_catalog"), 409
        cost_percent = wallet.find_hint_cost(catalog["manifests"], track, entry_name, tier)
        if cost_percent is None:
            return jsonify(error="hint_not_found"), 404

        status, cost_percent = wallet_store.unlock_hint(owner_id, track, entry_name, tier, cost_percent)
        content = wallet.find_hint_content(catalog["manifests"], track, entry_name, tier)
        return jsonify(status=status, cost_percent=cost_percent, content=content), 200

    @app.get("/wallet/unlocked/<owner_id>/<track>/<path:entry_name>")
    def wallet_unlocked(owner_id: str, track: str, entry_name: str):
        """The highest tier `owner_id` has opened for this specific
        challenge, if any -- used by CTFd's hint-wallet solve_hook to decide
        how much of that challenge's award to keep. Never 404s: "nothing
        opened" is a normal, common state (full value retained)."""
        result = wallet_store.highest_unlocked_tier(owner_id, track, entry_name)
        if result is None:
            return jsonify(owner_id=owner_id, track=track, entry_name=entry_name, tier=None, cost_percent=None), 200
        return jsonify(owner_id=owner_id, track=track, entry_name=entry_name, **result), 200

    # ── Admin ────────────────────────────────────────────────────────────────
    @app.get("/admin/instances")
    def admin_list_instances():
        # Deliberately NOT _instance_response(). This route used to splat
        # that whole body -- `access` included -- across every row of an
        # unbounded store.all(), which handed one caller presenting the single
        # static X-Admin-Auth header the SSH/noVNC passwords AND the per-team
        # flag values of every live instance in the event, at once. An ops
        # dashboard needs identity and lifecycle state (who owns what, is it
        # idle, is it paused, when does it expire) and has no use for any
        # credential, so the projection below is an explicit allowlist rather
        # than the player-facing body with pieces removed: a field added to
        # `access` or to _instance_response() can't leak through here by
        # default, it has to be added here on purpose. Credentials stay
        # readable through the owner-scoped, X-Orchestrator-Auth-protected
        # per-instance routes.
        return jsonify([
            {
                "owner_id": r.owner_id,
                "instance_key": r.instance_key,
                "type": r.plan.type,
                "created_at": r.created_at,
                "last_accessed": r.last_accessed,
                "idle_seconds": r.idle_seconds(),
                "stopped": r.stopped,
                "shutdown_at": r.shutdown_at,
                "extensions_used": r.extensions_used,
            }
            for r in store.all()
        ])

    @app.get("/admin/ranges")
    def admin_list_ranges():
        # Same reasoning as /admin/instances above: this listing used to
        # return range access wholesale, i.e. every team's generated
        # ssh_password and novnc_password in one response behind one static
        # header. The network, target inventory and timestamps an ops
        # dashboard actually renders all stay.
        return jsonify([
            {
                "owner_id": r.owner_id,
                "network": r.plan.network,
                "target_keys": sorted(r.target_keys),
                "created_at": r.created_at,
                "last_accessed": r.last_accessed,
                "idle_seconds": r.idle_seconds(),
                "stopped": r.stopped,
            }
            for r in range_store.all()
        ])

    @app.delete("/admin/instances/<owner_id>/<instance_key>")
    def admin_delete_instance(owner_id: str, instance_key: str):
        removed = controller.teardown(owner_id, instance_key)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    @app.delete("/admin/ranges/<owner_id>")
    def admin_delete_range(owner_id: str):
        removed = controller.teardown_range(owner_id)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    return app
