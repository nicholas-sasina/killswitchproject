"""Run the offline measurements and write results.json and RESULTS.md.

    uv run python run_offline.py [--trials 10]
Live and latency sections are merged in from out/*.json when those scripts have run.
"""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import catch_rate
import cost
import false_positives

HERE = Path(__file__).parent


def load_live() -> dict:
    """Merge the JSON files the live scripts leave in out/ (absent ones are skipped)."""
    out = HERE / "out"
    read = lambda name: json.loads((out / name).read_text()) if (out / name).exists() else None
    live = {k: v for k, v in {
        "gateway_latency": read("latency_gateway.json"),
        "scan_latency": read("latency_scan.json"),
        "proxy_overhead": read("proxy_overhead.json"),
        "load_small": read("loadtest_50x5.json"),
        "load_revocation": read("loadtest_40x5.json"),
        "load_1000": read("loadtest_1000x5.json"),
        "ddb_reads": read("ddb_reads.json"),
    }.items() if v}
    return live


def measured_usage(live: dict) -> dict:
    g = live.get("gateway_latency") or {}
    i, t = g.get("burn-gateway-interceptor"), g.get("burn-gateway-tools")
    m = {}
    d = live.get("ddb_reads")
    if d:
        meas = d["measured"]
        basis = (f"measured: CloudWatch over the load test (tool {meas['tool']}, ~{meas['output_bytes']} bytes output; "
                 "account-wide metric, load test dominates)")
        m["ddb_reads_per_call"] = (meas["read_units_per_call"], basis)
        m["ddb_writes_per_call"] = (meas["write_units_per_call"], basis)
    if i:
        m.update(interceptor_ms=(i["mean_billed_ms"], "measured: CloudWatch REPORT"), interceptor_mb=(i["memory_mb"], "measured"))
    if t:
        m.update(tool_ms=(t["mean_billed_ms"], "measured: CloudWatch REPORT"), tool_mb=(t["memory_mb"], "measured"))
    return m


def with_sensitivity(result: dict, live: dict) -> dict:
    d = live.get("ddb_reads")
    if d:
        fp = d["measured"]["tables"]["burn-fingerprints"]["units_per_call"]
        result["read_sensitivity"] = cost.read_sensitivity(
            d["candidates"], d["measured"]["read_units_per_call"], fp, cost.PRICES["dynamodb_per_1m_read_units"][0])
    return result


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, text=True).strip()
    except Exception:
        return "unknown"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def classify(row: dict) -> str:
    if row["attributed"] == row["n"]:
        return "caught"
    return "missed" if row["blocked"] == 0 else "partial"


def revoke_note(l: dict) -> str:
    r = l.get("revocation")
    if not r:
        return ""
    return (f"; after revoke: {r['refused_of_those']}/{r['calls_after_revoke_from_revoked']} calls from revoked agents refused, "
            f"{r['succeeded_of_those']}/{r['calls_after_revoke_from_others']} from other agents succeeded")


def sensitivity_lines(k: dict) -> list[str]:
    rows = k.get("read_sensitivity")
    if not rows:
        return []
    out = ["", "DynamoDB reads grow with tool output size, because the response step looks up every candidate "
           "fingerprint (0.5 read unit per key, found or not). Measured: "
           f"{k['usage']['ddb_reads_per_call']['value']} read units per call for a tiny output. Larger outputs, "
           "from the scanner's candidate count:", "", "| Output | Fingerprints requested | Read units per call | DynamoDB reads per 1M calls |",
           "|---|---|---|---|"]
    out += [f"| {r['output']} | {r['fingerprints_requested']} | {r['read_units_per_call']} | ${r['dynamodb_read_usd_per_1m_calls']:,.2f} |"
            for r in rows]
    return out


