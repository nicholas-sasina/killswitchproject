# Lane guide (rules for every lane, human or agent)

Read first, in order: `docs/spec.md`, this file, `contracts/README.md`, then your lane brief.

## Git
- You work on your own branch in your own worktree. **Keep the commit count low**: one commit per
  coherent feature, with its code, tests and docs together (e.g. "feat(minter): issue and rotate
  per-agent API keys"), not one per file or per small step. A typical PR has 1-4 commits. Squash fixups
  before pushing. Every commit includes a test (unit test, or a script that proves the behavior).
  Conventional prefixes (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`); the message explains *why*.
- PRs are **squash-merged** by the orchestrator, so `main` gets one commit per PR. Write the PR title
  as a good commit subject. End every commit message with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or your own model/tool attribution).
- Push your branch and open a PR to `main` with `gh pr create` when a milestone works (you may open
  several PRs in sequence). PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
  or the equivalent for your tool. **Do not merge.** The orchestrator reviews, runs integration, merges.
- Before opening each PR: `git fetch origin && git rebase origin/main`, rerun your tests.
- Stay in your folders (your lane brief lists them). Need a change elsewhere, including `/contracts`?
  Write it under "Requests to other lanes" in your PR body and in your final report. Do not edit
  `STATUS.md` (the orchestrator owns it).

## Secrets (this repo is PUBLIC)
- Never print, log, or commit a secret value. Not in tests, not in fixtures, not in commit messages, not
  in PR bodies. Log `secret_ref` or a fingerprint prefix instead.
- Tests generate fake values at runtime (`secrets.token_hex`, `os.urandom`). Never put a credential-shaped
  literal (an `AKIA...` string, an `mpk_live_...` string, a URL with a password) in the repo.
- The pre-commit hook runs gitleaks. **Never** use `--no-verify`. If it fires, remove the value.
- Local values (URLs, tokens, minted creds) live in `.env` or `.run/` (both gitignored).

## AWS
- Region `us-east-1` only (everything else is denied). Credentials come from the default chain.
- Deploy ONLY your own stack: `cd infra && uv sync && npx aws-cdk@2 deploy <YourStack> --require-approval never`.
  CDK is already bootstrapped. `BurnData` (tables + system secrets) is already deployed.
- Never modify or delete resources you did not create. Pre-existing workshop resources (names starting
  `ws-`, `WS`, `CDKToolkit`) are off limits.
- No wildcard actions on execution roles. Scope resources by the fixed names in
  `contracts/python/burn_contracts/names.py` (e.g. `arn:aws:secretsmanager:us-east-1:<acct>:secret:burn/*`).
- Bedrock model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (allowlisted; temperature 0 works).
  Its IAM resources: the inference profile ARN and `arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`.
- Values AWS generates at deploy time (user pool id, gateway URL, API URLs) are published as SSM
  parameters under `/burn/<component>/<name>` (e.g. `/burn/gateway/url`). Consumers read them at runtime
  or via `make env`. Never hard-code them.
- Ask the orchestrator before anything that costs real money beyond normal demo usage.

## Python
- Python 3.12 via `uv`. Each folder has its own `pyproject.toml`. Use the contracts helpers via a path
  dependency: `burn-contracts = { path = "../contracts", editable = true }` under `[tool.uv.sources]`.
- Lambdas: runtime `python3.12`, stdlib + boto3 only, handler code in your folder, bundled with
  `infra/burn_infra/assets.py:lambda_asset(name, "<your/folder>", shared=("burn_contracts", ...))`.
- Build every event with `burn_contracts.events.make_event`. Every fingerprint with `burn_contracts.fingerprint`.
- Keep files small, names plain, comments about *why*. Judges will quiz humans on this code.

## No hard-coding (from spec section 0)
No agent ids, credential values, expected results or demo special cases in product code. Scenario data
lives in `/agents/scenarios/*.yaml`. Detectors never match demo strings.

## Shared interfaces between lanes

### `burn-scenario` table items (written by the L4 scenario loader)
`pk` = `meta` | `agent#<agent_id>` | `ticket#<id>` | `file#<path>` | `customer#<id>`; attribute `data`
holds that object exactly as in the scenario YAML (`meta.data` = `{name, description, agent_ids}`).
Readers: tool Lambdas (L1), Burn API sessions (L6), runner (L4).

### `burn_detect` library (owner L3, used by L1 gateway, L3 proxy, L6, L7)
```python
from burn_detect import Scanner, Finding
scanner = Scanner(key=fingerprint_key_bytes, lookup=lookup_fn)
# lookup_fn(fingerprints: set[str]) -> dict[str, dict]   rows from burn-fingerprints that exist
findings: list[Finding] = scanner.scan(text, window=None)     # window = per-session recent payloads (exit check)
# Finding fields: secret_ref|None, fingerprint, secret_type, owner, owner_agent_id,
#                 kind ('exact'|'shingle'|'format'), encoding ('plain','base64',...), coverage (0..1)
```
`burn-fingerprints` rows (written by the L2 minter): `fingerprint`, `secret_ref`, `owner_agent_id`
(`shared` for shared), `secret_type`, `kind` (`exact`|`shingle`), `part` (e.g. `value`, `access_key_id`,
`secret_access_key`, `random`), `index` (shingles), `n_shingles`, `version`, `status` (`active`|`rotated`).

### Egress proxy (owner L3, used by L4 compose and the leak test)
- Listens on `proxy:8080` inside compose (and `localhost:8080` on the host).
- Agents set `HTTPS_PROXY`/`HTTP_PROXY` = `http://<agent_id>:<session_id>@proxy:8080`. The proxy reads
  the session from proxy auth, refuses revoked/unknown sessions, applies the allowlist and exit check.
- mitmproxy CA cert is written to the shared volume at `/certs/mitmproxy-ca-cert.pem`; agents set
  `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` to it. Certs are never committed.

## README format (every top-level folder)
What it does (2 lines) / How it works (≤5 lines) / Key files / How to test it / If a judge asks... (2-3 Q&A).

## Final report (agents)
End your final message with exactly one envelope:
```
SUPERSET_WORKER_DONE
task: <lane id>
summary: <one-line outcome>
files: <paths>
checks: <commands and outcomes>
handoff: <PR urls, requests to other lanes, deployed stacks, next steps>
```
or
```
SUPERSET_WORKER_BLOCKED
task: <lane id>
reason: <specific blocker>
needs: <decision, access, or dependency>
```
