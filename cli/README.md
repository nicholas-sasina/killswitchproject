# cli

## What it does
`burn` is a small command-line client for the Burn API. It manages swarms (roles in code, join tokens,
instances, burning an instance, a role, a lineage or a token) and inspects agents: ledger, blast radius and
the event stream. It is the terminal twin of the dashboard. `import burn` is a small SDK for the code it runs.

## How it works
- `argparse` + `urllib` for the API client; PyYAML and jsonschema for `burn.yaml`.
- `BURN_API_URL` and `BURN_DEMO_TOKEN` come from the environment, else from the repo-root `.env` (`make env`
  or `burn login`).
- The token is only sent as a request header in one function (`Api.request`) and is never printed. The one
  secret the CLI prints is a join token, once, from `burn token create`.
- Calls that can fail transiently (429, 5xx, network) retry with exponential backoff and jitter (`burn_cli/retry.py`).
- `--json` prints the raw API response. Exit codes: 0 ok, 1 API or network error, 2 usage or missing config.

## Key files
| File | What |
|---|---|
| `burn_cli/main.py` | Argument parsing and the commands |
| `burn_cli/spec.py` | `burn.yaml`: validation against `burn.schema.json`, the starter template, role mapping |
| `burn_cli/retry.py` | Exponential backoff with jitter |
| `burn_cli/runner/run.py` | `burn run`: session, lifecycle, heartbeat, stop on burn |
| `burn_cli/runner/env.py` | Scrubbing the host environment and building the instance's |
| `burn_cli/runner/credentials.py` | Credential sources: Secrets Manager, and a fake for tests and the mocks |
| `burn_cli/runner/badge.py` | Badge token minting and refresh |
| `burn_cli/runner/mcp_proxy.py` | The local MCP endpoint |
| `burn_cli/runner/spawn_endpoint.py` | The local spawn endpoint |
| `burn/__init__.py` | The SDK: `burn.session`, `burn.spawn` |
| `burn_cli/api.py` | The HTTP client |
| `burn_cli/config.py` | URL and token lookup |
| `burn_cli/render.py` | Plain-text tables and event lines |

### Swarms
```bash
make burn ARGS="login"                      # prompts for the token (hidden), checks it, stores URL + token in .env
make burn ARGS="init"                       # writes a starter burn.yaml
make burn ARGS="apply"                      # validates burn.yaml, then PUT /roles/{name} (managed_by code) for each role
make burn ARGS="pull"                       # writes dashboard-made roles out to burn.yaml
make burn ARGS="token create worker --uses 20 --ttl 3600"   # prints the join token once + a join command
make burn ARGS="ps --role worker --status active"           # instances grouped by role, children indented
make burn ARGS="kill sess_1a2b"             # one instance; or --role worker | --lineage sess_1a2b | --token TOKEN_ID
```
`burn kill` asks for confirmation unless you pass `--yes`. A role created in the dashboard stays editable there
until `burn apply` takes it over; after that the dashboard shows it read-only.

### `burn run`
```bash
BURN_JOIN_TOKEN=bjt_... burn run --role worker -- python agent.py
burn env            # what an instance's environment would hold (values hidden; --show reveals the kept ones)
```
`burn run` starts one instance:
1. `POST /sessions {role, enroll_token}`, retrying 429, 5xx and network errors with exponential backoff and
   jitter (new IAM keys take seconds to work, and 20 parallel spawns hit rate limits).
2. Reads the instance's own badge, AWS key and provider key from Secrets Manager with the host's AWS access
   (`BURN_CREDENTIAL_SOURCE=aws`, the default; `fake` is for the mock servers).
3. Starts the command with the host's credentials **removed** (`AWS_*` except the region and CA bundle,
   `BURN_JOIN_TOKEN`, the API token, and variables named `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_API_KEY`...) and
   the instance's own values **set**: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `BURN_API_KEY`,
   `BURN_SESSION_ID`, `BURN_AGENT_ID`, `BURN_ROLE`, `BURN_MCP_URL`, `BURN_SPAWN_URL`, and `HTTPS_PROXY`/`HTTP_PROXY`
   when `BURN_PROXY_URL` is set (with the agent id and session id as the proxy user and password, which is how
   the egress proxy attributes traffic).
