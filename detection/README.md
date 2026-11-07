# Detection

## What it does
Detects registered credentials and common credential formats in tool output and outbound traffic.
Reports fingerprints and attribution without returning or logging secret values.

## How it works
- `Scanner(key=..., lookup=...)` uses only the Python standard library and `burn_contracts`.
- One batched lookup compares exact token fingerprints and lowercase eight-character shingles.
- Decoding covers URLs/query strings, base64, hex, JSON/unicode escapes, separators and reversal.
- A match needs an exact fingerprint or 60% of a registered part's distinct shingle indices.
- `SessionWindow()` combines evidence from eight requests within 30 seconds; it retains no payloads.

## Key files
- `burn_detect/scanner.py`: `Scanner` and immutable `Finding` with the shared interface.
- `burn_detect/normalize.py`: bounded decoding and candidate extraction.
- `burn_detect/window.py`: per-session shingle evidence with count and time limits.
- `tests/test_scanner.py`: freshly generated credential tests independent of external evaluation.

## How to test it
```bash
cd detection
uv run --python 3.12 pytest -q
uv run --python 3.12 python scripts/benchmark.py
```

```python
from burn_detect import Scanner, SessionWindow

# lookup accepts set[str] and returns {fingerprint: burn_fingerprints_row}.
scanner = Scanner(key=fingerprint_key_bytes, lookup=lookup)
findings = scanner.scan(text, window=SessionWindow())
```

Reuse one window per session. A `Finding` contains `secret_ref`, `fingerprint`, `secret_type`,
`owner`, `owner_agent_id`, `kind`, `encoding`, and `coverage`. Rotated rows are checked too.
Shingle evidence is grouped by secret, part and version, so unrelated parts never add together.

## FAQ
**Why 60% coverage?** A 32-character random body has 25 shingles. Splitting it into two halves
reveals 18 distinct shingles (72%); one eight-character fragment reveals only 4%. The threshold
allows useful split-request detection without treating every short fragment as a full credential.

**Does this catch every leak?** No. Novel transforms, very small fragments and decoding beyond
two layers can escape detection. An outbound host allowlist is the backstop. Payloads larger
than 256 KiB raise an error for callers to reject. Runtime work is bounded to 96 variants.
The 10 KiB prose benchmark takes about 4 ms locally; high-entropy input takes about 42 ms,
so the 20 ms target is not met for every payload. These timings exclude remote lookups.

**Can this run in Lambda?** Yes. `burn_detect` has no third-party runtime dependencies beyond
the shared stdlib-only `burn_contracts` package. Optional service dependencies are separate extras.
