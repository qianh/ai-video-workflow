import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { SidecarApi, SidecarEvent, SidecarStatus } from "./sidecarApi";

const emptyOverview = {
  job_counts: {
    queued: 0,
    running: 0,
    paused: 0,
    succeeded: 0,
    failed: 0,
    cancelled: 0,
  },
  failed_jobs: [],
  disk: {
    assets_bytes: 0,
    renders_bytes: 0,
    temp_bytes: 0,
    logs_bytes: 0,
    snapshots_bytes: 0,
    project_db_bytes: 128,
    total_bytes: 128,
  },
  snapshots: [],
  queue_depth: 0,
};

function createApi(overrides: Partial<SidecarApi> = {}): SidecarApi {
  const status: SidecarStatus = { running: false, pid: null, restart_count: 0 };
  return {
    status: vi.fn().mockResolvedValue(status),
    request: vi.fn().mockImplementation(async (method: string) => {
      if (method === "project.current") return { project: null };
      if (method === "project.list_recent") return { projects: [] };
      if (method === "job.list") return { jobs: [] };
      if (method === "project.overview") return emptyOverview;
      if (method === "pack.current_lock") return { lock: null };
      if (method === "pack.list_compositions") return { compositions: [] };
      return { status: "ok", protocol_version: 1, echo: null };
    }),
    cancel: vi.fn().mockResolvedValue(true),
    restart: vi.fn().mockResolvedValue({ running: true, pid: 42, restart_count: 1 }),
    listen: vi.fn().mockResolvedValue(() => undefined),
    ...overrides,
  };
}

