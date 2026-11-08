# Burn dashboard

## What it does

Three views in a sidebar, each a bookmarkable location.

**Overview** (`#/overview`) is the live fleet in one place. **Active agents** is a collapsible,
scrollable list of every agent running right now, scenario agents and swarm workers alike, built
from `/events` and `/instances` merged. It is a list of rows rather than a grid of cards, so it
stays readable at a hundred agents. Selecting a row fills the containment detail below it.
Burned agents leave the roster as soon as the burn is known and move to Contained, and the
**Instances by role** panel starts filtered to `active` (its Status filter still offers every
state). Finished instances are counted beside the roster and remain in **Instances by role**.
The agent you just burned stays in the containment detail so you can read the outcome without
changing view.

**Contained** (`#/burned`) holds the agents that were burned, newest first, with each one's
exposure ledger and the CloudTrail history of its AWS keys. Two actions rebuild an agent:
_Re-add with the same setup_ mints a fresh join token for the same role, so the new instance gets
identical tools and permissions with its own newly minted credentials, and _Copy setup to a new
role_ prefills the Add agent form from that role (its reference mode included) so you can change
tools, credentials, permissions, egress or canary settings before creating it. A scenario agent is
started by its scenario and the API refuses join tokens for scenario roles, so those offer only the
copy. Nothing the burned agent held is ever reused.

**Alerts** (`#/alerts`) shows only the events that need an operator, most serious first, with what
each one proves and what to do about it beside every field recorded for it. Severity reflects what
an event actually shows rather than how alarming it sounds: a canary credential being presented is
**Critical**, because nothing legitimate ever touches it; a credential stopped at the egress proxy
is **High**; a request the proxy refused only for its destination, port or size carries no
credential and is **Review**. Refusals say which kind they are: a burned agent turned away, an
unknown caller, or a call outside the agent's role. Each tally states what its tier means and
doubles as a filter. The navigation marker counts everything above **Noted**, so you can stay on
the overview and still see the queue filling. The list draws the first 60 alerts the filters leave,
with a control to show them all.

The **Instances by role** panel groups sessions under their roles, with parent/child lineage,
status, last seen and exposure counts. Filter by role, status, agent, session or owner to inspect larger swarms.
Code-managed roles are labelled read-only, and scenario roles retain their names.

Use **Add agent** to create a dashboard-managed role with tools from the MCP contract,
credential types, message/spawn permissions, optional egress hosts and an owner. Choose the
join token's uses and lifetime, then use **Copy token**. The token appears separately from the
join commands, which use a hidden `read -rs BURN_JOIN_TOKEN` prompt or `pbpaste` with `--token-stdin`
to keep its value out of shell history and command-line arguments. For the clipboard command,
copy the command first, then use **Copy token** before running it. The token is shown once and
discarded when continuing or closing. The waiting view changes to connected when a new instance joins.
Existing dashboard-managed roles have a **New token** action. Both **Burn instance** and
**Burn role** require confirmation; burning a role revokes all its active instances.

## How it works

- Polls the Burn API every second with an incremental cursor; a reducer deduplicates events by `event_id`.
- Alerts are derived from that same stream, so the alerts view issues no requests of its own.
- The fleet is one list built from `/events` (the scenario agents) and `/instances` (every
  instance that joined), keyed by session, because one agent name can have many sessions with
  different outcomes. A session only moves forward, so its status is whichever source is further
  along, and sessions a burn from this page revoked are marked at once rather than on the next
  instance poll. Contained matches each burn to the session it revoked.
- Agent selection loads its exposure ledger, refreshed every five seconds.
- The exposure ledger and the CloudTrail timeline are collapsible and start closed, both with
  scrolling bodies. Closing means different things on purpose: the ledger keeps polling, because
  its heading summarises what it found and that has to stay true, while CloudTrail stops entirely,
  because a closed timeline says nothing and its query is the expensive one. CloudTrail opens
  itself when a burned agent is selected.
- CloudTrail groups calls by key so repetition collapses into one line with a count, and draws at
  most eight rows per group behind a "show all" control.
- Theme follows the operating system's light or dark setting. There is no in-app switch: the
  machine already knows, and it tracks sunset and policy without being asked. An inline script in
  `index.html` resolves it before first paint so reloads do not flash, and a `matchMedia` listener
  follows later changes.
