import { BoxRenderable, MarkdownRenderable, TextRenderable, type CliRenderer, type ScrollbackSurface } from "@opentui/core";
import type { SessionController, Card } from "../session/controller";
import type { MessageSnapshot } from "../transport/protocol.generated";
import { sharedMarkdownStyle, markdownNodes } from "../components/MarkdownBody";
import { visibleText } from "../components/content";
import { palettes } from "../theme/palette";

export class HistoryQueue {
  readonly committed = new Set<string>();
  error = "";
  private running: Promise<void> | null = null;
  private surface: ScrollbackSurface | null = null;
  private nextRow = 0;
  private currentId = "";
  private disposed = false;
  private requested = false;
  private pending: { id: string; message?: MessageSnapshot; card?: Card }[] = [];
  constructor(private renderer: CliRenderer, private controller: SessionController) {}

  drain(): Promise<void> {
    if (this.running) { this.requested = true; return this.running; }
    this.running = this.flush().catch((error: unknown) => {
      if (!this.disposed) {
        this.error = `History output failed: ${String(error)}`;
        this.controller.notify(this.error);
      }
    }).finally(() => {
      this.running = null;
      if (this.requested && !this.disposed && !this.error) { this.requested = false; void this.drain(); }
    });
    return this.running;
  }
  private async flush(): Promise<void> {
    if (this.error || this.disposed) return;
    const queued = new Set(this.pending.map((item) => item.id));
    for (const entry of this.controller.entries) {
      if (this.committed.has(entry.id) || queued.has(entry.id)) continue;
      const message = this.controller.messages.get(entry.id);
      const card = this.controller.cards.get(entry.id);
      if (!message && !card) continue;
      if (message?.status === "streaming") break;
      this.pending.push({ id: entry.id, message: message ? { ...message } : undefined, card: card ? { ...card } : undefined });
    }
    while (this.pending.length) {
      if (this.disposed) return;
      const entry = this.pending[0]!;
      const { message, card } = entry;
      if (!this.surface) {
        const surface = this.renderer.createScrollbackSurface({ startOnNewLine: true });
        this.surface = surface; this.currentId = entry.id; this.nextRow = 0;
        const theme = this.controller.theme;
        const p = palettes[theme];
        const box = new BoxRenderable(surface.renderContext, { flexDirection: "column", width: "100%" });
        const label = message ? `${message.source} · ${message.role} · ${message.status}` : `${card!.source} · ${card!.title}`;
        box.add(new TextRenderable(surface.renderContext, { content: visibleText(label), fg: p.accent }));
        const text = message?.text ?? card!.text;
        try {
          if (message?.format === "markdown" || card?.format === "markdown") box.add(new MarkdownRenderable(surface.renderContext, {
            content: visibleText(text), streaming: false, fg: p.text,
            syntaxStyle: sharedMarkdownStyle(this.renderer, theme),
            tableOptions: { widthMode: "full", wrapMode: "word", borderColor: p.accent },
            renderNode: markdownNodes(surface.renderContext, theme, surface.width),
          }));
          else box.add(new TextRenderable(surface.renderContext, { content: visibleText(text), wrapMode: "word", fg: p.text }));
          surface.root.add(box);
          await surface.settle();
        }
        catch (error) {
          surface.destroy();
          const fallback = this.renderer.createScrollbackSurface({ startOnNewLine: true });
          this.surface = fallback;
          fallback.root.add(new TextRenderable(fallback.renderContext, {
            content: `Markdown rendering failed: ${visibleText(String(error))}\n${visibleText(text)}`, wrapMode: "word",
          }));
          fallback.render();
        }
      }
      if (this.currentId !== entry.id) throw new Error("History order changed during a pending commit");
      const surface = this.surface!;
      // Parse and lay out once. Submit contiguous final rows within native snapshot capacity.
      const batch = Math.max(1, Math.min(128, Math.floor(200000 / surface.width)));
      while (this.nextRow < surface.height) {
        const end = Math.min(surface.height, this.nextRow + batch);
        surface.commitRows(this.nextRow, end, { trailingNewline: true });
        this.nextRow = end;
        await this.renderer.idle();
      }
      this.committed.add(entry.id);
      this.pending.shift();
      surface.destroy(); this.surface = null;
      this.controller.changed();
    }
  }
  retry(): Promise<void> { this.error = ""; return this.drain(); }
  dispose(): void { this.disposed = true; this.surface?.destroy(); this.surface = null; this.pending = []; }
}
