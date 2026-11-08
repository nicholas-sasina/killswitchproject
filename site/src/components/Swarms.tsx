import { Terminal, type TermLine } from "./Terminal";

const RUN: TermLine[] = [
  { text: "burn run --role worker -- python agent.py", prompt: true },
  {
    text: "burn: instance agent-worker-7f3a9c (session sess_4b1e09c27d5a6f80, role worker) started",
    muted: true,
  },
  { text: "" },
  {
    text: "# inside the agent, start a helper under its own role",
    muted: true,
  },
  { text: 'child = burn.spawn("reader", ["python", "task.py"])' },
];

const REFERENCE: TermLine[] = [
  { text: "# a tool reads a config file that holds a live key", muted: true },
  { text: 'read_config("config/app.env")' },
  { text: "" },
  {
    text: "# the content that comes back has a reference instead",
    muted: true,
  },
  {
    text: "",
    swap: {
      before: "CRM_DB_URL=postgres://svc_crm:",
      from: "••••••",
      to: "{{burn:ref:burn/shared/crm_db_url}}",
      after: "@crm-db.internal.example:5432/crm",
    },
  },
];

const BY_SOURCE: TermLine[] = [
  { text: "# ticket T-4821 turns out to be poisoned", muted: true },
  {
    text: 'curl -s -X POST "$BURN_API/burn/by-source" \\',
    prompt: true,
    at: 1,
  },
  { text: '    -H "authorization: Bearer $TOKEN" \\', at: 1 },
  { text: '    -d \'{"source_ref": "read_ticket:T-4821"}\' \\', at: 1 },
  { text: "  | jq -c '.readers[] | {agent_id, via}'", at: 1 },
  { text: '{"agent_id":"agent-worker-07c1d2","via":"read"}', at: 2 },
  { text: '{"agent_id":"agent-worker-12a9f4","via":"read"}', at: 3 },
  { text: '{"agent_id":"agent-reader-3be019","via":"handoff"}', at: 4 },
];

export function Swarms() {
  return (
    <section className="block wrap" id="swarms" aria-labelledby="swarms-title">
      <div className="block-rule">
        <h2 className="section-title" id="swarms-title">
          Built for agents that come and go
        </h2>
        <p className="swarms-intro">
          Swarms start and stop agents all day. Burn works per instance, so
          nobody registers agents by hand.
        </p>
      </div>
      <div className="swarm-rows">
        <div className="swarm-row">
          <div className="swarm-text">
            <h3>Start agents under a role</h3>
            <p>
              Define a role once, with the tools and keys it may use. Each
              instance started with <code>burn run</code> or{" "}
              <code>burn.spawn</code> gets its own identity and freshly minted
              keys, and anything it spawns records its parent.
            </p>
          </div>
          <Terminal lines={RUN} />
        </div>
        <div className="swarm-row">
          <div className="swarm-text">
            <h3>Keys arrive as references</h3>
            <p>
              When a tool returns a credential Burn issued, Burn swaps it for a
              reference before the agent sees it. In a connection string only
              the password is swapped, so the host stays readable. The real
              value stays in Secrets Manager, and a leaked context holds nothing
              usable.
            </p>
          </div>
          <Terminal lines={REFERENCE} />
        </div>
        <div className="swarm-row">
          <div className="swarm-text">
            <h3>Burn after the fact</h3>
            <p>
              The ledger keeps fingerprints, never values. If you learn later
              that a ticket was poisoned, one call burns every instance that
              read it, including ones that already exited, and every instance
              they messaged after reading it.
            </p>
          </div>
          <Terminal lines={BY_SOURCE} />
        </div>
      </div>
    </section>
  );
}
