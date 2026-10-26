import { afterEach, expect, jest, test } from "bun:test";
import { SessionController } from "../src/session/controller";
import type { MessageEvent } from "../src/transport/protocol.generated";

afterEach(() => { jest.useRealTimers(); });
const message = (id: string, text: string, source = "main"): MessageEvent => ({
  protocol_version: 2, type: "message", task_id: "fixture", message_id: id,
  source, role: "assistant", format: "markdown", operation: "append", text,
});

test("appends update data and version synchronously while sharing a 32 ms notification", () => {
  jest.useFakeTimers();
  const controller = new SessionController(); let notifications = 0;
  const unsubscribe = controller.subscribe(() => { notifications++; });
  controller.receive(message("main", "A"));
  expect(notifications).toBe(1);
  controller.receive(message("main", "B"));
  expect(controller.messages.get("main")!.text).toBe("AB");
  expect(controller.snapshot()).toBe(2);
  expect(notifications).toBe(1);
  jest.advanceTimersByTime(31);
  controller.receive(message("main", "C"));
  expect(notifications).toBe(1);
  jest.advanceTimersByTime(1);
  expect(notifications).toBe(2);
  expect(controller.messages.get("main")!.text).toBe("ABC");
  controller.receive(message("child", "one", "child"));
  expect(notifications).toBe(3);
  controller.receive(message("child", "two", "child"));
  controller.receive(message("main", "D"));
  expect(jest.getTimerCount()).toBe(1);
  jest.advanceTimersByTime(32);
  expect(notifications).toBe(4);
  expect(controller.messages.get("child")!.text).toBe("onetwo");
  unsubscribe();
});

for (const terminal of ["completed", "cancelled", "failed", "approval", "disconnect", "input"] as const) {
  test(`${terminal} publishes the latest stream immediately and cancels pending work`, () => {
    jest.useFakeTimers();
    const controller = new SessionController(); let notifications = 0;
    const unsubscribe = controller.subscribe(() => { notifications++; });
    controller.receive(message("message", "first"));
    controller.receive(message("message", " last"));
    if (terminal === "approval") controller.receive({ protocol_version: 2, type: "approval_required",
      task_id: "fixture", request_hash: "a".repeat(64), question: "approval" });
    else if (terminal === "disconnect") controller.disconnect(new Error("closed"));
    else if (terminal === "input") { controller.draft = "typed"; controller.changed(); }
    else controller.receive({ ...message("message", ""), operation: "end", status: terminal });
    expect(notifications).toBe(2);
    expect(controller.messages.get("message")!.text).toBe("first last");
    expect(jest.getTimerCount()).toBe(0);
    jest.advanceTimersByTime(100);
    expect(notifications).toBe(2);
    unsubscribe();
  });
}

test("unsubscribing the last listener removes a pending timer; unobserved streams need none", () => {
  jest.useFakeTimers();
  const controller = new SessionController();
  const unsubscribe = controller.subscribe(() => {});
  controller.receive(message("message", "first")); controller.receive(message("message", " second"));
  expect(jest.getTimerCount()).toBe(1);
  unsubscribe();
  expect(jest.getTimerCount()).toBe(0);
  controller.receive(message("message", " third"));
  expect(jest.getTimerCount()).toBe(0);
  expect(controller.messages.get("message")!.text).toBe("first second third");
});
