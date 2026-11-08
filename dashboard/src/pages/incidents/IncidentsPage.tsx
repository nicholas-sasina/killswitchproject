// STUB: the incidents page's branch builds this page from the Incidents board.
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, Hero } from "../../ui";
import "./incidents.css";

export function IncidentsPage() {
  const { incidentCounts } = useConsole();
  return (
    <>
      <Hero eyebrow="Incidents / burned agents" dot="ember" title="Incidents" />
      <Columns
        aside={
          <Card
            title="Still open"
            sub={incidentCounts.keysWaiting}
            pad="narrow"
          />
        }
      >
        <Card title="Burned agents" sub={incidentCounts.all} />
      </Columns>
    </>
  );
}
