import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

import { formatGeneratedTypes, generatedOutputMatches } from "./openapi-content.mjs";

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
const generated = formatGeneratedTypes(astToString(ast));

if (checkOnly) {
  if (!(await generatedOutputMatches(outputPath, generated))) {
    console.error("Generated API types are out of date. Run `npm run api:generate`.");
    process.exit(1);
  }
  console.log("Generated API types match the backend OpenAPI contract.");
} else {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
  console.log("Generated src/api/schema.d.ts from the backend OpenAPI contract.");
}
