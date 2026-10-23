import { expect, test, spyOn } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import type { TextareaRenderable } from "@opentui/core";
import { TaskForm } from "../src/components/TaskForm";
import { SessionController, record } from "../src/session/controller";
import { finalFrame } from "./render-helpers";

const initial = {
  task: { command: "run", target: "valid.test", resume: true, prompt: "retained instructions", options: {
    continuous: false, allow_actions: ["recon", "scan"], block_actions: ["exploit", "post_exploitation"], poc_verify: false, auto_report: true,
  } }, errors: {}, available_actions: ["recon", "scan", "exploit", "post_exploitation"],
  presets: [{ name: "Quick", actions: ["recon"] }, { name: "Standard", actions: ["recon", "scan"] }],
  defaults: { rounds_per_cycle: 17, max_cycles: 0, auto_report: true, continuous_auto_report: false },
};

test("task form keeps the complete editable draft after backend validation fails", async () => {
  const controller = new SessionController(); controller.draft = "composer survives";
  const requests: Record<string, unknown>[] = [];
  const control = spyOn(controller, "control").mockImplementation(async (_operation, args) => {
    const task = record(args?.task); requests.push(task);
    return task.target === "bad target" ? { errors: { target: "Invalid target" } } : { ...initial, task, errors: {} };
  });
  const started = spyOn(controller, "startTask").mockImplementation(() => {});
  const setup = await testRender(<TaskForm controller={controller} initial={initial} original="/run valid.test" active />, { width: 90, height: 32, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); });
    await finalFrame(setup);
    await act(async () => {
      const editor = setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable;
      editor.setText(""); await setup.mockInput.typeText("bad target"); setup.mockInput.pressEnter();
    });
    let frame = await finalFrame(setup);
    expect(frame).toContain("Invalid target"); expect(frame).toContain("bad target");
    expect(frame).toContain("Action preset"); expect(frame).not.toContain("Rounds per cycle");
    expect(requests[0]?.prompt).toBe("retained instructions"); expect(started).not.toHaveBeenCalled();
    await act(async () => { setup.mockInput.pressEnter(); });
    await finalFrame(setup);
    await act(async () => {
      (setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable).setText("");
      await setup.mockInput.typeText("fixed.test"); setup.mockInput.pressEnter();
    });
    frame = await finalFrame(setup); expect(frame).toContain("fixed.test"); expect(frame).not.toContain("Invalid target");
    await act(async () => { setup.mockInput.pressKey("s", { ctrl: true }); });
    await finalFrame(setup);
    expect(started).toHaveBeenCalledTimes(1);
    expect(started.mock.calls[0]?.[0].target).toBe("fixed.test");
    expect(controller.draft).toBe("composer survives");
  } finally { control.mockRestore(); started.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("task form preserves cycle values while sending only options for the chosen mode", async () => {
  const controller = new SessionController();
  const requests: Record<string, unknown>[] = [];
  const control = spyOn(controller, "control").mockImplementation(async (_operation, args) => {
    const task = record(args?.task); requests.push(task); return { ...initial, task, errors: {} };
  });
  const setup = await testRender(<TaskForm controller={controller} initial={initial} original="/run valid.test" active />, { width: 90, height: 32, kittyKeyboard: true });
  async function activate(index: number) {
    const row = setup.renderer.root.findDescendantById(`form-row-${index}`)!;
    await act(async () => { await setup.mockMouse.pressDown(row.x + 2, row.y); await setup.mockMouse.release(row.x + 2, row.y); setup.mockInput.pressEnter(); });
    await finalFrame(setup);
  }
  try {
    await finalFrame(setup);
    await activate(12); // Continuous execution after seven fields, presets and four actions.
    expect(await finalFrame(setup)).toContain("unlimited (0)");
    expect(record(requests.at(-1)?.options).rounds_per_cycle).toBe(17);
    await activate(13);
    await act(async () => {
      (setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable).setText("37");
      setup.mockInput.pressEnter();
    });
    await finalFrame(setup);
    await activate(12);
    expect(record(requests.at(-1)?.options).continuous).toBe(false);
    expect(record(requests.at(-1)?.options)).not.toHaveProperty("rounds_per_cycle");
    expect(record(requests.at(-1)?.options)).not.toHaveProperty("max_cycles");
    await activate(12);
    expect(record(requests.at(-1)?.options).rounds_per_cycle).toBe(37);
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("all four backend actions remain editable even when a preset omits post exploitation", async () => {
  const controller = new SessionController();
  const control = spyOn(controller, "control").mockImplementation(async (_operation, args) => ({ ...initial, task: args?.task, errors: {} }));
  const setup = await testRender(<TaskForm controller={controller} initial={initial} original="/run valid.test" active />, { width: 90, height: 32, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("post_exploit");
    const row = setup.renderer.root.findDescendantById("form-row-11")!;
    await act(async () => { await setup.mockMouse.click(row.x + 2, row.y); setup.mockInput.pressEnter(); }); await finalFrame(setup);
    const options = record(record(control.mock.calls.at(-1)?.[1]?.task).options);
    expect(options.allow_actions).toEqual(["recon", "scan", "post_exploitation"]);
    expect(options.block_actions).toEqual(["exploit"]);
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});
