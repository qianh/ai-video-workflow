import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export interface SidecarStatus {
  running: boolean;
  pid: number | null;
  restart_count: number;
}

export interface SidecarEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface SidecarApi {
  status(): Promise<SidecarStatus>;
  request(
    method: string,
    params: Record<string, unknown>,
    requestId: string,
  ): Promise<Record<string, unknown>>;
  cancel(requestId: string): Promise<boolean>;
  restart(): Promise<SidecarStatus>;
  listen(callback: (event: SidecarEvent) => void): Promise<UnlistenFn>;
}

export const tauriSidecarApi: SidecarApi = {
  status: () => invoke<SidecarStatus>("sidecar_status"),
  request: (method, params, requestId) =>
    invoke<Record<string, unknown>>("sidecar_request", {
      method,
      params,
      requestId,
    }),
  cancel: (requestId) => invoke<boolean>("sidecar_cancel", { requestId }),
  restart: () => invoke<SidecarStatus>("sidecar_restart"),
  listen: (callback) =>
    listen<SidecarEvent>("sidecar-event", ({ payload }) => callback(payload)),
};
