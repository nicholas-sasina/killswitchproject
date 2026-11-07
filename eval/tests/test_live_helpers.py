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
    m = measured_usage(live)
    assert {k: v[0] for k, v in m.items()} == {"interceptor_ms": 30, "interceptor_mb": 512, "tool_ms": 20, "tool_mb": 256}


def test_read_sensitivity_scales_only_the_fingerprint_lookups():
    from cost import read_sensitivity
    rows = read_sensitivity({"big": {"fingerprints_requested": 1000, "read_units": 500.0}},
                            base_read_units=16.0, measured_fp_units=13.0, price=0.125)
    assert rows[0]["read_units_per_call"] == 503.0      # 500 + the 3 units of other reads
    assert rows[0]["dynamodb_read_usd_per_1m_calls"] == round(503 * 0.125, 2)


def test_loadtest_refuses_too_many_calls(monkeypatch):
    monkeypatch.setattr("sys.argv", ["loadtest", "--agents", "5000", "--calls", "5"])
    with pytest.raises(SystemExit, match="exceeds --max-calls"):
        loadtest.main()


def test_revocation_report_counts_only_calls_after_the_revoke():
    run = loadtest.Run()
    run.revoked, run.revoked_at = {1}, 10.0
    # (agent, started_at, refused, is_error)
    run.calls_log = [(1, 5.0, False, False), (1, 11.0, True, True), (2, 11.0, False, False), (2, 12.0, False, False)]
    report = loadtest.revocation_report(run)
    assert report["refused_of_those"] == report["calls_after_revoke_from_revoked"] == 1
    assert report["succeeded_of_those"] == report["calls_after_revoke_from_others"] == 2
    assert report["revoked_calls_before_revoke_succeeded"] == 1


@pytest.mark.parametrize("status,payload,error,refused", [
    (200, '{"id":1,"result":{"content":[]}}', False, False),
    (200, '{"id":1,"result":{"isError": true,"content":[{"text":"BURN: revoked"}]}}', True, True),
    (200, '{"id":1,"error":{"code":-32603,"message":"unavailable"}}', True, False),
    (429, '{"error":"throttled"}', True, False),
    (200, 'not a valid RPC response', True, False),
    (200, 'data: {"id":1,"result":{"content":[]}}\n\n', False, False),
    (200, '{"id":1,"result":{"content":[{"text":"BURN: quoted ticket text"}]}}', False, False),
])
def test_mcp_classifies_real_rpc_outcomes(status, payload, error, refused):
    import asyncio
    import httpx
    from live.gateway import Mcp
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text=payload))) as http:
            mcp = Mcp(http, "https://gateway.example.test/mcp", "test-token")
            _, failed, actual_status = await mcp.call_tool("read_ticket", {})
            assert failed == error and mcp.last_refused == refused
            assert actual_status == status
    asyncio.run(check())


@pytest.mark.parametrize("startup_fails", [False, True])
def test_load_run_distinguishes_startup_failure_from_failed_tool_calls(monkeypatch, startup_fails):
    import asyncio
    class FakeMcp:
        last_refused = False
        def __init__(self, *args):
            self.calls = 0
        async def start(self):
            if startup_fails:
                raise RuntimeError("startup")
        async def call_tool(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transport")
            return .1, False, 200
    monkeypatch.setattr(loadtest, "Mcp", FakeMcp)
    run = loadtest.Run()
    asyncio.run(loadtest.one_agent(0, asyncio.Semaphore(1), None, "unused", "unused", 2, 0, run))
    if startup_fails:
        assert run.startup_errors == {"RuntimeError": 1} and run.calls_log == []
    else:
        assert run.startup_errors == {} and run.errors == {"TimeoutError": 1}
        assert loadtest.outcomes(run.calls_log) == dict(succeeded=1, other_errors=1, throttled=0, refused=0)
