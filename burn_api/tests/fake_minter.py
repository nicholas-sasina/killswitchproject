"""Minimal in-memory stand-in for the credential minter (same event shape as the real one)."""
import re

from burn_contracts import names


class FakeMinter:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, method, path, body=None, query=None):
        from minter_client import MinterError
        self.calls.append((method, path, body))
        if self.fail:
            raise MinterError("boom")
        if m := re.fullmatch(r"/agents/([^/]+)/credentials", path):
            return {"credentials": [
                {"secret_ref": names.agent_secret_ref(m[1], t), "type": t, "owner": "agent", "version": 1}
                for t in body["types"]]}
        if m := re.fullmatch(r"/agents/([^/]+)/rotate", path):
            own, other = [], []
            for ref in body["exposed_refs"]:
                (own if names.owner_of(ref) == m[1] else other).append(ref)
            return {"rotated": own, "flagged": other}
        if path.endswith("/key-activity"):
            return {"events": [{"ts": "2026-01-01T00:00:00Z", "key_ref": "burn/agent-a/aws_key#v1",
                                "key_hint": "…ABCD", "event_name": "ListBuckets"},
                               {"ts": "2026-01-01T00:01:00Z", "key_ref": "burn/agent-a/aws_key#v2",
                                "key_hint": "…WXYZ", "event_name": "GetCallerIdentity"}]}
        raise AssertionError(path)
