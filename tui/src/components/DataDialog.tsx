import { useKeyboard, useRenderer } from "@opentui/react";
import { copyText } from "../platform/clipboard";
import { palettes, type ThemeId } from "../theme/palette";
import { visibleText } from "./content";
import { useI18n } from "../i18n";

export function DataDialog({ title, text, active, theme, onClose, onNotify }: {
  title: string; text: string;
  active: boolean; theme: ThemeId; onClose: () => void; onNotify: (message: string) => void;
}) {
  const t = useI18n();
  const renderer = useRenderer();
  const p = palettes[theme];
  function copy(value: string) {
    void copyText(renderer, value).then(onNotify).catch((error: unknown) => onNotify(String(error)));
  }
  useKeyboard((key) => {
    if (!active || key.defaultPrevented) return;
    if (key.name === "escape") { onClose(); key.preventDefault(); }
    if (!key.ctrl && key.name === "m") { copy(text); key.preventDefault(); }
  });
  return <box id="data-pane" title={visibleText(title)} border borderColor={p.accent} backgroundColor={p.panel} flexDirection="column" flexGrow={1} minHeight={0}>
    <scrollbox id="data-scroll" flexGrow={1} scrollX={false} focused={active}>
      <text wrapMode="word" fg={p.text}>{visibleText(text)}</text>
    </scrollbox>
    <text fg={p.muted} wrapMode="word">{`${t("M copy content")} · ${t("Esc close")}`}</text>
  </box>;
}
