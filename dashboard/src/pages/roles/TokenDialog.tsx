import { useMemo } from "react";
import { useConsole } from "../../data/ConsoleData";
import type { Role } from "../../contracts";
import { AddAgent, type AddStep } from "./AddAgent";
import { rememberToken } from "./tokens";

export interface TokenDialogProps {
  /** Mint a join token for this existing role; otherwise create a role. */
  role?: string;
  onClose: () => void;
  /** Inside another dialog, without its own frame. */
  bare?: boolean;
  /** Told when a request starts or ends and when the step changes. */
  onState?: (state: { busy: boolean; step: AddStep }) => void;
}

/**
 * The join token flow, for CI, containers and swarms: create a role or
 * pick one, mint a token, show it once, and give the commands that read it
 * without holding it. Uses the console's roles, agents and join URLs.
 */
export function TokenDialog({
  role,
  onClose,
  bare,
  onState,
}: TokenDialogProps) {
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
    // The role is chosen once, when the flow opens.
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
      bare={bare}
      onState={onState}
    />
  );
}
