import { expect, test } from "bun:test";
import { YAML } from "bun";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { configurationFile, loadStartupAppearance, writeJson } from "../src/platform/preferences";
import { defaultThemeConfig } from "../src/theme/palette";
import { SessionController } from "../src/session/controller";
import { currentLanguage, setLanguage } from "../src/i18n";

test("configured language and theme apply together before backend readiness", async () => {
  const previous = currentLanguage();
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-startup-appearance-"));
  try {
    setLanguage("en");
    await writeFile(join(directory, "config.yaml"), "session:\n  language: zh\nterminal:\n  theme: orange\n");
    const { terminal, language } = await loadStartupAppearance(join(directory, "config.yaml"), directory);
    const controller = new SessionController();
    controller.applyConfigDocument({ config: { terminal, session: { language } } });
    expect(controller.connected).toBe(false);
    expect(currentLanguage()).toBe("zh");
    expect(controller.theme).toBe("orange");
  } finally { setLanguage(previous); await rm(directory, { recursive: true, force: true }); }
});

test("startup uses the backend config directory independently of the client directory", () => {
  expect(configurationFile({ VULNCLAW_HOME: "/client" }, "/home/test")).toBe("/home/test/.vulnclaw/config.yaml");
  expect(configurationFile({ VULNCLAW_HOME: "/client", VULNCLAW_CONFIG_DIR: "/config" })).toBe("/config/config.yaml");
});

test("startup reads named and custom themes without a backend and ignores unrelated configuration", async () => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-startup-theme-"));
  const configFile = join(directory, "config.yaml");
  try {
    expect((await loadStartupAppearance(configFile, directory)).terminal).toEqual(defaultThemeConfig);
    await writeFile(configFile, "terminal:\n  theme: orange\nllm:\n  api_key: synthetic-not-retained\n");
    expect(await loadStartupAppearance(configFile, directory)).toEqual({
      terminal: { ...defaultThemeConfig, theme: "orange" }, migration: null, language: "auto",
    });
    const terminal = { theme: "forest", themes: {
      forest: { ...defaultThemeConfig.themes.blue, background: "#123456" },
    } };
    await writeFile(configFile, YAML.stringify({ terminal }));
    expect(await loadStartupAppearance(configFile, directory)).toEqual({ terminal, migration: null, language: "auto" });
    await writeJson(join(directory, "themes.json"), { themes: { forest: { action: "#abcdef" } } });
    expect((await loadStartupAppearance(configFile, directory)).migration).toBeNull();
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("legacy theme is available for the first frame and scheduled for one migration", async () => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-startup-legacy-"));
  try {
    await writeJson(join(directory, "layout.json"), { format_version: 2, theme: "forest" });
    await writeJson(join(directory, "themes.json"), { themes: { forest: { background: "#123456" } } });
    const loaded = await loadStartupAppearance(join(directory, "config.yaml"), directory);
    expect(loaded.terminal.theme).toBe("forest");
    expect(loaded.terminal.themes.forest.background).toBe("#123456");
    expect(loaded.migration).toEqual(loaded.terminal);
    await writeFile(join(directory, "config.yaml"), YAML.stringify({ terminal: loaded.terminal }));
    expect((await loadStartupAppearance(join(directory, "config.yaml"), directory)).migration).toBeNull();
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test.each([
  "terminal: [invalid]", "terminal: null", "terminal:\n  theme: missing",
  "terminal:\n  themes:\n    blue:\n      background: red", "[invalid", "- not-an-object",
])("invalid startup theme reports an error and does not silently load legacy data: %s", async (yaml) => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-startup-invalid-"));
  try {
    await writeFile(join(directory, "config.yaml"), yaml);
    await writeJson(join(directory, "themes.json"), { themes: { blue: { background: "#123456" } } });
    await expect(loadStartupAppearance(join(directory, "config.yaml"), directory)).rejects.toThrow();
  } finally { await rm(directory, { recursive: true, force: true }); }
});
