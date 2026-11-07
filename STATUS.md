# Burn build status

## Phase: 0   Last integration run: none yet

| Lane | Owner agent | Status | Blocked on | Next |
|---|---|---|---|---|
| L0 Foundation | orchestrator | in progress | none | contracts + mocks PR |
| L1 Identity + Gateway | L1 agent | spike running | none | answer the interceptor hour-one check |
| L2 Credentials | not started | waiting | L0 contracts | |
| L3 Detection | not started | waiting | L0 contracts | |
| L4 Agents + scenario | not started | waiting | L0 contracts | |
| L5 Dashboard | not started | waiting | L0 contracts | |
| L6 Burn API + integration | not started | waiting | L1, L2 | |
| L7 Eval + scale | not started | waiting | L3, L6 | |

## Hour-one checks (2026-11-07, 12:00 PT)

- [x] Bedrock Claude via cross-region inference profile: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
      works with temperature 0. Newer models are denied by the event account's model allowlist.
- [x] AgentCore Gateway control plane reachable; `create-gateway` supports REQUEST/RESPONSE
      interceptors and a JWT authorizer with `allowedScopes`.
- [ ] Post-call (RESPONSE) interceptor receives the full tool response (L1 spike running).
- [x] IAM user + access key create / deactivate / delete works.
- [x] Secrets Manager create / update works.

## Decisions (date, decision, why)

- 2026-11-07: Region is us-east-1 only. The event account denies every other region.
- 2026-11-07: Agent model is Claude Haiku 4.5 through the `us.` inference profile. It is on the
  account allowlist and accepts temperature 0 (the spec asks for deterministic agents).
- 2026-11-07: Docker runtime is Colima (no Docker Desktop on the build machine).
- 2026-11-07: Secret gate is gitleaks, both as a pre-commit hook and as a CI check on every PR.

## Requests between lanes

(none yet)

## Known issues

(none yet)
