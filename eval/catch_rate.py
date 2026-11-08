"""Catch rate: run planted credentials, in many forms, through burn_detect.

Each trial draws brand-new credentials, so results never depend on one lucky value.
A form counts as 'attributed' when the scanner names the planted credential
(secret_ref) and as 'blocked' when it reports anything at all (the proxy blocks on any
finding, including format-only ones).
"""
from burn_detect import Scanner, SessionWindow

from payloads.credentials import Registry
from payloads.forms import FORMS


def run_form(scanner: Scanner, form, planted) -> dict:
    """Send the form's requests for one credential through one session window."""
    window = SessionWindow()
    blocked = attributed = False
    encodings = set()
    for request in form.build(planted.value):
        findings = scanner.scan(request, window=window)
        blocked = blocked or bool(findings)
        for f in findings:
            encodings.add(f.encoding)
            if f.secret_ref == planted.secret_ref:
                attributed = True
    return {"blocked": blocked, "attributed": attributed, "encodings": sorted(encodings)}


def run(trials: int = 10, forms=None) -> dict:
    if trials < 1:
        raise ValueError("trials must be positive")
    forms = FORMS if forms is None else forms
    if not forms:
        raise ValueError("at least one form is required")
    per_form = {f.name: {"describe": f.describe, "n": 0, "blocked": 0, "attributed": 0, "by_type": {}}
                for f in forms}
    for _ in range(trials):
        registry = Registry()
        scanner = Scanner(key=registry.key, lookup=registry.lookup)
        for planted in registry.fresh_set():
            for form in forms:
                r = run_form(scanner, form, planted)
                row = per_form[form.name]
                row["n"] += 1
                row["blocked"] += r["blocked"]
                row["attributed"] += r["attributed"]
                t = row["by_type"].setdefault(planted.kind, {"n": 0, "blocked": 0, "attributed": 0})
                t["n"] += 1
                t["blocked"] += r["blocked"]
                t["attributed"] += r["attributed"]
    total = sum(r["n"] for r in per_form.values())
    return {
        "trials_per_cell": trials,
        "forms": len(forms),
        "credential_types": ["api_key", "aws_key_id", "aws_secret", "conn_string"],
        "total_payload_runs": total,
        "blocked_rate": sum(r["blocked"] for r in per_form.values()) / total,
        "attributed_rate": sum(r["attributed"] for r in per_form.values()) / total,
        "per_form": per_form,
    }
