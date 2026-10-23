import { expect, test } from "bun:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadPreferences, restoreClientSession, saveClientSession, writeJson } from "../src/platform/preferences";
import { SessionController } from "../src/session/controller";

test("existing session history/mode survive while old layout/theme reset and permission is ignored", async () => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-prefs-"));
  try {
    await writeJson(join(directory, "layout.json"), { theme: "orange", primary_width: 35 });
    await writeJson(join(directory, "session.json"), { mode: "YOLO", permission: "full_access", history: ["hello 中文", 42], transcript: [{ text: "untrusted old business state" }], findings: [{ id: "old" }] });
    const preferences = await loadPreferences(directory);
    expect(preferences.theme).toBe("blue");
    expect(preferences.layout.primaryWidth).toBe(28);
    const controller = new SessionController();
    expect(await restoreClientSession(controller, directory)).toBe("agent");
    expect(controller.history).toEqual(["hello 中文"]);
    expect(controller.permission).toBe("ask");
    expect(controller.messages.size).toBe(0); expect(controller.findings).toHaveLength(0);
    await saveClientSession(controller, directory);
    expect(JSON.parse(await readFile(join(directory, "session.json"), "utf-8"))).toEqual({ mode: "Agent", history: ["hello 中文"] });
    expect(JSON.parse(await readFile(join(directory, "layout.json"), "utf-8"))).toHaveProperty("format_version", 2);
    await writeJson(join(directory, "session.json"), { mode: "full_access", history: ["retained"] });
    expect(await restoreClientSession(controller, directory)).toBeNull();
    expect(controller.history).toEqual(["retained"]);
    expect(controller.permission).toBe("ask");
  } finally { await rm(directory, { recursive: true, force: true }); }
});
