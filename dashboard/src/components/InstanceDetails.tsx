import type { Instance } from "../contracts";
import { ReplacementInfo } from "./ReplacementInfo";
import { SwarmDialog } from "./SwarmDialog";
import { Status, Timestamp } from "./common";

export function InstanceDetails({
  sessionId,
  instances,
  onClose,
}: {
  sessionId: string;
  instances: Instance[];
  onClose: () => void;
}) {
  const instance = instances.find(
    (candidate) => candidate.session_id === sessionId,
  );
  return (
    <SwarmDialog
      title={instance?.agent_id ?? "Instance details"}
      onClose={onClose}
    >
      {instance ? (
        <>
          <dl className="instance-details">
            <div>
              <dt>Session</dt>
              <dd>
                <code>{instance.session_id}</code>
              </dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{instance.role}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>
                <Status value={instance.status} />
              </dd>
            </div>
            <div>
              <dt>Last seen</dt>
              <dd>
                {instance.last_seen ? (
                  <Timestamp value={instance.last_seen} />
                ) : (
                  "Not reported"
                )}
              </dd>
            </div>
          </dl>
          <ReplacementInfo instance={instance} instances={instances} detail />
        </>
      ) : (
        <p>
          This instance is not in the current shared list. Waiting for the next
          update.
        </p>
      )}
    </SwarmDialog>
  );
}
