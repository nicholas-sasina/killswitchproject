# Live testing with real Slack, Telegram, Discord, Teams and SES

Use **private test destinations only**. Every message the tools below send says `[TEST]` and
"SYNTHETIC TEST … not a real security event". They are built from synthetic fixtures, never from a
real incident or credential. Nothing is sent unless you pass `--live` **and** set `BURN_NOTIFY_LIVE=1`.

## 0. Before you start
- Work from `notifications/`, and run `uv sync --all-groups` once.
- Secrets go in environment variables for local tests, or in Secrets Manager when deployed. Never put
  them in a file in the repo. The tools never print secrets, and their reports hold statuses, message
  ids and payload-check results only.
- Keep shell history clean: type `read -rs NAME` and paste the value, which avoids echoing it.

```bash
read -rs BURN_NOTIFY_SLACK_WEBHOOK; export BURN_NOTIFY_SLACK_WEBHOOK
read -rs BURN_NOTIFY_TELEGRAM_BOT_TOKEN; export BURN_NOTIFY_TELEGRAM_BOT_TOKEN
read -rs BURN_NOTIFY_DISCORD_WEBHOOK; export BURN_NOTIFY_DISCORD_WEBHOOK
read -rs BURN_NOTIFY_TEAMS_WORKFLOW_URL; export BURN_NOTIFY_TEAMS_WORKFLOW_URL
```

## 1. Create the private test destinations

### Slack (incoming webhook)
1. In Slack, create a **private** channel, for example `#burn-alerts-test`, with only the testers in it.
2. Go to <https://api.slack.com/apps> → **Create New App** → *From scratch* and pick your workspace.
3. Open **Incoming Webhooks**, switch it on, choose **Add New Webhook to Workspace**, then pick
   `#burn-alerts-test` and allow.
4. Copy the webhook URL (`https://hooks.slack.com/services/T…/B…/…`) into `BURN_NOTIFY_SLACK_WEBHOOK`.
   Anyone holding this URL can post to the channel, so treat it as a secret.
5. Config: `"options": {"private_channel_confirmed": true}`. Set it only after checking the channel
   is private.

What Slack confirms: HTTP 200 with body `ok`, which means Slack accepted the post. An incoming webhook
cannot read messages back, so look at the channel to confirm the message is visible.

### Telegram (Bot API)
1. In Telegram, message **@BotFather** and send `/newbot`. Pick a name and username, then copy the
   token into `BURN_NOTIFY_TELEGRAM_BOT_TOKEN`.
2. Create a **private group**, for example "Burn alerts test", and add the bot. You can also message
   the bot directly for a 1:1 test.
3. Send any message in the group. Then get the chat id without printing the token:
   ```bash
   uv run python - <<'EOF'
   import json, os, urllib.request
   url = "https://api.telegram.org/bot" + os.environ["BURN_NOTIFY_TELEGRAM_BOT_TOKEN"] + "/getUpdates"
   for u in json.load(urllib.request.urlopen(url))["result"]:
       chat = (u.get("message") or u.get("my_chat_member") or {}).get("chat", {})
       if chat: print(chat.get("id"), chat.get("type"), chat.get("title") or chat.get("username"))
   EOF
   ```
   Group ids are negative, for example `-100…`.
4. Config: put the id in both `"recipient"` and `"options": {"allowed_chat_ids": [...]}`.

What Telegram confirms: `ok: true` and a `message_id`, which the report shows as `ref=`. That means
Telegram created the message in that chat.

### Discord (incoming webhook)
1. Create a **private** channel: *Create Channel* → *Private Channel*, for example `#burn-alerts-test`.
2. Channel → **Edit Channel → Integrations → Webhooks → New Webhook**, then **Copy Webhook URL** into
   `BURN_NOTIFY_DISCORD_WEBHOOK`.
3. Config: `"options": {"private_channel_confirmed": true}`.

What Discord confirms: the service calls with `?wait=true`, so Discord returns the created message
id (`ref=`), which is proof the message exists.

### Microsoft Teams (Workflows)
1. In Teams, create a **private** channel, for example "Burn alerts test", in a team you own.
2. In the channel, open **… → Workflows** and pick the template **"Post to a channel when a webhook
   request is received"**. Name it, check the team and channel, then **Add workflow**.
3. Copy the URL it shows (`https://…logic.azure.com/workflows/…` or
   `https://…environment.api.powerplatform.com/powerautomate/automations/direct/workflows/…`) into
   `BURN_NOTIFY_TEAMS_WORKFLOW_URL`.
4. If your tenant restricts who can trigger the flow, keep "Anyone" for this signed-URL test, or test
   from an allowed identity. Old Office 365 connector URLs (`*.webhook.office.com`) are refused.

