import Ajv2020 from "ajv/dist/2020.js";
import schema from "../../../protocol/session-v2.schema.json";
import type { ClientMessage, ServerEvent } from "./protocol.generated";

const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema);
const eventValidator = ajv.compile<ServerEvent>({ $ref: `${schema.$id}#/$defs/serverEvent` });
const requestValidator = ajv.compile<ClientMessage>({ $ref: `${schema.$id}#/$defs/clientMessage` });

export function serverEvent(value: unknown): ServerEvent {
  if (!eventValidator(value)) throw new Error(`Invalid session event: ${ajv.errorsText(eventValidator.errors)}`);
  return value;
}

export function encodeRequest(value: ClientMessage): string {
  if (!requestValidator(value)) throw new Error(`Invalid session request: ${ajv.errorsText(requestValidator.errors)}`);
  return JSON.stringify(value) + "\n";
}

export class JsonlDecoder {
  private decoder = new TextDecoder("utf-8", { fatal: true });
  private pending = "";

  push(bytes: Uint8Array): ServerEvent[] {
    this.pending += this.decoder.decode(bytes, { stream: true });
    const lines = this.pending.split("\n");
    this.pending = lines.pop()!;
    return lines.filter((line) => line.trim()).map((line) => serverEvent(JSON.parse(line)));
  }

  finish(): void {
    this.pending += this.decoder.decode();
    if (this.pending.trim()) throw new Error("Backend ended with an incomplete JSONL message");
  }
}
