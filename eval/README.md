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
- `ddb_reads.py`: DynamoDB read/write units per call from CloudWatch over a load-test window, plus offline counts of fingerprints requested for larger outputs.
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
uv run python loadtest.py --agents 50 --calls 5 --think 0.3 --revoke 3   # small by default; see the cost warning
uv run python run_offline.py   # fold the results in
```

## Results so far
Details and the full tables are in `RESULTS.md` and `results.json`. Scale numbers come from three runs against the deployed Gateway. The first two give each agent its own badge:
- 50 agents x 5 calls: 250 calls, 0 errors, about 62 calls/s, p50 253 ms, p95 360 ms.
- 40 agents x 5 calls with 3 sessions revoked mid-run: every one of the 15 calls the revoked agents made after the revoke was refused, and all 185 calls from the other agents succeeded.

- 1,000 simulated agents x 5 calls sharing 100 badges (agent i uses badge i % 100), concurrency 50, 10 of the 100 badges revoked mid-run (about 100 agents lose access). Command: `uv run python loadtest.py --agents 1000 --calls 5 --badges 100 --concurrency 50 --revoke 10`.
  5,000 tool calls in 41.8 s (about 120 calls/s); calls that were served had p50 227 ms and p95 810 ms.
  4,593 succeeded, 262 were refused after the revoke, and 145 got HTTP 429 (the Gateway throttled at concurrency 50). No other errors.
  After the revoke, 262 of 281 calls from revoked agents were refused (the other 19 were throttled with 429, none succeeded) and 2,238 of 2,364 calls from the other agents succeeded (the rest were 429).
  All 100 badges, sessions and test events were deleted afterwards and checked.

## DynamoDB reads scale with tool output size
The Gateway's response step scans each tool output and looks up every candidate fingerprint (0.5 read unit per key, found or not). Measured over the 1,000-agent run with `read_ticket` (a ~36-byte output): 15.9 read units per call (13.3 fingerprints, 2.1 sessions, 0.5 scenario) and 0.07 write units. A 16 KiB config-like output asks for about 18,000 fingerprints, roughly $1,150 per 1M calls in reads alone; see `RESULTS.md`. The CloudWatch metrics are account-wide, so other traffic in the window is included.

## Cost warning
Every badge is one Cognito app client and one token request, billed at $0.00225 each, and every call also costs Gateway, Lambda and DynamoDB usage. `loadtest.py` defaults to 50 agents x 5 calls and refuses more than 20,000 calls, but a large run still costs real money (1,000 badges is about $2.25 in token requests alone; the 1,000-agent run above used 100 badges). Run big tests only deliberately.

## FAQ
**Why not just report one catch-rate number?** One number hides the shape. Some forms (plain, any base64, hex, URL-encoding) are always caught; others (gzip, base32, rot13, junk characters, partial leaks) are not. `RESULTS.md` lists every form so the limits are visible.

**Is the eval tuned to the detector?** No. The forms were fixed before the first run, credentials are random each run, and the detector is not modified. Misses are reported as misses, with the outbound allowlist as the backstop.

**Where do cost numbers come from?** AWS list prices fetched on the date in `results.json`, each with a source URL. Per-call usage that has not been measured on the deployed Gateway is marked `assumed`.

**Is there a no-Burn latency baseline?** Not for the Gateway: its interceptor cannot be switched off without redeploying, so we report the interceptor's own Lambda durations and its share of the client round trip instead. The proxy has a true baseline (direct request to the sink). The proxy figures use a local AWS emulator for fingerprint lookups, so real DynamoDB adds its own round trip.

**Do simulated agents share badges?** By default each has its own. `--badges N` makes fewer real badges and spreads the agents over them, which avoids Cognito's client-creation rate limit; the Gateway path is identical and the results state how many real badges were used.

## Separate layers and held-out tests

Run the offline report without replacing historical checked-in measurements:

```bash
uv run --python 3.12 python run_offline.py --trials 10 --output-dir out/current
```

The report separates the established scanner corpus from the eight forms in
`payloads/heldout.py`. That corpus was introduced against detector commit `d618897`, before
this workstream's policy changes. Its source hash is recorded in each report. Do not tune
detectors against it; if another owner adds decoders, report this corpus separately and
freeze a new unseen corpus for future tuning. It is developer-authored evaluation, not an
independent audit. Scanner results are offline measurements, not end-to-end protection rates.

References, canaries and post-cache reads require observations from their component owners.
Missing observations are **unmeasured**, never zero or a passing target. Use `--evidence-dir`
to select a private evidence directory (default `out/`). Keep inputs and draft reports there;
never commit raw secrets or fabricate observations to complete a report.

Each evidence file includes `source_commit`, a timezone-bearing `measured_at`, and:

| File | Required observations |
|---|---|
| `references.json` | `records`: unique `event_id`, integer `recognized` and `in_context` counts per observed response. Counts refer only to recognized credentials. |
| `canaries.json` | `records`: unique use `event_id`, `used_at`, optional `detected_at` and `burned_at` (timezone-bearing timestamps). Include every use, including undetected uses. |
| `fingerprint_cache.json` | `cache_enabled: true` and `samples`: `output_bytes`, `calls`, `read_units`; exactly one window each for 36, 1024 and 16384 bytes. Include cache refresh read units in the window. |

The canary report distinguishes detection from completed burns and reports latency only for
completed burns. Reference exposure has no rate when no recognized credentials were observed.
Cache measurements report DynamoDB read cost, not an entire infrastructure cost. Other cost
inputs retain their stated assumptions and the pricing date in `cost.py`. Owner-produced
observations are supplied evidence, not independently verified by the aggregator.

The destination audit uses this process's configured proxy endpoints. The default attacker
sink counts as readable; unverified application endpoints remain unverified. It does not load
a deployed proxy's environment automatically or claim the target of zero is met.

New load runs split post-revocation calls into refused, throttled, succeeded and other errors,
for revoked and unaffected agents separately. Latency includes successful calls only; no
successful calls produces null percentiles. HTTP 429 is recorded at the Gateway endpoint;
the exact originating service quota remains unverified until service evidence identifies it.
The configured concurrency is a load setting, not proof of an AWS quota. Historical reports
retain their original grouping and are labelled legacy when rendered.

Live measurements incur charges and change shared state. Coordinate them with Oliver and the
integration owner. After the fingerprint-cache change is deployed, collect the three output
sizes and full Gateway metrics together; do not reuse pre-cache inputs as post-cache results.
