import stringWidth from "string-width";

const collapsedDisclosure = "〉", expandedDisclosure = "﹀";
export const disclosureWidth = Math.max(stringWidth(collapsedDisclosure), stringWidth(expandedDisclosure));
export function disclosureIcon(expanded: boolean): string {
  return expanded ? expandedDisclosure : collapsedDisclosure;
}

/** Body text may contain terminal instructions from targets, tools or models.
 * Keep raw text in the model; render controls visibly at the display boundary.
 */
export function visibleText(raw: string): string {
  return raw.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, (char) =>
    char === "\u001b" ? "␛" : `\\u{${char.codePointAt(0)!.toString(16)}}`,
  );
}
