"""Minimal in-memory stand-in for the credential minter (same event shape as the real one)."""
import re

from burn_contracts import names


class FakeMinter:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.throttle = False
        self.rotate_fail = set()  # agent ids whose rotation fails
        self.delay = 0.0  # seconds every awaited call takes (a slow IAM call)
        self.queued = []

    def __call__(self, method, path, body=None, query=None):
        from minter_client import MinterError
        self.calls.append((method, path, body))
        if self.delay:
            import time
            time.sleep(self.delay)
        if self.fail:
            raise MinterError("boom")
        if self.throttle:
            from minter_client import MinterThrottled
            raise MinterThrottled("slow down")
        if method == "DELETE":
            return {"deleted": []}
        if m := re.fullmatch(r"/agents/([^/]+)/credentials", path):
            return {"credentials": [
                {"secret_ref": names.agent_secret_ref(m[1], t), "type": t, "owner": "agent", "version": 1}
                for t in body["types"]]}
        if m := re.fullmatch(r"/agents/([^/]+)/rotate", path):
            if m[1] in self.rotate_fail:
                raise MinterError("rotation failed")
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

    def call_async(self, method, path, body=None):
        """Queued, not awaited: returns at once whatever the minter's speed."""
        self.queued.append((method, path, body))
        self.calls.append((method, path, body))
