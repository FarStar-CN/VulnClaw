import { useState } from "react";

/** The optional map belongs to the transcript, so virtual unmounts retain disclosure state. */
export function useExpansion(id: string, expanded?: Map<string, boolean>): [boolean, () => void] {
  const [local, setLocal] = useState(expanded?.get(id) ?? false);
  const open = expanded?.get(id) ?? local;
  return [open, () => {
    if (expanded) {
      const next = !(expanded.get(id) ?? local);
      expanded.set(id, next); setLocal(next);
    } else setLocal((value) => !value);
  }];
}
