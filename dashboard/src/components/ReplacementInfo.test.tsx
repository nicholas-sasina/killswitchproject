// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { Instance, Replacement } from "../contracts";
import { instanceHref } from "../router";
import { InstanceLineage } from "./InstanceLineage";
import { InstanceDetails } from "./InstanceDetails";
import { ReplacementInfo } from "./ReplacementInfo";

afterEach(cleanup);

const snapshot: Replacement = {
  status: "linked",
  cutoff: "2026-11-08T05:00:00Z",
  cutoff_source: "evidence",
  done: ["item-a", "item-b"],
  quarantined: ["item-review"],
};
const old: Instance = {
  session_id: "session_old",
  agent_id: "agent-worker-a1b2c3",
  role: "worker",
  status: "revoked",
  created_at: "2026-11-08T04:00:00Z",
  replacement: snapshot,
  replaced_by: "session_new",
  items_started: 3,
  items_done: 2,
};
const replacement: Instance = {
  session_id: "session_new",
  agent_id: "agent-worker-d4e5f6",
  role: "worker",
  status: "active",
  created_at: "2026-11-08T05:01:00Z",
  replaces: old.session_id,
  generation: 1,
};

it.each([
  ["pending", "replacement pending"],
  ["needs_person", "needs a person"],
] as const)("shows the %s state in the instance row", (status, label) => {
  const instance = {
    ...old,
    replaced_by: null,
    replacement: { ...snapshot, status },
  };
  render(
    <InstanceLineage
      selected={[instance]}
      instances={[instance]}
      disabled={false}
      onBurn={() => {}}
    />,
  );
  expect(screen.getByText(label)).toBeTruthy();
});

it("links the burned row to the actual replacement and the replacement back to its predecessor", () => {
  render(
    <InstanceLineage
      selected={[old, replacement]}
      instances={[old, replacement]}
      disabled={false}
      onBurn={() => {}}
    />,
  );
  const rows = screen.getAllByRole("row").slice(1);
  expect(
    within(rows[0])
      .getByRole("link", { name: replacement.agent_id })
      .getAttribute("href"),
  ).toBe(instanceHref(replacement.session_id));
  expect(
    within(rows[1])
      .getByRole("link", { name: old.agent_id })
      .getAttribute("href"),
  ).toBe(instanceHref(old.session_id));
  expect(rows[1].textContent).toContain("resumed at 2/3 · 1 quarantined");
  expect(
    within(rows[0])
      .getByRole("link", { name: `Inspect ${old.agent_id}` })
      .getAttribute("href"),
  ).toBe(instanceHref(old.session_id));
});

it("omits the resume denominator when the predecessor has no started count", () => {
  render(
    <ReplacementInfo
      instance={replacement}
      instances={[{ ...old, items_started: undefined }, replacement]}
    />,
  );
  expect(screen.getByText(/resumed at 2 · 1 quarantined/)).toBeTruthy();
  expect(screen.queryByText(/reported|2\/\?/)).toBeNull();
});

it("retains a reported zero started count", () => {
  render(
    <ReplacementInfo
      instance={replacement}
      instances={[
        { ...old, items_started: 0, replacement: { ...snapshot, done: [] } },
        replacement,
      ]}
    />,
  );
  expect(screen.getByText(/resumed at 0\/0 · 1 quarantined/)).toBeTruthy();
});

it("shows quarantined IDs in replacement details from the shared predecessor record", () => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  render(
    <InstanceDetails
      sessionId={replacement.session_id}
      instances={[old, replacement]}
      onClose={() => {}}
    />,
  );
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("Quarantined item IDs")).toBeTruthy();
  expect(within(dialog).getByText("item-review")).toBeTruthy();
  expect(within(dialog).queryByText("item-a")).toBeNull();
});

it("uses the session ID and an explicit missing-progress state when a predecessor is absent", () => {
  render(
    <ReplacementInfo instance={replacement} instances={[replacement]} detail />,
  );
  expect(screen.getByRole("link", { name: old.session_id })).toBeTruthy();
  expect(screen.getByText(/resume progress not reported/)).toBeTruthy();
  expect(screen.queryByText(/0 quarantined/)).toBeNull();
});

it("shows no replacement status or quarantine claims for an ordinary instance", () => {
  const { container } = render(
    <ReplacementInfo
      instance={{ ...replacement, replaces: null }}
      instances={[]}
      detail
    />,
  );
  expect(container.textContent).toBe("");
});
