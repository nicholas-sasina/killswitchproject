# Eval

## What it does
Measures Burn honestly: how many planted credentials the exit check catches, how often normal
traffic is wrongly flagged, how much latency Burn adds, how it behaves under load, and what a
million tool calls cost on AWS list prices.
It measures `burn_detect` as it is; nothing here tunes a detector.

## How it works
- `payloads/credentials.py` mints fresh test credentials on every run (API key, AWS-shaped key id and secret, connection string) and registers their fingerprints in memory with the same rows the minter writes.
- `payloads/forms.py` defines 31 ways to smuggle a value out (encodings, separators, splits across requests, partial leaks). Split forms run through one `SessionWindow`.
- `catch_rate.py` runs every form for every credential type, 10 trials by default, and records "named" (the right credential identified) and "blocked" (any finding).
- `false_positives.py` runs 31 normal payload types against a registry of decoy credentials.
- Live scripts use throwaway `agent-eval-<random>` badges and sessions through the deployed Gateway and delete them afterwards: `latency_gateway.py` (client round trip plus Lambda REPORT durations from CloudWatch), `loadtest.py` (asyncio + httpx), `proxy_overhead.py` (same POST via the proxy and directly, in Docker).
- `cost.py` multiplies list prices (with source URLs) by per-call usage; assumed usage is labelled.

## Key files
- `run_offline.py`: runs the offline checks, merges any live output from `out/`, writes `results.json` and `RESULTS.md`.
- `latency_gateway.py`, `latency_scan.py`, `loadtest.py`, `proxy_overhead.py`, `live/`: live and latency measurements.
- `payloads/`: credential generators, smuggling forms, normal payloads.
- `catch_rate.py`, `false_positives.py`, `cost.py`.
- `out/` (gitignored): raw output.

## How to test it
```bash
cd eval
uv run pytest -q
uv run python run_offline.py --trials 10
# live (needs AWS credentials; the proxy test also needs Docker)
uv run python latency_gateway.py --calls 200
uv run python latency_scan.py
uv run python proxy_overhead.py --n 200
uv run python loadtest.py --agents 50 --calls 5 --badges 10   # refuses > 20,000 calls
uv run python run_offline.py   # fold the results in
```

## FAQ
**Why not just report one catch-rate number?** One number hides the shape. Some forms (plain, any base64, hex, URL-encoding) are always caught; others (gzip, base32, rot13, junk characters, partial leaks) are not. `RESULTS.md` lists every form so the limits are visible.

**Is the eval tuned to the detector?** No. The forms were fixed before the first run, credentials are random each run, and the detector is not modified. Misses are reported as misses, with the outbound allowlist as the backstop.

**Where do cost numbers come from?** AWS list prices fetched on the date in `results.json`, each with a source URL. Per-call usage that has not been measured on the deployed Gateway is marked `assumed`.

**Is there a no-Burn latency baseline?** Not for the Gateway: its interceptor cannot be switched off without redeploying, so we report the interceptor's own Lambda durations and its share of the client round trip instead. The proxy has a true baseline (direct request to the sink). The proxy figures use a local AWS emulator for fingerprint lookups, so real DynamoDB adds its own round trip.

**Why do load tests share badges?** Cognito rate-limits app-client creation. `--badges N` makes N real badges and sessions and spreads the simulated agents over them; the Gateway path is identical. The results say how many real badges were used.
