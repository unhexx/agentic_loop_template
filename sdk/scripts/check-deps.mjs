import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const require = createRequire(join(root, "package.json"));

const required = Object.keys(pkg.dependencies ?? {});
const missing = [];
for (const name of required) {
  try {
    require.resolve(name);
  } catch {
    missing.push(name);
  }
}

if (missing.length) {
  console.error("missing dependencies in sdk/: " + missing.join(", "));
  console.error("run: npm ci --prefix sdk");
  process.exit(1);
}

console.log("sdk dependencies present: " + required.join(", "));
