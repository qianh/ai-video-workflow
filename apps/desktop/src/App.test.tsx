import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { SidecarApi, SidecarEvent, SidecarStatus } from "./sidecarApi";


function createApi(overrides: Partial<SidecarApi> = {}): SidecarApi {
  const status: SidecarStatus = { running: false, pid: null, restart_count: 0 };
  return {
    status: vi.fn().mockResolvedValue(status),
    request: vi.fn().mockResolvedValue({ status: "ok", protocol_version: 1, echo: null }),
    cancel: vi.fn().mockResolvedValue(true),
    restart: vi.fn().mockResolvedValue({ running: true, pid: 42, restart_count: 1 }),
    listen: vi.fn().mockResolvedValue(() => undefined),
    ...overrides,
  };
}


describe("M0 sidecar console", () => {
  it("loads and displays the persisted supervisor status", async () => {
    const api = createApi({
      status: vi.fn().mockResolvedValue({ running: true, pid: 912, restart_count: 2 }),
    });

    render(<App api={api} showDiagnostics />);

    expect(await screen.findByText("在线")).toBeInTheDocument();
    expect(screen.getByText("PID 912")).toBeInTheDocument();
    expect(screen.getByText("2 次恢复")).toBeInTheDocument();
  });

  it("runs a ping and reports the round trip", async () => {
    const api = createApi({
      status: vi
        .fn()
        .mockResolvedValueOnce({ running: false, pid: null, restart_count: 0 })
        .mockResolvedValue({ running: true, pid: 52, restart_count: 0 }),
      request: vi
        .fn()
        .mockResolvedValue({ status: "ok", protocol_version: 1, echo: "ui-check" }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "发送 Ping" }));

    expect(await screen.findByText(/协议 v1 已响应/)).toBeInTheDocument();
    expect(api.request).toHaveBeenCalledWith(
      "system.ping",
      { echo: "ui-check" },
      expect.any(String),
    );
  });

  it("shows progress events for the active diagnostic request", async () => {
    let listener: ((event: SidecarEvent) => void) | undefined;
    let finishRequest: ((value: Record<string, unknown>) => void) | undefined;
    const api = createApi({
      listen: vi.fn().mockImplementation(async (callback) => {
        listener = callback;
        return () => undefined;
      }),
      request: vi.fn().mockImplementation(
        () =>
          new Promise<Record<string, unknown>>((resolve) => {
            finishRequest = resolve;
          }),
      ),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    await waitFor(() => expect(listener).toBeDefined());

    await user.click(screen.getByRole("button", { name: "运行 20 步诊断" }));
    const requestId = vi.mocked(api.request).mock.calls[0][2];
    listener?.({
      event: "request.progress",
      data: { request_id: requestId, current: 7, total: 20 },
    });

    expect(await screen.findByText("7 / 20")).toBeInTheDocument();
    finishRequest?.({ completed_steps: 20 });
    expect(await screen.findByText("诊断完成")).toBeInTheDocument();
  });

  it("cancels the active diagnostic request", async () => {
    const api = createApi({
      request: vi.fn().mockReturnValue(new Promise(() => undefined)),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "运行 20 步诊断" }));
    await user.click(screen.getByRole("button", { name: "取消当前请求" }));

    const requestId = vi.mocked(api.request).mock.calls[0][2];
    expect(api.cancel).toHaveBeenCalledWith(requestId);
    expect(await screen.findByText("已发送取消请求")).toBeInTheDocument();
  });

  it("recovers after an intentional crash without replaying the crash request", async () => {
    const api = createApi({
      request: vi
        .fn()
        .mockRejectedValueOnce(new Error("sidecar exited"))
        .mockResolvedValueOnce({ status: "ok", protocol_version: 1, echo: "recovered" }),
      status: vi.fn().mockResolvedValue({ running: true, pid: 77, restart_count: 1 }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "验证崩溃恢复" }));

    expect(await screen.findByText("已恢复为 PID 77")).toBeInTheDocument();
    expect(api.request).toHaveBeenNthCalledWith(
      1,
      "diagnostics.crash",
      { exit_code: 73 },
      expect.any(String),
    );
    expect(api.request).toHaveBeenNthCalledWith(
      2,
      "system.ping",
      { echo: "recovered" },
      expect.any(String),
    );
  });

  it("surfaces actionable errors", async () => {
    const api = createApi({
      status: vi.fn().mockRejectedValue(new Error("bridge unavailable")),
    });

    render(<App api={api} showDiagnostics />);

    expect(await screen.findByText("bridge unavailable")).toBeInTheDocument();
  });

  it("restarts the sidecar explicitly", async () => {
    const api = createApi({
      restart: vi.fn().mockResolvedValue({ running: true, pid: 204, restart_count: 3 }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "重启 Sidecar" }));

    expect(await screen.findByText("Sidecar 已重启 · PID 204")).toBeInTheDocument();
    expect(screen.getByText("3 次恢复")).toBeInTheDocument();
  });

  it("reports when a diagnostic already ended before cancellation", async () => {
    const api = createApi({
      request: vi.fn().mockReturnValue(new Promise(() => undefined)),
      cancel: vi.fn().mockResolvedValue(false),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "运行 20 步诊断" }));
    await user.click(screen.getByRole("button", { name: "取消当前请求" }));

    expect(await screen.findByText("请求已经结束，无需取消")).toBeInTheDocument();
  });

  it("hides destructive diagnostics outside development mode", async () => {
    render(<App api={createApi()} showDiagnostics={false} />);

    expect(await screen.findByRole("button", { name: "发送 Ping" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "验证崩溃恢复" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "运行 20 步诊断" })).not.toBeInTheDocument();
  });

  it("shows non-Error bridge failures", async () => {
    const api = createApi({
      request: vi.fn().mockRejectedValue("request failed"),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "发送 Ping" }));

    expect(await screen.findByText("request failed")).toBeInTheDocument();
  });
});
