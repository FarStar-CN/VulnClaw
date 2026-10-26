import { expect, test } from "bun:test";
import { BoxRenderable, MarkdownRenderable } from "@opentui/core";
import { createTestRenderer, MockTreeSitterClient } from "@opentui/core/testing";
import { markdownStyle } from "../src/theme/palette";
import { visibleText } from "../src/components/content";

test("native Markdown finalizes split tokens and resizes inside its pane", async () => {
  const setup = await createTestRenderer({ width: 80, height: 24 });
  const client = new MockTreeSitterClient();
  const style = markdownStyle(setup.renderer, "blue");
  try {
    const box = new BoxRenderable(setup.renderer, {
      id: "output", border: true, width: "100%", height: "100%",
    });
    const body = new MarkdownRenderable(setup.renderer, {
      content: "# 中文回答\n\n```ty", streaming: true,
      syntaxStyle: style, treeSitterClient: client,
      tableOptions: { widthMode: "full", wrapMode: "word" },
    });
    box.add(body);
    setup.renderer.root.add(box);
    await setup.renderOnce();
    body.content += "pescript\nconst text = '保留空白';\n```\n\n[文档][ref]\n\n[ref]: https://example.test\n";
    body.streaming = false;
    client.resolveAllHighlightOnce();
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("中文回答");
    expect(setup.captureCharFrame()).toContain("保留空白");
    expect(setup.captureCharFrame()).toContain("┌");
    setup.resize(40, 24);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("中文回答");
  } finally {
    await client.destroy();
    await setup.dispose();
    style.destroy();
  }
});

test("untrusted control sequences become visible text without changing original", () => {
  const original = "中文\u001b]52;c;payload\u0007\n    code\tvalue";
  expect(visibleText(original)).toBe("中文␛]52;c;payload\\u{7}\n    code\tvalue");
  expect(original).toContain("\u001b");
});
