"""docker/orchestrator/app/crypto.py

AEAD encryption for at-rest credential storage.

store.py persists each instance/range's full plan_json to the
`orchestrator_data` volume (see store.py's module docstring) so a restart
doesn't lose track of live Docker resources -- and that plan_json embeds
every credential the orchestrator ever generates: VNC_PASSWORD /
OPERATOR_PASSWORD (instance_types.plan_range_attacker), and any per-team
flag/answer secret briefly riding the `access` dict before
docker/ctfd/plugins/instance-launcher/routes.py's _persist_and_scrub_secrets
extracts and scrubs it CTFd-side. Before this module existed, all of that
sat in plaintext in a SQLite file on disk with no encryption at rest.

Fernet (AES-128-CBC + HMAC-SHA256, authenticated -- i.e. AEAD-equivalent: it
detects tampering, not just conceals plaintext) is used rather than a
custom cipher: key handling, nonce/IV generation, and MAC verification all
come from `cryptography`'s audited implementation instead of us hand-rolling
it for a P0 security patch.

The key is read from the `credential_encryption_key` Docker secret (see
config.py's _read_secret / docker/secrets.example/), matching this repo's
existing pattern of keeping secret material outside the database and out of
git (docker/secrets/ is gitignored; docker/secrets.example/ documents the
expected placeholder files -- see scripts/install.sh for how a real
deployment generates one). It is never stored in the same SQLite file as the
ciphertext it protects.

`from_key_material()` itself never refuses to produce a usable cipher (see
its docstring) -- that fallback is deliberately kept for local dev/test
convenience. The production entrypoint (app/main.py's create_app(), when
called with no explicit config -- i.e. never in tests) is what fails loudly
via `is_valid_key_material()` instead, matching this codebase's other
secret-gated components (operator/kali-novnc/Dockerfile's /start.sh exits 1
without VNC_PASSWORD/OPERATOR_PASSWORD; POST /wallet/sync 503s without
hint_wallet_sync_secret) rather than letting a misconfigured production
deployment silently run on an ephemeral key.
"""
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

__all__ = ["CredentialCipher", "InvalidToken", "is_valid_key_material"]


def is_valid_key_material(key_material: "str | None") -> bool:
    """True iff `key_material` is non-blank and a validly-formatted Fernet
    key (urlsafe-base64-encoded 32 bytes). Used both by
    `CredentialCipher.from_key_material()` (to decide whether to log the
    "unset" vs. "invalid" warning) and by app/main.py's create_app() (to
    decide whether to refuse to start in production). Never raises."""
    key_material = (key_material or "").strip()
    if not key_material:
        return False
    try:
        Fernet(key_material.encode("utf-8"))
        return True
    except (ValueError, TypeError):
        return False


class CredentialCipher:
    """Thin AEAD wrapper around Fernet, string-in/string-out for JSON blobs."""

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def from_key_material(cls, key_material: "str | None") -> "CredentialCipher":
        """`key_material` is the raw contents of the `credential_encryption_key`
        secret file -- a urlsafe-base64-encoded 32-byte Fernet key, e.g.
        generated with:
            python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

        Falls back to a fresh random in-process key (logged loudly) when the
        secret is unset, blank, or not a validly-formatted Fernet key (e.g.
        a deployment that forgot to replace docker/secrets.example's
        CHANGE_ME placeholder). This means every environment -- including
        tests and a misconfigured deployment -- still gets real encryption
        at rest for whatever the current process persists; it just won't be
        decryptable again after that process restarts. A real deployment
        MUST set this secret for persisted credentials to survive an
        orchestrator restart (the exact scenario -- 'VM password needs to be
        rehydrated after a restart' -- this module exists for).

        This method itself never refuses to run -- app/main.py's
        create_app() is where a production deployment with no valid key
        configured is refused (see is_valid_key_material() above)."""
        key_material = (key_material or "").strip()
        if key_material:
            try:
                return cls(key_material.encode("utf-8"))
            except (ValueError, TypeError):
                logger.warning(
                    "credential_encryption_key is set but is not a valid Fernet key "
                    "(expected urlsafe-base64-encoded 32 bytes) -- falling back to an "
                    "ephemeral in-process key. Persisted credentials will become "
                    "permanently undecryptable after this process restarts. Regenerate "
                    "the secret, e.g.: "
                    'python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
                )
        else:
            logger.warning(
                "credential_encryption_key not configured -- generating an ephemeral "
                "in-process key. Persisted credentials will become permanently "
                "undecryptable after this process restarts. Set the "
                "`credential_encryption_key` Docker secret for production deployments."
            )
        return cls(Fernet.generate_key())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
