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
`make swarm ROLE=worker N=20` starts N AI workers, each running under `burn run` in its own container. Each worker is
a Strands agent on Claude Haiku 4.5 (Bedrock, temperature 0), built the same way as the scenario agents
(`burn_agents/turn.py`). Its model runs on its instance's own AWS key, never the host's, so a burn also takes its model
away. Its tools are Burn's MCP tools through `BURN_MCP_URL`. Workers start offset by a hash of their agent id so 20 do
not all call at once, and they stop when the Gateway refuses their badge.

**It costs money.** A ticket is one turn of three to five Haiku calls, about 5,000 to 15,000 tokens in and a few
hundred out, roughly one to two cents (Haiku 4.5 is $1 per million input tokens and $5 per million output).
Model calls are capped: 10 per turn, 40 per worker (`--worker-model-calls`), and 400 for the whole run with
replacements included (`MAX_CALLS=`, `--max-model-calls`), about $1.50. Each worker is started with its share
and never makes more calls than that. When it exits, the launcher reads its last line (`model calls used: N of M`)
and adds the unused part back to the run's budget.

The workers handle the **loaded scenario's** tickets, so load one first (`uv run python scripts/load_scenario.py
scenarios/support-desk.yaml`, or `make demo`). Without one, `make swarm` stops before starting anything.
If the role does not exist, the launcher creates it with full access (every tool, `credentials: ["aws_key"]`,
replace on burn). An existing role must give its instances an AWS key (`aws_key`) and allow `list_tickets` and
`read_ticket`. Without `ITEMS` a worker polls `read_inbox` and gives each message one model turn.

`scripts/run_swarm.py` is the launcher. On the host it creates one join token, joins once per container, and starts
each container with `docker run -e NAME`, passing that container's own values (badge, api key, AWS key) from the
launcher's environment: no secret is in a command line or a file, and containers need no AWS access. In the
container `burn run` is PID 1 as root, takes the values out of its environment and starts the worker under a uid of
its own, so a worker can read neither the supervisor's environment nor another worker's. No container ever holds
a person's token: the launcher signs in as the automation person only for its own calls (join tokens, and with a task, reading and replacing a burned worker), joins send no auth header, and afterwards each
supervisor authenticates with its instance's own session token, which stays in the supervisor.

Without `MOCK=1` the workers join the demo stack's internal network, whose one way out is the egress proxy, and
the Burn API and Gateway come from `.env` (`make env`); start the stack first. The proxy's global allowlist already
covers Bedrock (`bedrock-runtime.us-east-1.amazonaws.com`). `MOCK=1` runs the containers against the mock servers on
the host (`host.docker.internal`) and nothing else: it ignores `.env`, uses a placeholder token and refuses any URL
that is not on localhost. The mock servers have no model, so under `MOCK=1` the workers are **scripted**
(`--scripted`): one tool call per item, no model calls and no AI. Ctrl-C stops only the workers.

### Tasks and replace on burn
`make swarm ROLE=worker N=5 ITEMS=10` gives each worker a task: worker 1 gets items `w01-item-01` to `w01-item-10`,
worker 2 `w02-item-01` to `w02-item-10`, and so on. Each item is one model turn on one ticket of the loaded scenario:
item n is ticket n of `list_tickets`, modulo the number of tickets, so a replacement handles an item on the same
ticket. For each item the worker tells `burn run` it started it, runs the turn, tells `burn run` it is done, and it
exits once every item is handled. A turn that does not finish (throttled, cut off at a cap, an error) is retried,
and the item is never reported done without a finished turn. Bedrock throttling is retried by botocore (adaptive,
6 attempts) and then by Strands. `burn run` sends the item ids
to Burn with its heartbeats. `ITEMS=0` (the default) keeps the endless inbox poll.

When the role has replace on burn and Burn burns one worker, its container exits and the launcher replaces it: it
joins one fresh instance (with a new single-use join token), links it to the burned one with
`POST /instances/{old}/replace`, and starts it on the same task. The replacement skips the items finished before
the breach and the ones the burned worker touched after it, which are quarantined for a person, and does the rest.
The launcher logs `replaced <old> with <new>: <n> done, <m> quarantined`. `MOCK=1` runs the same story against the
mock servers: burn a worker with `POST /burns {"scope": "instance", "id": <session_id>}` on the mock API.

## Key files
| File | What |
|---|---|
| `scenarios/*.yaml` | Scenario data, validated against `contracts/scenario.schema.json` |
| `burn_agents/main.py` | Container entry point |
| `burn_agents/badge.py` | Badge token minting, caching, refresh |
| `burn_agents/loop.py` | Ticket and inbox loops, stop-on-revoke |
| `burn_agents/turn.py` | One Strands agent turn, refusal watcher, local tools |
| `burn_agents/cloud.py` | `check_cloud_access`, `check_provider` (use the agent's own keys) |
| `burn_agents/swarm_worker.py` | The AI swarm worker (one model turn per item), and the scripted one for `MOCK=1` |
| `scripts/run_swarm.py`, `scripts/swarm_launch.py` | `make swarm`: join per container, `docker run -e NAME`, tasks, replacing a burned worker |
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
- **Who starts a replacement, Burn or the launcher?** The launcher, because it started the original and it holds the task. Burn only records what was done before the breach and links exactly one replacement, so a compromised worker cannot rewrite its task or start copies of itself.
- **Which exits does the launcher replace?** Only a worker that Burn burned on its own, in a role with replace on burn. A worker that finished its task, a burn of the whole role, and a task already replaced three times (Burn then says it needs a person) are logged and left stopped.
- **What if the replacement cannot start?** If the join token cannot be made, the join fails or Burn refuses the new instance, the launcher logs why and leaves the burned instance pending for a person. It does not retry in a loop. A fresh instance that joined but could not be linked is ended straight away.
- **Can an item be done twice?** Not because of a replacement: the replacement skips every item Burn saw done before the breach, and every item the burned worker was working on or touched after it is quarantined rather than retried.
- **Can two swarms run at once from different checkouts?** Yes. The worker image is tagged `burn-swarm-worker:<hash>`, a hash of what it is built from (`Dockerfile.swarm`, the worker, the CLI and the contracts), and each swarm's containers run its own tag, so one checkout's build never changes another's running swarm. The same code always gives the same tag. Once its workers have started, `make swarm` removes the other `burn-swarm-worker` tags that no container, running or stopped, was created from and that were not built or tagged in the last ten minutes. It never forces a removal, so another checkout's swarm, running or just built, keeps its image. `docker image ls burn-swarm-worker` shows what is left.
