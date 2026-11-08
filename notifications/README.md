# notifications

## What it does
Sends BURN security incidents to private Slack, Telegram, Discord and Microsoft Teams channels and
to email (Amazon SES). It reads the events BURN already writes to `burn-events`. It does not add
event types, does not change how events are written, and never writes to a BURN table. A failed
notification cannot block an agent, the Burn API or containment, and the event stays in the ledger
whatever happens to its notifications.

```
burn-events (DynamoDB stream, NEW_IMAGE)
  └─ EventBridge Pipe (filter: eventName = INSERT)
       └─ SQS burn-notify ──(5 receives)──▶ SQS burn-notify-dlq
            └─ Lambda burn-notify ─▶ classify ─▶ route ─▶ claim ─▶ send ─▶ record
                                          Slack · Telegram · Discord · Teams · SES
EventBridge schedule (1 min) ─▶ Lambda burn-notify-flush ─▶ aggregate summaries, digests,
                                                             containment summaries
DynamoDB burn-notify-state: delivery claims and history, ordering facts, windows (TTL)
```

**Why a Pipe instead of a Lambda on the stream:** the Pipe filters to INSERTs and drops them on SQS
with no code. SQS then provides per-message retries, a visibility timeout we can set from
`Retry-After`, and a DLQ. A Lambda reading the stream directly would block the shard on a slow
provider and retry whole batches. The cost is one more managed resource and its IAM role.

## How it works
| Module | Role |
|---|---|
| `events.py` | Unwraps a stream record, an SQS message or a plain event. Keeps only the contract's fields and validates them against the contract's enums and patterns. Ignores MODIFY and REMOVE. |
| `policy.py` | Event → category, severity and fixed wording. Reads only structured fields. For `source` `burn`/`provider` it also matches the fixed detail *prefixes* Burn writes (`rotation failed:`, `rotation deferred:`, `canary incident not contained:`, the canary-origin templates). The detail text itself is never copied into a message. |
| `incident.py` | The `Incident` DTO, the only thing formatters see. It holds no `detail`, `destination`, `fingerprint`, `secret_ref`, `tool` or `actor`. Incident ids are `inc_` + a hash of the event id, never derived from a fingerprint. |
| `providers/` | One adapter per platform behind a common interface: `build()` (pure, used by tests and the dry run) and `send()`. Errors come back as transient or permanent. |
| `config.py` | Destinations and routing, loaded from a JSON file. There is no API for changing them. |
| `store.py` | Atomic claims and delivery history. `MemoryStore` serves local runs, `DynamoStore` the deployment. |
| `processor.py` | Runs per destination, so one failure stays with its destination. Also handles aggregation, digests, rate limits and containment summaries. |
| `handler.py` | Lambda entry points: SQS batch with `ReportBatchItemFailures`, and the scheduled flush. |
| `http.py` | The outbound HTTPS client. Its SSRF guards are listed under Security. |

### Classification (defaults; configurable via `severity_overrides`)
| Severity | Category | From |
|---|---|---|
| CRITICAL | `containment_failed` | `flagged` "rotation failed: … containment incomplete" |
| HIGH | `containment_partial` | `flagged` "rotation deferred: …" |
| HIGH | `canary_not_contained`, `canary_report_only` | `flagged` "canary incident not contained: …" |
| HIGH | `canary_used` | `canary_used` from outside Burn |
| HIGH | `canary_contained` (containment update) | `revoked` with a canary `secret_ref` |
| HIGH | `flag_reported` | `flagged`, `flag_level` `reported` |
| HIGH | `disclosure_blocked` | `blocked` carrying a known credential |
| MEDIUM | `flag_attempted`, `unregistered_access`, `agent_burned` (update), `stale_key_rejected`, `egress_blocked`, `canary_used_in_burn` | |
| LOW | `flag_seen`, `credential_rotated`, `flag_dismissed`, `access_refused`, `instance_replaced`, `task_resumed` | |
| INFO | `activity` (`recorded`, `issued`, `inherited`, `started`, `exited`) | not sent by default |

