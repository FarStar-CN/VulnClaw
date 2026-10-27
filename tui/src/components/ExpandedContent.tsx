import type { ReactNode } from "react";
import type { ThemeId } from "../theme/palette";
import { usePalette } from "../theme/usePalette";

export function ExpandedContent({ id, theme, children }: {
  id: string; theme: ThemeId; children: ReactNode;
}) {
  const p = usePalette(theme);
  return <box id={id} border={["left"]} borderColor={p.action} paddingLeft={1}
    flexDirection="column" flexShrink={0} rowGap={1}>
    {children}
  </box>;
}
