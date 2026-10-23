import { useSyncExternalStore } from "react";
import { palettes, subscribeThemes, type ThemeId } from "./palette";

export function usePalette(theme: ThemeId) {
  return useSyncExternalStore(subscribeThemes, () => palettes[theme]);
}
