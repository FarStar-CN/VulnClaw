import type { SessionController } from "../session/controller";
import { palettes, permissionInfo } from "../theme/palette";
import { visibleText } from "./content";

export function PermissionStatus({ controller }: { controller: SessionController }) {
  const p = palettes[controller.theme];
  const policy = permissionInfo(controller.permission);
  const model = controller.runtime?.config_ready ? `◈ ${controller.runtime.model}` : "model: not configured";
  return <box height={1} flexShrink={0} flexDirection="row" backgroundColor={p.plate} paddingX={1}>
    <text id="permission-label" fg={policy.color}>{policy.label}</text>
    <box flexGrow={1} />
    <text fg={p.soft}>{visibleText(model)}</text>
  </box>;
}
