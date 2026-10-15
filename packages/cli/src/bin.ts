#!/usr/bin/env node
import { main } from "./cli.js";

const controller = new AbortController();
process.on("SIGINT", () => controller.abort());
process.on("SIGTERM", () => controller.abort());

process.exitCode = await main(process.argv.slice(2), {
  cwd: process.cwd(),
  out: (t) => process.stdout.write(t),
  err: (t) => process.stderr.write(t),
  signal: controller.signal,
});
