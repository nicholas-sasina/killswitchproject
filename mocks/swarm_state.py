"""In-memory roles, join tokens, instances and scoped burns for the development Burn API.

Mirrors contracts/README.md "Roles and instances". Scenario agents show up as roles with one
instance (managed_by scenario), so the scenario demo keeps working unchanged.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from burn_contracts.events import utc_now_iso
from burn_contracts.names import (
    SESSION_TOKEN_PREFIX, agent_secret_ref, instance_agent_id, is_reserved_role_name, parse_join_token,
    scenario_role_name,
)

ROLE_FIELDS = ("tools", "credentials", "may_message", "may_spawn", "egress", "owner", "canaries", "auto_burn",
               "references")


class SwarmError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _narrow(mine, parents):
    """A child's list is its role's list, limited to what the parent has (None = no limit)."""
    if mine is None or parents is None:
        return parents if mine is None else mine
    return [item for item in mine if item in parents]


class Swarm:
    def __init__(self, state):
        self.state = state
        self.scenario_ids = list(state.agents)
        self.roles = {}
        self.tokens = {}

    # --- roles -----------------------------------------------------------------------------

    def _scenario_role(self, agent_id):
        agent = self.state.agents[agent_id]
        return {"name": scenario_role_name(agent_id), "tools": list(agent["tools"]),
                "credentials": list(agent["credentials"]),
                "may_message": [scenario_role_name(peer) for peer in agent.get("send_to", [])],
                "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True, "references": "replace",
                "managed_by": "scenario"}

    def _scenario_roles(self):
        roles = {}
        for agent_id in self.scenario_ids:
            try:
                role = self._scenario_role(agent_id)
            except ValueError:  # agent id too long to read as a role name; still works by agent_id
                continue
            roles[role["name"]] = role | {"agent_id": agent_id}
        return roles

    def _view(self, role):
        active = [s for s in self.state.sessions.values()
                  if self._role_of(s) == role["name"] and s["status"] == "active"]
        return {key: value for key, value in role.items() if key != "agent_id"} | {
            "active_instances": len(active)}

    def role(self, name):
        role = self.roles.get(name) or self._scenario_roles().get(name)
        if role is None:
            raise SwarmError(404, "Unknown role")
        return role

    def list_roles(self):
        return [self._view(role) for role in [*self._scenario_roles().values(), *self.roles.values()]]

    def get_role(self, name):
        return self._view(self.role(name))

    def save_role(self, spec: dict, *, create: bool):
        name = spec["name"]
        if is_reserved_role_name(name):
            raise SwarmError(400, "Role names 'eval' and 'eval-*' are reserved for the eval harness")
        if name in self._scenario_roles():
            raise SwarmError(409, "Scenario roles are managed by the scenario file")
        existing = self.roles.get(name)
        if existing and create:
            raise SwarmError(409, "Role exists")
        if existing and existing["managed_by"] != spec["managed_by"]:
            raise SwarmError(409, f"Role is managed by {existing['managed_by']}")
        now = utc_now_iso()
        self.roles[name] = {"name": name, **{key: spec.get(key) for key in ROLE_FIELDS},
                            "managed_by": spec["managed_by"],
                            "created_at": existing["created_at"] if existing else now, "updated_at": now}
        return self._view(self.roles[name])

    # --- join tokens -----------------------------------------------------------------------

    def create_token(self, role_name, uses, ttl):
        self.role(role_name)
        token_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
        self.tokens[token_id] = {
            "token_id": token_id, "role": role_name, "uses": uses, "uses_left": uses,
            "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "expires_at": _iso(datetime.now(timezone.utc) + timedelta(seconds=ttl)),
            "revoked": False, "created_at": utc_now_iso(),
        }
        return self.token_view(token_id) | {"token": f"bjt_{token_id}.{secret}"}

    def token_view(self, token_id):
        row = self.tokens[token_id]
        status = ("revoked" if row["revoked"] else "expired" if row["expires_at"] <= utc_now_iso()
                  else "used_up" if row["uses_left"] <= 0 else "active")
        return {key: row[key] for key in ("token_id", "role", "uses", "uses_left", "expires_at", "created_at")} | {
            "status": status}

    def revoke_token(self, token_id):
        if token_id not in self.tokens:
            raise SwarmError(404, "Unknown token")
        self.tokens[token_id]["revoked"] = True
        return self.token_view(token_id)

    def _redeem(self, role_name, token):
        parsed = parse_join_token(token)
        row = self.tokens.get(parsed[0]) if parsed else None
        if not row or not hmac.compare_digest(hashlib.sha256(parsed[1].encode()).hexdigest(), row["secret_sha256"]):
            raise SwarmError(403, "Invalid join token")
        if row["role"] != role_name:
            raise SwarmError(403, "Join token is for a different role")
        status = self.token_view(row["token_id"])["status"]
        if status != "active":
            raise SwarmError(403, f"Join token {status}")
        row["uses_left"] -= 1
        return row["token_id"]

    # --- instances -------------------------------------------------------------------------

    def _role_of(self, session):
        if session.get("role"):
            return session["role"]
        try:
            return scenario_role_name(session["agent_id"])
        except ValueError:
            return None

    def _credential_types(self, agent_id):
        return [row["type"] for row in self.state.minter.credentials.rows.values()
                if row["owner_agent_id"] == agent_id]

    def start(self, role_name, *, enroll_token=None, parent_session=None):
        role = self.role(role_name)
        if role["managed_by"] == "scenario":
            raise SwarmError(403, "Scenario roles start by agent_id")
        tools, credentials, egress = role["tools"], role["credentials"] or [], role["egress"]
        token_id = None
        if parent_session is not None:
            parent = self.state.sessions.get(parent_session)
            if parent is None:
                raise SwarmError(404, "Unknown parent session")
            if parent["status"] != "active":
                raise SwarmError(403, "Parent session is not active")
            parent_role = self.role(self._role_of(parent))
            if role_name not in (parent_role["may_spawn"] or []):
                raise SwarmError(403, f"Role {parent_role['name']} may not spawn {role_name}")
            tools = _narrow(tools, parent.get("tools", parent_role["tools"]))
            credentials = _narrow(credentials, self._credential_types(parent["agent_id"]))
            egress = _narrow(egress, parent.get("egress"))
        else:
            token_id = self._redeem(role_name, enroll_token)
        agent_id = instance_agent_id(role_name, secrets.token_hex(3))
        self.state.agents[agent_id] = {"id": agent_id, "role": role_name, "tools": tools,
                                       "credentials": credentials, "send_to": []}
        self.state.ledgers[agent_id] = {}
        session_id = "session_" + secrets.token_hex(12)
        now = utc_now_iso()
        self.state.sessions[session_id] = {
            "session_id": session_id, "agent_id": agent_id, "status": "active", "tools": tools,
            "role": role_name, "parent_session": parent_session, "token_id": token_id,
            "owner": role["owner"], "egress": egress, "created_at": now, "last_seen": now, "exited_at": None,
        }
        issued = self.state.minter.credentials.issue(agent_id, credentials)
        fields = {"session_id": session_id, "role": role_name, "parent_session": parent_session}
        self.state.emit(agent_id, "session", "issued", **fields, detail="Mock session issued.")
        self.state.emit(agent_id, "instance", "started", **fields, detail="Mock instance started.")
        return {"session_id": session_id, "agent_id": agent_id, "badge_ref": agent_secret_ref(agent_id, "badge"),
                "credentials": issued, "role": role_name, "parent_session": parent_session,
                "token_id": token_id, "tools": tools, "egress": egress,
                "secrets": self._fake_secrets(session_id, credentials)}

    def _fake_secrets(self, session_id, credentials):
        """Obviously fake values, so nothing here can be mistaken for a real credential."""
        secrets_ = {"badge": {"client_id": "mock-client-" + session_id[-6:], "client_secret": "mock-badge-secret",
                              "token_url": "http://localhost:8700/mock-token", "scope": "burn/tools"},
                    # Well-formed like a real one (clients check the shape); made per join, so never in the source.
                    "session_token": SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)}
        if "api_key" in credentials:
            secrets_["api_key"] = "mock-api-key-not-real"
        if "aws_key" in credentials:
            secrets_["aws_key"] = {"access_key_id": "MOCKACCESSKEYNOTREAL", "secret_access_key": "mock-secret-not-real"}
        return secrets_

    def instance(self, session):
        return {"session_id": session["session_id"], "agent_id": session["agent_id"],
                "role": self._role_of(session), "status": session["status"],
                "tools": session.get("tools", self.state.agents[session["agent_id"]]["tools"]),
                **{key: session.get(key) for key in (
                    "parent_session", "token_id", "owner", "egress", "last_seen", "exited_at")},
                "created_at": session.get("created_at") or utc_now_iso(),
                "exposures": len(self.state.ledgers.get(session["agent_id"], {}))}

    def list_instances(self, role=None, status=None):
        rows = [self.instance(s) for s in self.state.sessions.values()]
        rows = [r for r in rows if r["role"] is not None and (role is None or r["role"] == role)
                and (status is None or r["status"] == status)]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def _session(self, session_id):
        session = self.state.sessions.get(session_id)
        if session is None:
            raise SwarmError(404, "Unknown session")
        return session

    def heartbeat(self, session_id):
        session = self._session(session_id)
        if session["status"] != "active":
            raise SwarmError(409, f"Instance is {session['status']}")
        session["last_seen"] = utc_now_iso()
        return self.instance(session)

    def exit(self, session_id):
        session = self._session(session_id)
        if session["status"] == "active":
            session["status"], session["exited_at"] = "exited", utc_now_iso()
            if session.get("role"):  # a scenario agent keeps its credentials for its next session
                self.state.minter.credentials.delete(session["agent_id"])
            self.state.emit(session["agent_id"], "instance", "exited", session_id=session_id,
                            role=self._role_of(session), parent_session=session.get("parent_session"),
                            detail="Mock instance exited cleanly.")
        return self.instance(session)

    # --- scoped burns ----------------------------------------------------------------------

    def _targets(self, scope, ident):
        sessions = self.state.sessions
        if scope == "instance":
            return [self._session(ident)]
        if scope == "role":
            self.role(ident)
            return [s for s in sessions.values() if self._role_of(s) == ident]
        if scope == "lineage":
            found, frontier = [self._session(ident)], [ident]
            while frontier:
                children = [s for s in sessions.values() if s.get("parent_session") in frontier]
                found += children
                frontier = [s["session_id"] for s in children]
            return found
        if scope == "token":
            self.revoke_token(ident)
            return [s for s in sessions.values() if s.get("token_id") == ident]
        raise SwarmError(400, "Unknown scope")

    def burn(self, scope, ident):
        targets = [s for s in self._targets(scope, ident) if s["status"] == "active"]
        agent_ids = list(dict.fromkeys(s["agent_id"] for s in targets))
        revoked = {}
        for session in targets:  # revoke every badge first, then rotate
            session["status"] = "revoked"
            revoked.setdefault(session["agent_id"], []).append(session["session_id"])
        burned = [self.state.burn(agent_id) | {"sessions_revoked": revoked[agent_id]} for agent_id in agent_ids]
        return {"scope": scope, "id": ident, "burned": burned, "failed": []}
