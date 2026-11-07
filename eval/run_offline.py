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
    for name, section in r.get("live", {}).items():
        out.append(f"| {name} | {section['summary']} |")
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
            f"Secret storage for 1,000 agents x 3 secrets: ${k['secret_storage_usd_per_month_for_1000_agents']}/month.", "",
            "Usage inputs (`assumed` ones are placeholders until measured on the deployed Gateway):", ""]
    out += [f"- {n}: {u['value']} ({u['basis']})" for n, u in k["usage"].items()]
    out += ["", "Price sources:", ""] + [f"- {n}: ${p['value']} - {p['source']}" for n, p in k["prices"].items()]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    args = ap.parse_args()
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "catch_rate": catch_rate.run(args.trials),
        "false_positives": false_positives.run(args.trials),
        "cost": cost.compute(),
        "live": {},
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (HERE / "RESULTS.md").write_text(markdown(results))
    print("wrote results.json and RESULTS.md")


if __name__ == "__main__":
    main()
