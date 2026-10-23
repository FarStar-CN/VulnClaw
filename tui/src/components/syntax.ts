import { addDefaultParsers } from "@opentui/core";
import pythonWasm from "tree-sitter-wasms/out/tree-sitter-python.wasm" with { type: "file" };
import jsonWasm from "tree-sitter-wasms/out/tree-sitter-json.wasm" with { type: "file" };
import pythonQuery from "../../assets/python.scm" with { type: "file" };
import jsonQuery from "../../assets/json.scm" with { type: "file" };

// Resolve all resources at build/install time. Unknown languages remain plain code.
addDefaultParsers([
  { filetype: "python", aliases: ["py"], wasm: pythonWasm, queries: { highlights: [pythonQuery] } },
  { filetype: "json", wasm: jsonWasm, queries: { highlights: [jsonQuery] } },
]);