- The page runs one `/instances` poll every five seconds, shared by the roster, Contained and the
  **Instances by role** panel, and pauses it on Alerts. Each call scans the sessions and counts
  every instance's exposures, so it is the most expensive request the dashboard makes.
- One roles request every five seconds refreshes the role panel, including the waiting view, with
  cancellation and no overlapping requests. Contained loads roles every thirty seconds, and only
  once an agent is selected, to offer its rebuild actions.
- Confirming Burn highlights the card with returned rotation/flag counts for ten seconds; full references remain below the ledger.
- A revoked agent's CloudTrail timeline refreshes every ten seconds.
- Every response is checked for `x-burn-mock: true`; once detected, a full-width red banner stays until reload.
- `src/contracts.ts` is a hand-maintained mirror of `contracts/ledger-event.schema.json`,
  `contracts/burn-api.openapi.yaml` and `contracts/burn.schema.json`; change both sides together.
  An event action the mirror does not know yet still renders, as plain text in the stream, and is
  not treated as an alert.

## Key files

| File                             | Purpose                                                               |
| -------------------------------- | --------------------------------------------------------------------- |
| `src/contracts.ts`               | TypeScript shapes matching the API and event contracts                |
| `src/api.ts`                     | Requests, demo-token header, timeouts and response-header observation |
| `src/events.ts`                  | Deduplication, cursor handling and newest-first ordering              |
| `src/polling.ts`                 | Polling with cancellation and no overlapping requests                 |
| `src/App.tsx`                    | View switching, agent selection and containment state                 |
| `src/alerts.ts`                  | Which events are alerts, their severity, and what each one means      |
| `src/fleet.ts`                   | The merged fleet, contained agents, and rebuilding roles              |
| `src/router.ts`                  | Hash routing for the three views                                      |
| `src/theme.ts`                   | Follows the system light or dark setting                              |
| `src/components/`                | Agent cards, confirmation, ledger, events, alerts and timeline        |
| `src/components/Brand.tsx`       | The Burn mark, its tile variant and the wordmark                      |
| `src/components/Collapsible.tsx` | Panel that collapses to its heading                                   |
| `src/MockBanner.tsx`             | Persistent mock-data indication                                       |
| `src/styles.css`                 | Design tokens for both themes, and the responsive layout              |

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
and the shared polling limits. Fleet and alert tests cover merging the two sources, burns showing
before the next instance poll, per-session burn times, alert wording for each kind of block and
refusal, the alerts row cap, which rebuild actions each role gets, and one instance poll per page.
`npm run preview` serves the production build. The Burn API must allow the dashboard's origin,
successful unauthenticated OPTIONS preflights, `x-burn-demo-token` and `Content-Type`.
Development services expose `x-burn-mock` in `Access-Control-Expose-Headers` so the browser can detect mock data.

## FAQ

**Does the browser receive credential values?** The dashboard reads agent credential references and metadata.
Creating a join token returns its value once so you can copy it separately from the join commands. It stays in the open
dialog's memory and is never written to localStorage, the URL or console logs. Continuing or closing
discards it; **Copy token** writes only the token to your clipboard. Blocked handoff and egress events describe
stopped traffic without exposing agent credential values.

**Why is the timeline empty after Burn?** CloudTrail history can lag several minutes.
The dashboard keeps polling every ten seconds and shows the API's lag note while waiting. If you
collapsed the panel it is not polling at all; reopen it to resume.

**Why does the interface look like this?** Burn is an audit ledger with a kill switch, so it is
built like an incident sheet: ruled columns, hairline rules, monospace for anything you would
compare or quote, and one signal colour. Navigation sits in a sidebar so it never competes with a
view's own header, and each view opens with a title, a sentence and its figures rather than
dropping straight into a dense table. Ember means severity or destruction
and nothing else, so a filled ember control always deserves attention. Severity is written as
well as coloured, which keeps it readable for colour-blind operators and on a washed-out
projector. Both themes are audited to WCAG AA for text and 3:1 for control boundaries, so neither
is the degraded one.

**What does MOCK DATA mean, and where does the token live?** At least one API response declared itself a development mock.
Switch URLs and reload to start a new connection. Vite embeds `VITE_` values in the browser bundle, including the demo token;
this is a demo access control, so serve the dashboard only to the intended audience and keep builds containing it out of git.
