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
- AWS measurements use throwaway `agent-eval-<random>` badges and sessions through the deployed Gateway: `latency_gateway.py` (client round trip plus Lambda REPORT durations from CloudWatch) and `loadtest.py` (asyncio + httpx). Their cleanup is part of the live run.
- `latency_scan.py` measures the scanner locally. `proxy_overhead.py` compares direct and proxied requests in Docker against a local AWS emulator, not the deployed Gateway.
- `cost.py` multiplies list prices (with source URLs) by per-call usage; assumed usage is labelled.

## Key files
- `run_offline.py`: runs the offline checks, merges any live output from `out/`, writes `results.json` and `RESULTS.md`.
- `ddb_reads.py`: DynamoDB read/write units per call from CloudWatch over a load-test window, plus offline counts of fingerprints requested for larger outputs.
- `capture_cache_cost.py`: validates a plan for three completed post-cache runs, reads CloudWatch capacity, and writes private measurement evidence.
- `quota_capture.py`: reads applied Service Quotas and records candidate limits without attributing a throttled request to one of them.
- `probes/`: runs actual Gateway response or canary handlers from an explicit source checkout against local AWS emulation.
- `latency_gateway.py`, `latency_scan.py`, `loadtest.py`, `proxy_overhead.py`, `live/`: live and latency measurements.
- `payloads/`: credential generators, smuggling forms, normal payloads.
- `catch_rate.py`, `false_positives.py`, `cost.py`.
- `out/` (gitignored): raw output.

## How to test it
```bash
cd eval
uv run pytest -q
uv run python run_offline.py --trials 10 --output-dir out/current
uv run python latency_scan.py
# local containers (requires Docker; no real AWS account)
uv run python proxy_overhead.py --n 200
# paid AWS measurements: run only on a stack you operate
uv run python latency_gateway.py --calls 200
uv run python loadtest.py --agents 50 --calls 5 --think 0.3 --revoke 3   # small by default; see the cost warning
uv run python run_offline.py --output-dir out/current   # combine the available evidence
```

## Published and historical measurements

`RESULTS.md` and `results.json` hold the published aggregate. Read each layer's source commit,
scope, observations and simulated steps; one report can combine measurements of different
source checkouts. A local or staged-source measurement does not establish deployed behavior.

The original deployed measurements, generated at commit `2c1d57c`, are preserved in
[`reports/initial-deployed.md`](reports/initial-deployed.md) and
[`reports/initial-deployed.json`](reports/initial-deployed.json). The following scale numbers
come from three runs against that historical Gateway deployment. The first two give each agent its own badge:
- 50 agents x 5 calls: 250 calls, 0 errors, about 62 calls/s, p50 253 ms, p95 360 ms.
- 40 agents x 5 calls with 3 sessions revoked mid-run: every one of the 15 calls the revoked agents made after the revoke was refused, and all 185 calls from the other agents succeeded.

- 1,000 simulated agents x 5 calls sharing 100 badges (agent i uses badge i % 100), concurrency 50, 10 of the 100 badges revoked mid-run (about 100 agents lose access). Command: `uv run python loadtest.py --agents 1000 --calls 5 --badges 100 --concurrency 50 --revoke 10`.
  5,000 tool calls in 41.8 s (about 120 calls/s); calls that were served had p50 227 ms and p95 810 ms.
  4,593 succeeded, 262 were refused after the revoke, and 145 got HTTP 429 at the Gateway endpoint. Concurrency was 50; this does not identify the exact service quota or its value. No other errors were recorded.
  After the revoke, 262 of 281 calls from revoked agents were refused (the other 19 were throttled with 429, none succeeded) and 2,238 of 2,364 calls from the other agents succeeded (the rest were 429).
  All 100 badges, sessions and test events were deleted afterwards and checked.

## Historical uncached read costs
The historical uncached Gateway response step looked up every candidate fingerprint. Over the
1,000-agent run with `read_ticket` (a roughly 36-byte output), the report measured 15.9 read units
per call and 0.07 write units. Larger-output figures in that report are estimates from scanner
candidate counts, not deployed 1 KiB or 16 KiB measurements. The CloudWatch metrics include
other account traffic in the window. New cache code needs fresh measurements, including cache
refreshes; candidate counts alone cannot establish its DynamoDB cost.

