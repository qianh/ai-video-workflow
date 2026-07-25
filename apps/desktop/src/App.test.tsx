import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { SidecarApi, SidecarEvent, SidecarStatus } from "./sidecarApi";


function createApi(overrides: Partial<SidecarApi> = {}): SidecarApi {
  const status: SidecarStatus = { running: false, pid: null, restart_count: 0 };
  return {
    status: vi.fn().mockResolvedValue(status),
    request: vi.fn().mockImplementation(async (method: string) => {
      if (method === "project.current") return { project: null };
      if (method === "project.list_recent") return { projects: [] };
      if (method === "job.list") return { jobs: [] };
      return { status: "ok", protocol_version: 1, echo: null };
    }),
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
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") return { project: null };
        if (method === "project.list_recent") return { projects: [] };
        return { status: "ok", protocol_version: 1, echo: null };
      }),
    });

    render(<App api={api} showDiagnostics />);

    expect(await screen.findByText("在线")).toBeInTheDocument();
    expect(screen.getByText("PID 912")).toBeInTheDocument();
    expect(screen.getByText("2 次恢复")).toBeInTheDocument();
  });

  it("creates a project through the workspace panel", async () => {
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") {
          return {
            project: {
              id: "p1",
              name: "试播项目",
              root_path: "/tmp/demo",
              schema_version: 1,
            },
          };
        }
        if (method === "project.list_recent") {
          return {
            projects: [
              { id: "p1", name: "试播项目", root_path: "/tmp/demo", schema_version: 1 },
            ],
          };
        }
        if (method === "project.create") {
          return {
            id: "p1",
            name: "试播项目",
            root_path: "/tmp/demo",
            schema_version: 1,
          };
        }
        if (method === "job.list") return { jobs: [] };
        return { status: "ok", protocol_version: 1 };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "创建项目" }));
    expect(await screen.findByText(/已创建并打开/)).toBeInTheDocument();
    expect(api.request).toHaveBeenCalledWith(
      "project.create",
      { parent_dir: "~/Documents/ai-video-projects", name: "试播项目" },
      expect.any(String),
    );
  });

  it("enqueues a demo job for the open project", async () => {
    let projectOpen = true;
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") {
          return projectOpen
            ? {
                project: {
                  id: "p1",
                  name: "试播项目",
                  root_path: "/tmp/demo",
                  schema_version: 1,
                },
              }
            : { project: null };
        }
        if (method === "project.list_recent") {
          return {
            projects: [
              { id: "p1", name: "试播项目", root_path: "/tmp/demo", schema_version: 1 },
            ],
          };
        }
        if (method === "job.list") {
          return {
            jobs: [
              { id: "j1", kind: "demo.ping", status: "queued", attempts: 0 },
            ],
          };
        }
        if (method === "job.enqueue") {
          return { id: "j1", kind: "demo.ping", status: "queued", attempts: 0 };
        }
        if (method === "project.open") {
          projectOpen = true;
          return {
            id: "p1",
            name: "试播项目",
            root_path: "/tmp/demo",
            schema_version: 1,
          };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "打开 试播项目" }));
    await user.click(await screen.findByRole("button", { name: "入队演示任务" }));
    expect(await screen.findByText(/已入队任务/)).toBeInTheDocument();
    expect(api.request).toHaveBeenCalledWith(
      "job.enqueue",
      { kind: "demo.ping", payload: { source: "ui" }, max_attempts: 3 },
      expect.any(String),
    );
  });

  it("runs a ping and reports the round trip", async () => {
    const api = createApi({
      status: vi
        .fn()
        .mockResolvedValueOnce({ running: false, pid: null, restart_count: 0 })
        .mockResolvedValue({ running: true, pid: 52, restart_count: 0 }),
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") return { project: null };
        if (method === "project.list_recent") return { projects: [] };
        if (method === "system.ping") {
          return { status: "ok", protocol_version: 1, echo: "ui-check" };
        }
        return { status: "ok", protocol_version: 1, echo: null };
      }),
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
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") return { project: null };
        if (method === "project.list_recent") return { projects: [] };
        if (method === "diagnostics.count") {
          return await new Promise<Record<string, unknown>>((resolve) => {
            finishRequest = resolve;
          });
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    await waitFor(() => expect(listener).toBeDefined());

    await user.click(screen.getByRole("button", { name: "运行 20 步诊断" }));
    const countCall = vi.mocked(api.request).mock.calls.find((call) => call[0] === "diagnostics.count");
    expect(countCall).toBeDefined();
    const requestId = countCall![2];
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
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") return { project: null };
        if (method === "project.list_recent") return { projects: [] };
        if (method === "diagnostics.count") {
          return await new Promise(() => undefined);
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "运行 20 步诊断" }));
    await user.click(screen.getByRole("button", { name: "取消当前请求" }));

    const countCall = vi.mocked(api.request).mock.calls.find((call) => call[0] === "diagnostics.count");
    expect(countCall).toBeDefined();
    expect(api.cancel).toHaveBeenCalledWith(countCall![2]);
    expect(await screen.findByText("已发送取消请求")).toBeInTheDocument();
  });

  it("recovers after an intentional crash without replaying the crash request", async () => {
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") return { project: null };
        if (method === "project.list_recent") return { projects: [] };
        if (method === "diagnostics.crash") {
          throw new Error("sidecar exited");
        }
        if (method === "system.ping") {
          return { status: "ok", protocol_version: 1, echo: "recovered" };
        }
        return { status: "ok" };
      }),
      status: vi.fn().mockResolvedValue({ running: true, pid: 77, restart_count: 1 }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "验证崩溃恢复" }));

    expect(await screen.findByText("已恢复为 PID 77")).toBeInTheDocument();
    expect(api.request).toHaveBeenCalledWith(
      "diagnostics.crash",
      { exit_code: 73 },
      expect.any(String),
    );
    expect(api.request).toHaveBeenCalledWith(
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
