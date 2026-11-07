"""Runs INSIDE a container: time N small POSTs to the sink, via the proxy or directly.

    python probe.py proxy|direct N SIZE_BYTES

Prints one JSON line of per-request seconds. The payload is ordinary text, no credential.
"""
import json
import os
import sys
import time

import httpx

mode, n, size = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
data = json.load(open("/test-data/runtime.json"))
sink = os.environ["BURN_SINK_URL"]
body = ("Quarterly report is ready for review. " * (size // 38 + 1))[:size]
kwargs = {"trust_env": False, "timeout": 10}
if mode == "proxy":
    kwargs.update(proxy=f"http://{data['agent']}:{data['session']}@proxy:8080", verify="/certs/mitmproxy-ca-cert.pem")
else:
    kwargs.update(verify="/test-data/sink-cert.pem")
with httpx.Client(**kwargs) as client:
    for _ in range(100):                      # wait until the path works
        try:
            if client.get(sink + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    for _ in range(10):
        client.post(sink + "/webhook", content=body)
    times = []
    for _ in range(n):
        start = time.perf_counter()
        status = client.post(sink + "/webhook", content=body).status_code
        times.append(time.perf_counter() - start)
        assert status == 200, status
print(json.dumps(times))
