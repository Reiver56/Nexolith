import { randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  formatGeneratedTypes,
  generatedOutputMatches,
  normalizeLineEndings,
} from "./openapi-content.mjs";

/** @type {string[]} */
const fixtures = [];

afterEach(async () => {
  await Promise.all(fixtures.splice(0).map((path) => rm(path, { force: true })));
});

describe("OpenAPI generated output comparison", () => {
  it("accepts matching LF content", async () => {
    const generated = "first\nsecond\n";

    expect(
      await generatedOutputMatches("fixture", generated, () => Promise.resolve(generated)),
    ).toBe(true);
  });

  it("accepts equivalent CRLF content", async () => {
    expect(
      await generatedOutputMatches("fixture", "first\nsecond\n", () =>
        Promise.resolve("first\r\nsecond\r\n"),
      ),
    ).toBe(true);
  });

  it("normalizes mixed and legacy line endings only", () => {
    expect(normalizeLineEndings("first\r\nsecond\nthird\rfourth\r\n")).toBe(
      "first\nsecond\nthird\nfourth\n",
    );
  });

  it("still rejects changed content with CRLF", async () => {
    expect(
      await generatedOutputMatches("fixture", "first\nsecond\n", () =>
        Promise.resolve("first\r\nchanged\r\n"),
      ),
    ).toBe(false);
  });

  it("rejects missing generated output", async () => {
    expect(
      await generatedOutputMatches("missing", "generated\n", () =>
        Promise.reject(new Error("missing")),
      ),
    ).toBe(false);
  });

  it("formats generated output deterministically with LF", () => {
    const renderedTypes = "export interface Example {\n  value: string;\n}\n";
    const first = formatGeneratedTypes(renderedTypes);

    expect(formatGeneratedTypes(renderedTypes)).toBe(first);
    expect(first).not.toContain("\r");
    expect(first.endsWith("\n")).toBe(true);
  });

  it("does not rewrite a checked CRLF fixture", async () => {
    const fixture = join(tmpdir(), `nexolith-openapi-${randomUUID()}.d.ts`);
    const original = "first\r\nsecond\r\n";
    fixtures.push(fixture);
    await writeFile(fixture, original, "utf8");

    expect(await generatedOutputMatches(fixture, "first\nsecond\n")).toBe(true);
    expect(await readFile(fixture, "utf8")).toBe(original);
  });
});
