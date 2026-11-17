import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The properties the whole front end must have, asserted over its source.
 *
 * These are not style checks. Each one is a rule the backend enforces
 * structurally, and the UI is the surface where it would most plausibly be
 * undone by somebody adding a convenience.
 */

const SRC = join(import.meta.dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry) ? [path] : [];
  });
}

const FILES = sources(SRC).filter((f) => !f.includes("__tests__"));

describe("the read side cannot write", () => {
  it("contains no mutating request anywhere", () => {
    // "A dashboard that can place an order is a trading system with a chart
    // attached." The service has no write routes by construction; the client
    // carries the same property rather than relying on nobody adding one.
    const offenders: string[] = [];
    for (const file of FILES) {
      const text = readFileSync(file, "utf8");
      if (
        /method:\s*["'`](POST|PUT|PATCH|DELETE)/i.test(text) ||
        /\.(post|put|patch|delete)\s*\(/.test(text)
      ) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("no verdict is rendered", () => {
  it("never labels a security undervalued or overvalued", () => {
    // ADR-0036. A multiple has no absolute meaning without growth and risk,
    // valuation is horizon-dependent, and no fundamental signal has passed on
    // this corpus. If a label reappears, this fails.
    const offenders: string[] = [];
    for (const file of FILES) {
      const text = readFileSync(file, "utf8");
      // Only flag the words used as a rendered label, not the ADR's own
      // explanation of why there isn't one.
      const stripped = text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
      if (/\b(undervalued|overvalued|fairly[ _]valued)\b/i.test(stripped)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("tokens are the only source of colour", () => {
  it("uses no raw hex in a component or view", () => {
    // A hex in a component is a value that cannot follow the theme, and the
    // dark mode is selected rather than flipped — so a stray hex is a bug
    // that only shows up in one theme.
    const offenders: string[] = [];
    for (const file of FILES) {
      if (file.includes("tokens")) continue;
      const text = readFileSync(file, "utf8").replace(
        /\/\*[\s\S]*?\*\/|\/\/.*$/gm,
        "",
      );
      const hits = text.match(/#[0-9a-fA-F]{6}\b/g);
      if (hits) offenders.push(`${file}: ${hits.join(", ")}`);
    }
    expect(offenders).toEqual([]);
  });
});
