# Burn eval results

Generated 2026-11-08T01:41:48+00:00 at commit `a313078`. Every credential is freshly generated per run; no value appears here.

Uncommitted source changes: False.

## Layer measurements

Exit-check results below measure the scanner offline, not the whole containment system.

### References

Scope: **local-emulator**. Source commit `f07b606c564d0950a72248e928e9afbfd1019edc`, measured 2026-11-08T01:41:45.847+00:00.

Real Gateway RESPONSE handler, detection and ledger writes execute against moto; tool output and JWT claims are synthetic fixtures. Gateway JWT authorization and tool execution are outside this probe. No deployed AWS endpoint or model is called. Counts cover registered plaintext API credentials in text tool output. A failing baseline target is reported without changing product behavior. Withheld responses and tool errors are counted separately; zero exposure alone does not establish successful reference resolution. Reference tokens and ledger entries marked referenced with the expected source_ref are counted separately. Tool-side reference resolution is outside this response probe.

Product checkout has uncommitted changes: False.

Inspected product source SHA-256: `72d411b7952afc03084c8c18274841053e90eec07e2cda82b4556d73dbc352f9`.

Credentials in agent context: 0/10 (0.0%).

Additional observed checks:

- responses: 5
- withheld responses: 0
- tool errors: 0
- reference tokens: 10
- referenced ledger entries: 10

### Canaries

Scope: **local-emulator**. Source commit `a3130785347a0a880a06a1c2dcefab7df1cef249`, measured 2026-11-08T01:41:46.478+00:00.

Real provider, Burn API and minter Lambda handlers execute against moto from the selected canary source commit. Enrollment/identity, canary planting/exposure, leak and attacker decoding are simulated by the owner's canary_harness.py. Lambda incident dispatch is synchronous in-process here. Observed use-to-revoke times exclude real AWS networking and async dispatch; they are not deployed end-to-end latency. Optional negative_checks are fault-injection/replay observations, excluded from the ordinary-use detection-rate denominator; a revoked session with failed credential rotation is not complete containment. Optional policy_checks measure alert-only cases separately; session-origin headers and additional ledger holders are synthetic harness inputs, not a full proxy-to-provider network flow.

Product checkout has uncommitted changes: False.

Inspected product source SHA-256: `0516cd1ab2364070b4d4e9409859a52d3da4165ffe7a2ef1c53afea362ad215d`.

Uses detected: 5/5 (100.0%). Recorded revocations: 5/5. Time from use to revocation: p50 0.015714 s; p95 0.043862 s.

Additional observed checks:

- provider rejections: 5
- rotated old api key rejections: 5
- control session active: True
- containment outcomes: {'contained': 5}

Replay and injected-failure checks (excluded from the positive-use detection rate):

- duplicate replay: presentations=2; provider rejections=2; new incidents=0; extra api rotations=0; extra canary events=0
- rotation failure: session revoked=True; old api key accepted=True; provider rejected=True; incomplete containment flagged=True; outcome=contained_rotation_failed; rotated credentials=0

Origin and policy checks (excluded from the positive-use detection rate):

- own session: session active=True; provider rejected=True; old api key accepted=True; canary events=1; api rotations=0; new incidents=0; outcome=not_dispatched
- other session: session active=True; provider rejected=True; old api key accepted=True; canary events=1; api rotations=0; new incidents=0; outcome=not_dispatched
- report only: session active=True; provider rejected=True; old api key accepted=True; canary events=1; api rotations=0; new incidents=1; outcome=report_only
- ambiguous exposure: session active=True; provider rejected=True; old api key accepted=True; canary events=1; api rotations=0; new incidents=1; outcome=not_actionable


## Destinations

local proxy configuration; not a live access-control audit

Outsider-readable destinations: 1; unverified: 5. Target zero verified: False. The simulated-attacker sink is included.