def live_rows(live: dict) -> list[str]:
    rows = []
    g = live.get("gateway_latency")
    if g:
        c, i = g["client_round_trip"], g.get("burn-gateway-interceptor")
        rows.append(f"| Tool call through Gateway, client round trip | p50 {c['p50_ms']} ms, p95 {c['p95_ms']} ms ({c['n']} calls) |")
        if i:
            rows.append(f"| Interceptor Lambda per invocation (2 per call) | p50 {i['p50_ms']} ms, p95 {i['p95_ms']} ms; "
                        f"about {100 * g['interceptor_share_of_median']:.0f}% of the median call |")
    s = live.get("scan_latency")
    if s:
        rows.append(f"| burn_detect scan, 1 KiB / 16 KiB prose | p50 {s['prose_1024']['p50_ms']} / {s['prose_16384']['p50_ms']} ms; "
                    f"random hex 1 KiB / 16 KiB: {s['random_hex_1024']['p50_ms']} / {s['random_hex_16384']['p50_ms']} ms |")
    p = live.get("proxy_overhead")
    if p:
        a, b = p["sizes"]["1024"], p["sizes"]["16384"]
        rows.append(f"| Egress proxy overhead, 1 KiB / 16 KiB POST (local) | +{a['added_p50_ms']} / +{b['added_p50_ms']} ms p50, "
                    f"+{a['added_p95_ms']} / +{b['added_p95_ms']} ms p95 |")
    for key, label in (("load_small", "Load test"), ("load_revocation", "Load test with 3 sessions revoked mid-run"),
                       ("load_1000", "Load test, simulated agents sharing badges, 10 badges revoked mid-run")):
        l = live.get(key)
        if l:
            rows.append(f"| {label}: {l['simulated_agents']} agents x {l['calls_per_agent']} calls, "
                        f"{l['real_badges_and_sessions']} real badges | {l['throughput_calls_per_s']} calls/s, "
                        f"p50 {l['latency']['p50_ms']} ms, p95 {l['latency']['p95_ms']} ms, errors {sum(l['errors'].values())} (HTTP {', '.join(l['errors']) or 'none'})"
                        + revoke_note(l) + " |")
    return rows


def markdown(r: dict) -> str:
    c, f, k = r["catch_rate"], r["false_positives"], r["cost"]
    forms = c["per_form"]
    missed = {n: x for n, x in forms.items() if classify(x) != "caught"}
    out = [
        "# Burn eval results", "",
        f"Generated {r['timestamp']} at commit `{r['git_sha']}`. Every credential is freshly generated per run; "
        "no value appears here.", "",
        "| Metric | Result |", "|---|---|",
        f"| Catch rate, named credential ({c['forms']} forms x 4 credential types x {c['trials_per_cell']} trials) | **{pct(c['attributed_rate'])}** |",
        f"| Catch rate, blocked at all (incl. format-only matches) | {pct(c['blocked_rate'])} |",
        f"| False positives ({f['payload_types']} normal payload types x {f['trials']} trials) | **{pct(f['false_positive_rate'])}** ({f['flagged']}/{f['total_payload_runs']}) |",
        f"| Cost per 1M tool calls (see assumptions) | **${k['total_per_1m_tool_calls_usd']:,.2f}** |",
    ]
    out += live_rows(r.get("live", {}))
    out += ["", "## Catch rate by form", "", "| Form | Named | Blocked | Result |", "|---|---|---|---|"]
    for n, x in forms.items():
        out.append(f"| {n} | {x['attributed']}/{x['n']} | {x['blocked']}/{x['n']} | {classify(x)} |")
    out += ["", "## Known misses", "",
            "Measured, not tuned. These forms were not fully caught:", ""]
    out += [f"- **{n}**: {x['describe']} ({x['attributed']}/{x['n']} named)" for n, x in missed.items()]
    out += ["", "The outbound allowlist is the backstop for these.", "",
            "## Cost per 1M tool calls", "", "| Line | USD |", "|---|---|"]
    out += [f"| {n} | {v:,.2f} |" for n, v in k["per_1m_tool_calls_usd"].items()]
    out += [f"| **Total** | **{k['total_per_1m_tool_calls_usd']:,.2f}** |", "",
            *sensitivity_lines(k), "",
            "Sensitivity: if every call were `read_config` with 3 credential placeholders, add $15.00 "
            "(3 GetSecretValue calls per call).", "",
            f"Secret storage for 1,000 agents x 3 secrets: ${k['secret_storage_usd_per_month_for_1000_agents']}/month.", "",
            "Usage inputs (`assumed` ones are placeholders until measured on the deployed Gateway):", ""]
    out += [f"- {n}: {u['value']} ({u['basis']})" for n, u in k["usage"].items()]
    out += ["", "Price sources:", ""] + [f"- {n}: ${p['value']} - {p['source']}" for n, p in k["prices"].items()]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    args = ap.parse_args()
    live = load_live()
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "catch_rate": catch_rate.run(args.trials),
        "false_positives": false_positives.run(args.trials),
        "cost": with_sensitivity(cost.compute(measured=measured_usage(live)), live),
        "live": live,
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (HERE / "RESULTS.md").write_text(markdown(results))
    print("wrote results.json and RESULTS.md")


if __name__ == "__main__":
    main()
