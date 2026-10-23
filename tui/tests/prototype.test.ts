import { expect, test } from "bun:test";
import { BoxRenderable, MarkdownRenderable, TextRenderable } from "@opentui/core";
import { createTestRenderer, MockTreeSitterClient } from "@opentui/core/testing";
import { markdownStyle } from "../src/theme/palette";
import { visibleText } from "../src/components/content";
import { paneText } from "../src/platform/pane-copy";

test("native Markdown finalizes split tokens, resizes, and copies the visible pane", async () => {
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
    const snapshot = paneText(setup.renderer, { x: 0, y: 0, width: 80, height: 24 });
    expect(snapshot).toContain("┌");
    expect(snapshot).toContain("中文回答");
    setup.resize(40, 24);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("中文回答");
  } finally {
    await client.destroy();
    await setup.dispose();
    style.destroy();
  }
});

test("split-footer accepts ordered completed Markdown snapshots through public API", async () => {
  const setup = await createTestRenderer({
    width: 60, height: 24, footerHeight: 8, screenMode: "split-footer",
    externalOutputMode: "capture-stdout",
  });
  const style = markdownStyle(setup.renderer, "blue");
  try {
    setup.renderer.root.add(new TextRenderable(setup.renderer, { content: "继续输入" }));
    const surface = setup.renderer.createScrollbackSurface();
    surface.root.add(new MarkdownRenderable(surface.renderContext, {
      content: "# 已完成\n\n第一段\n\n第二段", syntaxStyle: style,
    }));
    try {
      await surface.settle();
      surface.commitRows(0, surface.height, { trailingNewline: true });
      await setup.renderOnce();
      const history = setup.externalOutput.takeText();
      expect(history).toContain("已完成");
      expect(history.indexOf("第一段")).toBeLessThan(history.indexOf("第二段"));
      expect(setup.captureCharFrame()).toContain("继续输入");
      setup.resize(40, 24);
      await setup.renderOnce();
      expect(setup.externalOutput.take()).toHaveLength(0);
    } finally {
      surface.destroy();
    }
  } finally {
    await setup.dispose();
    style.destroy();
  }
});

test("untrusted control sequences become visible text without changing original", () => {
  const original = "中文\u001b]52;c;payload\u0007\n    code\tvalue";
  expect(visibleText(original)).toBe("中文␛]52;c;payload\\u{7}\n    code\tvalue");
  expect(original).toContain("\u001b");
});
