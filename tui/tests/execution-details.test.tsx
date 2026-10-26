import { expect, test, mock } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { Transcript } from "../src/components/Transcript";
import { record, SessionController } from "../src/session/controller";
import { ExecutionDetailCache, type DetailPage } from "../src/session/details";
import type { ExecutionDetailRef } from "../src/transport/protocol.generated";
import { serverEvent } from "../src/transport/jsonl";
import { finalFrame } from "./render-helpers";

const ref = (id: string): ExecutionDetailRef => ({ id, bytes: 100_000, preview: "preview" });
const page = (id: string, text = "😀", offset = 0): DetailPage => ({
  id, offset, next_offset: offset + Buffer.byteLength(text), bytes: 100_000, text,
});

test("detail references are validated with the execution event schema", () => {
  const event = { protocol_version: 2, type: "tool_execution", task_id: "t", call_id: "c",
    operation_id: "o", kind: "http", status: "completed", detail_refs: { response_body: ref("body") } } as const;
  expect(serverEvent(event)).toBe(event);
  for (const detail_refs of [{ unknown: ref("body") }, { stdout: { id: "body", bytes: -1, preview: "" } },
    { stdout: { id: "body", preview: "" } }]) {
    expect(() => serverEvent({ ...event, detail_refs })).toThrow("Invalid session event");
  }
});

test("large external results use the same paged reader as operation bodies", async () => {
  const controller = new SessionController();
  const load = mock(async () => ({ id: "result", offset: 0, next_offset: 12, bytes: 12, text: "full result!" }));
  controller.control = load;
  controller.receive({ protocol_version: 2, type: "tool_call", task_id: "t", call_id: "mcp",
    tool: "browser_snapshot", arguments: "{}" });
  controller.receive({ protocol_version: 2, type: "tool_result", task_id: "t", call_id: "mcp",
    result: "preview", result_ref: { id: "result", bytes: 12, preview: "preview" } });
  const setup = await testRender(<Transcript controller={controller} width={68} positions={new Map()} />,
    { width: 70, height: 22 });
  try {
    await finalFrame(setup); expect(load).not.toHaveBeenCalled();
    const id = `execution-${[...controller.cards.keys()][0]}`;
    const node = setup.renderer.root.findDescendantById(id)!;
    await act(async () => { await setup.mockMouse.click(node.x + 3, node.y); });
    expect(await finalFrame(setup)).toContain("full result!");
    expect(load).toHaveBeenCalledWith("execution.details.read", { id: "result", offset: 0 });
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("detail cache deduplicates requests and evicts least recently read pages by UTF-8 bytes", async () => {
  const load = mock(async (id: string, offset: number) => page(id, "😀", offset));
  const cache = new ExecutionDetailCache(load, 8);
  const a = cache.read(ref("a"));
  expect(cache.read(ref("a"))).toBe(a);
  await a; await cache.read(ref("b")); await cache.read(ref("a")); await cache.read(ref("c"));
  expect(load).toHaveBeenCalledTimes(3);
  await cache.read(ref("b"));
  expect(load).toHaveBeenCalledTimes(4);
  await cache.read(ref("b"), 4);
  expect(load).toHaveBeenLastCalledWith("b", 4);
});

test("failed and oversized pages are not retained, and reset ignores late requests", async () => {
  let resolve!: (value: DetailPage) => void;
  const load = mock(() => new Promise<DetailPage>((done) => { resolve = done; }));
  const cache = new ExecutionDetailCache(load, 4);
  const pending = cache.read(ref("a"));
  cache.clear(); resolve(page("a")); await pending;
  const next = cache.read(ref("a"));
  expect(load).toHaveBeenCalledTimes(2);
  resolve(page("a", "oversized")); await next;
  const oversized = cache.read(ref("a"));
  expect(load).toHaveBeenCalledTimes(3);
  resolve(page("a")); await oversized;
  const failedLoad = mock(async (): Promise<DetailPage> => { throw new Error("read failed"); });
  const failing = new ExecutionDetailCache(failedLoad);
  await expect(failing.read(ref("b"))).rejects.toThrow("read failed");
  await expect(failing.read(ref("b"))).rejects.toThrow("read failed");
  expect(failedLoad).toHaveBeenCalledTimes(2);
});

test("operation updates replace inline bodies and references without retaining stale text", () => {
  const controller = new SessionController();
  const event = { protocol_version: 2, type: "tool_execution", task_id: "t", call_id: "c",
    operation_id: "o", kind: "command", status: "running" } as const;
  controller.receive({ ...event, stdout: "old output" });
  controller.receive({ ...event, detail_refs: { stdout: ref("large") } });
  const record = [...controller.cards.values()][0]!.execution!;
  expect(record.operations.get("o")!.stdout).toBeUndefined();
  controller.receive({ ...event, status: "completed" });
  expect(record.operations.get("o")!.detail_refs!.stdout!.id).toBe("large");
  controller.receive({ ...event, stdout: "small replacement" });
  expect(record.operations.get("o")!.detail_refs!.stdout).toBeUndefined();
  expect(record.operations.get("o")!.stdout).toBe("small replacement");
});

test("folded details load only on opening, paginate in both directions, and retry failed reads", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  const requests: number[] = [];
  let fail = true;
  controller.attach({
    send(message) {
      if (message.type !== "control") return;
      const offset = Number(record(message.payload.arguments).offset ?? 0); requests.push(offset);
      queueMicrotask(() => controller.receive(fail
        ? { protocol_version: 2, type: "error", request_id: message.request_id, code: "invalid_control", message: "read failed" }
        : { protocol_version: 2, type: "control_result", request_id: message.request_id,
          operation: "execution.details.read", result: {
            id: "detail", bytes: 20, offset, next_offset: offset + 10,
            text: offset ? "page-two!!" : "page-one!!",
          } }));
    },
    async close() {},
  });
  controller.receive({ protocol_version: 2, type: "tool_execution", task_id: "t", call_id: "c",
    operation_id: "o", kind: "command", status: "completed", command: "printf example",
    detail_refs: { stdout: { id: "detail", bytes: 20, preview: "page-one!!" } } });
  const setup = await testRender(<Transcript controller={controller} width={68} positions={new Map()} focused />,
    { width: 70, height: 22, kittyKeyboard: true });
  const id = `execution-${[...controller.cards.keys()][0]}`;
  const click = async (target: string) => {
    await finalFrame(setup);
    const node = setup.renderer.root.findDescendantById(target)!;
    await act(async () => { await setup.mockMouse.click(node.x + 3, node.y); });
    return finalFrame(setup);
  };
  try {
    await finalFrame(setup); expect(requests).toEqual([]);
    expect(await click(id)).toContain("read failed");
    fail = false;
    expect(await click(`${id}-op-stdout-retry`)).toContain("page-one!!");
    expect(await click(`${id}-op-stdout-next`)).toContain("page-two!!");
    expect(await click(`${id}-op-stdout-previous`)).toContain("page-one!!");
    expect(requests).toEqual([0, 0, 10]);
    await click(id);
    expect(setup.renderer.root.findDescendantById(`${id}-op-stdout-content`)).toBeUndefined();
    await click(id); expect(requests).toEqual([0, 0, 10]);
  } finally { await act(async () => { await setup.dispose(); }); }
});
