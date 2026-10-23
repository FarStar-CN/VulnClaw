import { useSyncExternalStore } from "react";
import type { SessionController } from "../session/controller";

export function useSession(controller: SessionController): void {
  useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot);
}
