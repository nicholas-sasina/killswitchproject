# Burn eval results

Generated 2026-11-07T21:59:53+00:00 at commit `2c1d57c`. Every credential is freshly generated per run; no value appears here.

| Metric | Result |
|---|---|
| Catch rate, named credential (31 forms x 4 credential types x 10 trials) | **76.2%** |
| Catch rate, blocked at all (incl. format-only matches) | 79.3% |
| False positives (31 normal payload types x 10 trials) | **0.0%** (0/310) |
| Cost per 1M tool calls, small tool outputs (measured) | **$19.23**; about $109.94 with 1 KiB outputs (see sensitivity) |
| Tool call through Gateway, client round trip | p50 232.2 ms, p95 325.8 ms (200 calls) |
| Interceptor Lambda per invocation (2 per call) | p50 14.1 ms, p95 23.1 ms; about 12% of the median call |
| burn_detect scan, 1 KiB / 16 KiB prose | p50 0.56 / 6.38 ms; random hex 1 KiB / 16 KiB: 4.25 / 63.46 ms |
| Egress proxy overhead, 1 KiB / 16 KiB POST (local) | +13.6 / +20.1 ms p50, +57.5 / +31.7 ms p95 |
| Load test: 50 agents x 5 calls, 50 real badges | 61.8 calls/s, p50 253.1 ms, p95 360.5 ms, errors 0 (HTTP none) |
| Load test with 3 sessions revoked mid-run: 40 agents x 5 calls, 40 real badges | 26.5 calls/s, p50 239.3 ms, p95 374.8 ms, errors 0 (HTTP none); after revoke: 15/15 calls from revoked agents refused, 185/185 from other agents succeeded |
| Load test, simulated agents sharing badges, 10 badges revoked mid-run: 1000 agents x 5 calls, 100 real badges | 119.8 calls/s, p50 226.5 ms, p95 810.1 ms, errors 145 (HTTP 429); after revoke: 262/281 calls from revoked agents refused, 2238/2364 from other agents succeeded |

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
| homoglyph | 17/40 | 26/40 | partial |
| junk_interleaved | 38/40 | 38/40 | partial |
| base32 | 0/40 | 0/40 | missed |
| zero_width | 38/40 | 38/40 | partial |
| hex_of_base64 | 40/40 | 40/40 | caught |
| base64_of_reversed | 40/40 | 40/40 | caught |
| url_encoded_base64 | 40/40 | 40/40 | caught |
| split_query_params | 22/40 | 31/40 | partial |
| caesar_shift_1 | 0/40 | 10/40 | partial |

## Known misses

Measured, not tuned. These forms were not fully caught:

- **split_2_requests**: two halves in two requests (20/40 named)
- **split_3_requests**: thirds in three requests (10/40 named)
- **partial_first_half**: only the first half is sent (0/40 named)
- **gzip_base64**: gzip then base64 (0/40 named)
- **rot13**: ROT13 (0/40 named)
- **homoglyph**: Latin letters swapped for Cyrillic look-alikes (17/40 named)
- **junk_interleaved**: random junk character after each character (38/40 named)
- **base32**: base32 (0/40 named)
- **zero_width**: zero-width spaces between characters (38/40 named)
- **split_query_params**: two halves in two query params of one URL (22/40 named)
- **caesar_shift_1**: every letter/digit shifted by one (0/40 named)

The outbound allowlist is the backstop for these.

## Cost per 1M tool calls

| Line | USD |
|---|---|
| AgentCore Gateway invocations | 5.00 |
| Interceptor Lambda requests | 0.40 |
| Interceptor Lambda compute | 0.27 |
| Tool Lambda requests | 0.20 |
| Tool Lambda compute | 0.07 |
| DynamoDB reads | 1.99 |
| DynamoDB writes | 0.04 |
| Secrets Manager API calls | 0.00 |
| Cognito M2M tokens (amortised) | 11.25 |
| Burn API (API Gateway) | 0.00 |
| **Total** | **19.23** |


DynamoDB reads grow with tool output size, because the response step looks up every candidate fingerprint (0.5 read unit per key, found or not). Measured: 15.92 read units per call for a tiny output. Larger outputs, from the scanner's candidate count:

| Output | Fingerprints requested | Read units per call | DynamoDB reads per 1M calls |
|---|---|---|---|
| config_file_1024 | 1478 | 741.6 | $92.70 |
| random_hex_1024 | 2036 | 1020.6 | $127.58 |
| config_file_4096 | 5107 | 2556.1 | $319.51 |
| random_hex_4096 | 8180 | 4092.6 | $511.57 |
| config_file_16384 | 18386 | 9195.6 | $1,149.45 |
| random_hex_16384 | 32754 | 16379.6 | $2,047.45 |

Sensitivity: if every call were `read_config` with 3 credential placeholders, add $15.00 (3 GetSecretValue calls per call).

Secret storage for 1,000 agents x 3 secrets: $1200.0/month.

Usage inputs (`assumed` ones are placeholders until measured on the deployed Gateway):

- interceptor_requests_per_call: 2 (design: one pre-call and one post-call Lambda)
- interceptor_ms: 16.4 (measured: CloudWatch REPORT)
- interceptor_mb: 512 (measured)
- tool_ms: 8.8 (measured: CloudWatch REPORT)
- tool_mb: 512 (measured)
- ddb_reads_per_call: 15.92 (measured: CloudWatch over the load test (tool read_ticket, ~36 bytes output; account-wide metric, load test dominates))
- ddb_writes_per_call: 0.066 (measured: CloudWatch over the load test (tool read_ticket, ~36 bytes output; account-wide metric, load test dominates))
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
