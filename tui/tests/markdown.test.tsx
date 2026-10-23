import { expect, test, afterAll, spyOn } from "bun:test";
import React, { act } from "react";
import { getTreeSitterClient, destroyTreeSitterClient, MarkdownRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { MarkdownBody } from "../src/components/MarkdownBody";
import { finalFrame } from "./render-helpers";

const fixture = `# 中文标题 😀 é

Setext heading
--------------

**strong** *emphasis* ~~deleted~~ \`inline\` &amp; \\*escaped\\*

> quoted paragraph

1. first
   - nested
2. second

- [x] complete
- [ ] pending

---

[reference][later] <https://example.test/long/path>

![alt text](https://example.test/image.png)

<span>literal HTML</span>

\`\`\`mermaid
graph TD; A-->B
\`\`\`

$formula$ 

[later]: https://example.test/reference
`;

afterAll(async () => { await destroyTreeSitterClient(); });

test("bundled Python and JSON parsers highlight without fetching grammar resources", async () => {
  const client = getTreeSitterClient();
  const python = await client.highlightOnce('def main():\n    return "hello"', "python");
  expect(python.error).toBeUndefined();
  expect(python.highlights?.some((highlight) => highlight[2] === "keyword")).toBe(true);
  const json = await client.highlightOnce('{"key": true, "count": 2}', "json");
  expect(json.error).toBeUndefined();
  expect(json.highlights?.some((highlight) => highlight[2] === "string")).toBe(true);
}, 10000);

for (const width of [40, 80, 120]) test(`MD-01/02/13: common Markdown and literal fallbacks at ${width} columns`, async () => {
  const setup = await testRender(<MarkdownBody id="fixture" text={fixture} width={width} theme="blue" />, { width, height: 80 });
  try {
    const frame = await finalFrame(setup);
    for (const text of ["中文标题", "strong", "first", "nested", "pending", "alt text", "literal HTML", "graph TD", "$formula$"]) {
      expect(frame).toContain(text);
    }
    expect(frame).not.toContain("\u001b");
    expect(frame).toContain("Image: alt text");
    expect(frame).not.toContain("[later]:");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("MD-03/04/06: code, references and narrow tables remain readable", async () => {
  const text = "```python\n\tprint('中文')\n```\n\n| Name | Value |\n| :-- | --: |\n| 中文 | a long cell value |\n\n[text][end]\n\n[end]: https://example.test\n";
  const setup = await testRender(<MarkdownBody id="table" text={text} width={20} theme="orange" />, { width: 20, height: 30 });
  try {
    const frame = await finalFrame(setup);
    for (const expected of ["python", "中文", "Name:", "Value:", "a long cell", "text"]) expect(frame).toContain(expected);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("MD-15: an actual Markdown construction error shows original source and its error", async () => {
  const prototype = MarkdownRenderable.prototype as unknown as { createInitialStyledText: () => void };
  const failure = spyOn(prototype, "createInitialStyledText").mockImplementation(() => { throw new Error("injected Markdown parser failure"); });
  const errors: unknown[][] = [];
  const logging = spyOn(console, "error").mockImplementation((...args: unknown[]) => { errors.push(args); });
  let setup: Awaited<ReturnType<typeof testRender>> | null = null;
  try {
    setup = await testRender(<MarkdownBody id="fault" text="# Keep original\n\npartial body" width={50} theme="blue" />, { width: 50, height: 12 });
    const frame = await finalFrame(setup);
    expect(frame).toContain("Markdown rendering failed");
    expect(frame).toContain("# Keep original");
    expect(frame).toContain("partial body");
    expect(errors.length).toBeGreaterThan(0);
  } finally {
    if (setup) await act(async () => { await setup!.dispose(); });
    failure.mockRestore(); logging.mockRestore();
  }
});

test("MD-05/11: split Markdown tokens converge to the same final frame after resize and theme changes", async () => {
  let update: (text: string, streaming: boolean, theme: "blue" | "orange") => void = () => {};
  function Streaming() {
    const [state, setState] = React.useState({ text: "", streaming: true, theme: "blue" as "blue" | "orange" });
    update = (text, streaming, theme) => setState({ text, streaming, theme });
    return <MarkdownBody id="incremental" {...state} width={80} />;
  }
  const parts = ["# Stream 中文\n\n**em", "phasis** and [li", "nk][ref]\n\n```py", "thon\n\tprint('😀')\n", "```\n\n| A | B |\n| :-", "- | --: |\n| 中文 | 2 |\n\n[ref]: https://example.test\n"];
  const streamed = await testRender(<Streaming />, { width: 80, height: 30 });

  try {
    let text = "";
    for (const part of parts) {
      text += part;
      await act(async () => { update(text, true, "blue"); });
      expect(await finalFrame(streamed)).toContain("Stream 中文");
    }
    await act(async () => { streamed.resize(40, 30); update(text, false, "orange"); });
    const incremental = await finalFrame(streamed);
    await act(async () => { await streamed.dispose(); });
    const whole = await testRender(<MarkdownBody id="whole" text={text} width={80} theme="orange" />, { width: 40, height: 30 });
    try { expect(incremental).toBe(await finalFrame(whole)); }
    finally { await act(async () => { await whole.dispose(); }); }
  } finally {
    if (!streamed.renderer.isDestroyed) await act(async () => { await streamed.dispose(); });
  }
});
