# Burn dashboard

## What it does

Shows live agent status, credential exposure and containment activity with agents and events side by side.
Burn an agent, inspect its CloudTrail history, or exercise the egress proxy with the labeled test tool.

## How it works

- Polls the Burn API every second with an incremental cursor; a reducer deduplicates events by `event_id`.
- Agent selection loads its exposure ledger, refreshed every five seconds.
- Confirming Burn highlights the card with returned rotation/flag counts for ten seconds; full references remain below the ledger.
- A revoked agent's CloudTrail timeline refreshes every ten seconds; the separate test tool retries an unavailable service every thirty seconds.
- Every response is checked for `x-burn-mock: true`; once detected, a full-width red banner stays until reload.

## Key files

| File                 | Purpose                                                               |
| -------------------- | --------------------------------------------------------------------- |
| `src/contracts.ts`   | TypeScript shapes matching the API and event contracts                |
| `src/api.ts`         | Requests, demo-token header, timeouts and response-header observation |
| `src/events.ts`      | Deduplication, cursor handling and newest-first ordering              |
| `src/polling.ts`     | Polling with cancellation and no overlapping requests                 |
| `src/App.tsx`        | Agent selection and containment state                                 |
| `src/components/`    | Agent cards, confirmation, ledger, events, timeline and test tool     |
| `src/MockBanner.tsx` | Persistent mock-data indication                                       |
| `src/styles.css`     | Responsive dark presentation layout                                   |

## How to test it

Requires Node 24 and npm. From this folder:

```sh
npm ci
cp .env.example .env.local
```

Set these local fields, then run `npm run dev` and open the URL Vite prints:

| Variable               | Meaning                                     |
| ---------------------- | ------------------------------------------- |
| `VITE_BURN_API_URL`    | Burn API base URL, without an endpoint path |
| `VITE_BURN_DEMO_TOKEN` | Token sent as `x-burn-demo-token`           |
| `VITE_LEAKTEST_URL`    | Leak-test service base URL                  |

For local development, start `mocks/burn_api_mock.py` from the repository root according to its README.
Use its local URL (port 8700) for both service URLs. Generate a local test token if needed; the mock accepts any token.
Local configuration stays in the gitignored `.env.local`. Restart Vite after changing it.

```sh
npm test
npm run build
```

Tests cover reducer updates and cursor preservation, request headers and routes, persistent mock detection,
Burn confirmation and timed card feedback, and custom leak-test submission. `npm run preview` serves the production build.
Both services must allow the dashboard's origin and successful unauthenticated OPTIONS preflights.
The Burn API accepts `x-burn-demo-token` and `Content-Type`; the separate test tool accepts `Content-Type`
and does not receive the demo token. Services expose `x-burn-mock` in `Access-Control-Expose-Headers`
when used so the browser can detect development data.

## FAQ

**Does the browser receive credential values?** No. The dashboard reads credential references and metadata.
The leak-test service retrieves credentials internally. A custom test payload is cleared from the form when sent and is never saved locally.

**Why is the timeline empty after Burn?** CloudTrail history can lag several minutes.
The dashboard keeps polling every ten seconds and shows the API's lag note while waiting.

**What does MOCK DATA mean, and where does the token live?** At least one API response declared itself a development mock.
Switch URLs and reload to start a new connection. Vite embeds `VITE_` values in the browser bundle, including the demo token;
this is a demo access control, so serve the dashboard only to the intended audience and keep builds containing it out of git.
