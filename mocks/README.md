# mocks

## What it does
Runs local development substitutes for the Burn API, leak-test tool, MCP tools, and credential minter.
All state and freshly generated fake credentials stay in memory; no AWS resources are used.

## How it works
- Loads and validates a scenario YAML, defaulting to `../contracts/examples/minimal-scenario.yaml`.
- Shares contract event/fingerprint helpers and loads MCP names, descriptions, and schemas from JSON.
- Active sessions produce simulated exposure, handoff, and blocked events every two seconds.
- Burning revokes sessions, rotates owned credentials, flags other exposures, and simulates refusal.
- HTTP responses include `x-burn-mock: true`; restarting clears each process's independent state.

## Key files
| File | Purpose |
|---|---|
| `burn_api_mock.py` | All Burn and leak-test HTTP routes on port 8700 |
| `burn_state.py` | Simulated activity, sessions, and exposure ledger |
| `swarm_state.py` | Roles, join tokens, instances, spawning and scoped burns |
| `mcp_tools_mock.py` | Official FastMCP server at `http://localhost:8701/mcp` |
| `minter_mock.py` | `handle(event)` direct-invoke contract and fake key activity |
| `mock_data.py` | Scenario validation, random fake values, and placeholder rendering |
| `tests/` | API, minter, and real HTTP MCP client checks |

## How to test it
Use Python 3.12 and uv. From this folder:

```bash
uv sync
uv run pytest -q
uv run python burn_api_mock.py
```

In another terminal:

```bash
cd mocks
uv run python mcp_tools_mock.py
```

Both HTTP servers accept `--scenario /path/to/scenario.yaml` and `--port` overrides.
Point the dashboard's Burn and leak-test URLs to `http://localhost:8700` and the gateway URL to
`http://localhost:8701/mcp`. The API accepts any `x-burn-demo-token` and allows CORS from
`http://localhost:5173`, exposing the mock header to the browser.

Start sessions with `POST /sessions` and an `agent_id` from the loaded scenario; agents start idle.
For a swarm, create a role (`POST /roles`), a join token (`POST /roles/<name>/tokens`), then start
instances with `POST /sessions {role, enroll_token}` or spawn children with `{role, parent_session}`.
Scenario agents appear in `GET /roles` as read-only roles with one instance.
To try replace on burn, create the role with `"replace_on_burn": true`, send progress on an
instance's heartbeat (`{"progress": [{"item": "w01-item-01", "state": "done"}]}`), burn it with
`POST /burns {"scope": "instance", "id": <session_id>}`, then join a fresh instance and link it with
`POST /instances/<old>/replace {"session_id": <new>}`. `GET /instances/<session_id>` shows the result.
Use `Authorization: Bearer mock-<agent_id>` for MCP calls. Tool and `send_to` permissions come from
the scenario. `POST /mock/revoke/<agent_id>` makes later MCP calls return an MCP error.

Tool results contain one MCP text item holding a JSON object, matching the gateway's response
envelopes. Refusals set `isError` and their text starts with `BURN:`.

The minter is an importable direct-invoke function, not an HTTP server:

```bash
uv run python minter_mock.py
uv run python -c 'from minter_mock import handle; print(handle({"method": "DELETE", "path": "/shared/credentials"}))'
```

Use `MinterMock()` for an isolated store in tests. `GET /agents/<agent_id>/key-activity` returns
fake CloudTrail items; an optional `query.since` timestamp filters those items.

## FAQ
**Does a successful leak test prove containment?** No. Credential mode always reports blocked;
free-text mode only checks the provider prefix or an AWS-key-shaped token. Encodings and request
counts are simulated, no traffic leaves the machine, and the result is explicitly mock data.

**Does an API burn revoke the standalone MCP server?** Each process has separate memory. Use
the MCP revoke endpoint to exercise that server's badge checks. These mocks are development tools.

**Where does the mock's replacement cut-off come from?** The mock has no breach evidence, so it
always cuts off at the burn time (`cutoff_source: burn_time`). Which items count as done or
quarantined is decided by the same shared rules the real API uses (`burn_contracts.progress`).

**Can product code import these helpers?** No. Only development mocks and their tests use this folder;
real components use the contracts and their configured service URLs.
