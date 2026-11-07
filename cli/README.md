# cli

## What it does
`burn` is a small command-line client for the Burn API: list agents, burn one, and inspect its ledger,
blast radius and the event stream. It is the terminal twin of the dashboard.

## How it works
- Python standard library only (`argparse` + `urllib`); it calls the existing Burn API routes and nothing else.
- `BURN_API_URL` and `BURN_DEMO_TOKEN` come from the environment, else from the repo-root `.env` (`make env`).
- The token is only sent as a request header and is never printed.
- `--json` prints the raw API response. Exit codes: 0 ok, 1 API or network error, 2 usage or missing config.

## Key files
| File | What |
|---|---|
| `burn_cli/main.py` | Argument parsing and the commands |
| `burn_cli/api.py` | The HTTP client |
| `burn_cli/config.py` | URL and token lookup |
| `burn_cli/render.py` | Plain-text tables and event lines |

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
- **Does `burn kill` really burn the agent?** Yes, it calls `POST /burn`, the same call as the dashboard's
  Burn button. It asks for confirmation unless you pass `--yes`.
- **Why is the blast radius empty right after a burn?** CloudTrail can lag 5-15 minutes; the CLI says so when there is nothing yet.
- **Can it print credentials?** No. The API never returns credential values; the CLI shows secret refs and 8-character fingerprint prefixes only.
