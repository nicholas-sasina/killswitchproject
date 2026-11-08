/**
 * The console's shared data: one poll of each collection, read by every
 * page and by the navigation, so every count comes from the same answer.
 *
 *   /events     every second (polling.ts), with today's history paged in
 *   /instances  every five seconds: every agent that ever joined
 *   /roles      every five seconds
 *   /flags      every five seconds
 *
 * All four run while the console is open, on every page: the navigation's
 * Incidents count needs instances, events and flags, and the Overview, the
 * role pages and an agent's page all name agents from the same fleet. Each
 * page's own requests (a ledger, a blast radius) run only while that page
 * is mounted.
 *
 * A burn made from this console is reported here with `recordBurn`, so its
 * agents show as burned at once rather than on the next poll.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ApiConfig, BurnApi } from "../api";
import type {
  BurnResult,
  Flag,
  Instance,
  LedgerEvent,
  Role,
} from "../contracts";
import { newestEvents } from "../events";
import { markRevoked, mergeFleet } from "../fleet";
import type { JoinUrls } from "../join";
import { useEventFeed, type EventHistory } from "../polling";
import { callersSince, createPulseBus, type PulseBus } from "../model/activity";
import {
  agentForEvent,
  buildConsole,
  type ConsoleModel,
} from "../model/console";
import {
  incidentCounts,
  incidents,
  type Incident,
  type IncidentCounts,
} from "../model/incidents";
import { regionFromUrl } from "../model/region";
import type { SentenceContext } from "../model/sentences";
import { useSharedPoll } from "./poll";

/** A burn this console made, kept so its page can show what happened. */
export interface BurnRecord {
  result: BurnResult;
  /** When the API answered. */
  at: string;
}

export interface ConsoleData {
  api: BurnApi;
  config: ApiConfig;
  /** Whether the API is configured and signed in; nothing polls otherwise. */
  configured: boolean;
  /** The signed-in person's email; undefined in local development. */
  email?: string;
  /** The AWS region from the API URL, if it names one. */
  region?: string;
  /** What `burn run` needs besides a join token. */
  joinUrls: JoinUrls;

  /** Every event loaded, newest first. */
  events: LedgerEvent[];
  eventsError: string;
  /** When the event feed last answered; undefined until it first does. */
  updatedAt?: Date;
  /** How far back `events` reaches. */
  history: EventHistory;

  instances: Instance[];
  instancesLoaded: boolean;
  instancesError: string;
  roles: Role[];
  rolesLoaded: boolean;
  rolesError: string;
  flags: Flag[];
  flagsLoaded: boolean;
  flagsError: string;

  /** The fleet: agents, names, roles and every count. */
  model: ConsoleModel;
  /** Burned agents with whether each is still open. */
  incidents: Incident[];
  incidentCounts: IncidentCounts;
  /** Names for event sentences. */
  sentenceContext: SentenceContext;

  /** Burns made from this console, by session (or agent id). */
  burns: Record<string, BurnRecord>;
  /** Report what a burn did, so its agents show burned at once. */
  recordBurn: (results: BurnResult[]) => void;
  /** Put a flag's new state in the list at once (after /flags/resolve). */
  noteFlag: (flag: Flag) => void;
  refreshFlags: () => void;
  refreshRoles: () => void;
  refreshInstances: () => void;

  /** Open Connect an agent, with a role chosen or none. */
  openAddAgents: (role?: string) => void;
  /** Squares listen here for their agent's calls. */
  pulses: PulseBus;
}

const ConsoleContext = createContext<ConsoleData | null>(null);

export function useConsole(): ConsoleData {
  const data = useContext(ConsoleContext);
  if (!data) throw new Error("useConsole needs a ConsoleDataProvider.");
  return data;
}

/** For tests and stories: provide a ready-made value. */
export const ConsoleDataContext = ConsoleContext;

const DAY_MS = 24 * 60 * 60 * 1000;

