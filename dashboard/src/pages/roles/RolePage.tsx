// STUB: the role page's branch builds this page from the Roles board.
import { roleCounts } from "../../model/console";
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, Hero } from "../../ui";
import "./roles.css";

export function RolePage({ name }: { name: string }) {
  const { model } = useConsole();
  const counts = roleCounts(model, name);
  return (
    <>
      <Hero
        crumbs={[{ label: "Roles", href: "#/roles" }, { label: name }]}
        title={name}
        statSize="md"
        stats={[
          { label: "Running", value: counts.running },
          { label: "Burned today", value: counts.burnedToday, tone: "ember" },
        ]}
      />
      <Columns aside={<Card title={`What a ${name} may do`} pad="narrow" />}>
        <Card title={`${name}s`} sub="each one a running copy" />
      </Columns>
    </>
  );
}
