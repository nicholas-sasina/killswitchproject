// STUB: the agent page's branch builds this page from the Agent board.
import { findAgent } from "../../model/console";
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, EmptyState, Hero, StatusPill } from "../../ui";
import "./agent.css";

export function AgentPage({ sessionId }: { sessionId: string }) {
  const { model } = useConsole();
  const agent = findAgent(model, sessionId);
  return (
    <>
      <Hero
        crumbs={[
          { label: "Roles", href: "#/roles" },
          ...(agent
            ? [
                {
                  label: agent.role,
                  href: `#/roles/${encodeURIComponent(agent.role)}`,
                },
              ]
            : []),
          { label: agent?.name ?? "Agent" },
        ]}
        title={agent?.name ?? "Agent"}
        status={agent && <StatusPill state={agent.state} />}
      />
      <Columns aside={<Card title="Identity" pad="narrow" />}>
        <Card title="Exposure ledger">
          {!agent && (
            <EmptyState>This agent isn&rsquo;t in the list yet.</EmptyState>
          )}
        </Card>
      </Columns>
    </>
  );
}
