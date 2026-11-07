"""burn_detect scan time by payload size (local, lookup excluded by using an in-memory registry)."""
import json
import secrets
import statistics
import time
from pathlib import Path

from burn_detect import Scanner

from payloads.credentials import Registry

SIZES = [256, 1024, 4096, 16384, 65536, 262144 - 1]
OUT = Path(__file__).parent / "out"


def run(repeats: int = 15) -> dict:
    registry = Registry()
    registry.fresh_set()
    scanner = Scanner(registry.key, registry.lookup)
    prose = "Customer asked about the refund policy for order 4821 and wants an update by Friday. "
    result = {}
    for size in SIZES:
        for kind, text in (("prose", (prose * (size // len(prose) + 1))[:size]), ("random_hex", secrets.token_hex(size // 2 + 1)[:size])):
            times = []
            for _ in range(repeats):
                start = time.perf_counter()
                scanner.scan(text)
                times.append(1000 * (time.perf_counter() - start))
            times.sort()
            result[f"{kind}_{size}"] = {"bytes": size, "kind": kind, "p50_ms": round(statistics.median(times), 2),
                                        "p95_ms": round(times[int(0.95 * len(times)) - 1], 2)}
    return result


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    out = run()
    (OUT / "latency_scan.json").write_text(json.dumps(out, indent=2))
    for k, v in out.items():
        print(k, v["p50_ms"], v["p95_ms"])
