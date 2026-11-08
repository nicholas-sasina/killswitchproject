# Deploying Burn notifications

Deploying changes two things: it enables a stream on the existing `burn-events` table (in `BurnData`),
then creates the new `BurnNotifications` stack that reads it. Nothing else in Burn changes.

- **Testing a deployment:** pass `-c burn:notify_notice="SYNTHETIC TEST - ..."` and every message is
  labelled `[TEST]`. Redeploy without it to make alerts normal.
- **Email sender:** send from a verified SES **domain** identity with DKIM, plus SPF and DMARC records
  for that domain. Mailbox providers check that the From domain is authenticated. A free-mail sender
  such as a @gmail.com address is not aligned when sent through SES, and may be rejected, filtered or
  dropped.

## What changes on existing resources
Exactly one change, in `infra/burn_infra/data.py` (#120). The synthesized `BurnData` template gains two
things only:

```diff
+ "StreamSpecification": {"StreamViewType": "NEW_IMAGE"}     # on burn-events
+ "Outputs": {"EventsStreamArn": {"Fn::GetAtt": ["burnevents…", "StreamArn"]}}
```

- **No interruption.** CloudFormation updates `StreamSpecification` in place: the table is not
  replaced, and writes and reads continue.
- **Writers unchanged.** No code that writes events changes.
- **No back-pressure.** A stream never slows table writes, and reading it uses no table capacity. If
  notifications break, Burn keeps working.
- **One reader.** The Pipe is the stream's only consumer. AWS recommends at most 2 per shard.

Everything else is a **new** stack, `BurnNotifications`, from `notifications/infra/app.py`. It
deploys on its own and needs no edit to `infra/app.py`.

## Resources and approximate cost
List prices in us-east-1 at the volume of a hackathon or demo (thousands of events per month).
Verify current prices before relying on them.

| Resource | Notes | Approx. monthly |
|---|---|---|
| EventBridge Pipe `burn-events-to-notify` | stream → queue, INSERT filter, DLQ | ~$0 (about $0.40 per million events) |
| SQS `burn-notify` + `burn-notify-dlq` | SSE-SQS, 360 s visibility, 5 receives | ~$0 (first 1M requests free) |
| Lambda `burn-notify`, `burn-notify-flush` | Python 3.12, 256 MB, 60 s; flush every minute (~43k invocations) | ~$0 (free tier) |
| DynamoDB `burn-notify-state` | on demand, TTL | < $0.10 |
| DynamoDB Stream on `burn-events` | read by the Pipe | < $0.10 |
| Secrets Manager `burn-notify/*` | one per webhook or token (up to 4) | $0.40 each → up to $1.60 |
| CloudWatch alarms (4) + SNS topic + logs | permanent failures, DLQ not empty, worker errors, queue age; all notify `burn-notify-alarms` | ~$0.40 + logs |
| SES | $0.10 per 1,000 emails | ~$0 |
| **Total** | | **about $2–3 per month**, mostly secrets and alarms |

## Required IAM
**Deployer** (whoever runs `cdk deploy`): the account's existing CDK bootstrap roles
(`cdk-hnb659fds-*`), the same ones Burn's stacks already use. Plus, for the commands below,
`secretsmanager:CreateSecret`/`PutSecretValue` on `burn-notify/*`, `ses:CreateEmailIdentity`
(once), `dynamodb:DescribeTable` on `burn-events`, and `sqs:SendMessage` on `burn-notify` (for the
verification message).

**Runtime roles, created by the stack.** `tests/test_infra.py` checks them:
- **Pipe:**
  - `dynamodb:DescribeStream`, `GetRecords` and `GetShardIterator` on the `burn-events` stream ARN
  - `dynamodb:ListStreams` (`*`; it has no resource-level permissions)
  - `sqs:SendMessage` on `burn-notify` and `burn-notify-dlq`
- **Lambdas:**
  - read and write on `burn-notify-state` (and its index) only
  - `secretsmanager:GetSecretValue` on `secret:burn-notify/*` only
  - `ses:SendEmail` on the sender identity (plus recipient identities while in the sandbox), with the
    condition `ses:FromAddress` = the configured sender
  - consume, including `ChangeMessageVisibility`, on `burn-notify`
  - CloudWatch Logs

Neither role can read or write any Burn table or touch `burn/*` or `burn-system/*` secrets.

## Required secrets (AWS Secrets Manager, us-east-1)
| Secret | Value (JSON) | Config reference |
|---|---|---|
| `burn-notify/slack` | `{"webhook_url": "https://hooks.slack.com/services/…"}` | `aws-sm:burn-notify/slack#webhook_url` |
| `burn-notify/telegram` | `{"bot_token": "…"}` | `aws-sm:burn-notify/telegram#bot_token` |
| `burn-notify/discord` | `{"webhook_url": "https://discord.com/api/webhooks/…"}` | `aws-sm:burn-notify/discord#webhook_url` |
| `burn-notify/teams` | `{"workflow_url": "https://…logic.azure.com/…"}` | `aws-sm:burn-notify/teams#workflow_url` |

SES needs no secret: the role is allowed to send from the verified identity.

## Environment (set by the stack; listed for reference)
| Variable | Value |
|---|---|
| `BURN_NOTIFY_STATE_TABLE` | `burn-notify-state` |
| `BURN_NOTIFY_CONFIG_JSON` | the deployed config file, minified (must be under 3.5 KB; synth fails otherwise) |
| `BURN_NOTIFY_ALLOW_ENV_SECRETS` | `0`: only `aws-sm:` references work when deployed |
| `BURN_NOTIFY_MAX_RECEIVES` | `5` (equals the queue's `maxReceiveCount`) |
| `BURN_NOTIFY_LEASE_SECONDS` | `90` (longer than the 60 s Lambda timeout) |
| `BURN_NOTIFY_METRICS` | `1` (EMF metrics in `Burn/Notifications`) |
| `BURN_DASHBOARD_ORIGIN` | from `-c burn:dashboard_origin`, a bare `https://` origin |

## Deployment steps
```bash
export AWS_PROFILE=<approved profile> AWS_REGION=us-east-1

# 1. Infra owner: enable the stream (only this diff should appear), then deploy BurnData
cd infra && npx -y aws-cdk@2 diff BurnData        # expect StreamSpecification + EventsStreamArn only
make -C .. deploy-BurnData
STREAM_ARN=$(aws dynamodb describe-table --table-name burn-events --query Table.LatestStreamArn --output text)

# 2. Secrets, read from env vars so they never hit the terminal or shell history
printf '{"webhook_url":"%s"}' "$BURN_NOTIFY_SLACK_WEBHOOK" | \
  aws secretsmanager create-secret --name burn-notify/slack --secret-string file:///dev/stdin
printf '{"bot_token":"%s"}' "$BURN_NOTIFY_TELEGRAM_BOT_TOKEN" | \
  aws secretsmanager create-secret --name burn-notify/telegram --secret-string file:///dev/stdin
printf '{"webhook_url":"%s"}' "$BURN_NOTIFY_DISCORD_WEBHOOK" | \
  aws secretsmanager create-secret --name burn-notify/discord --secret-string file:///dev/stdin
printf '{"workflow_url":"%s"}' "$BURN_NOTIFY_TEAMS_WORKFLOW_URL" | \
  aws secretsmanager create-secret --name burn-notify/teams --secret-string file:///dev/stdin

# 3. SES sender (and sandbox recipients), once
aws sesv2 create-email-identity --email-identity <yourdomain or sender address>

# 4. Deployed config outside the repo: config.example.json with aws-sm: refs
#    (and private_channel_confirmed / allowed_chat_ids / from_address filled in)

# 5. Review, then deploy the notifications stack
cd notifications
CTX="-c burn:events_stream_arn=$STREAM_ARN -c burn:notify_config=$HOME/burn-notify-deployed.json \
     -c burn:notify_ses_identity=<identity> -c burn:dashboard_origin=https://<dashboard host> \
     -c burn:alarm_email=<on-call address>"
# optional while testing: every message is labelled [TEST]
#    -c burn:notify_notice="SYNTHETIC TEST - deployment test; not a real security event"
uv run --group infra npx -y aws-cdk@2 diff   --app "python infra/app.py" BurnNotifications $CTX
uv run --group infra npx -y aws-cdk@2 deploy --app "python infra/app.py" BurnNotifications $CTX
```

## Verification after deploying
```bash
aws pipes describe-pipe --name burn-events-to-notify --query '{state:CurrentState,reason:StateReason}'
aws lambda get-event-source-mapping --uuid "$(aws lambda list-event-source-mappings \
    --function-name burn-notify --query 'EventSourceMappings[0].UUID' --output text)" --query State
aws dynamodb describe-time-to-live --table-name burn-notify-state

# End to end WITHOUT writing to Burn's ledger: a [TEST]-labelled message straight onto the queue
QUEUE=$(aws sqs get-queue-url --queue-name burn-notify --query QueueUrl --output text)
aws sqs send-message --queue-url "$QUEUE" --message-body "$(uv run python -m burn_notify test-message)"
aws logs tail /aws/lambda/burn-notify --since 5m --format short      # Delivered metrics, no secrets
aws dynamodb scan --table-name burn-notify-state --filter-expression "begins_with(pk, :p)" \
    --expression-attribute-values '{":p":{"S":"delivery#"}}' \
    --projection-expression "destination_id, #s, attempts, failure_category" \
    --expression-attribute-names '{"#s":"status"}'
aws sqs get-queue-attributes --queue-url "$(aws sqs get-queue-url --queue-name burn-notify-dlq \
    --query QueueUrl --output text)" --attribute-names ApproximateNumberOfMessages   # expect 0
```

The test message goes straight onto the queue, so it checks the queue → Lambda → providers part only.
**The stream → Pipe → input template part is checked only by a real ledger event**, for example the
team's demo run (`make demo`) or a canary presentation through the deployed mock provider. That writes
a genuine (test-agent) event to the shared `burn-events` ledger, so agree it with the team first. Check its delivery row in
`burn-notify-state`, and confirm the Pipe shows no failures:
`aws pipes describe-pipe --name burn-events-to-notify` and the `burn-notify-dlq` count.

Then confirm in each channel that the `[TEST]` alert arrived. About a minute later, check for its
follow-up summary too if the test fixture was a containment update (`--fixture canary_revoked`).
Real Burn events flow automatically from then on.

## Stop, roll back, tear down
| Goal | Command | Effect on Burn |
|---|---|---|
| Pause all notifications | `aws pipes stop-pipe --name burn-events-to-notify` | none; resume with `start-pipe` (the stream keeps 24 h of records) |
| Pause delivery only | `aws lambda update-event-source-mapping --uuid <uuid> --no-enabled` | none; messages wait in SQS for up to 4 days |
| Disable one destination | set `"enabled": false` in the config and redeploy the stack | none |
| Remove the notifications stack | `uv run --group infra npx -y aws-cdk@2 destroy --app "python infra/app.py" BurnNotifications $CTX` | none; delivery history is deleted |
| Remove the secrets | `aws secretsmanager delete-secret --secret-id burn-notify/<name> --recovery-window-in-days 7` | none |
| Turn the stream off | remove the `stream=` argument for `burn-events` in `infra/burn_infra/data.py` (#120), then `make deploy-BurnData` | none (in-place update); destroy the notifications stack first |

## Operational notes
- **Order:** deploy the stream first, then the notifications stack; for teardown, the reverse.
- **Stream retention:** stream records are kept for 24 hours. A pipe stopped for longer loses the
  records in between. The ledger itself is unaffected.
- **Queue access:** any principal in the account allowed `sqs:SendMessage` on `burn-notify` could
  inject a message that looks like an event. The test envelope is always labelled `[TEST]`, but a raw
  stream-shaped record is not. Keep queue access to the Pipe role and the operators.
- **Data in the queue:** the Pipe's input template forwards only `burn_notify.events.FORWARDED`.
  `fingerprint`, `destination`, `actor` (a person's email), `tool`, `encoding`, `owner_agent_id` and
  `target_agent_id` never reach SQS or the DLQ. `detail` is forwarded, because the containment-failure
  categories are read from its fixed prefixes. It can hold a person's email for a dismissed flag.
- **Alarms:** subscribe an address with `-c burn:alarm_email=…` and confirm the email AWS sends.
  Without a subscription the alarms fire, but nobody is told.
- **Concurrency:** no reserved concurrency is set, because reserving it can fail deploys on accounts
  with the default limit of 10. The SQS mapping caps the worker at 2 concurrent invocations.
