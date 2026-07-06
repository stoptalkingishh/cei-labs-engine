"""docker/orchestrator/app/main.py

Internal-only HTTP API. Never exposed via Traefik/the public internet — only
reachable from CTFd's instance-launcher plugin over the orchestrator-internal
overlay network (see docker/stack.yml). All /instances* routes require the
X-Orchestrator-Auth header to match the shared secret both sides mount from
the same Docker secret.
"""
import hmac
import logging

from flask import Flask, jsonify, request

from .config import Config
from .controller import CapacityError, InstanceController
from .docker_client import DockerOrchestratorClient
from .instance_types import InvalidInstanceRequestError, VALID_TYPES
from .naming import InvalidIdentifierError
from .reaper import Reaper
from .store import InstanceStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _authorized(provided: "str | None", expected: str) -> bool:
    if not expected:
        # Refuse to run wide open if the deployment forgot to set a secret.
        return False
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def create_app(config: "Config | None" = None, docker_client=None, start_reaper: bool = True) -> Flask:
    """`docker_client`/`start_reaper` are injectable so tests can run without
    a real Docker daemon or background thread — production (wsgi/CLI) always
    calls this with defaults."""
    cfg = config or Config()
    app = Flask(__name__)

    docker_client = docker_client or DockerOrchestratorClient(cfg.DOCKER_SOCKET)
    store = InstanceStore()
    controller = InstanceController(
        docker_client=docker_client,
        store=store,
        base_domain=cfg.BASE_DOMAIN,
        challenge_network=cfg.CHALLENGE_NETWORK,
        max_instances=cfg.MAX_INSTANCES,
    )
    reaper = Reaper(controller, store, cfg.IDLE_GRACE_MINUTES, cfg.REAP_INTERVAL_SECONDS)
    if start_reaper:
        reaper.start()

    app.config["cfg"] = cfg
    app.config["controller"] = controller
    app.config["store"] = store
    app.config["reaper"] = reaper

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
        provided = request.headers.get("X-Orchestrator-Auth")
        if not _authorized(provided, cfg.PLUGIN_SHARED_SECRET):
            return jsonify(error="unauthorized"), 401
        return None

    @app.post("/instances")
    def create_instance():
        body = request.get_json(silent=True) or {}
        instance_type = body.get("type")
        owner_id = body.get("owner_id")
        instance_key = body.get("instance_key")
        spec = body.get("spec") or {}

        if instance_type not in VALID_TYPES:
            return jsonify(error=f"'type' must be one of {list(VALID_TYPES)}"), 400
        if not owner_id or not instance_key:
            return jsonify(error="'owner_id' and 'instance_key' are required"), 400

        try:
            plan, created = controller.create_or_get(instance_type, owner_id, instance_key, spec)
        except (InvalidInstanceRequestError, InvalidIdentifierError) as exc:
            return jsonify(error=str(exc)), 400
        except CapacityError as exc:
            return jsonify(error=str(exc)), 503
        except Exception:
            logger.exception("failed to create instance owner=%s key=%s", owner_id, instance_key)
            return jsonify(error="internal error creating instance"), 500

        return jsonify(status="created" if created else "exists", type=plan.type, access=plan.access), 201 if created else 200

    @app.get("/instances/<owner_id>/<instance_key>")
    def get_instance(owner_id: str, instance_key: str):
        record = store.get(owner_id, instance_key)
        if record is None:
            return jsonify(error="not found"), 404
        record.touch()
        return jsonify(type=record.plan.type, access=record.plan.access, idle_seconds=record.idle_seconds())

    @app.delete("/instances/<owner_id>/<instance_key>")
    def delete_instance(owner_id: str, instance_key: str):
        removed = controller.teardown(owner_id, instance_key)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    @app.get("/admin/instances")
    def admin_list_instances():
        return jsonify([
            {
                "owner_id": r.owner_id,
                "instance_key": r.instance_key,
                "type": r.plan.type,
                "access": r.plan.access,
                "idle_seconds": r.idle_seconds(),
                "created_at": r.created_at,
            }
            for r in store.all()
        ])

    @app.delete("/admin/instances/<owner_id>/<instance_key>")
    def admin_delete_instance(owner_id: str, instance_key: str):
        removed = controller.teardown(owner_id, instance_key)
        if not removed:
            return jsonify(error="not found"), 404
        return jsonify(status="removed"), 200

    return app
