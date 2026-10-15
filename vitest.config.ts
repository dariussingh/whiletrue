import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const src = (pkg: string) =>
  fileURLToPath(new URL(`./packages/${pkg}/src/index.ts`, import.meta.url));

export default defineConfig({
  resolve: {
    alias: [{ find: /^@whiletrue-ai\/(.+)$/, replacement: "$1", customResolver: (id) => src(id) }],
  },
  test: {
    include: ["packages/*/src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["packages/*/src/**/*.ts"],
      exclude: ["**/*.test.ts", "packages/*/src/bin.ts"],
      // Floor only; ratchet upward as packages grow (see docs/conventions.md).
      thresholds: { lines: 95, functions: 95, branches: 90, statements: 95 },
    },
  },
});
