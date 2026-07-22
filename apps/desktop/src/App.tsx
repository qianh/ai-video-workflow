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

interface AppProps {
  api?: SidecarApi;
  showDiagnostics?: boolean;
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

  useEffect(() => {
    void refreshStatus().catch(() => undefined);
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
  }, [api, refreshStatus]);

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

  return (
    <main className="shell">
      <div className="ambient-grid" aria-hidden="true" />
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">帧</span>
          <div>
            <p className="eyebrow">AI VIDEO WORKFLOW / M0</p>
            <h1>工作流核心台</h1>
          </div>
        </div>
        <div className={`health-pill ${healthTone}`}>
          <span className="health-dot" aria-hidden="true" />
          <span>{healthLabel}</span>
        </div>
      </header>

      <section className="hero-grid">
        <div className="hero-copy">
          <p className="chapter">基础设施验证 · 01</p>
          <h2>
            先让进程链路
            <span>可靠地呼吸。</span>
          </h2>
          <p className="lede">
            这一屏只验证一件事：React 经由 Rust 与 Python Sidecar 通信，
            请求可追踪、长任务可取消、进程崩溃后可安全恢复。
          </p>
        </div>

        <aside className="status-card" aria-label="Sidecar 状态">
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
          <div className={`notice ${notice.tone}`} role="status">
            <span className="notice-index">LOG</span>
            <span>{notice.text}</span>
          </div>
        </aside>
      </section>

      <section className="console-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">CONTROL SURFACE</p>
            <h3>链路动作</h3>
          </div>
          <span className="panel-number">M0.03</span>
        </div>

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
          {showDiagnostics && (
            <>
              <button aria-label="运行 20 步诊断" className="action" onClick={runDiagnostic} disabled={busy !== null}>
                <span className="action-no">03</span>
                <span><strong>运行 20 步诊断</strong><small>验证事件流与进度更新</small></span>
                <span className="arrow" aria-hidden="true">▶</span>
              </button>
              <button aria-label="验证崩溃恢复" className="action danger" onClick={verifyRecovery} disabled={busy !== null}>
                <span className="action-no">04</span>
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
      </section>

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
    </main>
  );
}
