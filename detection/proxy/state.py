"""Small DynamoDB caches; AWS failures propagate so the proxy can fail closed."""
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic, time

import boto3

from burn_contracts import names
from burn_contracts.events import make_event
from burn_contracts.fingerprint import key_from_hex


class AwsState:
    def __init__(self):
        self.ddb = boto3.resource("dynamodb", region_name=names.REGION)
        self.secrets = boto3.client("secretsmanager", region_name=names.REGION)
        self.sessions = self.ddb.Table(names.SESSIONS_TABLE)
        self.fingerprints = self.ddb.Table(names.FINGERPRINTS_TABLE)
        self.events = self.ddb.Table(names.EVENTS_TABLE)
        self._rows = {}
        self._refreshed = float("-inf")
        self._sessions = OrderedDict()

    def key(self) -> bytes:
        result = self.secrets.get_secret_value(SecretId=names.FINGERPRINT_KEY_SECRET)
        return key_from_hex(result["SecretString"])

    def lookup(self, fingerprints: set[str]) -> dict[str, dict]:
        if monotonic() - self._refreshed >= 2:
            rows, page = {}, {}
            while True:
                response = self.fingerprints.scan(**page, ConsistentRead=True)
                rows.update((row["fingerprint"], row) for row in response.get("Items", []))
                if not response.get("LastEvaluatedKey"):
                    break
                page = {"ExclusiveStartKey": response["LastEvaluatedKey"]}
            self._rows = rows
            self._refreshed = monotonic()
        return {fp: self._rows[fp] for fp in fingerprints if fp in self._rows}

    def session(self, agent_id: str, session_id: str) -> dict | None:
        now = monotonic()
        cached = self._sessions.get(session_id)
        if cached and now - cached[0] < 1:
            row = cached[1]
        else:
            row = self.sessions.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
            self._sessions[session_id] = (now, row)
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > 2048:
                self._sessions.popitem(last=False)
        if not row or row.get("agent_id") != agent_id or row.get("status") != "active":
            return None
        expires = row.get("expires_at")
        if expires is not None:
            if isinstance(expires, (int, float, Decimal)):
                if expires <= time():
                    return None
            elif isinstance(expires, str):
                try:
                    if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                        return None
                except (ValueError, TypeError):
                    return None
        return row

    def emit(self, *, agent_id: str, action: str, **fields) -> str:
        event = make_event(agent_id=agent_id, source="egress", action=action, **fields)
        self.events.put_item(Item={"stream": names.EVENTS_STREAM, **event})
        return event["event_id"]
