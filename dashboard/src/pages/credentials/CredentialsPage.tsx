// STUB: the incidents page's branch builds this page from the same pieces.
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, Hero } from "../../ui";
import "./credentials.css";

export function CredentialsPage() {
  const { incidentCounts } = useConsole();
  return (
    <>
      <Hero
        eyebrow="Credentials / flagged keys"
        dot="quiet"
        title="Credentials"
      />
      <Columns>
        <Card title="Flagged keys" sub={incidentCounts.keysWaiting} />
      </Columns>
    </>
  );
}
