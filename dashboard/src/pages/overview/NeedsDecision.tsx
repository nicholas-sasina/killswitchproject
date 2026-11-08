import { useCallback, useState } from "react";
import { useConsole } from "../../data/ConsoleData";
import { FlagActions } from "../../features/flags/FlagActions";
import { Card, Notice } from "../../ui";
import type { Decision, Ignored, Text } from "./overview";

/** Unknown callers someone chose to ignore, in this browser only. */
const IGNORED_KEY = "burn.console.ignoredCallers";

/** At most this many items; the rest wait behind a link. */
const SHOWN = 5;

function readIgnored(): Ignored {
  try {
    const value = JSON.parse(localStorage.getItem(IGNORED_KEY) ?? "{}");
    return value && typeof value === "object" ? (value as Ignored) : {};
  } catch {
    return {};
  }
}

/**
 * Ignored callers, kept per browser. There is no API to record the
 * decision, so it hides the caller here until it is refused again.
 */
export function useIgnoredCallers() {
  const [ignored, setIgnored] = useState<Ignored>(readIgnored);
  const ignore = useCallback((key: string, newest: string) => {
    setIgnored((current) => {
      const next = { ...current, [key]: newest };
      try {
        localStorage.setItem(IGNORED_KEY, JSON.stringify(next));
      } catch {
        /* Private mode: ignored for this visit only. */
      }
      return next;
    });
  }, []);
  return { ignored, ignore };
}

function Body({ text }: { text: Text }) {
  return (
    <p className="ov-decide-body">
      {text.map((part, index) =>
        typeof part === "string" ? (
          part
        ) : (
          <span key={index} className="id">
            {part.id}
          </span>
        ),
      )}
    </p>
  );
}

function Actions({
  item,
  onIgnore,
}: {
  item: Decision;
  onIgnore: (key: string, newest: string) => void;
}) {
  const { openAddAgents } = useConsole();
  switch (item.kind) {
    case "flag":
      return <FlagActions flag={item.flag} variant="compact" />;
    case "canary":
      return (
        <div className="ov-decide-actions">
          <a className="btn btn-dark btn-sm" href={item.href}>
            Open {item.name}
          </a>
        </div>
      );
    case "caller":
      // No route registers a badge Burn did not issue: Register it opens
      // Connect an agent, where a real agent is connected through Burn.
      return (
        <div className="ov-decide-actions">
          <button
            type="button"
            className="btn btn-dark btn-sm"
            onClick={() => openAddAgents()}
          >
            Register it
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onIgnore(item.key, item.newest)}
          >
            Ignore
          </button>
        </div>
      );
  }
}

/** "Needs a decision": flags, unknown callers and canary uses, worst first. */
export function NeedsDecision({
  items,
  onIgnore,
}: {
  items: Decision[];
  onIgnore: (key: string, newest: string) => void;
}) {
  const { flagsLoaded, flagsError } = useConsole();
  const shown = items.slice(0, SHOWN);
  const more = items.length - shown.length;
  return (
    <Card
      title="Needs a decision"
      sub={String(items.length)}
      pad="narrow"
      className="ov-decide"
    >
      {flagsError && (
        <div className="ov-card-notice">
          <Notice tone="error">Flags could not be loaded: {flagsError}</Notice>
        </div>
      )}
      {shown.length > 0 ? (
        <ul>
          {shown.map((item) => (
            <li key={item.key} className="ov-decide-item">
              <p className={`cap ov-decide-tag${item.hot ? " is-ember" : ""}`}>
                {item.tag}
              </p>
              <h3 className={`ov-decide-title${item.titleId ? " id" : ""}`}>
                {item.title}
              </h3>
              <Body text={item.body} />
              <Actions item={item} onIgnore={onIgnore} />
            </li>
          ))}
        </ul>
      ) : !flagsLoaded && !flagsError ? (
        <div className="ov-card-notice">
          <Notice>Loading flags.</Notice>
        </div>
      ) : (
        <p className="ov-empty">
          Nothing needs you. Shared keys, unknown callers and canary uses land
          here first.
        </p>
      )}
      {more > 0 && (
        <p className="ov-more">
          {more} more waiting.{" "}
          <a className="text-link" href="#/credentials">
            See flagged keys
          </a>
        </p>
      )}
    </Card>
  );
}
