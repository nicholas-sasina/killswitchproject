import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MockBanner, mockDataReducer } from "./MockBanner";

describe("mock data banner", () => {
  it("appears only after the mock response header is observed", () => {
    expect(mockDataReducer(false, new Headers())).toBe(false);
    expect(
      mockDataReducer(false, new Headers({ "x-burn-mock": "false" })),
    ).toBe(false);
    const visible = mockDataReducer(
      false,
      new Headers({ "X-Burn-Mock": " TRUE " }),
    );
    expect(visible).toBe(true);
    expect(renderToStaticMarkup(<MockBanner visible={visible} />)).toContain(
      "MOCK DATA",
    );
    expect(renderToStaticMarkup(<MockBanner visible={false} />)).toBe("");
  });

  it("stays visible when a later response lacks the header", () => {
    const visible = mockDataReducer(
      false,
      new Headers({ "x-burn-mock": "true" }),
    );
    expect(mockDataReducer(visible, new Headers())).toBe(true);
    expect(
      mockDataReducer(visible, new Headers({ "x-burn-mock": "false" })),
    ).toBe(true);
  });
});
