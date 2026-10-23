import { marked, type Token, type Tokens } from "marked";

export function codeBlocks(source: string): string[] {
  const result: string[] = [];
  function visit(tokens: Token[]) {
    for (const token of tokens) {
      if (token.type === "code") {
        const code = token as Tokens.Code;
        const lines = code.raw.split("\n");
        const opener = lines[0].match(/^ {0,3}(`{3,}|~{3,})/);
        if (opener) {
          lines.shift();
          const closing = lines.findIndex((line) => new RegExp(`^ {0,3}${opener[1][0]}{${opener[1].length},}\\s*$`).test(line));
          result.push((closing >= 0 ? lines.slice(0, closing).join("\n") + "\n" : lines.join("\n")));
        } else result.push(code.raw.replace(/^(?: {4}|\t)/gm, ""));
      } else if (token.type === "list") {
        for (const item of (token as Tokens.List).items) visit(item.tokens);
      } else if ("tokens" in token && Array.isArray(token.tokens)) visit(token.tokens);
    }
  }
  visit(marked.lexer(source));
  return result;
}
