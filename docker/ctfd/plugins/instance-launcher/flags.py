"""docker/ctfd/plugins/instance-launcher/flags.py

A CTFd flag type whose correct value varies per team, instead of the one
fixed string every built-in flag type (static/regex) compares against.
Registered into CTFd.plugins.flags.FLAG_CLASSES (a plain module-level dict
CTFd itself exposes -- confirmed by reading CTFd 3.8.2's actual installed
/opt/CTFd/CTFd/plugins/flags/__init__.py) by __init__.py's load(app).

Why this is needed: every wargame level's flag was previously an identical
hardcoded string baked into the shared image at build time, so every team
got the same answer for level N -- a real collusion/leakage risk once
teams compete against each other or challenges get reused across events
(see docs/security-audit-status.md). The orchestrator now generates a
random per-team value per level at instance-creation time (mirroring the
existing VNC_PASSWORD pattern in instance_types.py) and routes.py persists
it into TeamChallengeSecret, keyed by (owner_id, challenge_id). This flag
class's compare() looks up that row instead of a literal string.

A Flags row using this type stores the LEVEL KEY (e.g. "krypton2") in its
`data` column -- not the flag value itself, which never lives in CTFd's
own `flags.content` column at all for this type. `data` is what routes.py
uses to know which key to read out of the orchestrator's `access` dict for
this specific challenge.
"""
import hmac

from CTFd.plugins.flags import BaseFlag
from CTFd.utils.user import get_current_user

from .models import TeamChallengeSecret


class PerTeamDynamicFlag(BaseFlag):
    name = "per_team_dynamic"
    templates = {
        "create": "/plugins/instance-launcher/assets/flags/create.html",
        "update": "/plugins/instance-launcher/assets/flags/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        user = get_current_user()
        if user is None:
            return False

        row = TeamChallengeSecret.query.filter_by(
            owner_id=str(user.account_id),
            challenge_id=chal_key_obj.challenge_id,
        ).first()
        if row is None:
            # Team hasn't launched this challenge yet (or the launch
            # response never surfaced this level's key) -- fail closed
            # rather than falling through to any other comparison.
            return False

        return hmac.compare_digest(row.value.encode(), provided.encode())


def register(app) -> None:
    # Flags.data is a free-form db.Text column (confirmed against CTFd's
    # actual model), so reusing it for the level-key string (rather than
    # adding a new column anywhere) needs no schema changes at all.
    # Asset serving for the templates/ dict above is already covered by
    # __init__.py's existing register_plugin_assets_directory call for
    # this same base_path -- registering it twice would be redundant.
    from CTFd.plugins.flags import FLAG_CLASSES

    FLAG_CLASSES["per_team_dynamic"] = PerTeamDynamicFlag
