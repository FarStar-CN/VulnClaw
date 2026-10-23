import { expect, test } from "bun:test";
import { SessionController } from "../src/session/controller";
import type { ClientMessage } from "../src/transport/protocol.generated";

test("saving model credentials refreshes provider readiness immediately", async () => {
  const controller = new SessionController();
  const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.runtime = {
    config_ready: false, provider: "custom", model: "old", mcp_started: 0, skills: [],
  };
  const saved = controller.control("config.update", {
    changes: { "llm.api_key": "synthetic-key" },
  });
  controller.receive({
    protocol_version: 2, type: "control_result", request_id: requests.at(-1)!.request_id,
    state: {
      target: "", phase: "idle", task_constraints: {}, task: { active: false, task_id: null },
      last_run: null, findings: [], evidence: [], constraint_violations: [],
      runtime: { ...controller.runtime, config_ready: true, model: "replacement" },
    },
    operation: "config.update", result: { config: {
      llm: { provider: "custom", model: "replacement", api_key: "***", auth_mode: "static" },
    } },
  });
  await saved;
  expect(controller.runtime?.model).toBe("replacement");
  expect(controller.runtime?.config_ready).toBe(true);
});
