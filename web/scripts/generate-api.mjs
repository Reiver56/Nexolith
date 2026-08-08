import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(webRoot, "..");
const outputPath = resolve(webRoot, "src", "api", "schema.d.ts");
const checkOnly = process.argv.includes("--check");

const exported = spawnSync(
  "uv",
  ["run", "--project", repositoryRoot, "python", "scripts/export_openapi.py"],
  {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: false,
  },
);

if (exported.error) {
  throw exported.error;
}
if (exported.status !== 0) {
  process.stderr.write(exported.stderr);
  process.exit(exported.status ?? 1);
}

const ast = await openapiTS(exported.stdout);
const generated = [
  "/**",
  " * Generated from Nexolith's FastAPI OpenAPI contract.",
  " * Run `npm run api:generate`; do not edit by hand.",
  " */",
  "",
  astToString(ast).trimEnd(),
  "",
].join("\n");

if (checkOnly) {
  let current = "";
  try {
    current = await readFile(outputPath, "utf8");
  } catch {
    // A missing generated contract is drift too.
  }
  if (current !== generated) {
    console.error("Generated API types are out of date. Run `npm run api:generate`.");
    process.exit(1);
  }
  console.log("Generated API types match the backend OpenAPI contract.");
} else {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
  console.log("Generated src/api/schema.d.ts from the backend OpenAPI contract.");
}