What Teams confirms: `202 Accepted`, which means the workflow queued the run. To confirm the card was
posted, look at the channel, or check the flow's **Run history** in Power Automate.

### Amazon SES
SES needs AWS access, so it is tested against an account you are allowed to use. It must not be the
shared Burn account without approval.
1. SES console (us-east-1) → **Identities → Create identity**. Verify a sender address, or better, a
   domain.
2. In the **sandbox**, also verify every test recipient address. Each recipient clicks the link AWS
   emails them.
3. Credentials must allow `ses:SendEmail` on the sender identity, plus the recipient identities while
   in the sandbox. Use an AWS profile (`export AWS_PROFILE=…`), not keys in a file.
4. Config: `"recipient": "you@example.com"` and `"options": {"from_address": "burn-alerts@yourdomain"}`.

What SES confirms: a `MessageId` (`ref=`), which means SES accepted the mail. Delivery to the inbox is
confirmed in the mailbox, or through an SES configuration set with event publishing.

## 2. Write the test config (outside the repo)
Copy [`../config.live.example.json`](../config.live.example.json) to somewhere outside the repo, for
example `~/burn-notify-test.json`. Fill in your chat id, addresses and sender, and remove any
destination you have not set up yet. Secrets stay in the environment (`env:` refs).

## 3. Run the checks, in order
```bash
# a. Nothing sent: the same sequence against recording transports
uv run python -m burn_notify live-check --dry-run --config ~/burn-notify-test.json

# b. One message to one destination
BURN_NOTIFY_LIVE=1 uv run python -m burn_notify send --config ~/burn-notify-test.json \
    --destination slack-test --fixture canary_used --live

# c. Full step-4 sequence on every destination in the config, with a controlled failure and retry
BURN_NOTIFY_LIVE=1 uv run python -m burn_notify live-check --config ~/burn-notify-test.json \
    --live --inject-failure --report ~/burn-notify-live-report.json

# d. Burn's real containment code (on emulated AWS) driving real delivery
BURN_NOTIFY_LIVE=1 uv run python demo/backend_demo.py --live --config ~/burn-notify-test.json

# e. pytest form of (b), one alert per destination
BURN_NOTIFY_LIVE=1 BURN_NOTIFY_CONFIG=~/burn-notify-test.json uv run pytest -m live tests/test_live.py
```

`live-check` (c) does the following, and prints per step and per destination the status, attempts,
provider reference and payload check:

| Step | Expectation |
|---|---|
| 1 canary alert (HIGH) | `delivered` on every destination |
| 2 containment update (session revoked) | `delivered` |
| 3 rotation + flag results | recorded; not sent (LOW) |
| 4 containment summary | `delivered`, saying "Rotated where confirmed (1 …)" and "Flagged for review (1 …)" |
| 5 the same canary alert again | `skipped_delivered`, **0 provider requests** |
| 6a new alert, first attempt fails twice (injected before any request is made) | `retrying` |
| 6b SQS-style redelivery | `delivered`, exactly once |

In every row, `payload=pass` means the payload contains the event id, the affected agent, severity,
timestamp, dashboard link and the `[TEST]` label. It also means no sensitive event field (`detail`,
`destination`, `fingerprint`, `secret_ref`, `tool`, `actor`, …) and no destination secret appeared.

**Then check each channel by eye**: one alert, one update, one summary and one retry-probe alert, each
showing `[TEST]`. For each one, confirm the event id matches the report and the link opens
`<dashboard>/#/alerts`. On Discord, confirm nothing pinged anyone. On Teams, confirm identifiers such
as `evt_…` show without backslashes.

## 4. Already verified without credentials
- `BURN_NOTIFY_PROBE=1 uv run pytest -m live tests/test_live_probe.py` sends **negative probes**: real
  requests with made-up, invalid credentials to Slack, Telegram, Discord and Teams Workflows. They
  cannot deliver anything. A provider rejection (`404 no_team`, `401`, `404 Unknown Webhook`,
  `401 WorkflowNotFound`) shows that DNS, the public-address check, pinned TLS with certificate and
  hostname verification, and error classification all work against the real APIs.
- `tests/test_transport_real.py` runs the same production socket code against a local HTTPS server
  that has a throwaway CA. It covers an untrusted certificate, a wrong-host certificate, redirects,
  timeouts, oversized responses, 429 then success, and an outage followed by redelivery.

## 5. After testing
Delete the test webhooks and workflow, or rotate them if they were shared. Revoke the bot token with
`/revoke` in @BotFather if it is no longer needed. Remove test identities from SES. Delete the local
report if you don't need it; it holds no secrets.
