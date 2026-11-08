# docs

## What it does

Explains Burn as a whole: how the pieces fit, how to operate it, and how to check its behavior.

## How it works

Each component folder documents itself in its own README. The documents here cover what spans
components.

## Key files

- `architecture.md`: components, the four core flows (session, tool call, exit check, burn),
  data stores and least-privilege identity.
- `runbook.md`: deploy, run the demo, reset, checks, cost and teardown.
- `demo-script.md`: a neutral walkthrough of commands and the behavior they exercise.

## How to test it

Run `make help` to list the repository's operational targets. `make e2e` checks the containment
flow against a deployed stack and changes shared state. The local Docker containment check
uses an AWS emulator; it does not validate a deployed account.

## FAQ

**Where are the measured numbers?** `eval/RESULTS.md` and `eval/results.json` are the published
aggregate. The original deployed measurements are preserved under `eval/reports/initial-deployed.*`.
Generate a separate draft report under `eval/out/` as described in `eval/README.md`. Only replace
published artifacts after reviewing every layer's source, scope, sample counts and assumptions.
A missing layer measurement is not a passing result.
