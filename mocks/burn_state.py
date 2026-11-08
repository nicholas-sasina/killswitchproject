"""In-memory sessions, exposure ledger, and deliberately simulated activity."""

from burn_contracts.events import make_event, utc_now_iso
from burn_contracts.names import agent_secret_ref

if __package__:
    from .minter_mock import MinterMock
else:
    from minter_mock import MinterMock

ENCODINGS = ["plain", "base64", "hex", "url", "reversed", "spaced", "split_across_requests"]


class BurnState:
    def __init__(self, scenario):
        self.agents = {agent["id"]: agent for agent in scenario["agents"]}
        self.sessions = {}
        self.events = []
        self.ledgers = {agent_id: {} for agent_id in self.agents}
        self.minter = MinterMock()
        for shared in scenario.get("shared_credentials", []):
            self.minter.handle({"method": "POST", "path": "/shared/credentials", "body": shared})
        self.ticks = 0
        self.flags = {}  # secret_ref -> one flag per credential (contracts/README.md)

    def emit(self, agent_id, source, action, **fields):
        event = make_event(agent_id=agent_id, source=source, action=action, **fields)
        self.events.append(event)
        return event

    def agent_state(self, agent_id):
        sessions = [s for s in self.sessions.values() if s["agent_id"] == agent_id]
        active = [s for s in sessions if s["status"] == "active"]
        latest = (active or sessions or [None])[-1]
        return {"agent_id": agent_id, "role": self.agents[agent_id]["role"],
                "status": "active" if active else "revoked" if any(s["status"] == "revoked" for s in sessions)
                else "idle",
                "session_id": latest["session_id"] if latest else None,
                "exposures": len(self.ledgers[agent_id])}

    def record(self, agent_id, ref, *, sender=None, tool=None):
        row = self.minter.credentials.rows[ref]
        source, action = ("handoff", "inherited") if sender else ("tool_response", "recorded")
        previous = self.ledgers[agent_id].get(ref)
        self.ledgers[agent_id][ref] = {
            "secret_ref": ref, "fingerprint": row["fingerprint"], "secret_type": row["type"],
            "owner": row["owner"], "owner_agent_id": row["owner_agent_id"],
            "source": source, "via": sender or tool,
            "first_seen": previous["first_seen"] if previous else utc_now_iso(), "status": "active",
            "flag_level": None,
        }
        self.emit(agent_id, source, action, session_id=self.agent_state(agent_id)["session_id"],
                  secret_ref=ref, fingerprint=row["fingerprint"], secret_type=row["type"],
                  owner=row["owner"], owner_agent_id=row["owner_agent_id"], tool=tool,
                  target_agent_id=agent_id if sender else None,
                  detail="Mock handoff exposure." if sender else "Mock tool response exposure.")

    def tick(self):
        self.ticks += 1
        for agent_id, agent in self.agents.items():
            state = self.agent_state(agent_id)
            if state["status"] == "idle":
                continue
            if state["status"] == "revoked":
                self.emit(agent_id, "gateway", "refused", session_id=state["session_id"],
                          detail="Mock gateway refused a revoked session.")
                continue
            refs = [agent_secret_ref(agent_id, kind) for kind in agent["credentials"]]
            refs += [ref for ref, row in self.minter.credentials.rows.items() if row["owner"] == "shared"]
            for ref in refs:
                self.record(agent_id, ref, tool=next(iter(agent["tools"]), None))
            for recipient in agent.get("send_to", []):
                if self.agent_state(recipient)["status"] == "active":
                    for ref in list(self.ledgers[agent_id]):
                        self.record(recipient, ref, sender=agent_id, tool="send_message")
            if self.ticks % 3 == 0 and refs:
                row = self.minter.credentials.rows[refs[0]]
                self.emit(agent_id, "egress", "blocked", session_id=state["session_id"],
                          secret_ref=refs[0], fingerprint=row["fingerprint"],
                          encoding=ENCODINGS[self.ticks % len(ENCODINGS)],
                          destination="webhook.localhost", detail="Mock outbound credential blocked.")

    def flag(self, ref, agent_id, level="seen"):
        row, now = self.minter.credentials.rows[ref], utc_now_iso()
        flag = self.flags.setdefault(ref, {
            "secret_ref": ref, "secret_type": row["type"], "owner_agent_id": row["owner_agent_id"] or "shared",
            "flag_level": level, "instances": set(), "state": "open", "flagged_at": now, "updated_at": now,
            "evidence": {level: {"source": "burn", "agent_id": agent_id}}, "resolution": None})
        rank = ("seen", "attempted", "reported").index
        if rank(level) > rank(flag["flag_level"]):
            flag["flag_level"], flag["state"] = level, "open"
        flag["evidence"].setdefault(level, {"source": "burn", "agent_id": agent_id})
        flag["instances"].add(agent_id)
        flag["updated_at"] = now

    def _flag_view(self, flag):
        return {key: value for key, value in flag.items() if key != "instances"} | {
            "instance_count": len(flag["instances"])}

    def flag_list(self):
        rank = ("seen", "attempted", "reported").index
        return sorted((self._flag_view(f) for f in self.flags.values()), key=lambda f: -rank(f["flag_level"]))

    def resolve_flag(self, ref, action, reason, by):
        flag = self.flags.get(ref)
        if flag is None:
            return None
        flag["resolution"] = {"action": action, "reason": reason, "by": by, "at": utc_now_iso(),
                              "level": flag["flag_level"]}
        version = None
        if action == "rotate":
            row = self.minter.credentials.rows[ref]
            row["version"] = version = row["version"] + 1
            flag["state"] = "rotated"
            del self.flags[ref]
            self.emit("system", "burn", "rotated", secret_ref=ref, secret_type=row["type"],
                      detail="Mock owner rotation of a flagged credential.")
        else:
            flag["state"] = "dismissed"
            self.emit("system", "burn", "dismissed", secret_ref=ref, flag_level=flag["flag_level"],
                      detail="Mock flag dismissed by its owner.")
        return {"flag": self._flag_view(flag), "rotated_to_version": version}

    def burn(self, agent_id):
        revoked = []
        for session in self.sessions.values():
            if session["agent_id"] == agent_id and session["status"] == "active":
                session["status"] = "revoked"
                revoked.append(session["session_id"])
        self.emit(agent_id, "burn", "revoked", detail="Mock badge revocation.")
        result = self.minter.handle({"method": "POST", "path": f"/agents/{agent_id}/rotate",
                                    "body": {"exposed_refs": list(self.ledgers[agent_id])}})["body"]
        for action in ("rotated", "flagged"):
            for ref in result[action]:
                row = self.minter.credentials.rows[ref]
                self.emit(agent_id, "burn", action, secret_ref=ref,
                          secret_type=row["type"], owner=row["owner"],
                          owner_agent_id=row["owner_agent_id"], detail=f"Mock credential {action}.")
                ledgers = self.ledgers.values() if action == "rotated" else [self.ledgers[agent_id]]
                for ledger in ledgers:
                    if ref in ledger:
                        ledger[ref]["status"] = action
                        if action == "flagged":  # a burn flags what the agent saw at level seen
                            ledger[ref]["flag_level"] = "seen"
                if action == "flagged":
                    self.flag(ref, agent_id)
        return {"agent_id": agent_id, "revoked": True, "sessions_revoked": revoked, **result}
