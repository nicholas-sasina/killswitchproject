// STUB: the role page's branch restyles Add agents into the console's
// Dialog. Until then this mounts the existing AddAgent flow unchanged.
import { AddAgent } from "../../components/AddAgent";
import { useConsole } from "../../data/ConsoleData";

export interface AddAgentsDialogProps {
  /** Mint a join token for this existing role; otherwise create a role. */
  role?: string;
  onClose: () => void;
}

export function AddAgentsDialog({ role, onClose }: AddAgentsDialogProps) {
  const { api, roles, instances, joinUrls, refreshRoles } = useConsole();
  return (
    <AddAgent
      api={api}
      roles={roles}
      instances={instances}
      role={role ? roles.find((item) => item.name === role) : undefined}
      joinUrls={joinUrls}
      onCreated={() => refreshRoles()}
      onClose={onClose}
    />
  );
}
