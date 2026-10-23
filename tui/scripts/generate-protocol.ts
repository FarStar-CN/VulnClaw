import { compile } from "json-schema-to-typescript";
import schema from "../../protocol/session-v2.schema.json";

const output = await compile(schema, "SessionProtocol", {
  bannerComment: "/* Generated from protocol/session-v2.schema.json. Run bun run generate. */",
  unknownAny: true,
  additionalProperties: false,
});
const target = new URL("../src/transport/protocol.generated.ts", import.meta.url);
if (Bun.argv.includes("--check")) {
  if (await Bun.file(target).text() !== output) throw new Error("Protocol types are stale; run bun run generate");
} else {
  await Bun.write(target, output);
}