4. `BURN_MCP_URL` is a local endpoint (127.0.0.1, random port) that forwards to `BURN_GATEWAY_URL` and adds
   `Authorization: Bearer <badge token>`. The token comes from the badge's client-credentials token URL and is
   refreshed before it expires. The agent never sees the badge secret or token, and MCP activity triggers heartbeats.
5. `BURN_SPAWN_URL` is a local endpoint: `POST {role, command}` starts a child with `POST /sessions
   {role, parent_session}` and runs it under its own `burn run` (own credentials, own endpoints). It returns
   `{session_id, agent_id}`. Children stop when their parent's `burn run` ends.
6. Heartbeats go to `/instances/{id}/heartbeat`; a 409 (burned or exited) stops the agent (SIGTERM, then SIGKILL
   after 5 s) and `burn run` exits with 3. A normal exit posts `/instances/{id}/exit`. SIGINT and SIGTERM are
   forwarded to the agent.

`HTTPS_PROXY` is a convenience so well-behaved clients use the egress proxy. It is **not containment**: a process
can ignore it. Containment is the network rule in the demo stack, which gives agents no route except the proxy.
**Home directory.** The agent gets a fresh, private `HOME` (mode 0700, owned by the agent's user, removed when the
instance ends) with the XDG directories inside it, so SDKs do not pick up `~/.aws`, `~/.config/gh`, `~/.docker`
or `~/.netrc`. This stops tools from finding those files by accident; it does not stop an agent running as you from
reading them by path (see below).

**Who the agent runs as.** When `burn run` is root (a container's PID 1), the agent runs as `--user` (default `agent`
if it exists, else `nobody`), so it cannot read `burn run`'s environment (`/proc/1/environ`), which holds the API
token and the join token. Child supervisors stay root; only the agent command is dropped. When `burn run` is **not**
root (directly on a laptop), the agent runs as you: it can read your files and `burn run`'s environment. Containment
against a hostile agent then needs a container or a separate user.

`burn run` also needs `BURN_GATEWAY_URL` (written by `make env`) and, optionally, `BURN_PROXY_URL`.

### SDK
```python
import burn

with burn.session("worker") as me:       # joins with BURN_JOIN_TOKEN (or token=...); ends the session on exit
    print(me.session_id, me.agent_id)

burn.spawn("worker", ["python", "task.py"])   # asks the surrounding `burn run` for a child instance
```

### Inspection
```bash
make burn ARGS="agents"
make burn ARGS="kill agent-notifier --reason 'suspicious send' --yes"
make burn ARGS="ledger agent-reader"
make burn ARGS="blast-radius agent-reader"
make burn ARGS="events --follow --agent agent-reader"
```

## How to test it
```bash
cd cli && uv run pytest -q    # runs against a stub HTTP server, no AWS needed
```

## FAQ
- **Does `burn kill` really burn the agent?** Yes. An agent id calls `POST /burn`, the same call as the
  dashboard's Burn button; a session, `--role`, `--lineage` or `--token` calls `POST /burns`. It asks for
  confirmation unless you pass `--yes`.
- **Why does `burn apply` refuse a role?** The API answers 409 when a role is managed elsewhere, for example
  made in the dashboard. Run `burn pull` to bring it into `burn.yaml` first.
- **Does the agent get my AWS credentials?** No. `burn run` removes them from the agent's environment and gives it
  only its own key. `burn run` itself uses your AWS access to read that key.
- **Is `HTTPS_PROXY` a sandbox?** No. See above: it is a convenience, the network rule is the containment.
- **Where can I see a join token again?** Nowhere: only its hash is stored. Create another one.
- **Why is the blast radius empty right after a burn?** CloudTrail can lag 5-15 minutes; the CLI says so when there is nothing yet.
- **Can it print credentials?** No. The API never returns credential values; the CLI shows secret refs and 8-character fingerprint prefixes only.
