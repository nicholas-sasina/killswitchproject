"""Proxy overhead: the same POST to the sink via the egress proxy and directly (local Docker).

    uv run python proxy_overhead.py [--n 200]

Starts the detection test stack (proxy + sink + a local AWS emulator holding generated test
fingerprints), times both paths from inside containers, then removes only its own stack.
Caveat: the fingerprint lookup hits a local emulator, so a real DynamoDB adds its own round trip.
"""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

from live.gateway import summarize_ms

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "burn-eval-proxy"
OUT = Path(__file__).parent / "out"


# `docker compose` (plugin) if present, else the standalone `docker-compose`.
COMPOSE = (["docker", "compose"] if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0
           else [shutil.which("docker-compose") or "docker-compose"])


def compose(*args, capture=False):
    cmd = [*COMPOSE, "-p", PROJECT, "-f", str(ROOT / "detection/docker-compose.test.yml"),
           "-f", str(Path(__file__).parent / "live/proxy-direct.override.yml"), *args]
    return subprocess.run(cmd, check=True, text=True, capture_output=capture).stdout


def probe(service: str, mode: str, n: int, size: int) -> list[float]:
    out = compose("run", "--rm", "--no-deps", "-v", f"{Path(__file__).parent / 'live/probe.py'}:/probe.py:ro",
                  service, "python", "/probe.py", mode, str(n), str(size), capture=True)
    return json.loads(out.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args()
    subprocess.run(["python3", str(ROOT / "detection/scripts/prepare_build.py")], check=True)
    result = {"requests_each": args.n, "sizes": {}}
    try:
        compose("down", "-v")
        compose("up", "--build", "-d", "proxy", "sink")
        for size in (1024, 16384):
            direct = summarize_ms(probe("direct-client", "direct", args.n, size))
            via = summarize_ms(probe("test-client", "proxy", args.n, size))
            result["sizes"][str(size)] = {"direct": direct, "via_proxy": via,
                                          "added_p50_ms": round(via["p50_ms"] - direct["p50_ms"], 1),
                                          "added_p95_ms": round(via["p95_ms"] - direct["p95_ms"], 1)}
    finally:
        compose("down", "-v")
    OUT.mkdir(exist_ok=True)
    (OUT / "proxy_overhead.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
