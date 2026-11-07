# Demo walkthrough

Commands run from the repository root. Live commands mint credentials, make paid service calls,
or change shared state; the integration owner runs them on the shared stack after approval.
Deployment and resets belong to that owner until isolated stages are available.

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

This command runs offline and preserves the checked-in historical report. The generated report
separates established and held-out scanner corpora, references, canaries, destination review,
and post-cache read costs. Missing owner evidence stays unmeasured. Load-test results distinguish
refused, throttled, successful, and other failed calls; an exact throttling quota requires
independent service evidence.

## Reset or change the scenario

The integration owner can reset with `make reset` or start a second scenario with
`make demo SCENARIO=scenarios/billing-ops.yaml`. A reset deletes the scenario's minted credentials.
