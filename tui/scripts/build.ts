import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs } from "node:util";

const { values } = parseArgs({ args: Bun.argv.slice(2), options: {
  target: { type: "string" }, outfile: { type: "string" }, libc: { type: "string", default: "glibc" },
}, strict: true });
if (Bun.version !== "1.3.14") throw new Error(`Build with the pinned Bun 1.3.14 (found ${Bun.version})`);
if (!["glibc", "musl"].includes(values.libc!)) throw new Error("--libc must be glibc or musl");
await import("./generate-protocol");
const target = values.target ?? `bun-${process.platform === "win32" ? "windows" : process.platform}-${process.arch}`;
const outfile = resolve(values.outfile ?? `dist/vulnclaw-terminal${target.includes("windows") ? ".exe" : ""}`);
await mkdir(resolve(outfile, ".."), { recursive: true });
const result = await Bun.build({
  entrypoints: [resolve(import.meta.dir, "../src/main.tsx")],
  compile: { target: target as Bun.Build.CompileTarget, outfile },
  define: { "process.env.OPENTUI_LIBC": JSON.stringify(values.libc) },
  minify: true,
});
if (!result.success) throw new AggregateError(result.logs, "Terminal build failed");
process.stdout.write(`${outfile}\n`);