## Cost warning
Every badge is one Cognito app client and one token request, billed at $0.00225 each, and every call also costs Gateway, Lambda and DynamoDB usage. `loadtest.py` defaults to 50 agents x 5 calls and refuses more than 20,000 calls, but a large run still costs real money (1,000 badges is about $2.25 in token requests alone; the 1,000-agent run above used 100 badges). Run big tests only deliberately.

## FAQ
**Why not just report one catch-rate number?** One number hides the shape. Some forms were
caught in every observed trial, while others were only partly caught or missed. That is a
result for the sampled corpus, not a guarantee for every payload. `RESULTS.md` lists each form.

**Is the eval tuned to the detector?** No. The forms were fixed before the first run, credentials are random each run, and the detector is not modified. Misses are reported as misses, with the outbound allowlist as the backstop.

**Where do cost numbers come from?** `cost.py` records its pricing date and source URLs.
Per-call usage that has not been measured is marked `assumed`. These are estimates; price
inputs and observed usage must be reviewed before making a current deployment-cost claim.

**Is there a no-Burn latency baseline?** The Gateway report uses the interceptor's Lambda
durations and share of the client round trip; it has no deployed no-interceptor baseline.
The local proxy test compares direct and proxied requests to the sink. Its fingerprint
lookups use an AWS emulator, so its timings exclude real DynamoDB network latency.

**Do simulated agents share badges?** By default each has its own. `--badges N` makes fewer real badges and spreads the agents over them, which avoids Cognito's client-creation rate limit; the Gateway path is identical and the results state how many real badges were used.

## Separate layers and held-out tests

Generate a draft offline report without replacing the published aggregate:

```bash
uv run --python 3.12 python run_offline.py --trials 10 --output-dir out/current
```

The report separates the established scanner corpus from the eight forms in
`payloads/heldout.py`. That corpus was introduced against detector commit `d618897`, before
the egress policy changes. Its source hash is recorded in each report. Do not tune
detectors against it; when decoders are added, report this corpus separately and
freeze a new unseen corpus for future tuning. It is developer-authored evaluation, not an
independent audit. Scanner results are offline measurements, not end-to-end protection rates.

References, canaries and post-cache reads require observations from the code path being measured.
Missing observations are **unmeasured**, never zero or a passing target. Use `--evidence-dir`
to select a private evidence directory (default `out/`). Keep inputs and draft reports there;
never commit raw secrets or fabricate observations to complete a report.

Use a fresh directory for each run or source snapshot. The local examples below use
`out/local-01`; replace that name consistently on a later run. Keep the files from an earlier
run intact instead of overwriting them or accidentally combining them with new observations.

Each evidence file includes `source_commit` (a 7–40 character lowercase Git hash), a
timezone-bearing `measured_at`, and the observations below. Reference/canary evidence also
carries `scope`: `local-product-code`, `local-emulator`, or `deployed-aws`, with `notes` describing
simulated steps. Older owner-supplied artifacts without a scope remain environment-unspecified.
Never treat a local measurement as a deployed result.

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

## Measure actual handlers locally

From `eval/`, measure the Gateway response interceptor in the current checkout:

```bash
uv run --locked --python 3.12 --extra probes python -m probes references --source-root .. --trials 5 --output out/local-01/references.json
```

The probe generates private test credentials, supplies a synthetic tool response, and runs
the actual response handler with AWS services emulated in memory. It counts recognized raw
values remaining in the output the agent would receive. It does not replace or simulate a
reference implementation to obtain a passing result. If the selected source still passes
recognized values through, the measured exposure target fails.

To evaluate a staged reference implementation without merging it, select its detached source
checkout with `--source-root`. Set `BURN_REFERENCE_PROBE_SOURCE` to that checkout when running
the evaluation test suite. The optional test checks replacement tokens and ledger rows marked
`referenced` with the expected `source_ref`, in addition to zero remaining raw values and no
withheld responses. These response checks do not prove tool-side resolution or deployed behavior.

Canary measurements require a source checkout containing the canary owner's implementation
and `burn_api/scripts/canary_harness.py`. To evaluate that reviewed branch without merging it,
prepare a detached checkout from the repository root:

```bash
git fetch origin ws/canaries
git worktree add --detach .run/canary-source origin/ws/canaries
```

Record the checkout's commit with `git -C .run/canary-source rev-parse HEAD`. Fetching a branch
later does not update an existing detached checkout. Use another checkout path for a different
reviewed source snapshot rather than assuming an old path contains the newest implementation.

Then, from `eval/`:

