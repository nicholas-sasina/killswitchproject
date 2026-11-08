# Burn console

The console is where people see what their agents are doing and burn one when it goes wrong. It
continues from the website: the same night stage, white cards, Geist type and one signal colour,
ember, which only ever means a burn.

Two words carry the model. An **agent** is one running copy of a program, with its own identity,
keys and canary, named `<role>-NN` in the order it started (`claude-07`). A **role** groups agents
and can narrow what they may do. Roles are optional and reusable: an agent connected without one
has Full access and is grouped under its harness (`claude`), and a role takes as many agents as
you connect to it.
A burned agent's replacement takes the next number. The console never says "instance" or
"session" in a list or heading; a session id appears only in an agent's Identity card.

## Pages

Each page is a bookmarkable hash route. Old links still work: `#/burned` opens Incidents,
`#/alerts` the Overview, and `#/overview?instance=<id>` that agent's page.

- **Overview** (`#/overview`): what is running now and what was burned today. The hero says the
  state in one line, with the running and burned-today counts. **Running now** has a row per role
  with a square per agent; a square dims briefly when its agent makes a call. Beside it: what
  needs a person (flagged shared keys, unknown callers, canary uses), today's burns and the live
  feed, each event written as a sentence.
- **Roles** (`#/roles`) lists every role, with a square per agent running or burned today, and
  says whether each has **Full access** (every tool, no narrowing) or is **Limited**. **New role**
  makes one: Full access unless narrowed to chosen tools, keys and websites, with its protections
  under More. A **role page** (`#/roles/<name>`) shows its agents by state (Running, Burned,
  Finished, All; the filter is kept in the address), with an agent another one started indented
  under it. Beside them: what the role may do, its protections, and its join tokens with their uses
  and expiry, which can be revoked there. **Connect more agents** adds agents to the role; **Burn**
  on a row burns one agent; **Burn every <role>** burns them all.
- **Agent** (`#/agents/<session_id>`): one agent's exposure ledger, its timeline, and a
  containment card that shows what a burn will do before it and ticks through what it did after.
  A burned agent also shows its replacement and its AWS keys' blast radius. **Replay the burn**
  replays the recorded checklist without a request (hidden with reduced motion).
- **Incidents** (`#/incidents`): every burned agent, newest first, with why it was burned, what
  Burn did and what is still open. The navigation shows an ember count of open incidents.
- **Credentials** (`#/credentials`): shared keys an agent saw, flagged for their owners, who
  **Rotate** them or **Dismiss** the flag with a reason.

**Connect an agent** in the navigation connects an agent from the harness you already use. Once
per machine: install the `burn` CLI, set the API and gateway URLs and the console's own sign-in
(`BURN_PEOPLE_DOMAIN`, `BURN_PEOPLE_CLIENT_ID`), and run `burn login`. Then
`burn run claude`, `burn run opencode`, `burn run codex` or `burn run python agent.py`; choosing a
role adds `--role <name>`. The dialog says what the agent gets (its own identity, keys and canary;
burning it also cuts off its model, except for Codex, which brings no model yet) and waits until
the first new agent connects. For CI, containers or swarms, a disclosure holds the join token flow:
pick or create a role and mint a token. The token is shown once, with Copy token, and the commands
never contain it: they read it with a hidden prompt or from the clipboard, so its value stays out
of shell history. A role page's **Connect more agents** opens the same dialog with that role
chosen.

## Sign-in

Sign in through Cognito managed login using authorization code + PKCE S256 (`openid email profile`).
Set `VITE_BURN_PEOPLE_DOMAIN` and `VITE_BURN_PEOPLE_CLIENT_ID` alongside the API URL; register
`http://localhost:5173/` as the callback and sign-out URI. The callback checks state and strips the query.
Verifier/state and ID/access/refresh tokens stay in this tab's sessionStorage. The person menu shows
the signed-in email and Sign out. API calls use the ID bearer; a 401 refreshes and retries once, then
returns to Sign in on failure. Sign out clears Burn's sign-in state and uses managed logout.

For local mocks only, set `VITE_BURN_LOCAL_DEV=1` and `VITE_BURN_DEMO_TOKEN`. This bypasses
people sign-in and uses the demo header; otherwise that header is never sent. A Local development
screen asks once per tab before the console opens, so local runs show the same hand-off as a real
sign-in, and Sign out returns to it.

The sign-in screen continues from the website: the same night stage and dot grid, one white card,
and "Back to the website" to `VITE_SITE_URL`. When someone signs in, the card shrinks into the
console's brand as the console fades in (a view transition; instant where the browser has none or
reduced motion is on).

## How it works

- **One data layer** (`src/data/ConsoleData.tsx`) polls each collection once for the whole console:
  `/events` every second, and `/instances`, `/roles` and `/flags` every five seconds. Every page
  and the navigation read the same answer, so their counts agree. A page's own requests (an
  agent's ledger and blast radius, a role page's ledgers) run only while that page is open.
- **Today's history.** The first `/events` poll returns the newest 200 events, which on a busy
  swarm is minutes. The feed then pages forward from 24 hours ago, 500 events at a time, up to
  20,000, and keeps at most 25,000 events, so "burned today" and the last 24 hours are counted
  from real history. If the cap is reached, counts over events are shown as lower bounds.
- **One model** (`src/model/`) turns the fleet and events into agents with names, states and
  burn times, grouped by role. Every count on every page comes from it.
