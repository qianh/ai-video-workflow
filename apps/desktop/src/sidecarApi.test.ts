import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { tauriSidecarApi } from "./sidecarApi";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

describe("tauriSidecarApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("maps status, request, cancel and restart commands", async () => {
    vi.mocked(invoke)
      .mockResolvedValueOnce({ running: true, pid: 1, restart_count: 0 })
      .mockResolvedValueOnce({ status: "ok" })
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce({ running: true, pid: 2, restart_count: 1 });

    await tauriSidecarApi.status();
    await tauriSidecarApi.request("system.ping", { echo: 1 }, "request-1");
    await tauriSidecarApi.cancel("request-1");
    await tauriSidecarApi.restart();

    expect(invoke).toHaveBeenNthCalledWith(1, "sidecar_status");
    expect(invoke).toHaveBeenNthCalledWith(2, "sidecar_request", {
      method: "system.ping",
      params: { echo: 1 },
      requestId: "request-1",
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "sidecar_cancel", {
      requestId: "request-1",
    });
    expect(invoke).toHaveBeenNthCalledWith(4, "sidecar_restart");
  });

  it("unwraps the Tauri event payload", async () => {
    const callback = vi.fn();
    const unlisten = vi.fn();
    vi.mocked(listen).mockImplementation(async (_name, handler) => {
      handler({
        event: "sidecar-event",
        id: 1,
        payload: { event: "request.progress", data: { current: 1 } },
      });
      return unlisten;
    });

    const stop = await tauriSidecarApi.listen(callback);

    expect(listen).toHaveBeenCalledWith("sidecar-event", expect.any(Function));
    expect(callback).toHaveBeenCalledWith({
      event: "request.progress",
      data: { current: 1 },
    });
    expect(stop).toBe(unlisten);
  });
});