describe("M1 shell", () => {
  it("loads supervisor status in the link diagnostics view", async () => {
    const api = createApi({
      status: vi.fn().mockResolvedValue({ running: true, pid: 912, restart_count: 2 }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "链路诊断" }));
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
        if (method === "project.overview") {
          return {
            ...emptyOverview,
            queue_depth: 0,
            disk: { ...emptyOverview.disk, total_bytes: 2048 },
          };
        }
        return { status: "ok", protocol_version: 1 };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "项目" }));
    await user.click(await screen.findByRole("button", { name: "创建项目" }));
    expect(await screen.findByText(/已创建并打开/)).toBeInTheDocument();
    expect(api.request).toHaveBeenCalledWith(
      "project.create",
      { parent_dir: "~/Documents/ai-video-projects", name: "试播项目" },
      expect.any(String),
    );
    expect(await screen.findByRole("heading", { name: "总览" })).toBeInTheDocument();
    expect(await screen.findByText("2.0 KB")).toBeInTheDocument();
  });

  it("shows overview metrics for an open project", async () => {
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
        if (method === "project.list_recent") return { projects: [] };
        if (method === "job.list") {
          return {
            jobs: [{ id: "j1", kind: "demo.ping", status: "failed", attempts: 2, last_error: "boom" }],
          };
        }
        if (method === "project.overview") {
          return {
            job_counts: {
              queued: 1,
              running: 0,
              paused: 0,
              succeeded: 0,
              failed: 1,
              cancelled: 0,
            },
            failed_jobs: [
              { id: "j1", kind: "demo.ping", status: "failed", attempts: 2, last_error: "boom" },
            ],
            disk: {
              assets_bytes: 100,
              renders_bytes: 0,
              temp_bytes: 0,
              logs_bytes: 0,
              snapshots_bytes: 50,
              project_db_bytes: 10,
              total_bytes: 160,
            },
            snapshots: [{ name: "a.bak", path: "snapshots/a.bak", size_bytes: 50, reason: "manual" }],
            queue_depth: 1,
          };
        }
        return { status: "ok" };
      }),
    });
    render(<App api={api} showDiagnostics />);
    expect(await screen.findByRole("heading", { name: "总览" })).toBeInTheDocument();
    expect(await screen.findByText("队列深度")).toBeInTheDocument();
    const queueCard = screen.getByText("队列深度").closest(".stat-card");
    const failedCard = screen.getByText("失败任务").closest(".stat-card");
    expect(queueCard).toHaveTextContent("1");
    expect(failedCard).toHaveTextContent("1");
    expect(screen.getByLabelText("失败任务")).toHaveTextContent("demo.ping");
    expect(screen.getByLabelText("最近快照")).toHaveTextContent("manual");
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
            jobs: [{ id: "j1", kind: "demo.ping", status: "queued", attempts: 0 }],
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
        if (method === "project.overview") {
          return { ...emptyOverview, queue_depth: 1, job_counts: { ...emptyOverview.job_counts, queued: 1 } };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);

    await user.click(await screen.findByRole("button", { name: "项目" }));
    await user.click(await screen.findByRole("button", { name: "打开 试播项目" }));
    await user.click(await screen.findByRole("button", { name: "任务中心" }));
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
    await user.click(await screen.findByRole("button", { name: "链路诊断" }));
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
    await user.click(screen.getByRole("button", { name: "链路诊断" }));
    await user.click(screen.getByRole("button", { name: "运行 20 步诊断" }));
    const countCall = vi.mocked(api.request).mock.calls.find((call) => call[0] === "diagnostics.count");
    expect(countCall).toBeDefined();
    const reqId = countCall![2];
    listener?.({
      event: "request.progress",
      data: { request_id: reqId, current: 7, total: 20 },
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
    await user.click(await screen.findByRole("button", { name: "链路诊断" }));
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
    await user.click(await screen.findByRole("button", { name: "链路诊断" }));
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

  it("registers and locks a default creative pack composition", async () => {
    const revision = { id: "rev-1" };
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") {
          return {
            project: {
              id: "p1",
              name: "试播项目",
              root_path: "/tmp/demo",
              schema_version: 3,
            },
          };
        }
        if (method === "project.list_recent") return { projects: [] };
        if (method === "job.list") return { jobs: [] };
        if (method === "project.overview") return emptyOverview;
        if (method === "pack.register") {
          return {
            pack: { id: "pack-1", name: "demo" },
            revision,
          };
        }
        if (method === "pack.compose") {
          return {
            composition_revision_id: "comp-rev-1",
            status: "eligible",
          };
        }
        if (method === "pack.evaluate") {
          return { result: "pass", status_after: "eligible" };
        }
        if (method === "pack.lock") {
          return { id: "lock-1234567890", purpose: "production" };
        }
        if (method === "pack.current_lock") {
          return { lock: { id: "lock-1234567890" } };
        }
        if (method === "pack.list_compositions") {
          return {
            compositions: [
              {
                name: "夜市默认组合",
                status: "eligible",
                composition_revision_id: "comp-rev-1",
              },
            ],
          };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    await user.click(await screen.findByRole("button", { name: "创作包" }));
    await user.click(await screen.findByRole("button", { name: "注册并锁定默认组合" }));
    expect(await screen.findByText(/已锁定 Creative Pack/)).toBeInTheDocument();
    expect(screen.getByLabelText("创作包组合列表")).toHaveTextContent("夜市默认组合");
    expect(api.request).toHaveBeenCalledWith(
      "pack.lock",
      { composition_revision_id: "comp-rev-1", purpose: "production" },
      expect.any(String),
    );
  });

  it("imports story text, splits chapters, and lists events", async () => {
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") {
          return {
            project: { id: "p1", name: "试播项目", root_path: "/tmp/demo", schema_version: 2 },
          };
        }
        if (method === "project.list_recent") return { projects: [] };
        if (method === "job.list") return { jobs: [] };
        if (method === "project.overview") return emptyOverview;
        if (method === "story.import_source") {
          return {
            id: "s1",
            title: "试播小说",
            source_type: "novel",
            status: "imported",
            char_count: 40,
          };
        }
        if (method === "story.split_chapters") {
          return {
            chunks: [
              {
                id: "c1",
                title: "第一章 夜市",
                ordinal: 0,
                char_start: 0,
                char_end: 20,
              },
            ],
          };
        }
        if (method === "story.create_event") {
          return {
            event_id: "e1",
            title: "首章关键事件",
            summary: "从首个章节抽取的定位事件",
            origin: "extracted",
            order_key: 1,
          };
        }
        if (method === "story.list_sources") {
          return {
            sources: [
              {
                id: "s1",
                title: "试播小说",
                source_type: "novel",
                status: "split",
                char_count: 40,
              },
            ],
          };
        }
        if (method === "story.list_chunks") {
          return {
            chunks: [
              {
                id: "c1",
                title: "第一章 夜市",
                ordinal: 0,
                char_start: 0,
                char_end: 20,
              },
            ],
          };
        }
        if (method === "story.list_events") {
          return {
            events: [
              {
                event_id: "e1",
                title: "首章关键事件",
                summary: "从首个章节抽取的定位事件",
                origin: "extracted",
                order_key: 1,
              },
            ],
          };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    await user.click(await screen.findByRole("button", { name: "故事" }));
    await user.click(await screen.findByRole("button", { name: "导入并切分" }));
    expect(await screen.findByText(/已导入「试播小说」/)).toBeInTheDocument();
    expect(screen.getByLabelText("故事来源列表")).toHaveTextContent("试播小说");
    expect(screen.getByLabelText("章节列表")).toHaveTextContent("第一章 夜市");
    expect(screen.getByLabelText("事件列表")).toHaveTextContent("首章关键事件");
  });

  it("shows empty states across shell views", async () => {
    const api = createApi();
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    expect(await screen.findByText(/尚未打开项目/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "任务中心" }));
    expect(await screen.findByText("请先打开项目。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "项目" }));
    expect(await screen.findByRole("heading", { name: "项目" })).toBeInTheDocument();
  });

  it("shows empty queue and overview details for an open project", async () => {
    const api = createApi({
      request: vi.fn().mockImplementation(async (method: string) => {
        if (method === "project.current") {
          return {
            project: { id: "p1", name: "空队列", root_path: "/tmp/empty", schema_version: 1 },
          };
        }
        if (method === "project.list_recent") return { projects: [] };
        if (method === "job.list") return { jobs: [] };
        if (method === "project.overview") {
          return {
            ...emptyOverview,
            job_counts: { ...emptyOverview.job_counts, succeeded: 3 },
            disk: {
              assets_bytes: 0,
              renders_bytes: 0,
              temp_bytes: 0,
              logs_bytes: 0,
              snapshots_bytes: 0,
              project_db_bytes: 0,
              total_bytes: 0,
            },
          };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    expect(await screen.findByText("暂无失败任务")).toBeInTheDocument();
    expect(screen.getByText("0 B")).toBeInTheDocument();
    expect(screen.getByText("暂无快照")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "任务中心" }));
    expect(await screen.findByText("队列为空。")).toBeInTheDocument();
    expect(screen.getByText(/成功/)).toBeInTheDocument();
  });

  it("creates a snapshot and diagnostic pack from the shell", async () => {
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
        if (method === "project.list_recent") return { projects: [] };
        if (method === "job.list") return { jobs: [] };
        if (method === "project.overview") {
          return {
            ...emptyOverview,
            snapshots: [
              { name: "a.bak", path: "snapshots/a.bak", size_bytes: 50, reason: "manual-ui" },
            ],
          };
        }
        if (method === "snapshot.create") {
          return { name: "a.bak", path: "snapshots/a.bak", size_bytes: 50, reason: "manual-ui" };
        }
        if (method === "diagnostics.create_pack") {
          return { path: "/tmp/demo/temp/diagnostics/diagnostic-1.zip", includes: ["meta.json"] };
        }
        return { status: "ok" };
      }),
    });
    const user = userEvent.setup();
    render(<App api={api} showDiagnostics />);
    expect(await screen.findByRole("heading", { name: "总览" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建快照" }));
    expect(await screen.findByText("已创建项目快照")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "链路诊断" }));
    await user.click(screen.getByRole("button", { name: "生成诊断包" }));
    expect(await screen.findByText(/诊断包已生成/)).toBeInTheDocument();
  });
});
