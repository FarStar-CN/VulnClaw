import { compile } from "json-schema-to-typescript";
import Ajv2020 from "ajv/dist/2020.js";
import standaloneCode from "ajv/dist/standalone/index.js";
import schema from "../../protocol/session-v2.schema.json";

const output = await compile(schema, "SessionProtocol", {
  bannerComment: "/* Generated from protocol/session-v2.schema.json. Run bun run generate. */",
  unknownAny: true,
  additionalProperties: false,
});
const ajv = new Ajv2020({ strict: false, allErrors: true, code: { source: true, esm: true } });
ajv.addSchema(schema);
const validators = "// @ts-nocheck\n/* Generated from protocol/session-v2.schema.json. Run bun run generate. */\n" + standaloneCode(ajv, {
  validateServerEvent: `${schema.$id}#/$defs/serverEvent`,
  validateClientMessage: `${schema.$id}#/$defs/clientMessage`,
}) + "\n";
for (const [name, content] of [["protocol.generated.ts", output], ["validators.generated.ts", validators]]) {
  const target = new URL(`../src/transport/${name}`, import.meta.url);
  if (Bun.argv.includes("--check")) {
    if (await Bun.file(target).text() !== content) throw new Error(`${name} is stale; run bun run generate`);
  } else {
    await Bun.write(target, content);
  }
}