```bash
uv run --locked --python 3.12 --extra probes python -m probes canaries --source-root ../.run/canary-source --trials 5 --negative-checks --output out/local-01/canaries.json
uv run --python 3.12 python run_offline.py --evidence-dir out/local-01 --output-dir out/local-01/report
```

This executes the selected source's provider, Burn API and minter handlers against moto.
Enrollment, canary planting and exposure, the leak and attacker decoding are simulated by
the owner's harness. Incident dispatch is synchronous in process. The resulting timings
exclude real AWS networking and asynchronous Lambda dispatch, so they are local handler
measurements, not deployed end-to-end latency.

`--negative-checks` applies only to canaries. It adds duplicate replay and an injected rotation
failure as separate observations; they are excluded from the ordinary-use detection-rate
denominator. Revoking a session while its old credentials remain usable is incomplete
containment, not a successful credential rotation.

For a canary source that supports origin proofs, role policy and exposure-ledger attribution,
also pass `--policy-checks`. This measures four alert-only cases separately: presentation
through the owning session, through a different Burn session, outside use with `auto_burn`
disabled, and a canary exposed to an additional holder absent from the scenario agent list.
The report records whether sessions and old API keys remain active, rotations, event counts,
and incident outcomes. These cases do not enter the positive-use detection denominator.
The harness supplies origin headers and ledger rows directly; these are handler-policy
observations, not a deployed proxy-to-provider flow. An older source without these hooks
fails the requested probe instead of producing a passing observation.

Both probes run in a subprocess with outbound socket connections denied. They write private
observation files and print counts and provenance rather than credentials. Each artifact
identifies its source commit and source hashes; inspect the dirty-source flag and the scope
before publishing. The references and canary probes can measure different commits. Neither
asserts that the selected source is merged or deployed.

To include the selected canary implementation in the probe test suite, run from `eval/`:

```bash
BURN_CANARY_PROBE_SOURCE=../.run/canary-source uv run --locked --python 3.12 --extra probes pytest -q tests/test_measurement_probes.py
```

Without the explicit source path, the optional canary-source tests skip. Unit-test success
and measured handler behavior remain distinct from a deployed end-to-end check.
The suite also checks the current proxy's signed session-origin header against the selected
provider's verifier, including expiry, session tampering, and the wrong signing key. That
protocol check requires a selected source containing `minter/lambdas/provider/session_proof.py`.

The report defaults to this process's configured proxy endpoints. To audit an explicit
configuration, create a private JSON file with exactly `gateway_url`, `provider_url`,
`cognito_token_url`, and `sink_url` string fields. Empty strings disable optional endpoints.
URLs must omit credentials, query strings, and fragments. An optional session file contains
only `{"egress": null}` or `{"egress": ["exact.hostname"]}`, never a full session record.

```bash
uv run --python 3.12 python destinations.py --settings-json out/private-settings.json --session-json out/private-egress.json --output out/local-01/destinations.json
uv run --python 3.12 python run_offline.py --evidence-dir out/local-01 --destination-evidence out/local-01/destinations.json --output-dir out/local-01/report
```

The audit makes no network requests and writes mode-600 output only inside ignored `eval/out`.
Reports import validated hostnames, counts, and provenance hashes. Neither command loads a
deployed proxy's environment automatically. The configured attacker sink counts as readable;
AWS and application endpoints remain unverified until actual capability and access-control
evidence establishes what an outsider could read. An authenticated hostname alone does not
establish that it is safe.

`BURN_SINK_URL=''` explicitly removes the test sink from the proxy's deployment allowlist and
removes its private-address exception. The default still includes the demo sink. Run the
audit with the same configuration as the proxy being evaluated. Removing the sink does not
verify the remaining destinations. Valid session egress lists always preserve Burn's configured
Gateway, which enforces tools per role. An empty list is Gateway-only when it is configured,
or deny-all otherwise; neither state certifies a zero-exposure deployment.

New load runs split post-revocation calls into refused, throttled, succeeded and other errors,
for revoked and unaffected agents separately. Latency includes successful calls only; no
successful calls produces null percentiles. HTTP 429 is recorded at the Gateway endpoint;
the exact originating service quota remains unverified until service evidence identifies it.
The configured concurrency is a load setting, not proof of an AWS quota. Historical reports
retain their original grouping and are labelled legacy when rendered.

## Investigate throttling on an authorized live run

