from copy import deepcopy

import pytest

from capture_cache_cost import collect


def plan():
    return {"source_commit": "abcdef0", "cache_enabled": True, "samples": [
        {"output_bytes": n, "calls": 10, "tool": "read_config",
         "start": f"2026-11-07T23:0{i*2}:00Z", "end": f"2026-11-07T23:0{i*2+2}:00Z"}
        for i,n in enumerate((36, 1024, 16384))]}


def measured(*args):
    return {"window": list(args[:2]), "tables": {
        "sessions": {"metric": "ConsumedReadCapacityUnits", "sum_units": 20, "data_points": 2},
        "fingerprints": {"metric": "ConsumedReadCapacityUnits", "sum_units": 7, "data_points": 1},
        "events": {"metric": "ConsumedWriteCapacityUnits", "sum_units": 100, "data_points": 2}}}


def test_cache_capture_keeps_refresh_costs_excludes_writes_and_preserves_windows():
    result = collect(plan(), measured)
    assert result["scope"] == "deployed-aws"
    assert all(s["read_units"] == 27 for s in result["samples"])
    assert result["samples"][0]["window"] == ["2026-11-07T23:00:00Z", "2026-11-07T23:02:00Z"]


def test_missing_cloudwatch_metrics_are_not_treated_as_zero_cost():
    def missing(*args):
        r = measured(*args)
        r["tables"]["fingerprints"].update(sum_units=0, data_points=0)
        return r
    with pytest.raises(ValueError, match="metrics are missing"):
        collect(plan(), missing)


def test_overlapping_windows_do_not_measure_separate_output_sizes():
    p = plan()
    p["samples"][1]["start"] = p["samples"][0]["start"]
    with pytest.raises(ValueError, match="must not overlap"):
        collect(p, measured)


@pytest.mark.parametrize("field,value", [("calls", 0), ("calls", True),
    ("end", "2026-11-07T22:00:00Z"), ("start", "2026-11-07T23:00:01Z")])
def test_invalid_plan_fails_before_contacting_aws(field, value):
    p = deepcopy(plan())
    p["samples"][-1][field] = value
    def no_calls(*args):
        pytest.fail("invalid plan must not contact AWS")
    with pytest.raises(ValueError):
        collect(p, no_calls)