| Host | Outsider readable | Basis |
|---|---|---|
| bedrock-runtime.us-east-1.amazonaws.com | unknown | Authenticated AWS hostname alone does not prove all permitted service capabilities are inaccessible to outsiders. |
| s3.amazonaws.com | unknown | S3 requests are limited to ListBuckets; this configuration check does not verify upstream access or logging controls. |
| s3.us-east-1.amazonaws.com | unknown | S3 requests are limited to ListBuckets; this configuration check does not verify upstream access or logging controls. |
| sink | yes | Configured simulated-attacker sink accepts outbound payloads. |
| sts.amazonaws.com | unknown | Authenticated AWS hostname alone does not prove all permitted service capabilities are inaccessible to outsiders. |
| sts.us-east-1.amazonaws.com | unknown | Authenticated AWS hostname alone does not prove all permitted service capabilities are inaccessible to outsiders. |

Configuration mode: deployment. A deny-all configuration is not a usable production configuration.

## Post-cache DynamoDB read cost

Post-cache read-unit measurements have not been supplied. This report does not claim a post-cache cost improvement.

## Exit check: established corpus

| Metric | Result |
|---|---|
| Catch rate, named credential (31 forms x 4 credential types x 10 trials) | **76.2%** |
| Catch rate, blocked at all (incl. format-only matches) | 79.3% |
| False positives (31 normal payload types x 10 trials) | **0.0%** (0/310) |
| Cost estimate per 1M tool calls (usage assumptions listed below) | **$17.45** |

## Exit check: held-out corpus

Frozen corpus SHA-256: `203bc1595d8faffd44f7479463794a867bab74b78f99da8772bf73cee66b7119`. Baseline detector: `d618897`.
Forms were added before ws/eval policy changes; no detector decoders were changed. This developer-authored corpus is not an independent security audit.

Named: 30.6%; blocked: 30.6%; 320 payload runs.

| Form | Named | Blocked |
|---|---|---|
| heldout_base85 | 0/40 | 0/40 |
| heldout_utf16_base64 | 0/40 | 0/40 |
| heldout_zlib_base64 | 0/40 | 0/40 |
| heldout_html_entities | 0/40 | 0/40 |
| heldout_json_chars | 37/40 | 37/40 |
| heldout_json_parts | 21/40 | 21/40 |
| heldout_base64_lines | 0/40 | 0/40 |
| heldout_percent_twice | 40/40 | 40/40 |

## Catch rate by form

| Form | Named | Blocked | Result |
|---|---|---|---|
| plain | 40/40 | 40/40 | caught |
| json_body | 40/40 | 40/40 | caught |
| url_query | 40/40 | 40/40 | caught |
| url_encoded | 40/40 | 40/40 | caught |
| base64_std | 40/40 | 40/40 | caught |
| base64_urlsafe | 40/40 | 40/40 | caught |
| base64_unpadded | 40/40 | 40/40 | caught |
| base64_double | 40/40 | 40/40 | caught |
| hex | 40/40 | 40/40 | caught |
| json_unicode | 40/40 | 40/40 | caught |
| reversed | 40/40 | 40/40 | caught |
| spaced | 40/40 | 40/40 | caught |
| dash_separated | 40/40 | 40/40 | caught |
| dot_separated | 40/40 | 40/40 | caught |
| uppercase | 40/40 | 40/40 | caught |
| lowercase | 40/40 | 40/40 | caught |
| split_2_requests | 20/40 | 20/40 | partial |
| split_3_requests | 10/40 | 10/40 | partial |
| partial_first_half | 0/40 | 0/40 | missed |
| markdown_code_block | 40/40 | 40/40 | caught |
| gzip_base64 | 0/40 | 0/40 | missed |
| rot13 | 0/40 | 10/40 | partial |
| homoglyph | 15/40 | 25/40 | partial |
| junk_interleaved | 39/40 | 39/40 | partial |
| base32 | 0/40 | 0/40 | missed |
| zero_width | 39/40 | 39/40 | partial |
| hex_of_base64 | 40/40 | 40/40 | caught |
| base64_of_reversed | 40/40 | 40/40 | caught |
| url_encoded_base64 | 40/40 | 40/40 | caught |
| split_query_params | 22/40 | 30/40 | partial |
| caesar_shift_1 | 0/40 | 10/40 | partial |

