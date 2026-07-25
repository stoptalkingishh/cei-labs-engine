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
    /api/unlock                participant, session-authed (CTFd login,
                            @authed_only) -- browse a hint's tier percent
                            costs (content withheld until unlocked) and open
                            a tier. owner_id is
                            str(get_current_user().account_id), the SAME
                            convention instance-launcher/routes.py already
                            uses (team id in team mode, user id in user
                            mode) -- reused exactly, not reinvented, so a
                            team's instance-launcher usage and hint-wallet
                            unlocks are keyed identically.

Cost model (cei-labs-event#7): there is no shared team-currency balance
anywhere in this system -- see orchestrator_client.py and the
orchestrator's app/store.py WalletStore docstring. A tier's cost is a
percentage of ITS OWN CHALLENGE's point value, applied as a reduction of
that challenge's own score award at solve time (solve_hook.py), not a
spend from a pool. api_unlock() therefore never rejects for
"insufficient balance" -- the only rejections are an unknown hint
(404 hint_not_found), no catalog yet (409 no_active_catalog), and the
progression-window gate below.

Progression-window gating (cei-labs-event#7): api_unlock() also refuses to
open a hint for a challenge outside this owner's current unlock window for
that challenge's track -- see progression.py for the pure window logic.
The window is anchored on the next challenge in that track's sequence this
owner has NOT yet solved: that challenge, plus the one immediately after
it. A track's sequence is CTFd's own Challenges rows filtered to that
track's category, ordered by id (matching the order the Wargames builders
already create them in). This mirrors solve_hook.py's existing convention
of deriving everything from CTFd.models.Solves filtered by account_id
rather than a separate progress table.
"""
import json
import logging

from flask import Blueprint, Response, jsonify, request

from CTFd.models import Challenges, Solves, db
from CTFd.plugins import bypass_csrf_protection
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user

from .models import HintWalletCatalog
from .orchestrator_client import OrchestratorClient, OrchestratorError
from .progression import is_unlockable
from .track_mapping import category_for_track

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
    outcome); callers are expected to catch and log, not propagate.

    Strips every tier's `content` before persisting -- this cache exists
    only so /api/tiers can list tier numbers/costs pre-spend. Storing the
    real hint text here would put it, unencrypted, in CTFd's own database
    (readable by an SQLi, an admin-panel dump, or a DB backup) even though
    api_tiers() itself never serializes it -- the invariant has to be
    enforced at write time, not just at the one read call site."""
    bundle = json.loads(raw_body)
    stripped_manifests = []
    for manifest in bundle.get("manifests", []):
        stripped_manifest = dict(manifest)
        stripped_manifest["entries"] = [
            {
                **entry,
                "tiers": [
                    {k: v for k, v in tier.items() if k != "content"}
                    for tier in entry.get("tiers", [])
                ],
            }
            for entry in manifest.get("entries", [])
        ]
        stripped_manifests.append(stripped_manifest)
    stripped_bundle = {**bundle, "manifests": stripped_manifests}

    catalog = HintWalletCatalog.query.get(1)
    if catalog is None:
        catalog = HintWalletCatalog(id=1, bundle_json="{}")
        db.session.add(catalog)
    catalog.revision = stripped_bundle.get("revision")
    catalog.bundle_json = json.dumps(stripped_bundle)
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


def _track_ordered_challenge_ids(track: str) -> list:
    """This track's challenges in sequence order, ordered by challenge id --
    matches the order the build_*.py scripts create them in (level 0, 1,
    2, ...). See progression.py for what this feeds into, and
    track_mapping.py for the track->category resolution."""
    category = category_for_track(track)
    if category is None:
        return []
    rows = Challenges.query.filter(Challenges.category == category).order_by(Challenges.id.asc()).all()
    return [row.id for row in rows]


def _resolve_challenge(track: str, entry_name: str):
    """The CTFd Challenges row for this track+entry_name (entry_name is the
    challenge's own `name`, exactly as the Wargames build scripts write it
    into the hint-wallet manifest -- see deploy.sh/build_*.py), or None."""
    category = category_for_track(track)
    if category is None:
        return None
    return (
        Challenges.query.filter(
            Challenges.category == category,
            Challenges.name == entry_name,
        )
        .first()
    )


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

    # Progression-window gate (cei-labs-event#7): a hint can only be opened
    # for a challenge inside this owner's current unlock window for that
    # challenge's track -- see progression.py. A challenge this plugin
    # can't resolve to a real CTFd row is treated as locked rather than
    # falling through to the orchestrator (which would 404 anyway, but
    # failing here keeps the gate fail-closed even for a track this
    # deployment hasn't wired into CTFd's challenge set at all).
    challenge = _resolve_challenge(track, entry_name)
    if challenge is None:
        return jsonify(error="hint_not_found"), 404

    ordered_ids = _track_ordered_challenge_ids(track)
    solved_ids = {
        row.challenge_id
        for row in Solves.query.filter(Solves.challenge_id.in_(ordered_ids)).all()
        if str(row.account_id) == owner_id
    }
    if not is_unlockable(challenge.id, ordered_ids, solved_ids):
        return jsonify(error="progression_locked"), 409
    client = OrchestratorClient.from_env()

    try:
        result = client.unlock(owner_id, track, entry_name, tier)
    except OrchestratorError as exc:
        return jsonify(error=str(exc)), 502

    # unlock() returns success=False with the orchestrator's exact
    # status_code (404 hint_not_found, 409 no_active_catalog) for expected
    # rejections -- relayed as-is so a rejection reaches the player as a
    # real error response, never a 200. There is no "insufficient balance"
    # case anymore (cei-labs-event#7) -- costs are a percent-of-value score
    # reduction applied at solve time, not a spend.
    status_code = result.pop("status_code", 200)
    return jsonify(result), status_code
