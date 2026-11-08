# Burn dashboard

## What it does

Shows live agent status, credential exposure and containment activity with agents and events side by side.
Burn an agent and inspect its CloudTrail history alongside blocked events from real traffic.
The **Instances by role** panel groups sessions under their roles, with parent/child lineage,
status, last seen and exposure counts. Filter by role, status, agent, session or owner to inspect larger swarms.
Code-managed roles are labelled read-only, and scenario roles retain their names.

Use **Add agent** to create a dashboard-managed role with tools from the MCP contract,
credential types, message/spawn permissions, optional egress hosts and an owner. Choose the
join token's uses and lifetime, copy the `burn run` command, then continue to the waiting view.
The token is shown only in that view and discarded when continuing or closing; save the command
before leaving it. The waiting view changes to connected when a new instance joins.
Existing dashboard-managed roles have a **New token** action. Both **Burn instance** and
**Burn role** require confirmation; burning a role revokes all its active instances.

## How it works

- Polls the Burn API every second with an incremental cursor; a reducer deduplicates events by `event_id`.
- Agent selection loads its exposure ledger, refreshed every five seconds.
- One roles request and one instances request refresh the entire role panel every five seconds,
  including the waiting view, with cancellation and no overlapping requests.
- Confirming Burn highlights the card with returned rotation/flag counts for ten seconds; full references remain below the ledger.
- A revoked agent's CloudTrail timeline refreshes every ten seconds.
- Every response is checked for `x-burn-mock: true`; once detected, a full-width red banner stays until reload.

## Key files

| File                 | Purpose                                                               |
| -------------------- | --------------------------------------------------------------------- |
| `src/contracts.ts`   | TypeScript shapes matching the API and event contracts                |
| `src/api.ts`         | Requests, demo-token header, timeouts and response-header observation |
| `src/events.ts`      | Deduplication, cursor handling and newest-first ordering              |
| `src/polling.ts`     | Polling with cancellation and no overlapping requests                 |
| `src/App.tsx`        | Agent selection and containment state                                 |
| `src/components/`    | Agent cards, confirmation, ledger, events and timeline                |
| `src/MockBanner.tsx` | Persistent mock-data indication                                       |
| `src/styles.css`     | Responsive dark presentation layout                                   |

## How to test it

Requires Node 24 and npm. From this folder:

```sh
npm ci
cp .env.example .env.local
```

Set these local fields, then run `npm run dev` and open http://localhost:5173 (the only origin the Burn API accepts; the dev server listens on localhost only and will not move to another port):

| Variable               | Meaning                                     |
| ---------------------- | ------------------------------------------- |
| `VITE_BURN_API_URL`    | Burn API base URL, without an endpoint path |
| `VITE_BURN_DEMO_TOKEN` | Token sent as `x-burn-demo-token`           |

For local development, start `mocks/burn_api_mock.py` from the repository root according to its README.
Use its local URL (port 8700) for the Burn API URL. Generate a local test token if needed; the mock accepts any token.
Local configuration stays in the gitignored `.env.local`. Restart Vite after changing it.

```sh
npm test
npm run build
```

Tests cover reducer updates and cursor preservation, request headers and routes, persistent mock detection,
Burn confirmation and timed card feedback, nullable metadata, and blocked handoff/egress events.
Enrollment tests cover role creation, token display/copy/dismissal, retries and connection detection.
Role panel tests cover lineage across roles, filtering 25 instances, burn confirmations and partial failures,
and the shared polling limits.
`npm run preview` serves the production build. The Burn API must allow the dashboard's origin,
successful unauthenticated OPTIONS preflights, `x-burn-demo-token` and `Content-Type`.
Development services expose `x-burn-mock` in `Access-Control-Expose-Headers` so the browser can detect mock data.

## FAQ

**Does the browser receive credential values?** The dashboard reads agent credential references and metadata.
Creating a join token returns its value once so you can copy the join command. It stays in the open
dialog's memory and is never written to localStorage, the URL or console logs. Continuing or closing
discards it; copying writes the command to your clipboard. Blocked handoff and egress events describe
stopped traffic without exposing agent credential values.

**Why is the timeline empty after Burn?** CloudTrail history can lag several minutes.
The dashboard keeps polling every ten seconds and shows the API's lag note while waiting.

**What does MOCK DATA mean, and where does the token live?** At least one API response declared itself a development mock.
Switch URLs and reload to start a new connection. Vite embeds `VITE_` values in the browser bundle, including the demo token;
this is a demo access control, so serve the dashboard only to the intended audience and keep builds containing it out of git.
