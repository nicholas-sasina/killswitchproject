// STUB: the overview page's branch builds this page from the Main board.
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, EmptyState, Hero } from "../../ui";
import "./overview.css";

export function OverviewPage() {
  const { model } = useConsole();
  return (
    <>
      <Hero
        title="Overview"
        stats={[
          { label: "Running", value: model.counts.running },
          {
            label: "Burned today",
            value: model.counts.burnedToday,
            tone: "ember",
          },
        ]}
      />
      <Columns asideWidth={420} aside={<Card title="Live" pad="narrow" />}>
        <Card title="Running now">
          <EmptyState>
            No agents running. Add agents to start a role.
          </EmptyState>
        </Card>
      </Columns>
    </>
  );
}
