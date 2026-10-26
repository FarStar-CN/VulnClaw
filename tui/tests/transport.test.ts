import { expect, test } from "bun:test";
import { JsonlDecoder, serverEvent } from "../src/transport/jsonl";
import { PythonBackend } from "../src/transport/backend";
import type { ServerEvent } from "../src/transport/protocol.generated";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test("UTF-8 decoder preserves multibyte characters across every byte boundary", () => {
  const event = { protocol_version: 2, type: "message", task_id: "t", message_id: "m",
    source: "main", role: "assistant", format: "markdown", operation: "append", text: "中文😀é" } as const;
  const bytes = new TextEncoder().encode(JSON.stringify(event) + "\n");
  const decoder = new JsonlDecoder();
  const decoded: ServerEvent[] = [];
  for (const byte of bytes) decoded.push(...decoder.push(new Uint8Array([byte])));
  decoder.finish();
  expect(decoded).toEqual([event]);
  expect(() => serverEvent({ ...event, protocol_version: 1 })).toThrow();
  expect(() => serverEvent({ ...event, operation: "end", text: undefined })).toThrow();
  expect(() => serverEvent({ ...event, unexpected: true })).toThrow();
  const partial = new JsonlDecoder();
  partial.push(bytes.slice(0, -1));
  expect(() => partial.finish()).toThrow("incomplete");
});

test("JSONL preserves multiple events, CRLF and whitespace at every two-chunk split", () => {
  const events = ["中文😀é", "second"].map((message) => ({
    protocol_version: 2 as const, type: "log" as const, task_id: "fixture", message,
  }));
  const bytes = Buffer.from(` \r\n${JSON.stringify(events[0])}\r\n\t\n${JSON.stringify(events[1])}\n `);
  for (let cut = 0; cut <= bytes.length; cut++) {
    const decoder = new JsonlDecoder();
    expect([...decoder.push(bytes.subarray(0, cut)), ...decoder.push(bytes.subarray(cut))]).toEqual(events);
    decoder.finish();
  }
});

test("JSONL retains a large fragmented body exactly and rejects invalid input", () => {
  const event = { protocol_version: 2, type: "log", task_id: "fixture",
    message: "中文😀é".repeat(40000) } as const;
  const bytes = Buffer.from(JSON.stringify(event) + "\n");
  const decoder = new JsonlDecoder(), events: ServerEvent[] = [];
  for (let offset = 0; offset < bytes.length; offset += 4096) {
    events.push(...decoder.push(bytes.subarray(offset, offset + 4096)));
  }
  decoder.finish();
  expect(events).toEqual([event]);
  for (const text of ["{invalid}\n", '{"protocol_version":1}\n']) {
    expect(() => new JsonlDecoder().push(Buffer.from(text))).toThrow();
  }
  expect(() => new JsonlDecoder().push(new Uint8Array([0xff]))).toThrow();
  const incompleteUtf8 = new JsonlDecoder();
  incompleteUtf8.push(new Uint8Array([0xf0, 0x9f]));
  expect(() => incompleteUtf8.finish()).toThrow();
});

test("Python session initializes, reads state and shuts down with one subprocess", async () => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-terminal-"));
  // Real application runtime, with external MCP services explicitly disabled.
  await writeFile(join(directory, "config.yaml"), "mcp:\n  servers:\n    fetch:\n      enabled: false\n    memory:\n      enabled: false\n    chrome-devtools:\n      enabled: false\n    burp:\n      enabled: false\n");
  const events: ServerEvent[] = [];
  const diagnostics: string[] = [];
  let resolveReady!: () => void;
  let rejectReady!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const backend = new PythonBackend((event) => {
    events.push(event);
    if (event.type === "ready") resolveReady();
    if (event.type === "error") rejectReady(new Error(event.message));
  }, rejectReady, {
    python: process.env.VULNCLAW_PYTHON ?? (process.platform === "win32" ? "python" : resolve("../.venv/bin/python")), cwd: resolve(".."),
    env: { VULNCLAW_CONFIG_DIR: directory }, onDiagnostic: (text) => diagnostics.push(text),
  });
  try {
    backend.send({ protocol_version: 2, type: "initialize", request_id: "init", payload: {} });
    await ready;
    backend.send({ protocol_version: 2, type: "get_state", request_id: "state", payload: {} });
    await backend.close();
    expect(events.map((event) => event.type)).toEqual(["ready", "state", "shutdown_complete"]);
  } finally {
    await backend.close();
    await rm(directory, { recursive: true, force: true });
  }
}, 20000);
