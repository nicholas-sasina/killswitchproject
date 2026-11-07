# Eval

## What it does
Measures Burn honestly: how many planted credentials the exit check catches, how often normal
traffic is wrongly flagged, and what a million tool calls cost on AWS list prices.
It measures `burn_detect` as it is; nothing here tunes a detector.

## How it works
- `payloads/credentials.py` mints fresh test credentials on every run (API key, AWS-shaped key id and secret, connection string) and registers their fingerprints in memory with the same rows the minter writes.
- `payloads/forms.py` defines 31 ways to smuggle a value out (encodings, separators, splits across requests, partial leaks). Split forms run through one `SessionWindow`.
- `catch_rate.py` runs every form for every credential type, 10 trials by default, and records "named" (the right credential identified) and "blocked" (any finding).
- `false_positives.py` runs 31 normal payload types against a registry of decoy credentials.
- `cost.py` multiplies list prices (with source URLs) by per-call usage; assumed usage is labelled.

## Key files
- `run_offline.py`: runs everything, writes `results.json` and `RESULTS.md`.
- `payloads/`: credential generators, smuggling forms, normal payloads.
- `catch_rate.py`, `false_positives.py`, `cost.py`.
- `out/` (gitignored): raw output.

## How to test it
```bash
cd eval
uv run pytest -q
uv run python run_offline.py --trials 10
```

## FAQ
**Why not just report one catch-rate number?** One number hides the shape. Some forms (plain, any base64, hex, URL-encoding) are always caught; others (gzip, base32, rot13, junk characters, partial leaks) are not. `RESULTS.md` lists every form so the limits are visible.

**Is the eval tuned to the detector?** No. The forms were fixed before the first run, credentials are random each run, and the detector is not modified. Misses are reported as misses, with the outbound allowlist as the backstop.

**Where do cost numbers come from?** AWS list prices fetched on the date in `results.json`, each with a source URL. Per-call usage that has not been measured on the deployed Gateway is marked `assumed`.
