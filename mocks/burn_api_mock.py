"""Development Burn and leak-test APIs. No AWS or external requests."""

import argparse
import asyncio
import re
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Literal

from burn_contracts.events import utc_now_iso
from burn_contracts.names import MOCK_HEADER, ROLE_NAME_RE, agent_secret_ref
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

if __package__:
    from .burn_state import ENCODINGS, BurnState
    from .minter_mock import key_activity
    from .mock_data import DEFAULT_SCENARIO, load_scenario
    from .swarm_state import Swarm, SwarmError
else:
    from burn_state import ENCODINGS, BurnState
    from minter_mock import key_activity
    from mock_data import DEFAULT_SCENARIO, load_scenario
    from swarm_state import Swarm, SwarmError


class AgentRequest(BaseModel):
    agent_id: str


class BurnRequest(AgentRequest):
    reason: str | None = Field(default=None, max_length=200)


class SessionRequest(BaseModel):
    agent_id: str | None = None
    role: str | None = Field(default=None, pattern=ROLE_NAME_RE.pattern)
    enroll_token: str | None = None
    parent_session: str | None = None


class RoleSpec(BaseModel):
    name: str = Field(pattern=ROLE_NAME_RE.pattern)
    tools: list[str]
    credentials: list[Literal["api_key", "aws_key"]] = []
    may_message: list[str] = []
    may_spawn: list[str] = []
    egress: list[str] | None = None
    owner: str | None = None
    canaries: bool = True
    auto_burn: bool = True
    references: Literal["replace", "report", "off"] = "replace"
    managed_by: Literal["code", "ui"] = "ui"


class BySourceRequest(BaseModel):
    tool: str | None = None
    source_ref: str = Field(max_length=260)
    since: str | None = None
    reason: str | None = Field(default=None, max_length=200)


class FlagResolution(BaseModel):
    secret_ref: str
    action: Literal["rotate", "dismiss"]
    reason: str = Field(min_length=3, max_length=300)
    by: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 ._@-]{1,64}$")


class TokenRequest(BaseModel):
    uses: int = Field(default=1, ge=1, le=10000)
    ttl: int = Field(default=3600, ge=60, le=2_592_000)


class BurnsRequest(BaseModel):
    scope: Literal["instance", "role", "lineage", "token"]
    id: str
    reason: str | None = Field(default=None, max_length=200)


class LeakRequest(AgentRequest):
    secret_ref: str | None = None
    encoding: str = "random"
    free_text: str | None = None


