# cli

## What it does
`burn` is a small command-line client for the Burn API. It manages swarms (roles in code, join tokens,
instances, burning an instance, a role, a lineage or a token) and inspects agents: ledger, blast radius and
the event stream. It is the terminal twin of the dashboard. `import burn` is a small SDK for the code it runs.

## Install
```bash
uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"
burn --help
```
This needs [uv](https://docs.astral.sh/uv/) and no checkout; uv fetches Python 3.12 or later when the machine has none.
To update, run the same command with `--reinstall`. Use uv rather than pip or pipx: `burn` depends on `burn-contracts`
from the same repository, which uv finds through the CLI's `[tool.uv.sources]` and pip and pipx ignore. In a
checkout, `make burn ARGS="..."` runs the same CLI.

## Connect your agent
`burn run` turns any command into a Burn instance: `burn run claude`, `burn run opencode`, `burn run python agent.py`.
It gives the command the instance's own credentials and a local MCP endpoint for its tools, and stops it when the
instance is burned. Any harness that speaks MCP over HTTP works, with any model the instance's own AWS key can call.

1. **Point `burn` at your stack.** Whoever runs it has these values (`make env` writes all but `BURN_PROXY_URL`, the
   egress proxy's address, to the repository's `.env`). The dashboard's join commands set the first two for you:
   ```bash
   export BURN_API_URL=https://...           # the Burn API
   export BURN_GATEWAY_URL=https://.../mcp   # the MCP Gateway your agents' tools come from
   export BURN_PROXY_URL=http://...          # optional: the egress proxy
   export BURN_PEOPLE_ENABLED=on BURN_PEOPLE_DOMAIN=... BURN_PEOPLE_CLIENT_ID=...   # sign-in
   ```
   Without `BURN_PEOPLE_ENABLED=on`, `burn` asks AWS whether the stack requires sign-in. With no AWS access it
   assumes not, and then asks for a `BURN_DEMO_TOKEN` you should not have.
2. **Sign in** with `burn login`.
3. **Run your agent:**
   ```bash
   burn run claude                                                       # asks before tools run
   burn run claude -p "Summarise ticket T-1" --allowedTools mcp__burn    # headless
   burn run python agent.py
   ```
   Everything after the command is the command's own, flags included, so `--` is optional.
   - **The role** is named after the command, or after the script for `python`, `node`, `uv`, `npx` and other
     launchers: `burn run python agent.py` runs as role `agent`. A name that would be `eval` or `eval-*` (kept for
     the eval harness) or a scenario agent's is refused, and so is a command with no usable name, such as
     `python -c ...`. Pass `--role` to choose an existing role instead; one that does not exist is an error, never
     created.
   - **A role it named and cannot find is created with full access:** every tool, the instance's own AWS key,
     and no egress limits of its own.
   - **An existing role is reused as it is.** `burn run` says on stderr whether it is Full access or Limited, so a
     role someone narrowed is never mistaken for full access.
   - **No join token:** as a signed-in person, `burn run` mints a one-use token for the run and joins with it. The
     token is never printed, logged, put on a command line or given to the agent.

### In CI and containers
Where nobody signs in, give `burn run` a join token. A join token always wins over a sign-in.
1. **Make a role**, in the dashboard or in code: `burn init` writes a starter `burn.yaml`, which you edit and
   `burn apply`. A role sets an instance's tools, credentials and egress. For a model on Bedrock, give it an AWS
   key and, behind the egress proxy, the Bedrock endpoint:
   ```yaml
   version: 1
   roles:
     - name: coder
       owner: my-team
       tools: [read_ticket, notify]
       credentials: [api_key, aws_key]
       egress: [bedrock-runtime.us-east-1.amazonaws.com]
   ```
2. **Get a join token**, in the dashboard or with `burn token create coder --uses 1 --ttl 3600`. It is shown once.
3. **Run your agent:**
   ```bash
   read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN    # paste the token; nothing lands in shell history
   burn run --role coder -- <any command>
   ```

### What your agent gets
| Variable | What |
|---|---|
| `BURN_MCP_URL` | Burn's tools: MCP over streamable HTTP, on 127.0.0.1 |
| `BURN_LOCAL_TOKEN` | The bearer the local endpoints require |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | The instance's own AWS key, when the role has `aws_key` |
| `BURN_API_KEY` | The instance's own provider key, when the role has `api_key` |
| `BURN_SPAWN_URL`, `BURN_PROGRESS_URL` | Start a child instance; report task progress (see [`burn run`](#burn-run)) |
| `BURN_SESSION_ID`, `BURN_AGENT_ID`, `BURN_ROLE` | Who it is |
| `BURN_TASK`, `BURN_RESUME` | Its task and where to resume, when a launcher sets them |

Nothing else from your environment reaches it except an allow-list: `PATH`, locale and terminal settings, `TMPDIR`,
`AWS_REGION`/`AWS_DEFAULT_REGION` and the CA-bundle variables, plus what you pass with `--allow-env` (see
[Models](#models)). Its `HOME` is a new, empty directory, apart from a harness preset's files (see Claude Code
below). `burn env` names everything that would be removed.

### Connect your harness
Every harness connects the same way: MCP over streamable HTTP at `$BURN_MCP_URL`, sending
`Authorization: Bearer $BURN_LOCAL_TOKEN`. `burn run` swaps that bearer for the instance's badge token on the way to
the Gateway, so the agent never holds the badge.

**Claude Code**: `burn run` sets it up when the command is `claude`, so there is nothing to configure:
```bash
burn run --role coder -- claude                                  # interactive
burn run --role coder -- claude -p "Summarise ticket T-1"        # one prompt
```
It runs on Bedrock with the instance's own AWS key (see [Models](#models)). In the instance's new `HOME`, `burn run`
writes a `.claude.json` that connects Burn's MCP server, skips onboarding and trusts the current directory, and a
`.claude/settings.json` that allows the `mcp__burn` tools, which the role already limits. The server's entry says
`${BURN_MCP_URL}` and `Bearer ${BURN_LOCAL_TOKEN}`, which Claude Code expands, so no token is written to a file.
Your own `~/.claude` is never read or changed. Because the directory is trusted, a project's own `.claude`
settings, hooks included, run without the trust prompt, so start it only in a directory you trust.

Under another command, such as a wrapper script, no preset applies. Then put the server in a `.mcp.json` in the
directory it runs in (Claude Code expands `${VAR}` in `url` and `headers`), and allow its tools with
`--allowedTools mcp__burn` after the prompt; an interactive session asks you to approve the server on every run.
```json
{
  "mcpServers": {
    "burn": {
      "type": "http",
      "url": "${BURN_MCP_URL}",
      "headers": { "Authorization": "Bearer ${BURN_LOCAL_TOKEN}" }
    }
  }
}
```
The flag takes a list, so it would read a prompt placed after it as a tool name.

**Codex CLI**: `config.toml` does not expand variables, so pass the server on the command line, through a shell
that does:
```bash
burn run --role coder -- sh -c 'exec codex -c mcp_servers.burn.url="$BURN_MCP_URL" -c mcp_servers.burn.bearer_token_env_var=BURN_LOCAL_TOKEN'
```
This gives Codex Burn's tools but not a model: under `burn run` its sign-in and `OPENAI_API_KEY` do not reach it.

**OpenAI Agents SDK** (`pip install "openai-agents[litellm]"`):
```python
import asyncio, os
from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from agents.mcp import MCPServerStreamableHttp

set_tracing_disabled(True)  # traces are uploaded to OpenAI, which needs an OpenAI key


async def main():
    async with MCPServerStreamableHttp(name="burn", client_session_timeout_seconds=30, params={
        "url": os.environ["BURN_MCP_URL"],
        "headers": {"Authorization": f"Bearer {os.environ['BURN_LOCAL_TOKEN']}"},
    }) as burn:  # the default timeout, 5 s, can be short for a call that goes on to the Gateway
        agent = Agent(name="coder", mcp_servers=[burn],
                      model=LitellmModel("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"))
        print((await Runner.run(agent, "Summarise ticket T-1")).final_output)

asyncio.run(main())
```

**LangChain and LangGraph** (`pip install "langchain[mcp]" langchain-aws`). `create_agent` builds a LangGraph
graph, and `langchain[mcp]` installs the MCP client, `langchain.mcp`.
```python
import asyncio, os
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter
from langchain_aws import ChatBedrockConverse


async def main():
    burn = MCPAdapter({"mcpServers": {"burn": {
        "url": os.environ["BURN_MCP_URL"],
        "headers": {"Authorization": f"Bearer {os.environ['BURN_LOCAL_TOKEN']}"},
    }}})
    model = ChatBedrockConverse(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", region_name="us-east-1")
    agent = create_agent(model, await burn.list_tools())
    result = await agent.ainvoke({"messages": "Summarise ticket T-1"})
    print(result["messages"][-1].content)

asyncio.run(main())
```

**Strands Agents** (`pip install strands-agents`):
```python
import os
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

burn = MCPClient(url=os.environ["BURN_MCP_URL"],
                 headers={"Authorization": f"Bearer {os.environ['BURN_LOCAL_TOKEN']}"})
with burn:
    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", region_name="us-east-1")
    Agent(model=model, tools=burn.list_tools_sync())("Summarise ticket T-1")
```

### Models
The instance's own AWS key calls Bedrock, so every harness above can use it, and burning the instance cuts it off
from the model as well as its tools. That key may call only the models the stack allows: this repository's stack
allows Claude Haiku 4.5, `us.anthropic.claude-haiku-4-5-20251001-v1:0`, in us-east-1.

`burn run` sets this up for `claude`: `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION=us-east-1`, and Haiku 4.5 as
`ANTHROPIC_MODEL` and every model alias, so a subagent never asks for a model the key may not call. For other
commands, `burn run` strips the environment, so set non-secret settings inside the command:
```bash
burn run --role coder -- env AWS_REGION_NAME=us-east-1 python agent.py   # LiteLLM, for the Agents SDK
```
Other providers' keys, such as `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`, are removed like any other secret.
`--allow-env NAME` (repeatable) passes one through, and `burn run` warns about each one:
```bash
burn run --role coder --allow-env OPENAI_API_KEY -- python agent.py
# burn: OPENAI_API_KEY passes through to the agent; a burn cannot revoke it
```
That is the trade-off: a burn revokes the instance's own keys, not yours, so an agent that copied your key keeps
it after the burn. Rotate it yourself after an incident. A pass-through wins over a preset's setting of the same
name. Names `burn run` sets itself (`BURN_*`, `AWS_*`, `XDG_*`, `HOME`, `PATH` and the proxy variables) are
refused, and a child started with `burn.spawn` gets none of them. Codex connects to Burn's tools as above, but a
model for it is not covered here.

### Containment
On a laptop `burn run` is not root, so the agent runs as you. It can read your files, your `burn login` session in
`~/.config/burn` and `burn run`'s own environment. The new `HOME` only stops tools finding things by accident.
Real isolation takes two things:
- a container or a separate user: run `burn run` as root, for example as a container's PID 1, and each agent
  runs under a uid of its own (see [who the agent runs as](#burn-run));
- the network rule that gives agents no route out except the egress proxy. `HTTPS_PROXY` alone is a convenience
  that a process can ignore.

## How it works
- `argparse` + `urllib` for the API client; PyYAML and jsonschema for `burn.yaml`.
- `BURN_API_URL` comes from the environment, else from the repo-root `.env` (`make env`).
- People sign in with `burn login`: it opens the sign-in page in your browser (authorization code with PKCE and
  a random state), receives the reply on `http://localhost:8976/callback` and keeps the session in
  `~/.config/burn/credentials.json` (directory 700, file 600, written atomically). `BURN_PEOPLE_DOMAIN` and
  `BURN_PEOPLE_CLIENT_ID` say where to sign in (environment, else `.env`).
- Once the stack requires sign-in (`BURN_PEOPLE_ENABLED=on`, which `make env` writes to `.env`), every
  person-side request carries `Authorization: Bearer <ID token>`, added in one function (`Api.auth_headers`) and
  refreshed when under a minute remains. Until then, the API takes only `BURN_DEMO_TOKEN`, so that is what is sent.
  No token is ever printed; the one secret the CLI prints is a join token, once, from `burn token create`.
- Local development against the mock servers: `BURN_LOCAL_DEV=1` always sends `BURN_DEMO_TOKEN`.
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
| `burn_cli/runner/harness.py` | Presets for well-known harnesses (`claude`): settings and files for the new `HOME` |
| `burn_cli/runner/credentials.py` | The instance's own values, taken from the join reply's `secrets` |
| `burn_cli/runner/badge.py` | Badge token minting and refresh |
| `burn_cli/runner/mcp_proxy.py` | The local MCP endpoint |
| `burn_cli/runner/spawn_endpoint.py` | The local spawn and progress endpoint |
| `burn_cli/runner/progress.py` | Task progress the agent reports, queued for the next heartbeat |
| `burn/__init__.py` | The SDK: `burn.session`, `burn.spawn` |
| `burn_cli/api.py` | The HTTP client |
| `burn_cli/signin.py` | `burn login`/`logout`/`whoami`: browser sign-in, the cached session, refresh |
| `burn_cli/config.py` | URL and token lookup |
| `burn_cli/render.py` | Plain-text tables and event lines |

### Swarms
```bash
make burn ARGS="login"                      # sign in with your browser; prints "Signed in as <email>"
make burn ARGS="whoami"                     # your email, or "not signed in"
make burn ARGS="logout"                     # ends the session on this machine
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
burn run claude                                        # signed in: role "claude", reused or created with full access
read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN     # paste the token; nothing lands in shell history
burn run --role worker -- python agent.py
pbpaste | burn run --role worker --token-stdin -- python agent.py   # or read it from stdin
burn env            # what an instance's environment would hold (values hidden; --show reveals the kept ones)
```
`burn run` starts one instance:
0. Without a join token, as the signed-in person (or with the local-development token): `GET /roles/{name}`; when
   a role named after the command is missing, `POST /roles` with the full-access body (a 409 means another run
   created it first, so it is read again and reused), while a missing `--role` is an error; then
   `POST /roles/{name}/tokens {"uses": 1, "ttl": 300}`. The token stays inside `burn run`.
1. `POST /instances {role, enroll_token}` with no auth header (the join token is the authority), retrying 429, 5xx and network errors with exponential backoff and
   jitter (new IAM keys take seconds to work, and 20 parallel spawns hit rate limits).
2. Takes the instance's own badge, AWS key and provider key from the `secrets` field of that reply, which the API
   returns once, along with the instance's session token (`bst_...`). The session token authenticates the
   supervisor's heartbeat, exit and spawn calls (`Authorization: Bearer ...`) and stays in the supervisor: never in
   the agent's environment or any argv, and `burn run` refuses to pass on anything that looks like one. `burn run`
   uses your sign-in or the local-development token (`BURN_DEMO_TOKEN`) only for step 0, never when it is given a
   join token, and never forwards either. It needs only `BURN_API_URL`. No AWS access is needed on the host. An API that returns no `secrets` is an error ("API too old"),
   not a reason to look elsewhere. A child started with `burn.spawn` gets its values from its parent's reply and
   receives them in its supervisor's exec environment (`BURN_ADOPT_SECRETS`), never on a command line, where every
   user in the container could read them.
3. Starts the command with an **allow-list** of your environment (`PATH`, `LANG`, `LC_*`, `TZ`, `TERM`, `TMPDIR`,
   `AWS_REGION`/`AWS_DEFAULT_REGION` and the CA-bundle variables such as `SSL_CERT_FILE`); everything else is removed,
   including `AWS_*` credentials, `BURN_JOIN_TOKEN`, the API token, database passwords, DSNs and proxy URLs with
   credentials in them. It sets the instance's own values: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`,
   `BURN_API_KEY`, `BURN_SESSION_ID`, `BURN_AGENT_ID`, `BURN_ROLE`, `BURN_MCP_URL`, `BURN_SPAWN_URL`,
   `BURN_PROGRESS_URL`, `BURN_LOCAL_TOKEN`, and `HTTPS_PROXY`/`HTTP_PROXY` when `BURN_PROXY_URL` is set (with the
   agent id and session id as the proxy user and password, which is how the egress proxy attributes traffic).
   `BURN_TASK` (a JSON list of item ids) and `BURN_RESUME` (`{"done": [...], "quarantined": [...]}`) are passed on
   from your environment when they are set, but only if they hold item ids and nothing else; otherwise `burn run`
   refuses to start, before it joins. A child started with `burn.spawn` does not inherit them. `burn env` lists
   every host variable that would be removed, by name.
4. `BURN_MCP_URL` is a local endpoint (127.0.0.1, random port) that forwards to `BURN_GATEWAY_URL` and adds
   `Authorization: Bearer <badge token>`. The token comes from the badge's client-credentials token URL and is
   refreshed before it expires. The agent never sees the badge secret or token, and MCP activity triggers heartbeats.
5. Both local endpoints require `Authorization: Bearer $BURN_LOCAL_TOKEN`, a random value made for each run and
   given only to the agent, because any local process can reach 127.0.0.1. Without it they answer 401. The SDK
   sends it; MCP clients should be configured to send it as their bearer (it is replaced by the badge token on
   the way to the Gateway).
   `BURN_SPAWN_URL` is a local endpoint: `POST {role, command}` starts a child with `POST /instances
   {role, parent_session}` with the parent's session token and runs it under its own `burn run` (own credentials, own endpoints). It returns
   `{session_id, agent_id}`. Children stop when their parent's `burn run` ends.
   `BURN_PROGRESS_URL` is on the same port: `POST {"item": "<id>", "state": "started" | "done"}` reports task
   progress. It is checked with the same rules as the API (item ids match `^[A-Za-z0-9_.:-]{1,64}$`, at most 500
   items per instance); a broken rule is a 400 that names it. A `done` is answered at once, 200 `{"queued": true}`
   (`false` for a repeat that changes nothing), and sent with the next heartbeat. A `started` is answered only
   once Burn has recorded it, because the agent must not act on an item Burn does not know it touched: 200
   `{"queued": ..., "recorded": true}` means go ahead; 409 means the instance was burned; 503 means it was not
   recorded: Burn has not confirmed it within 15 s (it stays queued), or Burn refused the heartbeat that carried
   it, usually over another event (report it again). Only a 200 means go ahead.
6. Heartbeats go to `/instances/{id}/heartbeat`; a 409 (burned or exited) stops the agent (SIGTERM, then SIGKILL
   after 5 s) and `burn run` exits with 3. A normal exit posts `/instances/{id}/exit`. SIGINT and SIGTERM are
   forwarded to the agent. Queued progress goes in the heartbeat body, `{"progress": [...]}`, oldest first and at
   most 50 events per heartbeat. New progress sends a heartbeat at once, but never sooner than the minimum gap
   after the last one (3 s; 1 s while a start waits to be recorded, since the agent is blocked on it). A 400 means a progress rule was broken: that batch is logged and dropped, the agent keeps
   running, and a heartbeat without progress follows at once, because the refused one did not count. A 429 or any
   other transient failure keeps the batch for the next heartbeat. When the egress proxy refuses the heartbeat (a
   403 to its tunnel), it is retried the same way and never taken as a burn, since a misconfigured proxy answers
   alike; but every start waiting on it is answered 503 at once instead of at its deadline. If a batch fails twice in a row, one heartbeat
   goes without it, so the instance is never swept as idle while only its progress fails. Progress still queued
   when the agent exits is sent before the exit is reported, keeping the minimum gap, for at most 10 s. A 409 then
   means burned, and `burn run` exits with 3.

`HTTPS_PROXY` is a convenience so well-behaved clients use the egress proxy. It is **not containment**: a process
can ignore it. Containment is the network rule in the demo stack, which gives agents no route except the proxy.
**Home directory.** The agent gets a fresh, private `HOME` (mode 0700, owned by the agent's user, removed when the
instance ends) with the XDG directories inside it, so SDKs do not pick up `~/.aws`, `~/.config/gh`, `~/.docker`
or `~/.netrc`. This stops tools from finding those files by accident; it does not stop an agent running as you from
reading them by path (see below).

**Who the agent runs as.** When `burn run` is root (a container's PID 1), each instance's agent runs under a numeric
uid of its own from a reserved range (or `--user NAME`, shared by every instance given that name), so it cannot
read `burn run`'s environment (`/proc/1/environ`), which holds the API token and the join token, and one agent
cannot read another's: a child never sees its parent's credentials. Child supervisors stay root; only agent commands
are dropped. When `burn run` is **not** root (directly on a laptop), the agent runs as you: it can read your
files, including your `burn login` sign-in in `~/.config/burn`, and `burn run`'s environment. Containment against a hostile agent then needs a container or a separate user.

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
  only its own key, which it received from the API when the instance joined. `burn run` needs no AWS access.
- **Why does a command say "not signed in: run `burn login`"?** There is no sign-in on this machine, or it
  can no longer be refreshed. Run `burn login` again.
- **Do I need to sign in?** Only once your stack requires it. Until then the commands work as before, with
  `BURN_DEMO_TOKEN` from `.env`. When it is switched on, run `make env` again, then `burn login`. A command that
  gets "HTTP 401" says which of the two to do.
- **Where is my sign-in kept, and who can read it?** In `~/.config/burn/credentials.json`, readable only by you.
  `burn logout` revokes the session and deletes the file.
- **Does `burn run` need me to sign in?** Only when you give it no join token. Then your sign-in reads or creates
  the role and mints a one-use join token, and nothing else. The instance itself authenticates with that token and
  then its own session token. Given a join token, `burn run` never sends your sign-in, even when you are signed in
  on that machine.
- **Can I sign in on a machine without a browser?** `burn login` prints the sign-in address when it cannot open
  a browser. The reply comes back to `localhost:8976`, so open it on the same machine (or forward that port).
- **Is `HTTPS_PROXY` a sandbox?** No. See above: it is a convenience, the network rule is the containment.
- **Why can't my agent use my `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`?** `burn run` passes on only an allow-list of
  your environment, and a provider key is a secret like any other. A key the agent took from your shell would
  outlive a burn; the instance's own AWS key does not. Use Bedrock with that key, or pass yours with
  `--allow-env NAME` and accept that a burn cannot revoke it (see [Models](#models)).
- **What is task progress for?** When an instance of a role with "replace on burn" is burned, its launcher starts
  a clean replacement, which skips the items finished before the breach. Progress is how Burn knows which those
  are: the agent posts item ids to `BURN_PROGRESS_URL` and they reach Burn with the next heartbeat, stamped with
  Burn's own clock. Only item ids are sent, never free text.
- **Why does reporting "started" take a moment?** It returns only once Burn has recorded it, usually within one
  heartbeat. If the instance were burned between the agent starting an item and Burn hearing of it, a replacement
  would do that item again. Waiting closes that gap. Treat anything but a 200 as "stop, do not start this item".
- **Why does `burn run` refuse to start with my `BURN_TASK` or `BURN_RESUME`?** They must hold item ids only:
  `BURN_TASK` a JSON list such as `["item-01", "item-02"]` (at most 500), `BURN_RESUME` exactly
  `{"done": [...], "quarantined": [...]}`. The error names the variable and the rule. An empty value counts as unset.
- **My agent finished its task. Will it be replaced?** No. It exits 0, `burn run` reports the exit, and the instance
  ends as exited. Only a burned instance is ever replaced.
- **Where can I see a join token again?** Nowhere: only its hash is stored. Create another one.
- **Why is the blast radius empty right after a burn?** CloudTrail can lag 5-15 minutes; the CLI says so when there is nothing yet.
- **Can it print credentials?** No. The API never returns credential values; the CLI shows secret refs and 8-character fingerprint prefixes only.