- **Events stay still when nothing changes.** A poll that brings nothing new returns the same
  state, so nothing re-renders every second; a square dims through the Web Animations API, not
  a re-render.
- **A burn shows at once.** The burn dialog reports what the API revoked, and the model marks
  those agents burned before the next poll.
- Every response is checked for `x-burn-mock: true`; once detected, a banner stays until reload.
- `src/contracts.ts` is a hand-maintained mirror of `contracts/ledger-event.schema.json`,
  `contracts/burn-api.openapi.yaml` and `contracts/burn.schema.json`; change both sides together.
  An event action the mirror does not know yet still renders as a plain sentence.

## Key files

| Path                        | Purpose                                                               |
| --------------------------- | --------------------------------------------------------------------- |
| `src/styles/`               | The website's tokens and fonts, base type and motion                  |
| `src/ui/`                   | Shared pieces: Card, Hero, Square, Pill, Dialog, DigitRoll, Crossfade |
| `src/shell/`                | The navigation, person menu and page frame                            |
| `src/router.ts`             | Hash routes, redirects for old links and filters in the address       |
| `src/data/`                 | The shared polls and the console's data context                       |
| `src/model/`                | Names, roles, states, burns, incidents, sentences and times           |
| `src/features/burn/`        | The burn dialog and the containment checklist                         |
| `src/features/flags/`       | Rotate and Dismiss for a flagged key                                  |
| `src/pages/<page>/`         | Each page, with its own derivations and styles                        |
| `src/api.ts`                | Requests, bearer auth with refresh, timeouts and response observation |
| `src/events.ts`             | Deduplication, cursor handling and the bounded event list             |
| `src/polling.ts`            | The event feed with its history, and polling without overlap          |
| `src/fleet.ts`              | The merged fleet and burned sessions                                  |
| `src/alerts.ts`             | Blocks and refusals, as the Overview and event sentences read them    |
| `src/join.ts`               | The `burn run` join commands                                          |
| `src/components/SignIn.tsx` | People and local sign-in                                              |

## How to test it

Requires Node 24 and npm. From this folder:

```sh
npm ci
cp .env.example .env.local
```

Set these local fields, then run `npm run dev` and open http://localhost:5173 (the only origin the Burn API accepts; the dev server listens on localhost only and will not move to another port):

| Variable                     | Meaning                                                                      |
| ---------------------------- | ---------------------------------------------------------------------------- |
| `VITE_BURN_API_URL`          | Burn API base URL, without an endpoint path                                  |
| `VITE_BURN_PEOPLE_DOMAIN`    | Cognito managed-login hostname or HTTPS origin                               |
| `VITE_BURN_PEOPLE_CLIENT_ID` | Public people app client ID                                                  |
| `VITE_BURN_GATEWAY_URL`      | Gateway MCP URL for `burn run`, shown in the join commands                   |
| `VITE_BURN_LOCAL_DEV`        | `1` for local mocks only; defaults to `0`                                    |
| `VITE_BURN_DEMO_TOKEN`       | Local mock token, used only with local-dev mode                              |
| `VITE_SITE_URL`              | The website, for "Back to the website"; defaults to `http://localhost:5174/` |

For local development, start `mocks/burn_api_mock.py` from the repository root according to its README.
Use its local URL (port 8700) for the Burn API URL and set `VITE_BURN_LOCAL_DEV=1`. Generate a local test token if needed; the mock accepts any token.
Local configuration stays in the gitignored `.env.local`. Restart Vite after changing it.

```sh
npm test
npm run build
npm run format:check
```

Tests cover the event reducer, its cursor and its bound; the history backfill; request headers and
routes; persistent mock detection; agent names, role grouping, burned-today scoping, incidents and
event sentences; the routes and their redirects; one poll of each collection across pages; the
burn dialog's requests, partial failures and checklist; Rotate and Dismiss with their errors; join
tokens and the join commands; each page's empty, loading, error and populated states; and reduced motion.
`npm run preview` serves the production build. The Burn API must allow the console's origin,
successful unauthenticated OPTIONS preflights, `Authorization` and `Content-Type` (also `x-burn-demo-token` for local mocks).
Development services expose `x-burn-mock` in `Access-Control-Expose-Headers` so the browser can detect mock data.

## FAQ

**Does the browser receive credential values?** The console reads credential references and
metadata, and fingerprints, never values. Creating a join token returns its value once so you can
copy it separately from the join commands. It stays in the open dialog's memory and is never
written to storage, the URL or console logs; the role page keeps only the token's record (uses,
expiry, creator), never the token. Blocked handoff and egress events describe stopped traffic
without exposing credential values.

**Why is the blast radius empty after a burn?** CloudTrail history can lag several minutes. The
agent page keeps asking every ten seconds and says that CloudTrail runs a few minutes behind.

**Why does the console look like this?** It continues the website, so signing in feels like
walking further into the same product: a night stage, white cards, and numbers that roll rather
than jump. Ember means a burn and nothing else, so an ember control always deserves attention, and
errors are never ember. Motion happens only when something changed and is off with reduced
motion. Text meets WCAG AA, and every control shows its keyboard focus.

**What does MOCK DATA mean, and where does the token live?** At least one API response declared itself a development mock.
Switch URLs and reload to start a new connection. People tokens stay in this tab's sessionStorage and
are cleared by Sign out; they never go in localStorage, API URLs or console logs. The people domain
and client ID are public configuration. In local-dev mode Vite embeds the demo token in the bundle,
so keep local configuration and builds containing it out of git.
