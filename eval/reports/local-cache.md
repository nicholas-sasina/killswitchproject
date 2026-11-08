# Local cache operation measurements

Measured 2026-11-08 UTC against clean product commit
`5fb397170442b36dc85c573553f245c64a648f21`. These are local moto
observations of the actual Gateway RESPONSE handler, including reference replacement
and its second scan. They are not deployed AWS measurements or dollar costs.

| Output size | Warm cache: GetItem | Warm cache: BatchGetItem | Fallback: GetItem | Fallback: BatchGetItem | Fallback: requested keys |
|---|---:|---:|---:|---:|---:|
| 36 B | 4 | 0 | 4 | 3 | 141 |
| 1 KiB | 4 | 0 | 4 | 25 | 2,365 |
| 16 KiB | 4 | 0 | 4 | 371 | 36,925 |

The warm-cache path performed no Scan during these measured calls. The fixture warms
the cache first; initial loads and version-change refreshes are tested separately and
are excluded from this table. The fallback fixture omits the version item. Its output
contains a generated credential and random text, so candidate counts can vary between
runs. Operations include the handler's session and reference work, not only the cache
version lookup. The earlier cache-only PR description's two-GetItem result does not
describe this combined reference-replacement path.

The cache and references suites passed together: **40 tests**. They cover flat warm
reads, fallback, version refresh, newly minted credentials, rotation visibility,
unreadable cache, replacement, fail-closed output withholding, and reference resolution.
Passing these tests does not establish the deployed IAM permissions or remote behavior.

Reproduce from a clean checkout of the source above, using a Python environment with
the checkout's contracts and detection packages, boto3, moto and pytest installed:

```bash
PYTHONPATH=contracts/python:detection:gateway/lambdas/interceptor \
  python -m pytest -q -s gateway/tests/test_fingerprint_cache.py gateway/tests/test_references.py
```

Keep this observation separate from `fingerprint_cache.json`, whose schema requires
actual AWS consumed-capacity measurements. Counts of API operations cannot establish
read units without item sizes, consistency, cache refreshes and actual service usage.
