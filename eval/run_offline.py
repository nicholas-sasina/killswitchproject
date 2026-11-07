"""Run the offline measurements and write results.json and RESULTS.md.

    uv run python run_offline.py [--trials 10]
Live and latency sections are merged in from out/*.json when those scripts have run.
"""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import catch_rate
import cost
import false_positives
import destinations
import layers
import cache_costs
from proxy.policy import Settings
from payloads import heldout

HERE = Path(__file__).parent


def load_live(directory=None) -> dict:
    """Merge the JSON files the live scripts leave in out/ (absent ones are skipped)."""
    out = directory or HERE / "out"
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
        # Same total with the DynamoDB read line replaced by the one for 1 KiB outputs.
        row = next(r for r in result["read_sensitivity"] if r["output"] == "config_file_1024")
        swapped = result["total_per_1m_tool_calls_usd"] - result["per_1m_tool_calls_usd"]["DynamoDB reads"]
        result["total_with_1kib_outputs_usd"] = round(swapped + row["dynamodb_read_usd_per_1m_calls"], 2)
    return result


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, text=True).strip()
    except Exception:
        return "unknown"


def dirty_source() -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=HERE, text=True).strip())


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
    if "revoked_outcomes" in r:
        def describe(counts):
            return ", ".join(f"{v} {name.replace('_', ' ')}" for name, v in counts.items())
        return f"; after revoke: revoked agents ({describe(r['revoked_outcomes'])}); other agents ({describe(r['other_outcomes'])})"
    return ("; legacy report (throttling not separated by group): "
            f"{r['refused_of_those']}/{r['calls_after_revoke_from_revoked']} calls from revoked agents refused, "
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
        f"Uncommitted source changes: {r.get('working_tree_dirty', 'not recorded')}.", "",
        "## Layer measurements", "",
        "Exit-check results below measure the scanner offline, not the whole containment system.", "",
    ]
    for name, layer in r.get("layers", {}).items():
        if layer["status"] == "unmeasured":
            out.append(f"- {name}: **unmeasured**. {layer['reason']}")
        else:
            scope = layer.get("scope", "environment unspecified")
            out += [f"### {name.capitalize()}", "",
                    f"Scope: **{scope}**. Source commit `{layer['source_commit']}`, measured {layer['measured_at']}.", ""]
            if layer.get("notes"):
                out += [layer["notes"], ""]
            if "source_dirty" in layer:
                out += [f"Product checkout has uncommitted changes: {layer['source_dirty']}.", ""]
            if layer.get("product_source_sha256"):
                out += [f"Inspected product source SHA-256: `{layer['product_source_sha256']}`.", ""]
            if name == "references":
                rate = pct(layer["exposure_rate"]) if layer["exposure_rate"] is not None else "unmeasured (no recognized credentials)"
                out += [f"Credentials in agent context: {layer['in_context']}/{layer['recognized']} ({rate}).", ""]
            elif name == "canaries":
                timing = f"p50 {layer['burn_seconds_p50']} s; p95 {layer['burn_seconds_p95']} s" if layer['burned'] else "unmeasured (no completed burns)"
                out += [f"Uses detected: {layer['detected']}/{layer['uses']} ({pct(layer['detection_rate'])}). "
                        f"Recorded revocations: {layer['burned']}/{layer['uses']}. Time from use to revocation: {timing}.", ""]
            if layer.get("checks"):
                out += ["Additional observed checks:", ""]
                out += [f"- {key.replace('_', ' ')}: {value}" for key, value in layer["checks"].items()]
                out += [""]
    audit = r.get("destinations")
    if audit:
        out += ["", "## Destinations", "", audit["scope"], "",
                f"Outsider-readable destinations: {audit['outsider_readable']}; unverified: {audit['unverified']}. "
                f"Target zero verified: {audit['target_met']}. The simulated-attacker sink is included.", "",
                "| Host | Outsider readable | Basis |", "|---|---|---|"]
        out += [f"| {x['host']} | {'unknown' if x['outsider_readable'] is None else 'yes' if x['outsider_readable'] else 'no'} | {x['basis']} |" for x in audit["destinations"]]
        if "mode" in audit:
            out += ["", f"Configuration mode: {audit['mode']}. A deny-all configuration is not a usable production configuration."]
    cache = r.get("cache_costs")
    if cache:
        out += ["", "## Post-cache DynamoDB read cost", ""]
        if cache["status"] == "unmeasured":
            out += [cache["reason"] + " This report does not claim a post-cache cost improvement."]
        else:
            out += [f"Source commit `{cache['source_commit']}`, measured {cache['measured_at']}. {cache['scope']}.", "",
                    f"Measurement scope: {cache.get('measurement_scope', 'environment unspecified')}. {cache.get('notes', '')}", "",
                    "| Output bytes | Calls | Read units/call | Read USD/1M calls |", "|---|---|---|---|"]
            out += [f"| {x['output_bytes']} | {x['calls']} | {x['read_units_per_call']} | {x['dynamodb_read_usd_per_1m_calls']} |" for x in cache["samples"]]
    out += ["", "## Exit check: established corpus", "",
        "| Metric | Result |", "|---|---|",
        f"| Catch rate, named credential ({c['forms']} forms x 4 credential types x {c['trials_per_cell']} trials) | **{pct(c['attributed_rate'])}** |",
        f"| Catch rate, blocked at all (incl. format-only matches) | {pct(c['blocked_rate'])} |",
        f"| False positives ({f['payload_types']} normal payload types x {f['trials']} trials) | **{pct(f['false_positive_rate'])}** ({f['flagged']}/{f['total_payload_runs']}) |",
        f"| Cost estimate per 1M tool calls (usage assumptions listed below) | **${k['total_per_1m_tool_calls_usd']:,.2f}**"
        + (f"; about ${k['total_with_1kib_outputs_usd']:,.2f} with 1 KiB outputs (see sensitivity)" if "total_with_1kib_outputs_usd" in k else "")
        + " |",
    ]
    out += live_rows(r.get("live", {}))
    for name, measurement in r.get("live", {}).items():
        if measurement.get("throttling"):
            t = measurement["throttling"]
            out += ["", f"{name}: {t['http_429']} HTTP 429 responses at {t['service']}. {t['note']}"]
    held = r.get("heldout")
    if held:
        out += ["", "## Exit check: held-out corpus", "",
                f"Frozen corpus SHA-256: `{held['corpus_sha256']}`. Baseline detector: `d618897`.",
                "Forms were added before ws/eval policy changes; no detector decoders were changed. "
                "This developer-authored corpus is not an independent security audit.", "",
                f"Named: {pct(held['attributed_rate'])}; blocked: {pct(held['blocked_rate'])}; "
                f"{held['total_payload_runs']} payload runs.", "",
                "| Form | Named | Blocked |", "|---|---|---|"]
        out += [f"| {n} | {v['attributed']}/{v['n']} | {v['blocked']}/{v['n']} |" for n, v in held["per_form"].items()]
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
    ap.add_argument("--evidence-dir", type=Path, default=HERE / "out")
    ap.add_argument("--output-dir", type=Path, default=HERE)
    args = ap.parse_args()
    if args.trials < 1:
        ap.error("trials must be positive")
    live = load_live(args.evidence_dir)
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "working_tree_dirty": dirty_source(),
        "catch_rate": catch_rate.run(args.trials),
        "heldout": {**catch_rate.run(args.trials, heldout.FORMS),
                    "corpus_sha256": hashlib.sha256(Path(heldout.__file__).read_bytes()).hexdigest()},
        "layers": layers.load(args.evidence_dir),
        "cache_costs": cache_costs.load(args.evidence_dir),
        "destinations": destinations.audit(Settings.from_env()),
        "false_positives": false_positives.run(args.trials),
        "cost": with_sensitivity(cost.compute(measured=measured_usage(live)), live),
        "live": live,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (args.output_dir / "RESULTS.md").write_text(markdown(results))
    print(f"wrote report to {args.output_dir}")


if __name__ == "__main__":
    main()
