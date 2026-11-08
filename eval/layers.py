"""Aggregate private owner-produced observations; absent evidence stays unmeasured.

Input files live in gitignored out/. Reports contain counts and timings only.
"""
from datetime import datetime
import json
import math
import re
from pathlib import Path


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamps must include a timezone")
    return parsed


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("evidence counts must be nonnegative integers")
    return value


def source_commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{7,40}", value):
        raise ValueError("source_commit must be a Git commit hash")
    return value


def measure(kind, evidence):
    source_commit(evidence.get("source_commit"))
    timestamp(evidence["measured_at"])
    records = evidence["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("evidence must contain observations")
    ids = [r["event_id"] for r in records]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("observation event ids must be nonempty and unique")
    out = {"status": "measured", "source_commit": evidence["source_commit"],
           "measured_at": evidence["measured_at"], "observations": len(records)}
    scope = evidence.get("scope", "owner-supplied (environment unspecified)")
    if scope not in {"local-emulator", "local-product-code", "deployed-aws",
                     "owner-supplied (environment unspecified)"}:
        raise ValueError("unknown evidence scope")
    out["scope"] = scope
    if evidence.get("notes"):
        if not isinstance(evidence["notes"], str):
            raise ValueError("evidence notes must be text")
        out["notes"] = evidence["notes"]
    if "source_dirty" in evidence:
        if type(evidence["source_dirty"]) is not bool:
            raise ValueError("source_dirty must be a boolean")
        out["source_dirty"] = evidence["source_dirty"]
    if "product_source_sha256" in evidence:
        digest = evidence["product_source_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("product_source_sha256 must be a SHA-256 digest")
        out["product_source_sha256"] = digest
    if "checks" in evidence:
        checks = evidence["checks"]
        if not isinstance(checks, dict):
            raise ValueError("checks must be aggregate observations")
        safe = {}
        fields = ("responses", "withheld_responses", "tool_errors") if kind == "references" else ("provider_rejections", "rotated_old_api_key_rejections")
        for field in fields:
            if field in checks:
                safe[field] = count(checks[field])
        if kind == "canaries":
            if "control_session_active" in checks:
                if type(checks["control_session_active"]) is not bool:
                    raise ValueError("control_session_active must be a boolean")
                safe["control_session_active"] = checks["control_session_active"]
            if "containment_outcomes" in checks:
                outcomes = checks["containment_outcomes"]
                allowed = {"contained", "contained_rotation_deferred", "contained_rotation_failed",
                           "already_contained", "not_actionable", "ignored", "unknown"}
                if not isinstance(outcomes, dict) or set(outcomes) - allowed:
                    raise ValueError("unrecognized containment outcome")
                safe["containment_outcomes"] = {k: count(v) for k, v in outcomes.items()}
        out["checks"] = safe
    if kind == "canaries" and "negative_checks" in evidence:
        cases = evidence["negative_checks"]
        if not isinstance(cases, dict):
            raise ValueError("negative checks must be aggregate observations")
        safe_cases = {}
        if "duplicate_replay" in cases:
            case = cases["duplicate_replay"]
            fields = {"presentations", "provider_rejections", "new_incidents", "extra_api_rotations", "extra_canary_events"}
            if not isinstance(case, dict) or set(case) != fields:
                raise ValueError("invalid duplicate replay observations")
            safe_cases["duplicate_replay"] = {k: count(v) for k,v in case.items()}
        if "rotation_failure" in cases:
            case = cases["rotation_failure"]
            boolean_fields = {"provider_rejected", "session_revoked", "old_api_key_accepted", "incomplete_containment_flagged"}
            if (not isinstance(case, dict) or set(case) != boolean_fields | {"outcome", "rotated_credentials"}
                or any(type(case[k]) is not bool for k in boolean_fields)
                or case["outcome"] not in {"contained_rotation_failed", "contained_rotation_deferred", "contained"}):
                raise ValueError("invalid rotation failure observations")
            safe_cases["rotation_failure"] = {**{k:case[k] for k in boolean_fields},
                "outcome": case["outcome"], "rotated_credentials": count(case["rotated_credentials"])}
        out["negative_checks"] = safe_cases
    if kind == "canaries" and "policy_checks" in evidence:
        cases = evidence["policy_checks"]
        allowed = {"own_session", "other_session", "report_only", "ambiguous_exposure"}
        boolean_fields = {"provider_rejected", "session_active", "old_api_key_accepted"}
        count_fields = {"new_incidents", "api_rotations", "canary_events"}
        if not isinstance(cases, dict) or set(cases) - allowed:
            raise ValueError("unrecognized policy check")
        safe_cases = {}
        for name, case in cases.items():
            if (not isinstance(case, dict) or set(case) != boolean_fields | count_fields | {"outcome"}
                or any(type(case[k]) is not bool for k in boolean_fields)
                or not isinstance(case["outcome"], str)
                or case["outcome"] not in {"not_dispatched", "report_only", "not_actionable", "contained",
                                          "contained_rotation_failed", "contained_rotation_deferred"}):
                raise ValueError("invalid policy observations")
            safe_cases[name] = {**{k:case[k] for k in boolean_fields},
                                **{k:count(case[k]) for k in count_fields}, "outcome": case["outcome"]}
        out["policy_checks"] = safe_cases
    if kind == "references":
        recognized = exposed = 0
        for r in records:
            n, leaked = count(r["recognized"]), count(r["in_context"])
            if leaked > n:
                raise ValueError("in_context cannot exceed recognized")
            recognized += n
            exposed += leaked
        out.update(recognized=recognized, in_context=exposed,
                   exposure_rate=exposed / recognized if recognized else None,
                   target_met=exposed == 0 if recognized else None)
    elif kind == "canaries":
        detected, latencies = 0, []
        for r in records:
            used = timestamp(r["used_at"])
            seen = timestamp(r["detected_at"]) if r.get("detected_at") else None
            burned = timestamp(r["burned_at"]) if r.get("burned_at") else None
            if seen and seen < used or burned and (not seen or burned < seen):
                raise ValueError("canary timestamps are out of order")
            detected += seen is not None
            if burned:
                seconds = (burned - used).total_seconds()
                if not math.isfinite(seconds):
                    raise ValueError("invalid burn latency")
                latencies.append(seconds)
        ordered = sorted(latencies)
        out.update(uses=len(records), detected=detected, detection_rate=detected / len(records),
                   burned=len(latencies), target_met=detected == len(records),
                   burn_seconds_p50=ordered[len(ordered)//2] if ordered else None,
                   burn_seconds_p95=ordered[min(len(ordered)-1, int(len(ordered)*.95))] if ordered else None)
        if "checks" in out:
            checks = out["checks"]
            out["containment_target_met"] = (checks.get("containment_outcomes", {}).get("contained") == len(records)
                and checks.get("control_session_active") is True and len(latencies) == len(records))
    else:
        raise ValueError("unknown measurement layer")
    return out


def load(directory: Path):
    result = {}
    for kind in ("references", "canaries"):
        path = directory / f"{kind}.json"
        result[kind] = measure(kind, json.loads(path.read_text())) if path.exists() else {
            "status": "unmeasured", "reason": "Owner-produced observations have not been supplied."}
    return result
