import { useCallback, useEffect, useMemo, useState } from "react";

import {
  tauriSidecarApi,
  type SidecarApi,
  type SidecarEvent,
  type SidecarStatus,
} from "./sidecarApi";
import "./styles.css";

type Notice = { tone: "neutral" | "success" | "warning"; text: string };
type Progress = { requestId: string; current: number; total: number };
type ProjectInfo = {
  id: string;
  name: string;
  root_path: string;
  schema_version?: number;
};
type JobInfo = {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  last_error?: string | null;
};
type SnapshotInfo = {
  name: string;
  path: string;
  size_bytes: number;
  reason: string;
};
type ProjectOverview = {
  job_counts: Record<string, number>;
  failed_jobs: JobInfo[];
  disk: Record<string, number>;
  snapshots: SnapshotInfo[];
  queue_depth: number;
};
type ShellView = "overview" | "project" | "jobs" | "link";

interface AppProps {
  api?: SidecarApi;
  showDiagnostics?: boolean;
}

function formatBytes(value: number | undefined): string {
  if (!value || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function requestId(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `${prefix}-${suffix}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function App({
  api = tauriSidecarApi,
  showDiagnostics = import.meta.env.DEV,
}: AppProps) {
  const [status, setStatus] = useState<SidecarStatus | null>(null);
  const [notice, setNotice] = useState<Notice>({
    tone: "neutral",
    text: "正在读取桌面核心状态…",
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [recentEvents, setRecentEvents] = useState<SidecarEvent[]>([]);
  const [projectName, setProjectName] = useState("试播项目");
  const [parentDir, setParentDir] = useState("~/Documents/ai-video-projects");
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [recentProjects, setRecentProjects] = useState<ProjectInfo[]>([]);
  const [jobs, setJobs] = useState<JobInfo[]>([]);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [view, setView] = useState<ShellView>("overview");

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      return next;
    } catch (error) {
      setStatus(null);
      setNotice({ tone: "warning", text: errorMessage(error) });
      throw error;
    }
  }, [api]);

  const refreshProjectState = useCallback(async () => {
    const current = await api.request("project.current", {}, requestId("project-current"));
    const currentProject = (current.project as ProjectInfo | null | undefined) ?? null;
    setProject(currentProject);
    const recent = await api.request(
      "project.list_recent",
      { limit: 8 },
      requestId("project-recent"),
    );
    const list = (recent.projects as ProjectInfo[] | undefined) ?? [];
    setRecentProjects(list);
    if (currentProject) {
      const jobList = await api.request("job.list", { limit: 20 }, requestId("job-list"));
      setJobs(((jobList.jobs as JobInfo[] | undefined) ?? []).map((job) => ({
        id: job.id,
        kind: job.kind,
        status: job.status,
        attempts: job.attempts,
        last_error: (job.last_error as string | null | undefined) ?? null,
      })));
      const overviewResult = await api.request(
        "project.overview",
        {},
        requestId("project-overview"),
      );
      setOverview({
        job_counts: (overviewResult.job_counts as Record<string, number>) ?? {},
        failed_jobs: ((overviewResult.failed_jobs as JobInfo[] | undefined) ?? []).map(
          (job) => ({
            id: job.id,
            kind: job.kind,
            status: job.status,
            attempts: job.attempts,
            last_error: job.last_error ?? null,
          }),
        ),
        disk: (overviewResult.disk as Record<string, number>) ?? {},
        snapshots: (overviewResult.snapshots as SnapshotInfo[] | undefined) ?? [],
        queue_depth: Number(overviewResult.queue_depth ?? 0),
      });
    } else {
      setJobs([]);
      setOverview(null);
    }
  }, [api]);

  useEffect(() => {
    void refreshStatus().catch(() => undefined);
    void refreshProjectState().catch(() => undefined);
    let disposed = false;
    let stop: (() => void) | undefined;

    void api
      .listen((event) => {
        if (disposed) return;
        setRecentEvents((current) => [event, ...current].slice(0, 6));
        if (event.event !== "request.progress") return;
        const eventRequestId = event.data.request_id;
        const current = numeric(event.data.current);
        const total = numeric(event.data.total);
        if (typeof eventRequestId === "string" && current !== null && total !== null) {
          setProgress((active) =>
            active?.requestId === eventRequestId
              ? { requestId: eventRequestId, current, total }
              : active,
          );
        }
      })
      .then((unlisten) => {
        if (disposed) unlisten();
        else stop = unlisten;
      })
      .catch((error) => {
        if (!disposed) setNotice({ tone: "warning", text: errorMessage(error) });
      });

    return () => {
      disposed = true;
      stop?.();
    };
  }, [api, refreshProjectState, refreshStatus]);

  const healthLabel = status?.running ? "在线" : status ? "离线" : "未知";
  const healthTone = status?.running ? "online" : "offline";
  const progressPercent = useMemo(() => {
    if (!progress || progress.total <= 0) return 0;
    return Math.min(100, Math.round((progress.current / progress.total) * 100));
  }, [progress]);

  const ping = async () => {
    const id = requestId("ping");
    const started = performance.now();
    setBusy("ping");
    setNotice({ tone: "neutral", text: "正在发送版本化 NDJSON 请求…" });
    try {
      const result = await api.request("system.ping", { echo: "ui-check" }, id);
      const elapsed = Math.max(0, Math.round(performance.now() - started));
      setNotice({
        tone: "success",
        text: `协议 v${String(result.protocol_version)} 已响应 · ${elapsed} ms`,
      });
      await refreshStatus();
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const runDiagnostic = async () => {
    const id = requestId("count");
    setBusy("count");
    setProgress({ requestId: id, current: 0, total: 20 });
    setNotice({ tone: "neutral", text: "诊断请求运行中" });
    try {
      await api.request("diagnostics.count", { steps: 20, delay_ms: 80 }, id);
      setProgress({ requestId: id, current: 20, total: 20 });
      setNotice({ tone: "success", text: "诊断完成" });
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const cancelDiagnostic = async () => {
    if (!progress) return;
    try {
      const cancelled = await api.cancel(progress.requestId);
      setNotice({
        tone: cancelled ? "success" : "warning",
        text: cancelled ? "已发送取消请求" : "请求已经结束，无需取消",
      });
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    }
  };

  const verifyRecovery = async () => {
    setBusy("recovery");
    setNotice({ tone: "neutral", text: "正在触发受控崩溃…" });
    try {
      await api.request(
        "diagnostics.crash",
        { exit_code: 73 },
        requestId("crash"),
      );
    } catch {
      // The crash request must fail. It is intentionally never replayed.
    }

    try {
      await api.request(
        "system.ping",
        { echo: "recovered" },
        requestId("recover"),
      );
      const next = await refreshStatus();
      setNotice({
        tone: "success",
        text: `已恢复为 PID ${String(next.pid ?? "未知")}`,
      });
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const restart = async () => {
    setBusy("restart");
    try {
      const next = await api.restart();
      setStatus(next);
      setNotice({ tone: "success", text: `Sidecar 已重启 · PID ${String(next.pid)}` });
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const createProject = async () => {
    setBusy("project-create");
    setNotice({ tone: "neutral", text: "正在创建项目…" });
    try {
      const created = (await api.request(
        "project.create",
        { parent_dir: parentDir, name: projectName },
        requestId("project-create"),
      )) as ProjectInfo;
      setProject(created);
      setNotice({ tone: "success", text: `已创建并打开：${created.name}` });
      await refreshProjectState();
      setView("overview");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const openProjectPath = async (rootPath: string) => {
    setBusy("project-open");
    try {
      const opened = (await api.request(
        "project.open",
        { root_dir: rootPath },
        requestId("project-open"),
      )) as ProjectInfo;
      setProject(opened);
      setNotice({ tone: "success", text: `已打开：${opened.name}` });
      await refreshProjectState();
      setView("overview");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const enqueueDemoJob = async () => {
    setBusy("job-enqueue");
    try {
      const job = (await api.request(
        "job.enqueue",
        { kind: "demo.ping", payload: { source: "ui" }, max_attempts: 3 },
        requestId("job-enqueue"),
      )) as JobInfo;
      setNotice({ tone: "success", text: `已入队任务 ${job.id.slice(0, 8)}…` });
      await refreshProjectState();
      setView("jobs");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const createSnapshot = async () => {
    setBusy("snapshot");
    try {
      await api.request(
        "snapshot.create",
        { reason: "manual-ui" },
        requestId("snapshot-create"),
      );
      setNotice({ tone: "success", text: "已创建项目快照" });
      await refreshProjectState();
      setView("overview");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const createDiagnosticPack = async () => {
    setBusy("diag-pack");
    try {
      const pack = await api.request(
        "diagnostics.create_pack",
        {},
        requestId("diag-pack"),
      );
      setNotice({
        tone: "success",
        text: `诊断包已生成：${String(pack.path ?? "").split("/").slice(-1)[0] || "ok"}`,
      });
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const navItems: { id: ShellView; label: string }[] = [
    { id: "overview", label: "项目总览" },
    { id: "project", label: "项目" },
    { id: "jobs", label: "任务中心" },
    { id: "link", label: "链路诊断" },
  ];

  return (
    <main className="shell">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">帧</span>
          <div>
            <p className="eyebrow">AI VIDEO WORKFLOW / M1</p>
            <h1>工作流核心台</h1>
          </div>
        </div>
        <div className={`health-pill ${healthTone}`}>
          <span className="health-dot" aria-hidden="true" />
          <span>{healthLabel}</span>
        </div>
      </header>

      <nav className="shell-nav" aria-label="主导航">
        {navItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={view === item.id ? "nav-item active" : "nav-item"}
            aria-label={item.label}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => setView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className={`notice bar ${notice.tone}`} role="status">
        <span className="notice-index">LOG</span>
        <span>{notice.text}</span>
      </div>

      {view === "overview" && (
        <section className="console-panel" aria-label="项目总览">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">PROJECT OVERVIEW</p>
              <h3>总览</h3>
            </div>
            <span className="panel-number">M1.10</span>
          </div>
          {!project || !overview ? (
            <p className="empty-hint">尚未打开项目。请到「项目」创建或打开一个工作区。</p>
          ) : (
            <>
              <div className="overview-grid" aria-label="总览指标">
                <article className="stat-card">
                  <span>队列深度</span>
                  <strong>{overview.queue_depth}</strong>
                  <small>queued + running + paused</small>
                </article>
                <article className="stat-card">
                  <span>失败任务</span>
                  <strong>{overview.job_counts.failed ?? 0}</strong>
                  <small>需人工处理</small>
                </article>
                <article className="stat-card">
                  <span>磁盘占用</span>
                  <strong>{formatBytes(overview.disk.total_bytes)}</strong>
                  <small>assets / renders / temp / logs / snapshots</small>
                </article>
                <article className="stat-card">
                  <span>快照</span>
                  <strong>{overview.snapshots.length}</strong>
                  <small>最近列表</small>
                </article>
              </div>
              <div className="overview-columns">
                <div>
                  <h4>磁盘明细</h4>
                  <ul className="job-list" aria-label="磁盘明细">
                    <li>assets · {formatBytes(overview.disk.assets_bytes)}</li>
                    <li>renders · {formatBytes(overview.disk.renders_bytes)}</li>
                    <li>temp · {formatBytes(overview.disk.temp_bytes)}</li>
                    <li>logs · {formatBytes(overview.disk.logs_bytes)}</li>
                    <li>snapshots · {formatBytes(overview.disk.snapshots_bytes)}</li>
                    <li>project.db · {formatBytes(overview.disk.project_db_bytes)}</li>
                  </ul>
                </div>
                <div>
                  <h4>失败项</h4>
                  {overview.failed_jobs.length === 0 ? (
                    <p className="empty-hint">暂无失败任务</p>
                  ) : (
                    <ul className="job-list" aria-label="失败任务">
                      {overview.failed_jobs.map((job) => (
                        <li key={job.id}>
                          <code>failed</code> {job.kind}
                          {job.last_error ? ` · ${job.last_error}` : ""}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>最近快照</h4>
                  {overview.snapshots.length === 0 ? (
                    <p className="empty-hint">暂无快照</p>
                  ) : (
                    <ul className="job-list" aria-label="最近快照">
                      {overview.snapshots.map((snap) => (
                        <li key={snap.name}>
                          {snap.reason} · {formatBytes(snap.size_bytes)}
                        </li>
                      ))}
                    </ul>
                  )}
                  <button
                    className="text-button"
                    aria-label="创建快照"
                    onClick={() => void createSnapshot()}
                    disabled={busy !== null}
                  >
                    创建快照
                  </button>
                </div>
              </div>
              <p className="project-meta">
                当前项目：<strong>{project.name}</strong> · {project.root_path}
              </p>
            </>
          )}
        </section>
      )}

      {view === "project" && (
        <section className="console-panel" aria-label="项目工作区">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">PROJECT WORKSPACE</p>
              <h3>项目</h3>
            </div>
            <span className="panel-number">M1.05</span>
          </div>
          <div className="project-form">
            <label>
              项目名称
              <input
                aria-label="项目名称"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
              />
            </label>
            <label>
              父目录
              <input
                aria-label="项目父目录"
                value={parentDir}
                onChange={(event) => setParentDir(event.target.value)}
              />
            </label>
          </div>
          <div className="action-grid compact">
            <button
              aria-label="创建项目"
              className="action primary"
              onClick={createProject}
              disabled={busy !== null}
            >
              <span className="action-no">P1</span>
              <span><strong>创建项目</strong><small>project.create + 打开</small></span>
            </button>
            <button
              aria-label="刷新总览"
              className="action"
              onClick={() => void refreshProjectState().then(() => setView("overview"))}
              disabled={busy !== null || !project}
            >
              <span className="action-no">P2</span>
              <span><strong>查看总览</strong><small>队列 / 磁盘 / 快照</small></span>
            </button>
          </div>
          <div className="project-meta">
            <p>
              当前项目：{" "}
              <strong>{project ? `${project.name} · ${project.root_path}` : "未打开"}</strong>
            </p>
            {recentProjects.length > 0 && (
              <div className="recent-list" aria-label="最近项目">
                {recentProjects.map((item) => (
                  <button
                    key={item.id}
                    className="text-button"
                    onClick={() => void openProjectPath(item.root_path)}
                    disabled={busy !== null}
                  >
                    打开 {item.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {view === "jobs" && (
        <section className="console-panel" aria-label="任务中心">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">TASK CENTER</p>
              <h3>任务中心</h3>
            </div>
            <span className="panel-number">M1.08</span>
          </div>
          <div className="action-grid compact">
            <button
              aria-label="入队演示任务"
              className="action primary"
              onClick={enqueueDemoJob}
              disabled={busy !== null || !project}
            >
              <span className="action-no">J1</span>
              <span><strong>入队演示任务</strong><small>job.enqueue demo.ping</small></span>
            </button>
            <button
              aria-label="刷新任务"
              className="action"
              onClick={() => void refreshProjectState()}
              disabled={busy !== null || !project}
            >
              <span className="action-no">J2</span>
              <span><strong>刷新列表</strong><small>job.list + overview</small></span>
            </button>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : jobs.length === 0 ? (
            <p className="empty-hint">队列为空。</p>
          ) : (
            <ul className="job-list" aria-label="任务列表">
              {jobs.map((job) => (
                <li key={job.id}>
                  <code>{job.status}</code> {job.kind} · 尝试 {job.attempts}
                  {job.last_error ? ` · ${job.last_error}` : ""}
                </li>
              ))}
            </ul>
          )}
          {overview && (
            <p className="project-meta">
              队列深度 <strong>{overview.queue_depth}</strong>
              {" · "}
              失败 <strong>{overview.job_counts.failed ?? 0}</strong>
              {" · "}
              成功 <strong>{overview.job_counts.succeeded ?? 0}</strong>
            </p>
          )}
        </section>
      )}

      {view === "link" && (
        <section className="console-panel" aria-label="链路诊断">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CONTROL SURFACE</p>
              <h3>链路诊断</h3>
            </div>
            <span className="panel-number">M0.03</span>
          </div>

          <aside className="status-card inline" aria-label="Sidecar 状态">
            <div className="status-card-head">
              <span>SIDECAR / SUPERVISOR</span>
              <span className="live-code">{status?.running ? "RUN" : "STOP"}</span>
            </div>
            <dl className="metrics">
              <div>
                <dt>进程</dt>
                <dd>{status?.pid ? `PID ${status.pid}` : "尚未启动"}</dd>
              </div>
              <div>
                <dt>协议</dt>
                <dd>NDJSON · v1</dd>
              </div>
              <div>
                <dt>监督恢复</dt>
                <dd>{status ? `${status.restart_count} 次恢复` : "—"}</dd>
              </div>
            </dl>
          </aside>

          <div className="action-grid">
            <button aria-label="发送 Ping" className="action primary" onClick={ping} disabled={busy !== null}>
              <span className="action-no">01</span>
              <span><strong>发送 Ping</strong><small>启动并验证协议握手</small></span>
              <span className="arrow" aria-hidden="true">↗</span>
            </button>
            <button aria-label="重启 Sidecar" className="action" onClick={restart} disabled={busy !== null}>
              <span className="action-no">02</span>
              <span><strong>重启 Sidecar</strong><small>终止旧进程并重新握手</small></span>
              <span className="arrow" aria-hidden="true">↻</span>
            </button>
            <button
              aria-label="生成诊断包"
              className="action"
              onClick={() => void createDiagnosticPack()}
              disabled={busy !== null}
            >
              <span className="action-no">03</span>
              <span><strong>生成诊断包</strong><small>脱敏日志与任务摘要</small></span>
            </button>
            {showDiagnostics && (
              <>
                <button aria-label="运行 20 步诊断" className="action" onClick={runDiagnostic} disabled={busy !== null}>
                  <span className="action-no">04</span>
                  <span><strong>运行 20 步诊断</strong><small>验证事件流与进度更新</small></span>
                  <span className="arrow" aria-hidden="true">▶</span>
                </button>
                <button aria-label="验证崩溃恢复" className="action danger" onClick={verifyRecovery} disabled={busy !== null}>
                  <span className="action-no">05</span>
                  <span><strong>验证崩溃恢复</strong><small>崩溃请求不会自动重放</small></span>
                  <span className="arrow" aria-hidden="true">⚡</span>
                </button>
              </>
            )}
          </div>

          {progress && (
            <div className="progress-block">
              <div className="progress-copy">
                <span>REQUEST / {progress.requestId.slice(0, 18)}</span>
                <strong>{progress.current} / {progress.total}</strong>
              </div>
              <div className="progress-track" aria-label="诊断进度">
                <span style={{ width: `${progressPercent}%` }} />
              </div>
              <button className="text-button" onClick={cancelDiagnostic} disabled={busy !== "count"}>
                取消当前请求
              </button>
            </div>
          )}

          <section className="event-strip" aria-label="最近事件">
            <span className="event-title">EVENT STREAM</span>
            <div className="event-list">
              {recentEvents.length === 0 ? (
                <span className="event-empty">等待 Sidecar 事件</span>
              ) : (
                recentEvents.map((event, index) => (
                  <span className="event-item" key={`${event.event}-${index}`}>
                    {event.event}
                  </span>
                ))
              )}
            </div>
          </section>
        </section>
      )}
    </main>
  );
}
