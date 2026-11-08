# Demo walkthrough

Commands run from the repository root. Live commands mint credentials, make paid service calls
and change the deployed stack's state, so run them only on a stack you operate.

## Start the current scenario

```bash
make demo
make dashboard
```

Run these in separate terminals. Open http://localhost:5173. The current scenario starts
three agents with separate badges and credentials. Agent activity appears in the event feed
and credential references appear in the exposure ledger. A MOCK DATA banner identifies a
mock connection. `make demo-step` advances work one ticket at a time.

## Contain one agent

Select an active agent and confirm Burn. The response lists revoked sessions, rotated owned
credentials, and shared credentials flagged for review. The agent stops when a subsequent
request is refused. Other agents retain their own credentials.

The CLI provides the same operation:

```bash
make burn ARGS="agents"
make burn ARGS="kill <agent-id> --yes"
```

Replace `<agent-id>` with an actual id from the first command.

## Verify old credentials are refused

```bash
make replay-old-key AGENT=<burned-agent-id>
```

The command privately loads the previous credential versions. The mock provider rejects the
old API key; AWS rejects the inactive access key. No credential values should be displayed.

## Inspect outbound containment

The event feed includes blocked tool handoffs and proxy requests when they occur. To exercise
the outbound proxy independently, choose an active agent with a recorded exposure:

```bash
make exit-check-test AGENT=<active-agent-id> ENCODING=base64
```

A recognized credential produces `credential_detected`. A burned session produces
`session_inactive`. A missed encoding is an observed miss, not a successful detection.
The simulated-attacker sink remains an explicitly configured destination for these tests.
S3 accepts only HTTPS `GET /` without a query or body; object reads and writes are refused.

## Inspect recorded AWS activity

```bash
make burn ARGS="blast-radius <burned-agent-id>"
```

The dashboard also provides a blast-radius view. CloudTrail records can arrive several minutes
late; an empty view does not prove that no activity occurred. Start activity at least 15 minutes
before checking this view.

## Inspect measurements

```bash
cd eval
uv run --python 3.12 python run_offline.py --output-dir out/current
```

This command runs offline and preserves the published aggregate. The generated draft report
separates established and held-out scanner corpora, references, canaries, destination review,
and post-cache read costs. Missing owner evidence stays unmeasured. Load-test results distinguish
refused, throttled, successful, and other failed calls; an exact throttling quota requires
independent service evidence.

## Run the attack scenario

`scenarios/support-desk-attack.yaml` is the support desk with one added poisoned ticket
(`T-1099`) and one added file (`config/app.env.bak`). The backup carries the reading session's
canary on a commented, dead-looking line; everything else is the support desk unchanged, so the
end-to-end containment check still passes while this scenario is loaded. The attack is two beats
from that one ticket.

Blocked at the gateway. A model agent that has only Burn's tools reads `T-1099`, reads the
backup, and tries to forward it with `notify` or `send_message`. The backup's canary is a
credential Burn minted, so the gateway's exit check blocks the message and records a `blocked`
egress event that names the canary. The real keys in `config/app.env` are already
`{{burn:ref:...}}` references by the time the agent sees them, so there is nothing usable to send.

Burned from outside. `make attack` is the attacker: it takes whatever leaked out and tries it
against the provider as an outsider (`GET /v1/whoami`, a bearer token, no session proof). It
prints only `refused` or `accepted` and a short fingerprint, never a value, makes no Burn API
call, and takes no shortcut that causes a burn. Once the attacker has the text, it does not
matter how it got out, so there are two equivalent ways to hand it over:

```bash
make attack                 # listen on http://127.0.0.1:8099/audit for a POST
pbpaste | make attack       # the attacker already has the text; try it
make attack FROM=leak.txt   # or from a file
```

The listener is for an agent whose egress Burn does not mediate — for example a laptop session
under `burn run` with no egress proxy — that follows the ticket and POSTs the backup to that URL.
The stdin form is for when you already hold the text: ask a laptop agent to show
`config/app.env.bak` (it is fetched through Burn, so the canary binds to that session), copy it,
and pipe it in. Either way the canary is refused, and presenting it from outside Burn is what
trips Burn's canary path, which burns that one session.

A current Claude model usually declines the injection outright — it recognises the ticket as
social engineering and does not exfiltrate — which is a result worth showing in itself. Burn does
not rely on the model saying no: a weaker model, a jailbreak, or a copy-paste by hand all put the
canary in the attacker's hands, and the burn is the same.

## Reset or change the scenario

Reset with `make reset`, or start a second scenario with
`make demo SCENARIO=scenarios/billing-ops.yaml`. A reset deletes the scenario's minted credentials.
