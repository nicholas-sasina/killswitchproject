# agents

## What it does
Runs the demo's AI agents: three Strands agents on a fixed graph (reader -> analyst -> notifier)
that work a support queue with real Bedrock model calls, each behind its own badge and credentials.
Scenarios (agents, tickets, files, customers) live in YAML, so a new scenario needs no code change.

## How it works
- One image, one container per agent; `AGENT_ID` plus the scenario file decide everything (tools, instructions, graph).
- The model is Bedrock (`BURN_MODEL_ID`, temperature 0) called with the agent's **own** AWS key, so burning the agent also cuts it off from the model.
- Tools come from the MCP gateway with `Authorization: Bearer <badge token>`; the token is minted once with OAuth client credentials, reused, and refreshed before it expires.
- An agent with `list_tickets` works the ticket queue; the others poll `read_inbox`. Boot and a periodic heartbeat use the agent's own AWS and API keys, which gives CloudTrail history for the blast radius.
- A refused call (`BURN:`), a refused token or a model auth failure logs `badge revoked/credentials rotated - stopping` and the agent stops.

## Swarm workers
`make swarm ROLE=worker N=20` starts N deterministic workers, each running under `burn run` in its own container.
A worker makes no model calls, so a swarm costs nothing: it calls one MCP tool (`read_inbox`
by default) every few seconds through `BURN_MCP_URL`, offset by a hash of its agent id so 20 workers do not all call
at once, and stops when the Gateway refuses its badge. The role must exist (`burn apply`).

`scripts/run_swarm.py` is the launcher. On the host it creates one join token, joins once per container, and starts
each container with `docker run -e NAME`, passing that container's own values (badge, api key, AWS key) from the
launcher's environment: no secret is in a command line or a file, and containers need no AWS access. In the
container `burn run` is PID 1 as root, takes the values out of its environment and starts the worker under a uid of
its own, so a worker can read neither the supervisor's environment nor another worker's. No container ever holds
a person's token: the launcher signs in as the automation person only to create the join token, joins send no auth header, and afterwards each
supervisor authenticates with its instance's own session token, which stays in the supervisor.

Without `MOCK=1` the workers join the demo stack's internal network, whose one way out is the egress proxy, and
the Burn API and Gateway come from `.env` (`make env`); start the stack first. `MOCK=1` runs the same
containers against the mock servers on the host (`host.docker.internal`) and nothing else: it ignores `.env`, uses a
placeholder token and refuses any URL that is not on localhost. Ctrl-C stops only the workers.

## Key files
| File | What |
|---|---|
| `scenarios/*.yaml` | Scenario data, validated against `contracts/scenario.schema.json` |
| `burn_agents/main.py` | Container entry point |
| `burn_agents/badge.py` | Badge token minting, caching, refresh |
| `burn_agents/loop.py` | Ticket and inbox loops, stop-on-revoke |
| `burn_agents/turn.py` | One Strands agent turn, refusal watcher, local tools |
| `burn_agents/cloud.py` | `check_cloud_access`, `check_provider` (use the agent's own keys) |
| `burn_agents/swarm_worker.py` | The deterministic swarm worker (no model calls) |
| `scripts/run_swarm.py`, `scripts/swarm_launch.py` | `make swarm`: join per container, `docker run -e NAME` |
| `Dockerfile.swarm` | `burn run` plus the worker (build context: repository root) |
| `docker-compose.yml` | Proxy, sink and networks (built from `.run/detection-build`, proxy credentials from `.run/proxy.env`); agent services are generated per scenario into `.run/` |
| `scripts/` | `load_scenario.py`, `reset.py`, `run_demo.py`, `mock_badge_issuer.py` (dev) |

## How to test it
```bash
cd agents
uv sync
uv run pytest -q                      # schema, token cache, loop/revoke logic, one agent turn vs a fake MCP server
uv run python scripts/run_demo.py --scenario scenarios/support-desk.yaml          # needs the stacks deployed
uv run python scripts/run_demo.py --scenario scenarios/billing-ops.yaml --step    # presenter mode
```
`run_demo.py` prepares the proxy build context and the proxy's own credentials (no host AWS credentials enter any container), resets demo state, loads the scenario, starts one session per agent, writes
`.run/<agent>.env` (mode 600, gitignored) and starts Docker Compose. In `--step` mode the reader waits;
press Enter to let it take one ticket, `q` to stop. Against `mocks/mcp_tools_mock.py`, run `scripts/mock_badge_issuer.py` as the token URL (client id = agent id, bearer `mock-<agent_id>`) and set `BURN_API_URL`, `BURN_GATEWAY_URL`
and `BURN_PROVIDER_URL` to the mock servers; nothing else changes.

## FAQ
- **How does it know which agent reads tickets?** From the scenario: an agent allowed `list_tickets` works the queue, the others read their inbox. No agent id appears in code.
- **Why can the agents not reach the internet?** They sit only on `agents_internal` (Docker `internal: true`). The proxy is on both networks and is the only way out, so everything they send is checked.
- **What happens to an agent after Burn?** Its badge is refused at the gateway, its AWS key is rotated (so the model call fails) and the loop logs it and stops. Other agents are unaffected because they hold their own copies.
