import type { Subprocess } from "bun";
import { encodeRequest, JsonlDecoder } from "./jsonl";
import type { ClientMessage, ServerEvent } from "./protocol.generated";

export interface SessionTransport {
  send(message: ClientMessage): void;
  close(): Promise<void>;
}

export class PythonBackend implements SessionTransport {
  private child: Subprocess<"pipe", "pipe", "pipe">;
  private closing = false;
  readonly completed: Promise<void>;

  constructor(
    onEvent: (event: ServerEvent) => void,
    onError: (error: Error) => void,
    options: { python?: string; env?: Record<string, string | undefined>; cwd?: string; onDiagnostic?: (text: string) => void } = {},
  ) {
    const python = options.python ?? process.env.VULNCLAW_PYTHON ?? "python3";
    this.child = Bun.spawn([python, "-u", "-m", "vulnclaw.session_backend"], {
      stdin: "pipe", stdout: "pipe", stderr: "pipe", cwd: options.cwd,
      env: { ...process.env, ...options.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    });
    this.completed = Promise.all([this.read(onEvent), this.diagnostics(options.onDiagnostic ?? ((text) => { process.stderr.write(text + "\n"); }))]).then(() => {}).catch((error: unknown) => {
      if (!this.closing) onError(error instanceof Error ? error : new Error(String(error)));
      this.child.kill();
    });
  }

  send(message: ClientMessage): void {
    if (this.closing || this.child.exitCode !== null) throw new Error("Backend is disconnected");
    this.child.stdin.write(encodeRequest(message));
    void this.child.stdin.flush();
  }

  private async diagnostics(onDiagnostic: (text: string) => void): Promise<void> {
    const decoder = new TextDecoder();
    let pending = "";
    for await (const bytes of this.child.stderr) {
      pending += decoder.decode(bytes, { stream: true });
      const lines = pending.split(/\r?\n/); pending = lines.pop()!;
      for (const line of lines) if (line) onDiagnostic(line);
    }
    pending += decoder.decode();
    if (pending) onDiagnostic(pending);
  }

  private async read(onEvent: (event: ServerEvent) => void): Promise<void> {
    const decoder = new JsonlDecoder();
    for await (const bytes of this.child.stdout) {
      for (const event of decoder.push(bytes)) onEvent(event);
    }
    decoder.finish();
    const code = await this.child.exited;
    if (!this.closing) throw new Error(`Backend disconnected (exit ${code})`);
  }

  async close(): Promise<void> {
    if (this.closing) return this.completed;
    if (this.child.exitCode === null) {
      this.send({ protocol_version: 2, type: "shutdown", request_id: "client-shutdown", payload: {} });
    }
    this.closing = true;
    this.child.stdin.end();
    const timer = setTimeout(() => this.child.kill(), 3000);
    try { await this.completed; }
    finally { clearTimeout(timer); }
  }
}
