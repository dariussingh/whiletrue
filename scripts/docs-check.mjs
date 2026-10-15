// docs:check — validates that markdown links between repo docs resolve.
// Starts minimal (link validation); extend with example/CLI-reference checks.
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const skip = new Set(["node_modules", ".git", "dist", "coverage", ".whiletrue"]);

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    if (skip.has(name)) return [];
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith(".md") ? [p] : [];
  });
}

let errors = 0;
for (const file of walk(root)) {
  const text = readFileSync(file, "utf8").replace(/```[\s\S]*?```/g, "");
  for (const m of text.matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)) {
    const target = m[1].split("#")[0];
    if (!target || /^[a-z]+:/i.test(target)) continue;
    if (!existsSync(resolve(dirname(file), target))) {
      console.error(`${file.slice(root.length + 1)}: broken link -> ${m[1]}`);
      errors++;
    }
  }
}
if (errors) process.exit(1);
console.log("docs:check ok");