export function ConsoleDataProvider({
  api,
  config,
  configured,
  email,
  openAddAgents,
  children,
}: {
  api: BurnApi;
  config: ApiConfig;
  configured: boolean;
  email?: string;
  openAddAgents: (role?: string) => void;
  children: ReactNode;
}) {
  // History reaches back 24 hours: "today" and the "last 24 hours" both fit.
  const [historySince] = useState(() => Date.now() - DAY_MS);
  const feed = useEventFeed(api, configured, { historySince });
  const events = useMemo(() => newestEvents(feed.state), [feed.state]);

  const loadInstances = useCallback(
    (signal: AbortSignal) => api.instances(undefined, signal),
    [api],
  );
  const loadRoles = useCallback(
    (signal: AbortSignal) => api.roles(signal),
    [api],
  );
  const loadFlags = useCallback(
    (signal: AbortSignal) => api.flags(signal),
    [api],
  );
  const instancePoll = useSharedPoll(configured ? loadInstances : null, 5_000);
  const rolePoll = useSharedPoll(configured ? loadRoles : null, 5_000);
  const flagPoll = useSharedPoll(configured ? loadFlags : null, 5_000);

  const [burns, setBurns] = useState<Record<string, BurnRecord>>({});
  const recordBurn = useCallback((results: BurnResult[]) => {
    const at = new Date().toISOString();
    setBurns((current) => {
      const next = { ...current };
      for (const result of results) {
        const keys = result.sessions_revoked.length
          ? result.sessions_revoked
          : [result.agent_id];
        for (const key of keys) next[key] = { result, at };
      }
      return next;
    });
  }, []);
  const burnedSessions = useMemo(
    () =>
      new Set(
        Object.values(burns).flatMap(
          (record) => record.result.sessions_revoked,
        ),
      ),
    [burns],
  );

  // A flag resolved here shows at once; the next poll confirms it.
  const [flagNotes, setFlagNotes] = useState<Record<string, Flag>>({});
  const flagData = flagPoll.data;
  useEffect(() => setFlagNotes({}), [flagData]);
  const noteFlag = useCallback(
    (flag: Flag) =>
      setFlagNotes((current) => ({ ...current, [flag.secret_ref]: flag })),
    [],
  );
  const flags = useMemo(() => {
    const list = (flagData?.flags ?? []).map(
      (flag) => flagNotes[flag.secret_ref] ?? flag,
    );
    // GET /flags drops a rotated flag; so does this list.
    return list.filter((flag) => flag.state !== "rotated");
  }, [flagData, flagNotes]);

  const instances = instancePoll.data?.instances;
  const roles = rolePoll.data?.roles;
  const model = useMemo(
    () =>
      buildConsole({
        fleet: markRevoked(
          mergeFleet(feed.state.agents, instances ?? []),
          burnedSessions,
        ),
        events,
        roles: roles ?? [],
        now: new Date(),
      }),
    [feed.state.agents, instances, burnedSessions, events, roles],
  );
  const incidentList = useMemo(
    () => incidents(model, events, flags),
    [model, events, flags],
  );
  const counts = useMemo(
    () => incidentCounts(incidentList, flags),
    [incidentList, flags],
  );
  const sentenceContext = useMemo<SentenceContext>(
    () => ({
      nameOf: ({ agentId, sessionId }) =>
        agentForEvent(model, {
          agent_id: agentId ?? "",
          session_id: sessionId,
        })?.name,
    }),
    [model],
  );

  // Squares dim for agents that made a call in the newest poll.
  const [pulses] = useState(createPulseBus);
  const seen = useRef<string | undefined>(undefined);
  const modelRef = useRef(model);
  modelRef.current = model;
  useEffect(() => {
    const newest = events[0]?.event_id;
    if (!newest) return;
    const after = seen.current;
    seen.current = after && after > newest ? after : newest;
    if (after === undefined || newest <= after) return;
    const fresh: LedgerEvent[] = [];
    for (const event of events) {
      if (event.event_id <= after) break;
      fresh.push(event);
    }
    const keys = new Set<string>();
    for (const caller of callersSince(fresh, after)) {
      const agent =
        modelRef.current.byKey.get(caller) ??
        agentForEvent(modelRef.current, { agent_id: caller, session_id: null });
      if (agent) keys.add(agent.key);
    }
    if (keys.size) pulses.emit(keys);
  }, [events, pulses]);

  const joinUrls = useMemo(
    () => ({ api: config.burnUrl, gateway: config.gatewayUrl }),
    [config.burnUrl, config.gatewayUrl],
  );

  const value = useMemo<ConsoleData>(
    () => ({
      api,
      config,
      configured,
      email,
      region: regionFromUrl(config.burnUrl),
      joinUrls,
      events,
      eventsError: feed.error,
      updatedAt: feed.updatedAt,
      history: feed.history,
      instances: instances ?? [],
      instancesLoaded: instances !== undefined,
      instancesError: instancePoll.error,
      roles: roles ?? [],
      rolesLoaded: roles !== undefined,
      rolesError: rolePoll.error,
      flags,
      flagsLoaded: flagData !== undefined,
      flagsError: flagPoll.error,
      model,
      incidents: incidentList,
      incidentCounts: counts,
      sentenceContext,
      burns,
      recordBurn,
      noteFlag,
      refreshFlags: flagPoll.refresh,
      refreshRoles: rolePoll.refresh,
      refreshInstances: instancePoll.refresh,
      openAddAgents,
      pulses,
    }),
    [
      api,
      config,
      configured,
      email,
      joinUrls,
      events,
      feed.error,
      feed.updatedAt,
      feed.history,
      instances,
      instancePoll.error,
      instancePoll.refresh,
      roles,
      rolePoll.error,
      rolePoll.refresh,
      flags,
      flagData,
      flagPoll.error,
      flagPoll.refresh,
      model,
      incidentList,
      counts,
      sentenceContext,
      burns,
      recordBurn,
      noteFlag,
      openAddAgents,
      pulses,
    ],
  );
  return <ConsoleContext value={value}>{children}</ConsoleContext>;
}
