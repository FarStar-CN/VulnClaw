import { expect, test } from "bun:test";
import { Terminal } from "@xterm/headless";

for (const history of [false, true]) {
  test.skipIf(process.platform === "win32")(`REPL ${history ? "conversation and streaming output" : "MCP startup"} survives native terminal resize`, async () => {
    const terminal = new Terminal({ cols: 120, rows: 40, scrollback: 5000, allowProposedApi: true });
    const backgroundChanges: string[] = [];
    let resets = 0, background = "#234567";
    terminal.parser.registerOscHandler(11, (value) => {
      if (value !== "?") backgroundChanges.push(value);
      return false;
    });
    terminal.parser.registerOscHandler(111, () => { resets++; return false; });
    let output = new Promise<void>((resolve) => terminal.write("SHELL-HISTORY\r\n", resolve));
    let lastOutput = Date.now(), anchor = "HISTORY-END";
    const child = Bun.spawn([process.execPath, `${import.meta.dir}/fixtures/repl-reflow.tsx`], {
      cwd: import.meta.dir,
      env: { ...process.env, TERM: "xterm-256color", COLORTERM: "truecolor",
        VULNCLAW_LANG: "en", REFLOW_HISTORY: history ? "1" : "0" },
      terminal: { cols: 120, rows: 40, data(_pty, data) {
        const bytes = new Uint8Array(data); lastOutput = Date.now();
        output = output.then(() => new Promise<void>((resolve) => terminal.write(bytes, resolve)));
      } },
    });
    terminal.onData((data) => child.terminal!.write(data));
    const lines = () => Array.from({ length: terminal.buffer.active.length }, (_, i) =>
      terminal.buffer.active.getLine(i)?.translateToString(true, 0, terminal.cols) ?? "");
    const count = (text: string, value: string) => text.split(value).length - 1;
    const ready = async () => {
      const deadline = Date.now() + 4000;
      while (Date.now() < deadline) {
        await Bun.sleep(20); await output;
        const rows = lines(), text = rows.join("\n");
        const status = rows.find((row) => row.includes("model: not configured"));
        if (Date.now() - lastOutput >= 120 && count(text, " > ") === 1
          && count(text, "model: not configured") === 1
          && status?.trimEnd().length === terminal.cols - 1
          && terminal.buffer.active.type === "alternate"
          && backgroundChanges.at(-1) === background
          && (!history || count(text, anchor) === 1)) return text;
      }
      throw new Error(`Terminal did not settle at ${terminal.cols}x${terminal.rows}:\n${lines().join("\n")}`);
    };
    const resize = (cols: number, rows: number) => {
      lastOutput = Date.now(); terminal.resize(cols, rows); child.terminal!.resize(cols, rows);
    };
    const check = (text: string) => {
      expect(count(text, " > ")).toBe(1);
      expect(count(text, "model: not configured")).toBe(1);
      expect(terminal.buffer.normal.getLine(0)?.translateToString(true)).toContain("SHELL-HISTORY");
      if (history) expect(text).toContain("draft 中文");
      else expect(text).toContain("Preparing MCP services (3/4)");
      const cellBackgrounds = new Set<string>();
      const inputTop = lines().findIndex((row) => row.includes(" > ")) - 1;
      for (let y = 0; y < terminal.rows; y++) {
        const line = terminal.buffer.active.getLine(y)!;
        for (let x = 0; x < terminal.cols; x++) {
          // The transcript scrollbar keeps its own track and thumb colors.
          if (x === terminal.cols - 1 && y < inputTop) continue;
          const cell = line.getCell(x)!;
          cellBackgrounds.add(cell.isBgDefault() ? "default" : `explicit:${cell.getBgColor()}`);
        }
      }
      expect([...cellBackgrounds]).toEqual(["default"]);
    };
    try {
      check(await ready());
      for (const [key, color] of [["\x1b[19~", "#654321"], ["\x1b[20~", "#345678"]]) {
        background = color; child.terminal!.write(key);
        check(await ready());
      }
      const sizes = [[60, 24], [160, 48], [30, 16], [240, 60], [80, 24], [120, 40]];
      for (const [cols, rows] of sizes) { resize(cols, rows); check(await ready()); }
      if (history) { anchor = "BULK-END"; child.terminal!.write("\x1b[15~"); }
      for (const [cols, rows] of [...sizes, ...sizes]) {
        resize(cols, rows); await Bun.sleep(10);
      }
      const text = await ready(); check(text);
      expect(backgroundChanges).toEqual(["#234567", "#654321", "#345678"]);
      if (history) {
        anchor = "HISTORY-BEGIN"; child.terminal!.write("\x1b[17~");
        expect(await ready()).toContain("history-0:");
        anchor = "BULK-END"; child.terminal!.write("\x1b[18~");
        expect(await ready()).toContain("bulk-299:");
      }
    } finally {
      child.terminal?.write("\x03\x03");
      await Promise.race([child.exited, Bun.sleep(1000)]);
      if (child.exitCode === null) child.kill("SIGKILL");
      child.terminal?.close(); await child.exited;
      await output;
      const restored = terminal.buffer.active.type;
      const shell = terminal.buffer.normal.getLine(0)?.translateToString(true);
      terminal.dispose();
      expect(restored).toBe("normal"); expect(shell).toContain("SHELL-HISTORY");
      expect(resets).toBe(1);
    }
  }, 15000);
}
