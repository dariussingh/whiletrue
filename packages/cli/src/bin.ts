#!/usr/bin/env node
// node:sqlite is stable enough for us but still prints an ExperimentalWarning; hide only that one.
const emitWarning = process.emitWarning.bind(process);
process.emitWarning = ((warning: string | Error, ...rest: unknown[]) => {
  const text = typeof warning === "string" ? warning : warning.message;
  if (text.includes("SQLite")) return;
  (emitWarning as (...args: unknown[]) => void)(warning, ...rest);
}) as typeof process.emitWarning;

const { main } = await import("./cli.js");

const controller = new AbortController();
process.on("SIGINT", () => controller.abort());
process.on("SIGTERM", () => controller.abort());

process.exitCode = await main(process.argv.slice(2), {
  cwd: process.cwd(),
  out: (t) => process.stdout.write(t),
  err: (t) => process.stderr.write(t),
  signal: controller.signal,
});