An override can never push `containment_failed`, `containment_partial` or `canary_not_contained`
below HIGH. The wording follows the evidence:
- A `canary_used` alert says "Containment: Awaiting verified outcome".
- A blocked request is described as "not evidence that data left".
- A flag at `seen` is "not evidence of compromise".
- A `rotated` event covers one credential only.
- Failed or partial containment is never called successful.

### Canary alert, then containment update
1. **`canary_used`**: *BURN SECURITY ALERT — HIGH*, with Signal: Canary use detected, Containment:
   Awaiting verified outcome, and Credential action: Not yet confirmed.
2. **`revoked`** (canary ref): *BURN CONTAINMENT UPDATE*, with Status: Session revoked and Other
   instances: No broad containment requested. Burn writes `revoked` before the rotation results, so
   at this point it shows what it knows so far, for example "Rotation not yet confirmed".
3. **Containment summary**: about a minute later the flush sends the rotations and flags recorded
   since, for example "Rotated where confirmed (N rotation event(s) so far)" and "Flagged for review".
   If a `rotation failed` event arrived instead, it shows "NOT rotated" and goes out as CRITICAL. It
   never states that other workers are healthy.

### Delivery guarantees
- **Deduplication.** Each (event_id, destination_id) has one delivery item. A worker may send only
  after it wins a conditional claim. `delivered`, `permanently_failed` and `suppressed` are final.
  `sending` carries a 60 s lease, so a worker that crashes is retried once the lease expires.
- **Retries.**
  - A short network error or a `Retry-After` of 2 s or less gets one immediate retry.
  - Anything else marks the destination `retrying` and fails the SQS message. Its visibility is set to
    `Retry-After`, or to full-jitter exponential backoff (5 s base, 300 s cap). On redelivery the
    destinations that already succeeded are skipped.
  - On the 5th receive the remaining destinations are recorded as `permanently_failed:
    retries_exhausted`, and the message goes to the DLQ.
- **Permanent errors are not retried:** 401/403, 404/410, 400/413/422, an invalid token or recipient,
  a retired Teams connector URL, an unsafe URL, a redirect, or a missing secret.
- **Ordering.**
  - SQS standard queues do not keep order.
  - Flag levels only go up: a late `seen` arriving after `reported` is recorded as
    `suppressed: superseded_by_higher_flag_level`.
  - Containment facts are kept as sets, so duplicates and reordering are harmless.
- **Alert fatigue.**
  - Per destination, the first 3 similar events (same category and agent) in a 5-minute window are
    sent one by one. The rest are summarized at the window's end, for example "BURN blocked 47 related
    requests from agent-worker-07 … No successful disclosure has been established by these events."
  - Distinct incidents (containment failures, canary incidents, `reported` flags, containment updates)
    and anything CRITICAL are never aggregated.
  - Optional hourly digest for LOW events (`digest_low`).
  - Per-destination rate limit (`rate_per_minute`); CRITICAL is exempt.
- **Delivery history** records event id, destination, provider, status, attempts, first and last
  attempt, failure category and a safe provider message id. It never holds URLs, tokens, chat ids,
  addresses or provider bodies. Items expire after `delivery_ttl_days`.
- **Residual risk.** If a provider accepts a message and the worker dies before recording it, the
  lease expires and the message can be sent again. Delivery is at least once, never exactly once.

## Configuration
`BURN_NOTIFY_CONFIG=path/to/config.json` (local), or `BURN_NOTIFY_CONFIG_JSON` (set by the stack).
See [`config.example.json`](config.example.json).

| Field | Meaning |
|---|---|
| `id`, `provider` | Unique id; `slack` / `telegram` / `discord` / `teams` / `email` |
| `enabled` | `false` stops all sends to this destination |
| `min_severity` | `info`/`low`/`medium`/`high`/`critical` |
| `categories` / `exclude_categories` | Category allow / deny lists (names from the table above) |
| `roles` / `agents` | Only incidents for these roles / agent ids |
| `recipient` | Telegram chat id, or comma-separated email addresses (max 10) |
| `secret_ref` | `env:NAME` (local only) or `aws-sm:<secret-id>[#json-key]` |
| `digest_low`, `rate_per_minute` | LOW → hourly digest; local rate limit |
| `options` | Provider-specific: see setup below |

