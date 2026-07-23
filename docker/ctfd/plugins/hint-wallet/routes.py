"""docker/ctfd/plugins/hint-wallet/routes.py

Two audiences:
  - /machine/sync            machine-to-machine, NOT session-authed --
                            called by CEI-Labs-Wargames/deploy.sh's
                            sync_hint_wallet_bundle() from outside the
                            Docker overlay network (see that repo's
                            deploy.sh and this repo's
                            docs/P0-FIX-LOG-2026-07-23.md). A thin
                            pass-through proxy: takes the raw request body
                            and X-Hint-Wallet-Signature header exactly as
                            received and POSTs them unchanged to the
                            orchestrator's own /wallet/sync (reachable only
                            from inside the Docker overlay, e.g. from this
                            ctfd container -- the whole reason this plugin
                            exists), then relays back whatever status
                            code/body the orchestrator returns. The
                            orchestrator does its own HMAC-SHA256
                            verification against hint_wallet_sync_secret;
                            this route does NOT duplicate that secret or
                            re-verify the signature -- it only fails fast
                            on a request that's missing what the orchestrator
                            would obviously reject anyway (no signature
                            header at all, or an empty body), so a
                            malformed sync doesn't waste a call to the
                            orchestrator. @bypass_csrf_protection is needed
                            for the same reason instance-launcher's
                            /admin/mappings/sync route needs it -- this is
                            deliberately called without a CTFd session.
  - /api/tiers/<track>/<entry_name>,
    /api/balance,
    /api/unlock                participant, session-authed (CTFd login,
                            @authed_only) -- browse a hint's tier costs
                            (content withheld until unlocked), check wallet
                            balance, and spend points to unlock a tier.
                            owner_id is str(get_current_user().account_id),
                            the SAME convention instance-launcher/routes.py
                            already uses (team id in team mode, user id in
                            user mode) -- reused exactly, not reinvented,
                            so a team's instance-launcher usage and
                            hint-wallet balance are keyed identically.
"""
import json
import logging

from flask import Blueprint, Response, jsonify, request

from CTFd.models import db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user

from .models import HintWalletCatalog
from .orchestrator_client import OrchestratorClient, OrchestratorError

logger = logging.getLogger(__name__)

hint_wallet_bp = Blueprint(
    "hint_wallet",
    __name__,
    url_prefix="/plugins/hint-wallet",
)


def _cache_catalog_for_browsing(raw_body: bytes) -> None:
    """Best-effort local cache update, called only after the orchestrator
    itself has already returned 200 for this exact body -- see
    machine_sync() below. Any failure here must never affect the response
    already sent for the sync (the orchestrator's acceptance is the real
    outcome); callers are expected to catch and log, not propagate."""
    bundle = json.loads(raw_body)
    catalog = HintWalletCatalog.query.get(1)
    if catalog is None:
        catalog = HintWalletCatalog(id=1, bundle_json="{}")
        db.session.add(catalog)
    catalog.revision = bundle.get("revision")
    catalog.bundle_json = json.dumps(bundle)
    db.session.commit()


@hint_wallet_bp.route("/machine/sync", methods=["POST"])
@bypass_csrf_protection
def machine_sync():
    signature = request.headers.get("X-Hint-Wallet-Signature", "")
    raw_body = request.get_data()

    # Fail fast on what the orchestrator would obviously reject anyway --
    # don't waste a call to it for a request that's missing the one thing
    # its own auth check requires, or that carries no body to validate.
    if not signature:
        logger.warning("hint-wallet sync rejected before reaching orchestrator: missing X-Hint-Wallet-Signature")
        return jsonify(error="invalid_signature"), 401
    if not raw_body:
        logger.warning("hint-wallet sync rejected before reaching orchestrator: empty body")
        return jsonify(error="invalid_schema"), 400

    client = OrchestratorClient.from_env()
    try:
        resp = client.proxy_wallet_sync(raw_body, signature)
    except OrchestratorError as exc:
        logger.error("hint-wallet sync: %s", exc)
        return jsonify(error="orchestrator_unreachable"), 502

    if resp.status_code == 200:
        try:
            _cache_catalog_for_browsing(raw_body)
        except Exception:
            db.session.rollback()
            logger.exception(
                "hint-wallet sync: orchestrator accepted the bundle but caching it locally for "
                "tier browsing failed -- balances/unlocks are unaffected, only /api/tiers is stale"
            )

    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))


@hint_wallet_bp.route("/api/tiers/<track>/<path:entry_name>", methods=["GET"])
@authed_only
def api_tiers(track: str, entry_name: str):
    """Tier numbers and costs only -- never `content` -- so a player can
    see what a hint would cost before spending on it."""
    catalog = HintWalletCatalog.query.get(1)
    if catalog is None:
        return jsonify(error="no_active_catalog"), 409

    bundle = json.loads(catalog.bundle_json)
    for manifest in bundle.get("manifests", []):
        if manifest.get("track") != track:
            continue
        for entry in manifest.get("entries", []):
            if entry.get("name") == entry_name:
                tiers = [{"tier": t["tier"], "cost": t["cost"]} for t in entry.get("tiers", [])]
                return jsonify(track=track, entry_name=entry_name, tiers=tiers), 200
    return jsonify(error="hint_not_found"), 404


@hint_wallet_bp.route("/api/balance", methods=["GET"])
@authed_only
def api_balance():
    user = get_current_user()
    owner_id = str(user.account_id)
    client = OrchestratorClient.from_env()

    try:
        result = client.balance(owner_id)
    except OrchestratorError as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(result), 200


@hint_wallet_bp.route("/api/unlock", methods=["POST"])
@authed_only
def api_unlock():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="request body must be a JSON object"), 400

    track = body.get("track")
    entry_name = body.get("entry_name")
    tier = body.get("tier")
    if not track or not entry_name or not isinstance(tier, int) or isinstance(tier, bool):
        return jsonify(error="'track', 'entry_name', and integer 'tier' are required"), 400

    user = get_current_user()
    owner_id = str(user.account_id)
    client = OrchestratorClient.from_env()

    try:
        result = client.deduct(owner_id, track, entry_name, tier)
    except OrchestratorError as exc:
        return jsonify(error=str(exc)), 502

    # deduct() returns success=False with the orchestrator's exact
    # status_code (402 insufficient_balance, 404 hint_not_found,
    # 409 no_active_catalog) for expected rejections -- relayed as-is so a
    # rejection reaches the player as a real error response, never a 200.
    status_code = result.pop("status_code", 200)
    return jsonify(result), status_code
