# Burn eval results

Generated 2026-11-07T20:41:06+00:00 at commit `812ec44`. Every credential is freshly generated per run; no value appears here.

| Metric | Result |
|---|---|
| Catch rate, named credential (31 forms x 4 credential types x 10 trials) | **69.8%** |
| Catch rate, blocked at all (incl. format-only matches) | 72.8% |
| False positives (31 normal payload types x 10 trials) | **0.0%** (0/310) |
| Cost per 1M tool calls (see assumptions) | **$60.64** |

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
| homoglyph | 15/40 | 23/40 | partial |
| junk_interleaved | 0/40 | 0/40 | missed |
| base32 | 0/40 | 0/40 | missed |
| zero_width | 0/40 | 0/40 | missed |
| hex_of_base64 | 40/40 | 40/40 | caught |
| base64_of_reversed | 40/40 | 40/40 | caught |
| url_encoded_base64 | 40/40 | 40/40 | caught |
| split_query_params | 20/40 | 30/40 | partial |
| caesar_shift_1 | 0/40 | 10/40 | partial |

## Known misses

Measured, not tuned. These forms were not fully caught:

- **split_2_requests**: two halves in two requests (20/40 named)
- **split_3_requests**: thirds in three requests (10/40 named)
- **partial_first_half**: only the first half is sent (0/40 named)
- **gzip_base64**: gzip then base64 (0/40 named)
- **rot13**: ROT13 (0/40 named)
- **homoglyph**: Latin letters swapped for Cyrillic look-alikes (15/40 named)
- **junk_interleaved**: random junk character after each character (0/40 named)
- **base32**: base32 (0/40 named)
- **zero_width**: zero-width spaces between characters (0/40 named)
- **split_query_params**: two halves in two query params of one URL (20/40 named)
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
| DynamoDB reads | 37.62 |
| DynamoDB writes | 0.75 |
| Secrets Manager API calls | 5.00 |
| Cognito M2M tokens (amortised) | 11.25 |
| Burn API (API Gateway) | 0.00 |
| **Total** | **60.64** |

Secret storage for 1,000 agents x 3 secrets: $1200.0/month.

Usage inputs (`assumed` ones are placeholders until measured on the deployed Gateway):

- interceptor_requests_per_call: 2 (design: one pre-call and one post-call Lambda)
- interceptor_ms: 30 (assumed)
- interceptor_mb: 256 (assumed)
- tool_ms: 40 (assumed)
- tool_mb: 256 (assumed)
- ddb_reads_per_call: 301 (assumed: 1 session read + ~600 fingerprint keys at 0.5 RRU (eventually consistent) for a ~1 KB response)
- ddb_writes_per_call: 1.2 (assumed: 1 event + ~0.2 ledger writes)
- secrets_calls_per_call: 1 (assumed: tool resolves its placeholders with one GetSecretValue)
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
