"""Cost per 1M tool calls, from AWS list prices (us-east-1), with the arithmetic shown.

Prices are inputs, looked up on 2026-11-07 (sources below). Per-call usage figures
are inputs too: `measured` ones come from the latency run, the rest are stated
assumptions and are labelled as such in the output.
"""

PRICES = {
    "gateway_per_1k_invocations": (0.005, "https://aws.amazon.com/bedrock/agentcore/pricing/ (InvokeTool, $0.005 per 1,000)"),
    "lambda_per_1m_requests": (0.20, "https://aws.amazon.com/lambda/pricing/ (x86, $0.20 per 1M)"),
    "lambda_per_gb_second": (0.0000166667, "https://aws.amazon.com/lambda/pricing/ (x86, per GB-s)"),
    "dynamodb_per_1m_write_units": (0.625, "https://aws.amazon.com/dynamodb/pricing/on-demand/ (standard class)"),
    "dynamodb_per_1m_read_units": (0.125, "https://aws.amazon.com/dynamodb/pricing/on-demand/ (standard class)"),
    "secrets_per_10k_api_calls": (0.05, "https://aws.amazon.com/secrets-manager/pricing/"),
    "secrets_per_secret_month": (0.40, "https://aws.amazon.com/secrets-manager/pricing/"),
    "cognito_per_m2m_token": (0.00225, "https://aws.amazon.com/cognito/pricing/ (M2M token request, no free tier)"),
    "apigw_http_per_1m": (1.00, "https://aws.amazon.com/api-gateway/pricing/ (HTTP API)"),
}

# Per-call usage. `source` is "assumed" until the deployed Gateway has been measured.
USAGE = {
    "interceptor_requests_per_call": (2, "design: one pre-call and one post-call Lambda"),
    "interceptor_ms": (30, "assumed until measured"),          # average billed duration per interceptor invocation
    "interceptor_mb": (256, "assumed"),
    "tool_ms": (40, "assumed until measured"),
    "tool_mb": (256, "assumed"),
    "ddb_reads_per_call": (1.5, "assumed baseline; replace with observed reads for the deployed code, including cache refreshes"),
    "ddb_writes_per_call": (0, "assumed steady allowed calls; exposure and refusal events require measured writes"),
    "secrets_calls_per_call": (0, "counted from gateway code: only read_config resolves secrets (one GetSecretValue per placeholder)"),
    "calls_per_token": (200, "assumed: one badge token (1 h) covers ~200 calls"),
    "burn_api_requests_per_call": (0, "tool calls do not touch the Burn API"),
}
SECRETS_AT_REST = {"agents": 1000, "secrets_per_agent": 3}   # api_key, aws_key, badge


def read_sensitivity(candidates: dict, base_read_units: float, measured_fp_units: float, price: float) -> list[dict]:
    """Cost per 1M calls if the tool output were larger, from the scan's candidate count.

    Only the fingerprint lookups scale with output size (0.5 RRU per requested key); the
    other reads (session, scenario) are unchanged.
    """
    other = base_read_units - measured_fp_units
    rows = []
    for name, c in candidates.items():
        units = c["read_units"] + other
        rows.append({"output": name, "fingerprints_requested": c["fingerprints_requested"],
                     "read_units_per_call": round(units, 1), "dynamodb_read_usd_per_1m_calls": round(units * price, 2)})
    return rows


def compute(prices=PRICES, usage=USAGE, measured: dict | None = None) -> dict:
    measured = measured or {}
    u = {k: v[0] for k, v in usage.items()}
    # measured values are (value, basis) pairs
    u.update({k: v[0] for k, v in measured.items()})
    p = {k: v[0] for k, v in prices.items()}
    n = 1_000_000
    gb_s = lambda ms, mb: ms / 1000 * mb / 1024
    lines = {
        "AgentCore Gateway invocations": n * p["gateway_per_1k_invocations"] / 1000,
        "Interceptor Lambda requests": n * u["interceptor_requests_per_call"] * p["lambda_per_1m_requests"] / 1e6,
        "Interceptor Lambda compute": n * u["interceptor_requests_per_call"] * gb_s(u["interceptor_ms"], u["interceptor_mb"]) * p["lambda_per_gb_second"],
        "Tool Lambda requests": n * p["lambda_per_1m_requests"] / 1e6,
        "Tool Lambda compute": n * gb_s(u["tool_ms"], u["tool_mb"]) * p["lambda_per_gb_second"],
        "DynamoDB reads": n * u["ddb_reads_per_call"] * p["dynamodb_per_1m_read_units"] / 1e6,
        "DynamoDB writes": n * u["ddb_writes_per_call"] * p["dynamodb_per_1m_write_units"] / 1e6,
        "Secrets Manager API calls": n * u["secrets_calls_per_call"] * p["secrets_per_10k_api_calls"] / 1e4,
        "Cognito M2M tokens (amortised)": n / u["calls_per_token"] * p["cognito_per_m2m_token"],
        "Burn API (API Gateway)": n * u["burn_api_requests_per_call"] * p["apigw_http_per_1m"] / 1e6,
    }
    at_rest = SECRETS_AT_REST["agents"] * SECRETS_AT_REST["secrets_per_agent"] * p["secrets_per_secret_month"]
    return {
        "per_1m_tool_calls_usd": {k: round(v, 2) for k, v in lines.items()},
        "total_per_1m_tool_calls_usd": round(sum(lines.values()), 2),
        "secret_storage_usd_per_month_for_1000_agents": round(at_rest, 2),
        "usage": {k: {"value": u[k], "basis": measured[k][1] if k in measured else usage[k][1]} for k in u},
        "prices": {k: {"value": v[0], "source": v[1]} for k, v in prices.items()},
        "priced_on": "2026-11-07",
    }