Load reports retain bounded, allowlisted AWS exception-type labels from HTTP 429 responses.
They do not store response bodies or request ids. The endpoint where a 429 was observed and
an exception label do not by themselves establish which underlying service limit was hit;
`originating_service` remains unset until supporting evidence identifies it.

An authorized operator can inspect applied quotas without starting a load test, deploying a
stack or requesting a limit change. From `eval/`, list the service names and codes first:

```bash
uv run --python 3.12 python quota_capture.py --list-services
```

Set `SERVICE_CODE` to the exact code returned for the service being investigated; do not infer
an AgentCore code from its display name. Then capture a private snapshot:

```bash
uv run --python 3.12 python quota_capture.py --service-code "$SERVICE_CODE" --output out/live-01/service-quotas.json
```

The snapshot records applied quota names, codes, values and units, with no account identifiers
or resource ARNs. It remains `causal_attribution: unverified`: a configured quota is a candidate
limit, not proof that it caused a particular response. Correlate the run window with the relevant
service's metrics or other verified evidence before making that claim. The quota collector makes
AWS read requests and is separate from the no-network local probes.

Live measurements incur charges and change the deployed stack's state. Collect the three output
sizes and full Gateway metrics together on a stack with the fingerprint cache deployed; do not
reuse pre-cache inputs as post-cache results.

## Capture post-cache read costs

Local operation counts for the combined cache and reference-replacement path are in
[the local cache report](reports/local-cache.md). They do not replace AWS read-unit data.

Before starting a live measurement, verify the deployment:

1. Record the exact deployed source revision and Lambda package hashes. An open PR or
   updated CloudFormation stack timestamp does not establish which code is running.
   The interceptor package must include the intended `redact.py`, `resolve.py`,
   `record.py` and fingerprint-cache implementation; the minter must bump the version
   item on registration, rotation and deletion.
2. Verify the interceptor can `GetItem` and `Scan` `burn-fingerprints`, in addition to
   its fallback `BatchGetItem` permission. A functioning fallback can hide a missing
   cache permission; successful calls alone do not establish that the cache is used.
3. Wait for `burn-exposures`' `by_source` index to be `ACTIVE` and verify the Burn API
   can query it before evaluating burn-by-source. This index is a separate prerequisite
   for that feature, not proof that the cache is active.
4. Run the end-to-end check on disposable evaluation agents. Verify revoked
   credentials fail and an unrevoked control still works. Verify reference tokens and
   ledger entries, not just absence of raw credentials, and exercise the intended
   request-side reference resolution when a tool declares it.
5. Audit the actual proxy settings and the evaluation role's egress. Test each allowed
   destination's capabilities with the evaluation identity before declaring it
   inaccessible to outsiders. A configured test sink counts as readable. Unknown
   capabilities remain unknown; removing all useful destinations is not success.

Do not run `make reset`, `make demo` or teardown to prepare a measurement on a shared stack:
those commands can affect other agents. Use only run-owned evaluation resources and
their cleanup. If deployment or permissions are missing, stop before collecting a
post-cache result; preserve the existing report's unmeasured status.

First verify which cache-enabled source commit is deployed, then run the 36 B, 1 KiB and
16 KiB output measurements. Record the actual completed call count
and CloudWatch window for each run in a fresh private directory, for example
`out/cache-01/windows.json`:

- `source_commit`: the deployed Git hash, and `cache_enabled: true` confirmed by the owner.
- `samples`: exactly three entries, with `output_bytes` equal to 36, 1024 and 16384.
- Each entry has `calls`, `tool`, `start` and `end`. Times include a timezone, are aligned to
  whole minutes, and cover the completed run. Use separate, non-overlapping windows for the
  three sizes. `calls` is the number completed in that window,
  not the planned load-test count.

Then, from `eval/`:

```bash
uv run --python 3.12 python capture_cache_cost.py --plan out/cache-01/windows.json --output out/cache-01/fingerprint_cache.json
uv run --python 3.12 python run_offline.py --evidence-dir out/cache-01 --output-dir out/cache-01/report
```

The collector writes `out/fingerprint_cache.json` by default; `--output` selects another path.
It reads AWS metrics but does not deploy a cache or start a load test. Missing read-capacity
datapoints fail the collection rather than being reported as zero. Allow time for CloudWatch
publication and investigate missing metrics before trying again.

The sum covers the selected account tables and includes unrelated traffic and cache refreshes.
Dividing it by the supplied call count is an estimate over that window, not proof that every read
was caused by those calls. Keep that attribution limit beside published cost results.