Top level: `dashboard_origin` (or `BURN_DASHBOARD_ORIGIN`) must be a bare `https://` origin. Links go
to `<origin>/#/alerts`, the dashboard's existing alerts view, because there is no per-incident route.
`aggregation.window_seconds` / `individual_per_window`, `digest.window_seconds`, `severity_overrides`.

The suggested routing (critical → Slack + Telegram + Teams + email; high → Slack + Telegram +
Discord; medium → Slack + Discord; low → digest) is expressed with `min_severity` per destination.
Nothing is hard-coded.

### Provider setup
- **Slack**: create a Slack app with Incoming Webhooks and add a webhook to a **private** security
  channel. Store the URL as a secret. Set `options.private_channel_confirmed: true`; without it the
  destination is rejected, because the webhook's channel cannot be checked from here. Only
  `hooks.slack.com/services/…` is accepted.
- **Telegram**: create a bot with @BotFather and store its token as a secret. Add the bot to the
  private chat or group and put the numeric chat id in both `recipient` and
  `options.allowed_chat_ids`. The bot only calls `sendMessage` and has no commands. CRITICAL, HIGH and
  MEDIUM notify with sound; LOW is silent.
- **Discord**: Channel → Integrations → Webhooks on a **private** channel. Store the URL as a secret
  and set `options.private_channel_confirmed: true`. Messages are sent with
  `allowed_mentions: {parse: []}`, so `@everyone`, `@here`, users and roles are never pinged.
- **Microsoft Teams (Workflows)**:
  1. In the channel, open "…" → **Workflows** → template **"Post to a channel when a webhook request
     is received"**.
  2. Pick the team and channel and save.
  3. Copy the HTTP POST URL (`*.logic.azure.com/workflows/…` or
     `*.environment.api.powerplatform.com/powerautomate/…`) into a secret.

  Retired Office 365 connector URLs (`*.webhook.office.com`, `outlook.office.com`) are refused. The
  payload is an Adaptive Card 1.4 message. A `202` means the workflow accepted the run, not that the
  card was posted.
- **Email (SES)**: verify the sender identity. `options.from_address` must belong to it. In the SES
  sandbox, every recipient must be verified as well, and SES rejects others with `MessageRejected`,
  which counts as a permanent failure. No SMS is used.
- **WhatsApp (not implemented)**: possible later through the official WhatsApp Business Platform Cloud
  API, using pre-approved message templates sent to opted-in recipients, as one more adapter. It needs
  an approved business account, template approval and recipient opt-in, none of which exist yet. No
  unofficial automation.

Public social networks are not supported, on purpose.

## Run it
```bash
cd notifications
uv run pytest -q                                   # unit + local AWS emulation (moto); no network
uv run --group infra pytest -q tests/test_infra.py # synth the proposed stack
uv run python -m burn_notify dry-run --demo        # canary alert -> containment update -> summary
uv run python -m burn_notify dry-run --fixture blocked --fixture rotation_failed --config config.example.json
```
The dry run prints redacted previews of every provider payload, followed by the local delivery
table. No message leaves the machine.

Live checks against **authorized test channels** are opt-in. They need both switches:
```bash
BURN_NOTIFY_LIVE=1 uv run python -m burn_notify send --config test.json --destination slack-test --live
BURN_NOTIFY_LIVE=1 BURN_NOTIFY_CONFIG=test.json uv run pytest -m live tests/test_live.py
```

## Deploying (needs the infrastructure owner)
Nothing here is deployed. Deploying takes these steps:

