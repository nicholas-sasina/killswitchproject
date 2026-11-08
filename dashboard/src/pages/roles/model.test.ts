import { describe, expect, it } from "vitest";
import { rolePlural } from "./model";

describe("rolePlural", () => {
  it.each([
    ["worker", "workers"],
    ["code-reader", "code-readers"],
    ["analysis", "analyses"],
    ["registry", "registries"],
    ["relay", "relays"],
    ["inbox", "inboxes"],
    ["batch", "batches"],
    ["", "agents"],
  ])("%s becomes %s", (name, plural) => {
    expect(rolePlural(name)).toBe(plural);
  });
});
