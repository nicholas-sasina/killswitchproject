import { useMemo } from "react";
import { useConsole } from "../../data/ConsoleData";
import type { Role } from "../../contracts";
import { AddAgent } from "./AddAgent";
import { rememberToken } from "./tokens";

export interface AddAgentsDialogProps {
  /** Mint a join token for this existing role; otherwise create a role. */
  role?: string;
  onClose: () => void;
}

/**
 * Add agents, as the navigation and a role page open it: the AddAgent flow
 * with the console's roles, agents and join URLs.
 */
export function AddAgentsDialog({ role, onClose }: AddAgentsDialogProps) {
  const { api, roles, instances, joinUrls, refreshRoles } = useConsole();
  // A role known only from its agents still takes a token by name.
  const target = useMemo<Role | undefined>(
    () =>
      role
        ? (roles.find((item) => item.name === role) ?? {
            name: role,
            tools: [],
            managed_by: "code",
          })
        : undefined,
    // The role is chosen once, when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [role],
  );
  return (
    <AddAgent
      api={api}
      roles={roles}
      instances={instances}
      role={target}
      joinUrls={joinUrls}
      onCreated={() => refreshRoles()}
      onToken={rememberToken}
      onClose={onClose}
    />
  );
}
