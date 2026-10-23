import "./syntax";
import React, { memo, useEffect, useMemo, useState } from "react";
import { BoxRenderable, CodeRenderable, TextRenderable, type MarkdownOptions, type Renderable, type CliRenderer, type RenderContext } from "@opentui/core";
import { useRenderer } from "@opentui/react";
import type { Tokens } from "marked";
import { visibleText } from "./content";
import { markdownStyle, palettes, type Palette, type ThemeId } from "../theme/palette";
import { usePalette } from "../theme/usePalette";
import { t, useI18n, currentLanguage } from "../i18n";

export interface MarkdownBodyProps {
  id: string; text: string; streaming?: boolean; width: number; theme: ThemeId;
}

const errorHandlers = new WeakMap<CliRenderer, Map<string, (error: string) => void>>();
const styles = new WeakMap<CliRenderer, Map<Palette, ReturnType<typeof markdownStyle>>>();
export function sharedMarkdownStyle(renderer: CliRenderer, theme: ThemeId) {
  let cache = styles.get(renderer);
  if (!cache) {
    cache = new Map(); styles.set(renderer, cache);
    renderer.on("destroy", () => { for (const style of cache!.values()) style.destroy(); cache!.clear(); });
  }
  const palette = palettes[theme];
  let style = cache.get(palette);
  if (!style) { style = markdownStyle(renderer, theme); cache.set(palette, style); }
  return style;
}

/** Shared node adaptation for live messages, transcript messages and completed scrollback. */
export function markdownNodes(renderer: RenderContext, theme: ThemeId, width: number): MarkdownOptions["renderNode"] {
  return (token, context) => {
    if (token.type === "def") return new BoxRenderable(renderer, { height: 0 });
    if (["paragraph", "heading", "text"].includes(token.type) && "tokens" in token && Array.isArray(token.tokens)) {
      const images: Tokens.Image[] = [];
      const visit = (tokens: Tokens.Generic[]) => {
        for (const child of tokens) {
          if (child.type === "image") images.push(child as Tokens.Image);
          else if (Array.isArray(child.tokens)) visit(child.tokens);
        }
      };
      visit(token.tokens);
      if (images.length) {
        const box = new BoxRenderable(renderer, { flexDirection: "column", width: "100%" });
        const body = context.defaultRender(); if (body) box.add(body);
        for (const image of images) box.add(new TextRenderable(renderer, {
          content: visibleText(`Image: ${image.text || "image"} (${image.href})`), wrapMode: "word", fg: palettes[theme].muted,
        }));
        return box;
      }
    }
    if (token.type === "code") {
      const code = token as Tokens.Code;
      const language = code.lang?.trim().split(/\s+/)[0] ?? "text";
      const box = new BoxRenderable(renderer, { flexDirection: "column", width: "100%" });
      box.add(new TextRenderable(renderer, { content: visibleText(language), fg: palettes[theme].muted }));
      box.add(new CodeRenderable(renderer, {
        content: visibleText(code.text), filetype: language === "text" ? undefined : language,
        fg: palettes[theme].text,
        syntaxStyle: context.syntaxStyle, treeSitterClient: context.treeSitterClient,
        drawUnstyledText: true, conceal: false, wrapMode: "char",
      }));
      return box;
    }
    if (token.type === "table" && width < (token as Tokens.Table).header.length * 12) {
      const table = token as Tokens.Table;
      const text = table.rows.map((row, i) => t("Row {index}", { index: i + 1 }) + "\n" + row.map((cell, j) =>
        `${table.header[j]?.text || t("Column {index}", { index: j + 1 })}: ${cell.text || t("(empty)")}`,
      ).join("\n")).join("\n\n");
      return new TextRenderable(renderer, { content: visibleText(text), wrapMode: "word", width: "100%" });
    }
    return undefined;
  };
}

class MarkdownBoundary extends React.Component<MarkdownBodyProps & { children: React.ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  render() {
    return this.state.error ? <box flexDirection="column">
      <text fg={palettes[this.props.theme].error}>{t("Markdown rendering failed: {error}", { error: visibleText(this.state.error) })}</text>
      <text wrapMode="word">{visibleText(this.props.text)}</text>
    </box> : this.props.children;
  }
}

function MarkdownContent(props: MarkdownBodyProps & { palette: Palette }) {
  useI18n();
  const language = currentLanguage();
  const renderer = useRenderer();
  const [error, setError] = useState("");
  const syntaxStyle = useMemo(() => sharedMarkdownStyle(renderer, props.theme), [renderer, props.theme, props.palette]);
  const renderNode = useMemo(() => markdownNodes(renderer, props.theme, props.width), [renderer, props.theme, props.width, props.palette, language]);
  useEffect(() => {
    let handlers = errorHandlers.get(renderer);
    if (!handlers) {
      handlers = new Map(); errorHandlers.set(renderer, handlers);
      const owned = handlers;
      renderer.on("render:error", (event: { error: unknown; renderable?: Renderable }) => {
        if (!event.renderable) { for (const handler of owned.values()) handler(String(event.error)); return; }
        let node: Renderable | undefined = event.renderable;
        while (node) {
          const handler = owned.get(node.id);
          if (handler) { handler(String(event.error)); return; }
          node = node.parent ?? undefined;
        }
      });
    }
    handlers.set(props.id, setError);
    return () => { if (handlers.get(props.id) === setError) handlers.delete(props.id); };
  }, [renderer, props.id]);
  if (error) return <box flexDirection="column">
    <text fg={palettes[props.theme].error}>{t("Markdown rendering failed: {error}", { error: visibleText(error) })}</text>
    <text wrapMode="word">{visibleText(props.text)}</text>
  </box>;
  // OpenTUI's incremental block cache can retain streaming spacing at finalization.
  // Build the final document once from its complete source, then keep that node stable.
  return <markdown key={props.streaming ? "streaming" : "completed"} id={props.id} content={visibleText(props.text)} streaming={props.streaming ?? false}
    syntaxStyle={syntaxStyle} fg={palettes[props.theme].text} width="100%"
    renderNode={renderNode} tableOptions={{ widthMode: "full", wrapMode: "word", borderColor: palettes[props.theme].accent }} />;
}

export const MarkdownBody = memo(function MarkdownBody(props: MarkdownBodyProps) {
  useI18n();
  const palette = usePalette(props.theme);
  return <MarkdownBoundary {...props}><MarkdownContent {...props} palette={palette} /></MarkdownBoundary>;
});
