import type { ValidateFunction } from "ajv";
import { validateServerEvent, validateClientMessage } from "./validators.generated";
import type { ClientMessage, ServerEvent } from "./protocol.generated";

const eventValidator = validateServerEvent as ValidateFunction<ServerEvent>;
const requestValidator = validateClientMessage as ValidateFunction<ClientMessage>;
function errorsText(validate: ValidateFunction): string {
  return validate.errors?.map((error) => `data${error.instancePath} ${error.message}`).join(", ") || "No errors";
}

export function serverEvent(value: unknown): ServerEvent {
  if (!eventValidator(value)) throw new Error(`Invalid session event: ${errorsText(eventValidator)}`);
  return value;
}

export function encodeRequest(value: ClientMessage): string {
  if (!requestValidator(value)) throw new Error(`Invalid session request: ${errorsText(requestValidator)}`);
  return JSON.stringify(value) + "\n";
}

export class JsonlDecoder {
  private decoder = new TextDecoder("utf-8", { fatal: true });
  private fragments: string[] = [];

  push(bytes: Uint8Array): ServerEvent[] {
    const text = this.decoder.decode(bytes, { stream: true });
    const events: ServerEvent[] = [];
    let start = 0;
    for (let end = text.indexOf("\n"); end !== -1; end = text.indexOf("\n", start)) {
      this.fragments.push(text.slice(start, end));
      const line = this.fragments.join("");
      this.fragments = [];
      if (line.trim()) events.push(serverEvent(JSON.parse(line)));
      start = end + 1;
    }
    if (start < text.length) this.fragments.push(text.slice(start));
    return events;
  }

  finish(): void {
    this.fragments.push(this.decoder.decode());
    if (this.fragments.join("").trim()) throw new Error("Backend ended with an incomplete JSONL message");
    this.fragments = [];
  }
}
