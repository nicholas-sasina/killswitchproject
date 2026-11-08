import type { Instance } from "../contracts";
import { instanceHref } from "../router";

export function ReplacementInfo({
  instance,
  instances,
  detail = false,
}: {
  instance: Instance;
  instances: Instance[];
  detail?: boolean;
}) {
  const previous = instances.find(
    (candidate) => candidate.session_id === instance.replaces,
  );
  const next = instances.find(
    (candidate) => candidate.session_id === instance.replaced_by,
  );
  const resume = previous?.replacement;
  const snapshot = instance.replacement ?? resume;
  const quarantineLabel = instance.replacement
    ? "Quarantined when this instance was burned"
    : "Inherited quarantine";
  if (!instance.replacement && !instance.replaces && !instance.replaced_by)
    return null;
  return (
    <div className="replacement-info">
      {instance.replaced_by ? (
        <p>
          replaced by{" "}
          <a
            className="instance-link"
            href={instanceHref(instance.replaced_by)}
          >
            {next?.agent_id ?? instance.replaced_by}
          </a>
        </p>
      ) : instance.replacement?.status === "pending" ? (
        <p>replacement pending</p>
      ) : instance.replacement?.status === "needs_person" ? (
        <p>needs a person</p>
      ) : null}
      {instance.replaces && (
        <p>
          replaces{" "}
          <a className="instance-link" href={instanceHref(instance.replaces)}>
            {previous?.agent_id ?? instance.replaces}
          </a>
          {resume ? (
            <>
              {" "}
              · resumed at {resume.done.length}
              {previous?.items_started != null &&
                `/${previous.items_started}`}{" "}
              · {resume.quarantined.length} quarantined
            </>
          ) : (
            " · resume progress not reported"
          )}
        </p>
      )}
      {detail && snapshot && (
        <div className="replacement-quarantine">
          <h3>{quarantineLabel}</h3>
          {snapshot.quarantined.length ? (
            <ul
              className="quarantined-items"
              tabIndex={0}
              aria-label={quarantineLabel}
            >
              {snapshot.quarantined.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>No quarantined items.</p>
          )}
        </div>
      )}
    </div>
  );
}