def create_app(scenario_path=DEFAULT_SCENARIO, *, activity_interval=2.0):
    state = BurnState(load_scenario(scenario_path))
    swarm = Swarm(state)

    async def activity():
        while True:
            await asyncio.sleep(activity_interval)
            state.tick()

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(activity()) if activity_interval > 0 else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Burn development mock", lifespan=lifespan)
    app.state.mock = state
    app.state.swarm = swarm
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"],
                       expose_headers=[MOCK_HEADER])

    @app.middleware("http")
    async def mock_header(request, call_next):
        response = await call_next(request)
        response.headers[MOCK_HEADER] = "true"
        return response

    @app.exception_handler(SwarmError)
    async def swarm_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    def require_agent(agent_id):
        if agent_id not in state.agents:
            raise HTTPException(404, "Unknown scenario agent")

    @app.post("/sessions", status_code=201)
    async def session(body: SessionRequest):
        if body.agent_id is None:
            if body.role is None or (body.enroll_token is None) == (body.parent_session is None):
                raise HTTPException(400, "Send agent_id, or role with exactly one of enroll_token or parent_session")
            return swarm.start(body.role, enroll_token=body.enroll_token, parent_session=body.parent_session)
        require_agent(body.agent_id)
        session_id = "session_" + secrets.token_hex(12)
        state.sessions[session_id] = {"session_id": session_id, "agent_id": body.agent_id, "status": "active",
                                      "tools": state.agents[body.agent_id]["tools"], "created_at": utc_now_iso()}
        result = state.minter.handle({"method": "POST", "path": f"/agents/{body.agent_id}/credentials",
                                      "body": {"types": state.agents[body.agent_id]["credentials"]}})["body"]
        state.emit(body.agent_id, "session", "issued", session_id=session_id, detail="Mock session issued.")
        return {"session_id": session_id, "agent_id": body.agent_id,
                "badge_ref": agent_secret_ref(body.agent_id, "badge"), **result}

    @app.post("/burn")
    async def burn(body: BurnRequest):
        require_agent(body.agent_id)
        # Request reasons may contain pasted secrets, so never echo them into the event feed.
        return state.burn(body.agent_id)

    @app.get("/burn/{agent_id}/blast-radius")
    async def blast_radius(agent_id: str):
        require_agent(agent_id)
        return key_activity(agent_id, state.minter.credentials)

    @app.get("/badge/{session_id}")
    async def badge(session_id: str):
        if session_id not in state.sessions:
            raise HTTPException(404, "Unknown session")
        return state.sessions[session_id]

    @app.get("/events")
    async def events(since: str | None = None, limit: int = Query(default=200, ge=1, le=500)):
        ordered = sorted(state.events, key=lambda event: event["event_id"])
        selected = ([event for event in ordered if event["event_id"] > since][:limit]
                    if since is not None else ordered[-limit:])
        return {"events": selected, "agents": [state.agent_state(agent_id) for agent_id in swarm.scenario_ids],
                "cursor": selected[-1]["event_id"] if selected else since}

    @app.get("/agents/{agent_id}/ledger")
    async def ledger(agent_id: str):
        require_agent(agent_id)
        return {"agent_id": agent_id, "entries": list(state.ledgers[agent_id].values())}

    @app.post("/burn/by-source")
    async def burn_by_source(body: BySourceRequest):
        source_ref = body.source_ref if body.tool is None or ":" in body.source_ref else f"{body.tool}:{body.source_ref}"
        result = state.burn_by_source(source_ref, body.since)
        if not result["readers"]:
            raise HTTPException(404, "Nobody read that source in the window")
        return result

    @app.get("/flags")
    async def flags():
        return {"flags": state.flag_list()}

    @app.post("/flags/resolve")
    async def resolve_flag(body: FlagResolution):
        result = state.resolve_flag(body.secret_ref, body.action, body.reason, body.by)
        if result is None:
            raise HTTPException(404, "Not a flagged credential")
        return result

    @app.get("/encodings")
    async def encodings():
        return {"encodings": [*ENCODINGS, "random"]}

    @app.post("/leak-test")
    async def leak_test(body: LeakRequest):
        require_agent(body.agent_id)
        encoding = secrets.choice(ENCODINGS) if body.encoding == "random" else body.encoding
        if encoding not in ENCODINGS:
            raise HTTPException(400, "Unknown mock encoding")
        ref = None
        if body.free_text is not None:
            # Deliberately simple mock rule, not the real detector or a decoding/egress test.
            blocked = "mpk_live_" in body.free_text or bool(re.search(r"AKIA[A-Z0-9]{16}", body.free_text))
        else:
            refs = list(state.ledgers[body.agent_id])
            ref = body.secret_ref or (secrets.choice(refs) if refs else None)
            if ref is None or ref not in refs:
                raise HTTPException(400, "Credential must be in the agent's mock ledger")
            blocked = True
        event = None
        if blocked:
            event = state.emit(body.agent_id, "egress", "blocked", secret_ref=ref,
                               session_id=state.agent_state(body.agent_id)["session_id"],
                               encoding=encoding, destination="webhook.localhost",
                               detail="Mock leak-test rule matched.")
        return {"blocked": blocked, "encoding": encoding, "secret_ref": ref,
                "destination": "webhook.localhost", "requests_sent": 2 if encoding == "split_across_requests" else 1,
                "reason": "Mock rule matched; no network request was sent." if blocked else "Mock rule did not match.",
                "event_id": event["event_id"] if event else None}

    @app.get("/roles")
    async def roles():
        return {"roles": swarm.list_roles()}

    @app.post("/roles", status_code=201)
    async def create_role(body: RoleSpec):
        return swarm.save_role(body.model_dump(), create=True)

    @app.get("/roles/{name}")
    async def get_role(name: str):
        return swarm.get_role(name)

    @app.put("/roles/{name}")
    async def put_role(name: str, body: RoleSpec):
        if body.name != name:
            raise HTTPException(400, "Body name must match the path")
        return swarm.save_role(body.model_dump(), create=False)

    @app.post("/roles/{name}/tokens", status_code=201)
    async def create_token(name: str, body: TokenRequest):
        return swarm.create_token(name, body.uses, body.ttl)

    @app.delete("/tokens/{token_id}")
    async def revoke_token(token_id: str):
        return swarm.revoke_token(token_id)

    @app.get("/instances")
    async def instances(role: str | None = None,
                        status: Literal["active", "revoked", "exited"] | None = None):
        return {"instances": swarm.list_instances(role, status)}

    @app.post("/instances/{session_id}/heartbeat")
    async def heartbeat(session_id: str):
        return swarm.heartbeat(session_id)

    @app.post("/instances/{session_id}/exit")
    async def exit_instance(session_id: str):
        return swarm.exit(session_id)

    @app.post("/burns")
    async def burns(body: BurnsRequest):
        # Like /burn, the reason is never echoed into the event feed.
        return swarm.burn(body.scope, body.id)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--port", default=8700, type=int)
    args = parser.parse_args()
    uvicorn.run(create_app(args.scenario), host="127.0.0.1", port=args.port)