1. **Enable the stream on `burn-events`.** This is the only change to an existing resource. In
   `infra/burn_infra/data.py`, `table()` gets a `stream` argument, and `burn-events` is created with
   `stream=ddb.StreamViewType.NEW_IMAGE`. Event writing does not change.
2. **Deploy `BurnNotifications`** from [`infra/notifications_stack.py`](infra/notifications_stack.py),
   passing `-c burn:events_stream_arn=… -c burn:notify_config=… -c burn:notify_ses_identity=…
   -c burn:dashboard_origin=…`. It creates:
   - SQS `burn-notify` (SSE, 180 s visibility, redrive after 5 receives) and `burn-notify-dlq`
     (14 days).
   - EventBridge Pipe `burn-events-to-notify`: stream → queue, INSERT filter, `LATEST`.
   - DynamoDB `burn-notify-state`: `pk` S, TTL `ttl`, sparse GSI `due` (`due` S, `window_end` N).
   - Lambdas `burn-notify` (SQS, batch 10, `ReportBatchItemFailures`) and `burn-notify-flush`
     (1-minute schedule). Both run Python 3.12, 256 MB, 30 s, with reserved concurrency 5.
   - CloudWatch alarms for a non-empty DLQ, worker errors and queue age over 5 minutes. EMF metrics
     go to the `Burn/Notifications` namespace (`Delivered`, `DeliveryFailed`, `DeliverySkipped`,
     `SendLatency`).
3. **IAM, least privilege:**
   - Pipe role: `dynamodb:DescribeStream`, `GetRecords` and `GetShardIterator` on the stream ARN,
     `dynamodb:ListStreams`, and `sqs:SendMessage` on `burn-notify`.
   - Lambda role:
     - read/write on `burn-notify-state` only
     - `secretsmanager:GetSecretValue` on `secret:burn-notify/*` only
     - `ses:SendEmail` on the one identity
     - consume and `ChangeMessageVisibility` on `burn-notify`

   Neither role gets access to any BURN table or to `burn/*` / `burn-system/*` secrets.
4. **Create the secrets** `burn-notify/<destination>` (webhook URLs, bot token). Reference them as
   `aws-sm:burn-notify/slack#webhook_url` and so on. The deployed Lambda refuses `env:` refs
   (`BURN_NOTIFY_ALLOW_ENV_SECRETS=0`).
5. The Lambda needs outbound internet. It runs outside a VPC, or needs a NAT gateway inside one.

## Security
- **Allowlisted fields only.** Messages are built from the `Incident` allowlist.
  `test_no_sensitive_event_field_reaches_any_payload` checks every fixture against every provider.
- **Escaping per channel.** Slack `&<>`, Telegram HTML, Discord markdown plus disabled mentions, Teams
  markdown, and HTML email. Control and bidi characters are stripped and `@` mentions are defused.
- **SSRF guards:**
  - HTTPS on port 443 only, with no userinfo and no IP literals.
  - Hosts and paths must match the provider's allowlist.
  - Every DNS answer must be a public address. The connection is pinned to the vetted IP, with TLS
    verified against the hostname, which defeats DNS rebinding.
  - No redirects; bounded request and response sizes; 5 s timeouts.
- **Logs** go through a redacting filter that removes webhook URLs, bot tokens, AWS keys, JWTs,
  bearer tokens and long hex strings. Errors are logged by class name, never by message or body.
- **No feedback loops and no inbound surface.** The service never writes `burn-events`, has no HTTP
  endpoint, and accepts no commands.

### Known limits
- Delivery is at least once: a crash after the provider accepts a message can duplicate it.
- If the same event is redelivered after a crash, aggregation counts can be slightly high.
- Teams `202` and SES `MessageId` mean the provider accepted the message, not that it was displayed.
- Whether a webhook's channel is private is declared in config, not verified.
- Classifying containment failures depends on the Burn API's fixed detail prefixes. If they change,
  `tests/test_policy.py` and the fixtures must change too. A structured outcome field in the contract
  would remove this coupling.
- The deployed stack has not been exercised yet. It is synth-tested only.
