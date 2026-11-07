"""Planted test credentials, generated fresh on every run.

Nothing here is a fixed value: each call draws new random material and
registers its fingerprints in memory with the same rows the minter writes to
burn-fingerprints (see minter/lambdas/minter/fingerprints.py), so the scanner
sees exactly what it would see in production.
"""
import secrets
import string
from dataclasses import dataclass

from burn_contracts.fingerprint import fingerprint, shingle_fingerprints

ALNUM = string.ascii_letters + string.digits
AWS_SECRET_ALPHABET = ALNUM + "/+"
CONN_TEMPLATE = "postgres://svc:{random}@db.internal.example/crm"


def random_text(length: int, alphabet: str = ALNUM) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class Planted:
    """One credential value an eval payload will try to smuggle out."""
    kind: str        # api_key | aws_key_id | aws_secret | conn_string
    secret_ref: str
    value: str       # the text that gets leaked
    body: str        # the random part (what partial leaks reveal)

    def __repr__(self):  # never let a value reach a log or a failure message
        return f"<Planted {self.kind} {self.secret_ref}>"


class Registry:
    """In-memory stand-in for the burn-fingerprints table."""

    def __init__(self):
        self.key = secrets.token_bytes(32)
        self.rows: dict[str, dict] = {}
        self.lookups = 0

    def lookup(self, fingerprints: set[str]) -> dict[str, dict]:
        self.lookups += 1
        return {fp: self.rows[fp] for fp in fingerprints if fp in self.rows}

    def _register(self, ref, owner, secret_type, parts):
        for part, text, exact, shingles in parts:
            base = {"secret_ref": ref, "owner_agent_id": owner, "secret_type": secret_type,
                    "part": part, "version": 1, "status": "active"}
            if exact:
                fp = fingerprint(text, self.key)
                self.rows[fp] = {**base, "fingerprint": fp, "kind": "exact"}
            if shingles:
                windows = shingle_fingerprints(text, self.key)
                for index, fp in windows:
                    self.rows.setdefault(fp, {**base, "fingerprint": fp, "kind": "shingle",
                                              "index": index, "n_shingles": len(windows)})

    def api_key(self, agent_id: str) -> Planted:
        body = random_text(32)
        value = "mpk_live_" + body
        ref = f"burn/{agent_id}/api_key"
        self._register(ref, agent_id, "api_key", [("value", value, True, False), ("random", body, False, True)])
        return Planted("api_key", ref, value, body)

    def aws_key(self, agent_id: str) -> tuple[Planted, Planted]:
        key_id = "AKIA" + random_text(16, string.ascii_uppercase + string.digits)
        secret = random_text(40, AWS_SECRET_ALPHABET)
        ref = f"burn/{agent_id}/aws_key"
        self._register(ref, agent_id, "aws_key", [("access_key_id", key_id, True, True),
                                                  ("secret_access_key", secret, True, True)])
        return Planted("aws_key_id", ref, key_id, key_id), Planted("aws_secret", ref, secret, secret)

    def conn_string(self, name: str = "crm") -> Planted:
        body = random_text(24)
        value = CONN_TEMPLATE.replace("{random}", body)
        ref = f"burn/shared/{name}"
        self._register(ref, "shared", "other", [("value", value, True, False), ("random", body, True, True)])
        return Planted("conn_string", ref, value, body)

    def fresh_set(self) -> list[Planted]:
        """One of each credential type, owned by a throwaway agent."""
        agent = "agent-eval-" + secrets.token_hex(3)
        key_id, secret = self.aws_key(agent)
        return [self.api_key(agent), key_id, secret, self.conn_string("crm-" + secrets.token_hex(3))]
