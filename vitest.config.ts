import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["packages/*/src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["packages/*/src/**/*.ts"],
      exclude: ["**/*.test.ts"],
      // Floor only; ratchet upward as packages grow (see docs/conventions.md).
      thresholds: { lines: 90, functions: 90, branches: 85, statements: 90 },
    },
  },
});
