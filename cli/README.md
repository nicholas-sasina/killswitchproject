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
- **Where can I see a join token again?** Nowhere: only its hash is stored. Create another one.
- **Why is the blast radius empty right after a burn?** CloudTrail can lag 5-15 minutes; the CLI says so when there is nothing yet.
- **Can it print credentials?** No. The API never returns credential values; the CLI shows secret refs and 8-character fingerprint prefixes only.