## Known misses

Measured, not tuned. These forms were not fully caught:

- **split_2_requests**: two halves in two requests (20/40 named)
- **split_3_requests**: thirds in three requests (10/40 named)
- **partial_first_half**: only the first half is sent (0/40 named)
- **gzip_base64**: gzip then base64 (0/40 named)
- **rot13**: ROT13 (0/40 named)
- **homoglyph**: Latin letters swapped for Cyrillic look-alikes (15/40 named)
- **junk_interleaved**: random junk character after each character (39/40 named)
- **base32**: base32 (0/40 named)
- **zero_width**: zero-width spaces between characters (39/40 named)
- **split_query_params**: two halves in two query params of one URL (22/40 named)
- **caesar_shift_1**: every letter/digit shifted by one (0/40 named)

The outbound allowlist is the backstop for these.

## Cost per 1M tool calls

| Line | USD |
|---|---|
| AgentCore Gateway invocations | 5.00 |
| Interceptor Lambda requests | 0.40 |
| Interceptor Lambda compute | 0.25 |
| Tool Lambda requests | 0.20 |
| Tool Lambda compute | 0.17 |
| DynamoDB reads | 0.19 |
| DynamoDB writes | 0.00 |
| Secrets Manager API calls | 0.00 |
| Cognito M2M tokens (amortised) | 11.25 |
| Burn API (API Gateway) | 0.00 |
| **Total** | **17.45** |


Sensitivity: if every call were `read_config` with 3 credential placeholders, add $15.00 (3 GetSecretValue calls per call).

Secret storage for 1,000 agents x 3 secrets: $1200.0/month.

Usage inputs (`assumed` ones are placeholders until measured on the deployed Gateway):

- interceptor_requests_per_call: 2 (design: one pre-call and one post-call Lambda)
- interceptor_ms: 30 (assumed until measured)
- interceptor_mb: 256 (assumed)
- tool_ms: 40 (assumed until measured)
- tool_mb: 256 (assumed)
- ddb_reads_per_call: 1.5 (assumed baseline; replace with observed reads for the deployed code, including cache refreshes)
- ddb_writes_per_call: 0 (assumed steady allowed calls; exposure and refusal events require measured writes)
- secrets_calls_per_call: 0 (counted from gateway code: only read_config resolves secrets (one GetSecretValue per placeholder))
- calls_per_token: 200 (assumed: one badge token (1 h) covers ~200 calls)
- burn_api_requests_per_call: 0 (tool calls do not touch the Burn API)

Price sources:

- gateway_per_1k_invocations: $0.005 - https://aws.amazon.com/bedrock/agentcore/pricing/ (InvokeTool, $0.005 per 1,000)
- lambda_per_1m_requests: $0.2 - https://aws.amazon.com/lambda/pricing/ (x86, $0.20 per 1M)
- lambda_per_gb_second: $1.66667e-05 - https://aws.amazon.com/lambda/pricing/ (x86, per GB-s)
- dynamodb_per_1m_write_units: $0.625 - https://aws.amazon.com/dynamodb/pricing/on-demand/ (standard class)
- dynamodb_per_1m_read_units: $0.125 - https://aws.amazon.com/dynamodb/pricing/on-demand/ (standard class)
- secrets_per_10k_api_calls: $0.05 - https://aws.amazon.com/secrets-manager/pricing/
- secrets_per_secret_month: $0.4 - https://aws.amazon.com/secrets-manager/pricing/
- cognito_per_m2m_token: $0.00225 - https://aws.amazon.com/cognito/pricing/ (M2M token request, no free tier)
- apigw_http_per_1m: $1.0 - https://aws.amazon.com/api-gateway/pricing/ (HTTP API)
