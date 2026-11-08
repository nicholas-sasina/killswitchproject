import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { build } from "vite";
import { describe, expect, it } from "vitest";

// The hosted dashboard's Content-Security-Policy is script-src 'self': the
// browser runs scripts from the dashboard's own files and blocks any script
// written into the page, including inline event handlers.
const root = fileURLToPath(new URL("..", import.meta.url));

function parse(html) {
  return new JSDOM(html).window.document;
}

function inlineScripts(document) {
  const bodies = [...document.querySelectorAll("script")]
    .filter((script) => script.textContent.trim() !== "")
    .map((script) => script.outerHTML.slice(0, 80));
  const handlers = [...document.querySelectorAll("*")].flatMap((element) =>
    element
      .getAttributeNames()
      .filter((name) => name.startsWith("on"))
      .map((name) => `<${element.localName} ${name}>`),
  );
  return [...bodies, ...handlers];
}

describe("first paint under the hosted CSP", () => {
  const source = parse(readFileSync(`${root}/index.html`, "utf8"));

  it("index.html has no inline scripts", () => {
    expect(inlineScripts(source)).toEqual([]);
  });

  it("loads first-paint.js from <head>, blocking, before the page paints", () => {
    const script = source.head.querySelector('script[src="/first-paint.js"]');
    expect(script).not.toBeNull();
    // A module, defer or async script would run after the first paint.
    expect(script.hasAttribute("type")).toBe(false);
    expect(script.hasAttribute("defer")).toBe(false);
    expect(script.hasAttribute("async")).toBe(false);
    expect(existsSync(`${root}/public/first-paint.js`)).toBe(true);
  });

  it("the production build's index.html has no inline scripts", async () => {
    const result = await build({
      root,
      logLevel: "silent",
      build: { write: false },
    });
    const files = [result].flat().flatMap((bundle) => bundle.output);
    const page = files.find((file) => file.fileName === "index.html");
    const built = parse(page.source);
    expect(inlineScripts(built)).toEqual([]);
    expect(
      built.head.querySelector('script[src="/first-paint.js"]'),
    ).not.toBeNull();
  });
});
