"""Pure helpers of the live measurements (no AWS or Docker needed)."""
import pytest

import loadtest
from latency_gateway import REPORT
from live.gateway import percentile, summarize_ms
from run_offline import measured_usage


def test_percentiles_and_summary():
    assert percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert percentile(list(range(100)), 0.95) == 95
    assert summarize_ms([0.010, 0.020, 0.030])["p50_ms"] == 20.0


def test_report_line_parsing():
    line = "REPORT RequestId: x Duration: 13.70 ms\tBilled Duration: 14 ms\tMemory Size: 512 MB\tMax Memory Used: 90 MB"
    m = REPORT.search(line)
    assert (float(m[1]), int(m[2]), int(m[3])) == (13.7, 14, 512)


def test_measured_usage_overrides_cost_inputs():
    live = {"gateway_latency": {"burn-gateway-interceptor": {"mean_billed_ms": 30, "memory_mb": 512},
                                "burn-gateway-tools": {"mean_billed_ms": 20, "memory_mb": 256}}}
    assert measured_usage(live) == {"interceptor_ms": 30, "interceptor_mb": 512, "tool_ms": 20, "tool_mb": 256}


def test_loadtest_refuses_too_many_calls(monkeypatch):
    monkeypatch.setattr("sys.argv", ["loadtest", "--agents", "5000", "--calls", "5"])
    with pytest.raises(SystemExit, match="exceeds --max-calls"):
        loadtest.main()
