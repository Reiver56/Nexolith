import { readFile } from "node:fs/promises";

/** @param {string} content */
export function normalizeLineEndings(content) {
  return content.replace(/\r\n?/g, "\n");
}

/** @param {string} renderedTypes */
export function formatGeneratedTypes(renderedTypes) {
  return [
    "/**",
    " * Generated from Nexolith's FastAPI OpenAPI contract.",
    " * Run `npm run api:generate`; do not edit by hand.",
    " */",
    "",
    renderedTypes.trimEnd(),
    "",
  ].join("\n");
}

/**
 * @param {string} path
 * @param {string} generated
 * @param {(path: string, encoding: "utf8") => Promise<string>} [reader]
 */
export async function generatedOutputMatches(path, generated, reader = readFile) {
  try {
    const current = await reader(path, "utf8");
    return normalizeLineEndings(current) === normalizeLineEndings(generated);
  } catch {
    return false;
  }
}
