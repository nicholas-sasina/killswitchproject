// STUB: the role page's branch builds the list of roles.
import { useConsole } from "../../data/ConsoleData";
import { Card, Columns, EmptyState, Hero } from "../../ui";
import "./roles.css";

export function RolesPage() {
  const { model } = useConsole();
  return (
    <>
      <Hero title="Roles" />
      <Columns>
        <Card title="Roles" sub={`${model.counts.roles}`}>
          {!model.roles.length && (
            <EmptyState>
              No roles yet. Add agents to create a role and its first join
              token.
            </EmptyState>
          )}
        </Card>
      </Columns>
    </>
  );
}
