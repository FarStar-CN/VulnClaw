import { expect, mock, test } from "bun:test";
import { act } from "react";
import type { TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { ScopeForm, type ScopePreview } from "../src/components/ScopeForm";
import { scopeSummary } from "../src/session/controller";
import { finalFrame } from "./render-helpers";

const initial: ScopePreview = {
  target: "https://current.test", scope: { only_host: null, blocked_host: "blocked.test", allowed_ports: [443],
    blocked_ports: null, only_path: "/api", blocked_path: null, only_port: null,
    allow_actions: ["recon", "report"], block_actions: ["scan", "exploit", "post_exploitation", "persistent"] },
  constraints: { allowed_hosts: ["current.test"], allowed_ports: [443], allowed_paths: ["/api"] },
  actions: ["recon"], available_actions: ["recon", "scan", "exploit", "post_exploitation"],
  action_policy: { allow_actions: ["recon", "report"], block_actions: ["scan", "exploit", "post_exploitation", "persistent"] },
  presets: [{ name: "Quick", actions: ["recon"] }, { name: "Standard", actions: ["recon", "scan"] }], errors: {},
};

async function renderForm({ readOnly = false, width = 90, height = 34 } = {}) {
  const onSave = mock(async (scope: Record<string, unknown>, _reset: boolean): Promise<ScopePreview> => ({ ...initial, scope, errors: {} }));
  const onSaved = mock(() => {}), onCancel = mock(() => {});
  const onPreview = mock(async (scope: Record<string, unknown>): Promise<ScopePreview> => ({ ...initial, scope, errors: {} }));
  const setup = await testRender(<ScopeForm initial={initial} loading={false} active readOnly={readOnly} theme="blue" summary={scopeSummary}
    onPreview={onPreview} onSave={onSave} onSaved={onSaved} onCancel={onCancel} />, { width, height, kittyKeyboard: true });
  await finalFrame(setup);
  async function select(index: number) {
    // Scroll and keyboard navigation work in narrow terminals too.
    await act(async () => { for (let i = 0; i < index; i++) setup.mockInput.pressArrow("down"); });
    await finalFrame(setup);
  }
  async function edit(text: string) {
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    await act(async () => { (setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable).setText(text); setup.mockInput.pressKey("s", { ctrl: true }); });
    await finalFrame(setup);
  }
  return { setup, select, edit, onSave, onSaved, onCancel, onPreview };
}

test("scope edits clear a port list, preserve untouched constraints, and Ctrl+S while editing only commits the field", async () => {
  const form = await renderForm();
  try {
    await form.select(3); await form.edit("");
    expect(form.onSave).not.toHaveBeenCalled();
    await act(async () => { form.setup.mockInput.pressKey("s", { ctrl: true }); });
    expect(form.onSave).toHaveBeenCalledTimes(1);
    expect(form.onSave.mock.calls[0]?.[0]).toEqual({ ...initial.scope, allowed_ports: "" });
    expect(form.onSave.mock.calls[0]?.[1]).toBe(false);
    expect(form.onSaved).toHaveBeenCalledTimes(1);
  } finally { await act(async () => { await form.setup.dispose(); }); }
});

test("scope keeps invalid text and field errors after a save failure, then permits a corrected save", async () => {
  const form = await renderForm();
  form.onPreview.mockImplementation(async (scope) => ({ ...initial, scope, errors: { allowed_ports: "Bad port list" } }));
  form.onSave.mockImplementationOnce(async (scope) => ({ scope, errors: { allowed_ports: "Bad port list" } }));
  try {
    await form.select(3); await form.edit("443,oops");
    expect(await finalFrame(form.setup)).toContain("443,oops");
    await act(async () => { form.setup.mockInput.pressKey("y"); });
    expect(await finalFrame(form.setup)).toContain("Bad port list");
    expect(form.onSaved).not.toHaveBeenCalled();
    form.onPreview.mockImplementation(async (scope) => ({ ...initial, scope, errors: {} }));
    await form.edit("80,443");
    form.onSave.mockImplementationOnce(async () => { throw new Error("Save unavailable"); });
    await act(async () => { form.setup.mockInput.pressKey("y"); });
    expect(await finalFrame(form.setup)).toContain("Save unavailable");
    expect(await finalFrame(form.setup)).toContain("80,443");
    await act(async () => { form.setup.mockInput.pressKey("y"); });
    expect(form.onSaved).toHaveBeenCalledTimes(1);
  } finally { await act(async () => { await form.setup.dispose(); }); }
});

for (const index of [7, 9]) test(`scope action selection at row ${index} retains non-test action policy`, async () => {
  const form = await renderForm();
  try {
    await form.select(index);
    await act(async () => { form.setup.mockInput.pressEnter(); });
    const scope = form.onPreview.mock.calls[0]?.[0];
    expect(scope?.allow_actions).toEqual(["report", "recon", "scan"]);
    expect(scope?.block_actions).toEqual(["persistent", "exploit", "post_exploitation"]);
    expect(form.onSave).not.toHaveBeenCalled();
    await act(async () => { form.setup.mockInput.pressKey("n"); });
    expect(form.onCancel).toHaveBeenCalledTimes(1);
  } finally { await act(async () => { await form.setup.dispose(); }); }
});

test("reset changes only the draft, preserves target-derived restrictions, and mouse confirmation uses reset", async () => {
  const form = await renderForm();
  form.onPreview.mockImplementation(async () => ({ ...initial, scope: {}, actions: initial.available_actions,
    constraints: { allowed_hosts: ["current.test"] }, errors: {} }));
  try {
    await form.select(13);
    await act(async () => { form.setup.mockInput.pressEnter(); });
    expect(form.onSave).not.toHaveBeenCalled();
    expect(await finalFrame(form.setup)).toContain("current.test");
    const confirm = form.setup.renderer.root.findDescendantById("form-row-14")!;
    await act(async () => { await form.setup.mockMouse.click(confirm.x + 2, confirm.y); });
    expect(form.onSave.mock.calls[0]).toEqual([{}, true]);
    expect(form.onSaved).toHaveBeenCalledTimes(1);
  } finally { await act(async () => { await form.setup.dispose(); }); }
});

for (const key of ["y", "s"] as const) test(`active task scope form forbids saving with ${key}, scrolls in a narrow terminal and cancels`, async () => {
  const form = await renderForm({ readOnly: true, width: 44, height: 16 });
  try {
    await form.select(12);
    expect(await finalFrame(form.setup)).toContain("current.test");
    await act(async () => { form.setup.mockInput.pressKey(key, { ctrl: key === "s" }); });
    expect(form.onSave).not.toHaveBeenCalled();
    await act(async () => { form.setup.mockInput.pressEscape(); });
    expect(form.onCancel).toHaveBeenCalledTimes(1);
  } finally { await act(async () => { await form.setup.dispose(); }); }
});

test("cancelled field preview does not apply a late validation result", async () => {
  const form = await renderForm();
  let finish!: (value: ScopePreview) => void;
  form.onPreview.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  try {
    await form.select(3); await form.edit("80");
    await act(async () => { form.setup.mockInput.pressEscape(); });
    expect(form.onCancel).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ scope: { allowed_ports: [12345] }, errors: {} }); });
    expect(await finalFrame(form.setup)).not.toContain("12345");
    expect(form.onSave).not.toHaveBeenCalled();
  } finally { await act(async () => { await form.setup.dispose(); }); }
});
