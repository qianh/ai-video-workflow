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
type ShellView =
  | "overview"
  | "project"
  | "story"
  | "packs"
  | "package"
  | "scripts"
  | "characters"
  | "world"
  | "continuity"
  | "drafts"
  | "generation"
  | "jobs"
  | "link";
type StorySourceInfo = {
  id: string;
  title: string;
  source_type: string;
  status: string;
  char_count: number;
};
type StoryChunkInfo = {
  id: string;
  title: string | null;
  ordinal: number;
  char_start: number;
  char_end: number;
};
type StoryEventInfo = {
  event_id: string;
  title: string;
  summary: string;
  origin: string;
  order_key: number;
};

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
  const [storyText, setStoryText] = useState(
    "# 第一章 夜市\n\n女孩在雨中捡到发光的 U 盘。\n\n# 第二章 追索\n\n她发现 U 盘里藏着一段失踪消息。\n",
  );
  const [storyTitle, setStoryTitle] = useState("试播小说");
  const [sources, setSources] = useState<StorySourceInfo[]>([]);
  const [chunks, setChunks] = useState<StoryChunkInfo[]>([]);
  const [storyEvents, setStoryEvents] = useState<StoryEventInfo[]>([]);
  const [packLock, setPackLock] = useState<string | null>(null);
  const [packCompositions, setPackCompositions] = useState<
    { name: string; status: string; composition_revision_id: string }[]
  >([]);
  const [branches, setBranches] = useState<
    { id: string; name: string; status: string; is_primary: boolean }[]
  >([]);
  const [drafts, setDrafts] = useState<
    { id: string; title: string; status: string; schema_id: string }[]
  >([]);
  const [revisions, setRevisions] = useState<
    { id: string; title: string; revision_no: number; status: string }[]
  >([]);
  const [genRuns, setGenRuns] = useState<
    { id: string; title: string; status: string; iteration: number }[]
  >([]);
  const [worldRules, setWorldRules] = useState<
    { id: string; category: string; rule_text: string; force_level: string }[]
  >([]);
  const [timelineBeats, setTimelineBeats] = useState<
    { id: string; beat_no: number; title: string; summary: string }[]
  >([]);
  const [episodes, setEpisodes] = useState<
    { id: string; episode_no: number; title: string; status: string }[]
  >([]);
  const [packageRevisions, setPackageRevisions] = useState<
    {
      id: string;
      name?: string;
      status: string;
      contains_media_prompts: boolean;
    }[]
  >([]);
  const [scriptSummary, setScriptSummary] = useState<{
    scriptId: string;
    status: string;
    title: string;
    sceneCount: number;
    lineCount: number;
    hookCount: number;
    episodeStatus: string;
  } | null>(null);
  const [characterSummary, setCharacterSummary] = useState<{
    characterCount: number;
    relationshipCount: number;
    voiceCount: number;
    names: string[];
    identityConfirmed: boolean;
    lookCount: number;
    gateReady: boolean;
  } | null>(null);
  const [worldSummary, setWorldSummary] = useState<{
    locationCount: number;
    propCount: number;
    linkCount: number;
    locationNames: string[];
    coreGateReady: boolean;
  } | null>(null);
  const [continuitySummary, setContinuitySummary] = useState<{
    stateCount: number;
    blockerCount: number;
    warningCount: number;
    snapshotCount: number;
    sampleKeys: string[];
  } | null>(null);

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
      setSources([]);
      setChunks([]);
      setStoryEvents([]);
      setPackLock(null);
      setPackCompositions([]);
      setBranches([]);
      setDrafts([]);
      setRevisions([]);
      setGenRuns([]);
      setWorldRules([]);
      setTimelineBeats([]);
      setEpisodes([]);
      setPackageRevisions([]);
      setScriptSummary(null);
      setCharacterSummary(null);
      setWorldSummary(null);
      setContinuitySummary(null);
    }
  }, [api]);

  const refreshSeasonPackageState = useCallback(async () => {
    if (!project) {
      setWorldRules([]);
      setTimelineBeats([]);
      setEpisodes([]);
      setPackageRevisions([]);
      return;
    }
    const overview = await api.request(
      "season.overview",
      {},
      requestId("season-overview"),
    );
    setWorldRules(
      ((overview.world_rules as Array<Record<string, unknown>> | undefined) ?? []).map(
        (item) => ({
          id: String(item.id ?? ""),
          category: String(item.category ?? ""),
          rule_text: String(item.rule_text ?? ""),
          force_level: String(item.force_level ?? ""),
        }),
      ),
    );
    setTimelineBeats(
      ((overview.timeline as Array<Record<string, unknown>> | undefined) ?? []).map(
        (item) => ({
          id: String(item.id ?? ""),
          beat_no: Number(item.beat_no ?? 0),
          title: String(item.title ?? ""),
          summary: String(item.summary ?? ""),
        }),
      ),
    );
    setEpisodes(
      ((overview.episodes as Array<Record<string, unknown>> | undefined) ?? []).map(
        (item) => ({
          id: String(item.id ?? ""),
          episode_no: Number(item.episode_no ?? 0),
          title: String(item.title ?? ""),
          status: String(item.status ?? ""),
        }),
      ),
    );
    setPackageRevisions(
      ((overview.packages as Array<Record<string, unknown>> | undefined) ?? []).map(
        (item) => ({
          id: String(item.id ?? ""),
          status: String(item.status ?? ""),
          contains_media_prompts: Boolean(item.contains_media_prompts),
        }),
      ),
    );
  }, [api, project]);

  const refreshGenerationState = useCallback(async () => {
    if (!project) {
      setGenRuns([]);
      return;
    }
    const listed = await api.request(
      "generation.list",
      { limit: 20 },
      requestId("gen-list"),
    );
    setGenRuns(
      ((listed.runs as Array<Record<string, unknown>> | undefined) ?? []).map((item) => ({
        id: String(item.id ?? ""),
        title: String(item.title ?? ""),
        status: String(item.status ?? ""),
        iteration: Number(item.iteration ?? 1),
      })),
    );
  }, [api, project]);

  const refreshDraftState = useCallback(async () => {
    if (!project) {
      setDrafts([]);
      setRevisions([]);
      return;
    }
    const listed = await api.request("draft.list", { limit: 20 }, requestId("draft-list"));
    setDrafts(
      ((listed.drafts as Array<Record<string, unknown>> | undefined) ?? []).map((item) => ({
        id: String(item.id ?? ""),
        title: String(item.title ?? ""),
        status: String(item.status ?? ""),
        schema_id: String(item.schema_id ?? ""),
      })),
    );
    const revs = await api.request("revision.list", { limit: 20 }, requestId("revision-list"));
    setRevisions(
      ((revs.revisions as Array<Record<string, unknown>> | undefined) ?? []).map((item) => ({
        id: String(item.id ?? ""),
        title: String(item.title ?? ""),
        revision_no: Number(item.revision_no ?? 0),
        status: String(item.status ?? ""),
      })),
    );
  }, [api, project]);

  const refreshBranches = useCallback(async () => {
    if (!project) {
      setBranches([]);
      return;
    }
    const listed = await api.request("story.list_branches", {}, requestId("story-branches"));
    setBranches(
      ((listed.branches as Array<Record<string, unknown>> | undefined) ?? []).map((item) => ({
        id: String(item.id ?? ""),
        name: String(item.name ?? ""),
        status: String(item.status ?? ""),
        is_primary: Boolean(item.is_primary),
      })),
    );
  }, [api, project]);

  const refreshPackState = useCallback(async () => {
    if (!project) {
      setPackLock(null);
      setPackCompositions([]);
      return;
    }
    const lockResult = await api.request("pack.current_lock", {}, requestId("pack-lock"));
    const lock = lockResult.lock as { id?: string } | null | undefined;
    setPackLock(lock?.id ?? null);
    const compositions = await api.request(
      "pack.list_compositions",
      {},
      requestId("pack-compositions"),
    );
    setPackCompositions(
      ((compositions.compositions as Array<Record<string, string>> | undefined) ?? []).map(
        (item) => ({
          name: String(item.name ?? ""),
          status: String(item.status ?? ""),
          composition_revision_id: String(item.composition_revision_id ?? ""),
        }),
      ),
    );
  }, [api, project]);

  const refreshStoryState = useCallback(async () => {
    if (!project) {
      setSources([]);
      setChunks([]);
      setStoryEvents([]);
      return;
    }
    const listed = await api.request("story.list_sources", {}, requestId("story-sources"));
    const nextSources = (listed.sources as StorySourceInfo[] | undefined) ?? [];
    setSources(nextSources);
    if (nextSources[0]) {
      const chunkList = await api.request(
        "story.list_chunks",
        { source_id: nextSources[0].id },
        requestId("story-chunks"),
      );
      setChunks((chunkList.chunks as StoryChunkInfo[] | undefined) ?? []);
    } else {
      setChunks([]);
    }
    const events = await api.request("story.list_events", {}, requestId("story-events"));
    setStoryEvents((events.events as StoryEventInfo[] | undefined) ?? []);
  }, [api, project]);

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

  const setupDefaultPacks = async () => {
    setBusy("pack-setup");
    try {
      const visual = await api.request(
        "pack.register",
        {
          name: "赛博夜景",
          pack_type: "visual_style",
          rules: { palette: "neon", hard_ratio: "9:16" },
        },
        requestId("pack-visual"),
      );
      const narrative = await api.request(
        "pack.register",
        {
          name: "都市悬疑",
          pack_type: "narrative_genre",
          rules: { hooks: "mystery", pace: "fast" },
        },
        requestId("pack-narrative"),
      );
      const technique = await api.request(
        "pack.register",
        {
          name: "Grok 图技",
          pack_type: "model_technique",
          rules: { prompt_prefix: "manhua" },
          resources: { required: ["lut"], available: ["lut"] },
        },
        requestId("pack-technique"),
      );
      const composed = await api.request(
        "pack.compose",
        {
          name: "夜市默认组合",
          visual_revision_id: (visual.revision as { id: string }).id,
          narrative_revision_id: (narrative.revision as { id: string }).id,
          technique_revision_ids: [(technique.revision as { id: string }).id],
        },
        requestId("pack-compose"),
      );
      const compositionRevisionId = String(composed.composition_revision_id);
      await api.request(
        "pack.evaluate",
        { composition_revision_id: compositionRevisionId },
        requestId("pack-eval"),
      );
      const lock = await api.request(
        "pack.lock",
        { composition_revision_id: compositionRevisionId, purpose: "production" },
        requestId("pack-lock-create"),
      );
      setNotice({
        tone: "success",
        text: `已锁定 Creative Pack：${String(lock.id).slice(0, 8)}…`,
      });
      await refreshPackState();
      setView("packs");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const buildContinuityLedger = async () => {
    setBusy("continuity-setup");
    try {
      await api.request(
        "continuity.add",
        {
          subject_type: "character",
          subject_id: "char-aning",
          state_key: "outfit",
          value: { item: "raincoat" },
          story_time_from: "E01",
          time_from_ord: 100,
          story_time_to: "E02",
          time_to_ord: 200,
          priority: 0,
          source_type: "script",
        },
        requestId("cont-outfit"),
      );
      await api.request(
        "continuity.add",
        {
          subject_type: "character",
          subject_id: "char-aning",
          state_key: "outfit",
          value: { item: "hospital gown" },
          story_time_from: "E01.injury",
          time_from_ord: 120,
          time_to_ord: 160,
          priority: 10,
          source_type: "user",
        },
        requestId("cont-outfit-hi"),
      );
      await api.request(
        "continuity.add",
        {
          subject_type: "character",
          subject_id: "char-aning",
          state_key: "injury",
          value: { part: "left arm", severity: "bruise" },
          story_time_from: "E01",
          time_from_ord: 100,
          priority: 0,
        },
        requestId("cont-injury"),
      );
      await api.request(
        "continuity.add",
        {
          subject_type: "prop",
          subject_id: "prop-usb",
          state_key: "owner",
          value: { character_id: "char-aning" },
          story_time_from: "E01",
          time_from_ord: 100,
          priority: 0,
        },
        requestId("cont-owner"),
      );
      const check = await api.request(
        "continuity.check",
        {},
        requestId("cont-check"),
      );
      await api.request(
        "continuity.snapshot",
        {
          at_story_time: "E01.mid",
          at_time_ord: 130,
          purpose: "ui sample",
        },
        requestId("cont-snap"),
      );
      const overview = await api.request(
        "continuity.overview",
        {},
        requestId("cont-overview"),
      );
      const states =
        (overview.states as Array<{ state_key?: string }> | undefined) ?? [];
      const conflicts = (overview.conflicts as {
        blocker_count?: number;
        warning_count?: number;
      } | undefined) ?? {};
      const snapshots = (overview.snapshots as unknown[] | undefined) ?? [];
      setContinuitySummary({
        stateCount: Number(overview.active_state_count ?? states.length),
        blockerCount: Number(conflicts.blocker_count ?? check.blocker_count ?? 0),
        warningCount: Number(conflicts.warning_count ?? check.warning_count ?? 0),
        snapshotCount: snapshots.length,
        sampleKeys: [...new Set(states.map((s) => String(s.state_key ?? "")))],
      });
      setNotice({
        tone: "success",
        text: `状态账本就绪 · states=${String(overview.active_state_count)} · blockers=${String(conflicts.blocker_count ?? 0)} · snaps=${snapshots.length}`,
      });
      setView("continuity");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const buildWorldSpace = async () => {
    setBusy("world-setup");
    try {
      const market = await api.request(
        "location.create",
        {
          name: "夜市东口",
          location_type: "exterior",
          description: "雨夜摊位区",
          is_core: true,
        },
        requestId("loc-market"),
      );
      const alley = await api.request(
        "location.create",
        {
          name: "后巷",
          location_type: "exterior",
          description: "狭窄后巷",
        },
        requestId("loc-alley"),
      );
      await api.request(
        "location.approve",
        {
          revision_id: String(
            (market.current_revision as { id?: string } | undefined)?.id ?? "",
          ),
        },
        requestId("loc-market-ok"),
      );
      await api.request(
        "location.approve",
        {
          revision_id: String(
            (alley.current_revision as { id?: string } | undefined)?.id ?? "",
          ),
        },
        requestId("loc-alley-ok"),
      );
      await api.request(
        "spatial.add_link",
        {
          source_location_id: String(market.id),
          target_location_id: String(alley.id),
          link_type: "connected",
          description: "东口通往后巷",
        },
        requestId("spatial-link"),
      );
      const prop = await api.request(
        "prop.create",
        {
          name: "发光 U 盘",
          appearance: "半透明冷蓝光",
          state_notes: "首次出现在夜市",
        },
        requestId("prop-create"),
      );
      await api.request(
        "prop.approve",
        {
          revision_id: String(
            (prop.current_revision as { id?: string } | undefined)?.id ?? "",
          ),
        },
        requestId("prop-ok"),
      );
      const pack = await api.request(
        "location.create_pack",
        {
          location_id: String(market.id),
          layout: { zones: ["stalls", "entrance"] },
          direction_axis: "east-west",
          primary_view: "from entrance looking east",
          camera_angles: ["wide establishing", "medium stall"],
          entrances: [{ id: "main", side: "west" }],
          day_variant: { light: "overcast neon" },
          night_variant: { light: "rain neon", wet_ground: true },
        },
        requestId("loc-pack"),
      );
      const packRev = String(
        (pack.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      await api.request(
        "location.anchor_prop",
        {
          revision_id: packRev,
          prop_id: String(prop.id),
          anchor_label: "stall_floor",
          position: { x: 1.2, y: 0, z: 0.4 },
        },
        requestId("loc-anchor"),
      );
      await api.request(
        "location.confirm_pack",
        { revision_id: packRev },
        requestId("loc-confirm"),
      );
      const gate = await api.request(
        "location.gate",
        { location_id: String(market.id) },
        requestId("loc-gate"),
      );
      const overview = await api.request(
        "location.overview",
        {},
        requestId("loc-overview"),
      );
      const locations =
        (overview.locations as Array<{
          current_revision?: { name?: string };
        }> | undefined) ?? [];
      const props = (overview.props as unknown[] | undefined) ?? [];
      const links = (overview.spatial_links as unknown[] | undefined) ?? [];
      setWorldSummary({
        locationCount: locations.length,
        propCount: props.length,
        linkCount: links.length,
        locationNames: locations.map((item) =>
          String(item.current_revision?.name ?? ""),
        ),
        coreGateReady: Boolean(gate.ready_for_production),
      });
      setNotice({
        tone: "success",
        text: `场景世界已确认 · locs=${locations.length} · props=${props.length} · links=${links.length} · gate=${String(gate.ready_for_production)}`,
      });
      setView("world");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const buildCharacterCast = async () => {
    setBusy("character-setup");
    try {
      const hero = await api.request(
        "character.create",
        {
          name: "阿宁",
          role: "protagonist",
          age_feel: "二十出头",
          body_type: "纤瘦",
          appearance_rules: "短发，雨衣，冷色调",
          personality: ["冷静", "好奇"],
          goals: "查清失踪真相",
          immutable_traits: ["左眉疤"],
        },
        requestId("char-hero"),
      );
      const support = await api.request(
        "character.create",
        {
          name: "陈叔",
          role: "supporting",
          appearance_rules: "中年摊主，围裙",
          personality: ["热心", "碎嘴"],
          goals: "守住夜市摊子",
        },
        requestId("char-support"),
      );
      const heroRev = String(
        (hero.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      const supportRev = String(
        (support.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      await api.request(
        "character.approve",
        { revision_id: heroRev },
        requestId("char-hero-approve"),
      );
      await api.request(
        "character.approve",
        { revision_id: supportRev },
        requestId("char-support-approve"),
      );
      const rel = await api.request(
        "relationship.create",
        {
          source_character_id: String(hero.id),
          target_character_id: String(support.id),
          relationship_type: "acquaintance",
          description: "夜市摊主认识阿宁",
          story_time_from: "E01",
        },
        requestId("rel-create"),
      );
      const relRev = String(
        (rel.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      await api.request(
        "relationship.approve",
        { revision_id: relRev },
        requestId("rel-approve"),
      );
      const voice = await api.request(
        "voice.create",
        {
          character_id: String(hero.id),
          label: "阿宁默认",
          engine_adapter_id: "local-tts",
          speed: 1.05,
          emotion_range: ["平静", "警惕"],
          pronunciation_rules: { U盘: "优盘" },
        },
        requestId("voice-create"),
      );
      const voiceRev = String(
        (voice.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      await api.request(
        "voice.approve",
        { revision_id: voiceRev },
        requestId("voice-approve"),
      );
      const pack = await api.request(
        "identity.create",
        {
          character_id: String(hero.id),
          positive_prompt: "cold-tone night market girl, short hair, raincoat",
          negative_prompt: "blurry, extra limbs",
          height_cm: 165,
          proportion_notes: "head:body 1:7",
          voice_profile_id: String(voice.id),
        },
        requestId("identity-create"),
      );
      const packRev = String(
        (pack.current_revision as { id?: string } | undefined)?.id ?? "",
      );
      const looks = await api.request(
        "identity.generate_looks",
        { revision_id: packRev, count: 3 },
        requestId("identity-looks"),
      );
      const candidates =
        (looks.candidates as Array<{ id?: string }> | undefined) ?? [];
      const firstLook = String(candidates[0]?.id ?? "");
      if (firstLook) {
        await api.request(
          "identity.select_look",
          { candidate_id: firstLook },
          requestId("identity-select"),
        );
      }
      await api.request(
        "identity.confirm",
        { revision_id: packRev },
        requestId("identity-confirm"),
      );
      const gate = await api.request(
        "identity.gate",
        { character_id: String(hero.id) },
        requestId("identity-gate"),
      );
      const overview = await api.request(
        "character.overview",
        {},
        requestId("char-overview"),
      );
      const characters =
        (overview.characters as Array<{ current_revision?: { name?: string } }> | undefined) ??
        [];
      const relationships = (overview.relationships as unknown[] | undefined) ?? [];
      const voices = (overview.voice_profiles as unknown[] | undefined) ?? [];
      setCharacterSummary({
        characterCount: characters.length,
        relationshipCount: relationships.length,
        voiceCount: voices.length,
        names: characters.map((item) => String(item.current_revision?.name ?? "")),
        identityConfirmed: Boolean(gate.ready_for_production),
        lookCount: candidates.length,
        gateReady: Boolean(gate.ready_for_production),
      });
      setNotice({
        tone: "success",
        text: `角色+定妆完成 · chars=${characters.length} · looks=${candidates.length} · gate=${String(gate.ready_for_production)}`,
      });
      setView("characters");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const buildEpisodeScript = async () => {
    setBusy("script-setup");
    try {
      const ensured = await api.request(
        "season.ensure_episodes",
        { count: 1 },
        requestId("script-episodes"),
      );
      const episodeId = String(
        ((ensured.episodes as Array<{ id?: string }> | undefined) ?? [])[0]?.id ?? "",
      );
      if (!episodeId) throw new Error("no episode available");
      const created = await api.request(
        "script.create",
        {
          episode_id: episodeId,
          title: "夜市开端",
          goal: "建立悬念并引出失踪线索",
          main_conflict: "发光 U 盘与失踪消息",
          opening_hook: "雨夜捡到发光 U 盘",
          ending_hook: "未知号码打来电话",
          twist: "U 盘像是她自己寄回的",
          estimated_duration_ms: 90000,
        },
        requestId("script-create"),
      );
      const scriptId = String(created.id);
      const scene = await api.request(
        "script.add_scene",
        {
          script_id: scriptId,
          purpose: "发现道具",
          action_text: "女孩在摊位旁捡起发光 U 盘",
          time_of_day: "night",
          location_ref: "夜市东口",
        },
        requestId("script-scene"),
      );
      await api.request(
        "script.add_dialogue",
        {
          scene_id: String(scene.id),
          speaker_name: "阿宁",
          text: "这光……不像普通 U 盘。",
          line_type: "dialogue",
          emotion: "警惕",
        },
        requestId("script-line"),
      );
      await api.request(
        "script.add_dialogue",
        {
          scene_id: String(scene.id),
          text: "雨声盖过她的呼吸。",
          line_type: "narration",
        },
        requestId("script-narration"),
      );
      await api.request(
        "script.add_hook",
        {
          script_id: scriptId,
          hook_type: "mid",
          text: "加密视频最后一帧是她自己的脸",
          position_scene_no: 1,
        },
        requestId("script-hook"),
      );
      const validated = await api.request(
        "script.validate",
        { script_id: scriptId },
        requestId("script-validate"),
      );
      if (!validated.valid) {
        throw new Error(
          `script validation failed: ${JSON.stringify(validated.validation_errors)}`,
        );
      }
      const approved = await api.request(
        "script.approve",
        { script_id: scriptId },
        requestId("script-approve"),
      );
      const tree = await api.request(
        "script.tree",
        { script_id: scriptId },
        requestId("script-tree"),
      );
      const scenes = (tree.scenes as unknown[] | undefined) ?? [];
      const lines = (tree.dialogue as unknown[] | undefined) ?? [];
      const hooks = (tree.hooks as unknown[] | undefined) ?? [];
      const episode = tree.episode as { status?: string } | undefined;
      setScriptSummary({
        scriptId,
        status: String(approved.status ?? "approved"),
        title: String((tree.script as { title?: string } | undefined)?.title ?? "夜市开端"),
        sceneCount: scenes.length,
        lineCount: lines.length,
        hookCount: hooks.length,
        episodeStatus: String(episode?.status ?? ""),
      });
      setNotice({
        tone: "success",
        text: `分集剧本已批准 · scenes=${scenes.length} · lines=${lines.length} · hooks=${hooks.length} · episode=${String(episode?.status ?? "")}`,
      });
      setView("scripts");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const buildSeasonPackage = async () => {
    setBusy("package-setup");
    try {
      const existing = await api.request(
        "season.overview",
        {},
        requestId("season-overview-pre"),
      );
      let ruleIds = (
        (existing.world_rules as Array<{ id?: string }> | undefined) ?? []
      ).map((item) => String(item.id));
      if (ruleIds.length === 0) {
        const hard = await api.request(
          "world.add_rule",
          {
            category: "continuity",
            rule_text: "forbid:时间旅行",
            force_level: "hard",
          },
          requestId("world-hard"),
        );
        const soft = await api.request(
          "world.add_rule",
          {
            category: "tone",
            rule_text: "保持冷色夜市氛围",
            force_level: "soft",
          },
          requestId("world-soft"),
        );
        ruleIds = [String(hard.id), String(soft.id)];
      }

      let beatIds = (
        (existing.timeline as Array<{ id?: string }> | undefined) ?? []
      ).map((item) => String(item.id));
      if (beatIds.length === 0) {
        const beat1 = await api.request(
          "season.add_beat",
          {
            beat_no: 1,
            title: "发现",
            summary: "雨夜捡到发光 U 盘",
            arc_tag: "setup",
            episode_nos: [1],
          },
          requestId("season-beat-1"),
        );
        const beat2 = await api.request(
          "season.add_beat",
          {
            beat_no: 2,
            title: "追索",
            summary: "追查失踪消息来源",
            arc_tag: "rising",
            episode_nos: [2, 3],
          },
          requestId("season-beat-2"),
        );
        beatIds = [String(beat1.id), String(beat2.id)];
      }

      const ensured = await api.request(
        "season.ensure_episodes",
        { count: 3 },
        requestId("season-episodes"),
      );
      const episodeIds = (
        (ensured.episodes as Array<{ id?: string }> | undefined) ?? []
      ).map((item) => String(item.id));
      const created = await api.request(
        "package.create",
        {
          name: "试播季故事包",
          positioning: { theme: "都市悬疑", audience: "短剧" },
          world_rule_ids: ruleIds,
          timeline_beat_ids: beatIds,
          episode_ids: episodeIds,
          notes: "UI sample package",
          claims_for_rules: ["雨夜追逐"],
        },
        requestId("package-create"),
      );
      const approved = await api.request(
        "package.approve",
        { revision_id: String(created.id) },
        requestId("package-approve"),
      );
      setNotice({
        tone: "success",
        text: `故事包已批准 · status=${String(approved.status)} · media_prompts=${String(approved.contains_media_prompts)} · 分集 ${episodeIds.length}`,
      });
      await refreshSeasonPackageState();
      setView("package");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const runGenerationPipeline = async () => {
    setBusy("generation");
    try {
      const created = await api.request(
        "generation.create",
        {
          title: "E01 AI 大纲",
          schema_id: "episode_outline_v1",
          intent: { constraints: ["竖屏", "悬疑"] },
          target_type: "episode_outline",
          target_id: "episode-ai-1",
        },
        requestId("gen-create"),
      );
      const runId = String(created.id);
      await api.request("generation.plan", { run_id: runId }, requestId("gen-plan"));
      await api.request(
        "generation.execute",
        {
          run_id: runId,
          output: {
            episode_no: 1,
            title: "夜市开端",
            summary: "雨夜发现发光 U 盘",
            hooks: ["发光 U 盘"],
          },
        },
        requestId("gen-exec"),
      );
      const review = await api.request(
        "generation.review",
        {
          run_id: runId,
          verdict: "pass",
          findings: [{ category: "structure", severity: "info", message: "ok" }],
        },
        requestId("gen-review"),
      );
      const gate = await api.request(
        "generation.open_draft_gate",
        { run_id: runId },
        requestId("gen-gate"),
      );
      setNotice({
        tone: "success",
        text: `生成流水线完成 · review=${String((review as { verdict?: string }).verdict ?? "pass")} · can_promote=${String((gate as { can_promote?: boolean }).can_promote)} · 未自动正式修订`,
      });
      await refreshGenerationState();
      setView("generation");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const createValidatePromoteDraft = async () => {
    setBusy("draft-flow");
    try {
      const created = await api.request(
        "draft.create",
        {
          schema_id: "episode_outline_v1",
          title: "E01 夜市开端",
          target_type: "episode_outline",
          target_id: "episode-1",
          payload: {
            episode_no: 1,
            title: "夜市开端",
            summary: "雨夜发现发光 U 盘",
            hooks: ["发光 U 盘", "失踪消息"],
          },
        },
        requestId("draft-create"),
      );
      const draftId = String(created.id);
      await api.request("draft.validate", { draft_id: draftId }, requestId("draft-validate"));
      const formal = await api.request(
        "draft.promote",
        { draft_id: draftId },
        requestId("draft-promote"),
      );
      setNotice({
        tone: "success",
        text: `正式修订 r${String(formal.revision_no)} 已创建（经草稿+Schema 门禁）`,
      });
      await refreshDraftState();
      setView("drafts");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const forkExploreBranch = async () => {
    setBusy("story-fork");
    try {
      await refreshBranches();
      const primary = branches.find((item) => item.is_primary);
      const listed = await api.request("story.list_branches", {}, requestId("story-branches-2"));
      const currentBranches =
        (listed.branches as Array<Record<string, unknown>> | undefined) ?? [];
      const primaryId = String(
        currentBranches.find((item) => item.is_primary)?.id ?? primary?.id ?? "",
      );
      if (!primaryId) {
        throw new Error("未找到主线分支");
      }
      const forked = await api.request(
        "story.fork_branch",
        { from_branch_id: primaryId, name: `探索线 ${currentBranches.length}` },
        requestId("story-fork"),
      );
      setNotice({
        tone: "success",
        text: `已分叉分支「${String(forked.name)}」· 复制事件 ${String(forked.copied_events ?? 0)}`,
      });
      await refreshBranches();
      setView("story");
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const promoteSelectedBranch = async (branchId: string) => {
    setBusy("story-primary");
    try {
      const branch = await api.request(
        "story.set_primary",
        { branch_id: branchId },
        requestId("story-primary"),
      );
      setNotice({
        tone: "success",
        text: `已将「${String(branch.name)}」设为生产主线`,
      });
      await refreshBranches();
    } catch (error) {
      setNotice({ tone: "warning", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  };

  const importAndSplitStory = async () => {
    setBusy("story-import");
    try {
      const source = (await api.request(
        "story.import_source",
        {
          source_type: "novel",
          title: storyTitle,
          text: storyText,
        },
        requestId("story-import"),
      )) as StorySourceInfo;
      const split = await api.request(
        "story.split_chapters",
        { source_id: source.id },
        requestId("story-split"),
      );
      const firstChunk = ((split.chunks as StoryChunkInfo[] | undefined) ?? [])[0];
      if (firstChunk) {
        const quoteStart = Math.min(
          firstChunk.char_start + 1,
          Math.max(firstChunk.char_end - 1, firstChunk.char_start),
        );
        const quoteEnd = Math.min(firstChunk.char_end, quoteStart + 12);
        await api.request(
          "story.create_event",
          {
            title: "首章关键事件",
            summary: "从首个章节抽取的定位事件",
            order_key: 1,
            origin: "extracted",
            story_source_id: source.id,
            char_start: quoteStart,
            char_end: quoteEnd,
          },
          requestId("story-event"),
        );
      }
      setNotice({
        tone: "success",
        text: `已导入「${source.title}」并完成章节切分`,
      });
      await refreshStoryState();
      setView("story");
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
    { id: "story", label: "故事" },
    { id: "packs", label: "创作包" },
    { id: "package", label: "故事包" },
    { id: "scripts", label: "分集剧本" },
    { id: "characters", label: "角色声音" },
    { id: "world", label: "场景道具" },
    { id: "continuity", label: "状态账本" },
    { id: "drafts", label: "草稿修订" },
    { id: "generation", label: "生成流水线" },
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

      {view === "story" && (
        <section className="console-panel" aria-label="故事工作区">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">STORY GRAPH</p>
              <h3>故事来源与事件</h3>
            </div>
            <span className="panel-number">M2.01</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="project-form">
                <label>
                  来源标题
                  <input
                    aria-label="故事来源标题"
                    value={storyTitle}
                    onChange={(event) => setStoryTitle(event.target.value)}
                  />
                </label>
              </div>
              <label className="story-text-label">
                原文
                <textarea
                  aria-label="故事原文"
                  className="story-text"
                  value={storyText}
                  onChange={(event) => setStoryText(event.target.value)}
                  rows={8}
                />
              </label>
              <div className="action-grid compact">
                <button
                  aria-label="导入并切分"
                  className="action primary"
                  onClick={() => void importAndSplitStory()}
                  disabled={busy !== null}
                >
                  <span className="action-no">S1</span>
                  <span>
                    <strong>导入并切分</strong>
                    <small>import + split + 样例事件</small>
                  </span>
                </button>
                <button
                  aria-label="刷新故事"
                  className="action"
                  onClick={() => {
                    void refreshStoryState();
                    void refreshBranches();
                  }}
                  disabled={busy !== null}
                >
                  <span className="action-no">S2</span>
                  <span>
                    <strong>刷新列表</strong>
                    <small>sources / chunks / events</small>
                  </span>
                </button>
                <button
                  aria-label="分叉探索线"
                  className="action"
                  onClick={() => void forkExploreBranch()}
                  disabled={busy !== null}
                >
                  <span className="action-no">S3</span>
                  <span>
                    <strong>分叉探索线</strong>
                    <small>fork primary branch</small>
                  </span>
                </button>
              </div>
              <div className="overview-columns">
                <div>
                  <h4>分支</h4>
                  {branches.length === 0 ? (
                    <p className="empty-hint">打开后自动创建主线</p>
                  ) : (
                    <ul className="job-list" aria-label="故事分支列表">
                      {branches.map((branch) => (
                        <li key={branch.id}>
                          <code>{branch.is_primary ? "primary" : branch.status}</code>{" "}
                          {branch.name}
                          {!branch.is_primary && branch.status !== "archived" ? (
                            <>
                              {" "}
                              <button
                                className="text-button"
                                aria-label={`设为主线 ${branch.name}`}
                                onClick={() => void promoteSelectedBranch(branch.id)}
                                disabled={busy !== null}
                              >
                                设为主线
                              </button>
                            </>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>来源</h4>
                  {sources.length === 0 ? (
                    <p className="empty-hint">暂无来源</p>
                  ) : (
                    <ul className="job-list" aria-label="故事来源列表">
                      {sources.map((source) => (
                        <li key={source.id}>
                          <code>{source.status}</code> {source.title} · {source.char_count} 字
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>章节</h4>
                  {chunks.length === 0 ? (
                    <p className="empty-hint">尚未切分</p>
                  ) : (
                    <ul className="job-list" aria-label="章节列表">
                      {chunks.map((chunk) => (
                        <li key={chunk.id}>
                          #{chunk.ordinal + 1} {chunk.title ?? "未命名"} · [{chunk.char_start},{" "}
                          {chunk.char_end})
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>事件</h4>
                  {storyEvents.length === 0 ? (
                    <p className="empty-hint">暂无事件</p>
                  ) : (
                    <ul className="job-list" aria-label="事件列表">
                      {storyEvents.map((event) => (
                        <li key={event.event_id}>
                          <code>{event.origin}</code> {event.title} · {event.summary}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {view === "packs" && (
        <section className="console-panel" aria-label="创作包">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CREATIVE PACK</p>
              <h3>创作包锁定</h3>
            </div>
            <span className="panel-number">M2.03</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="注册并锁定默认组合"
                  className="action primary"
                  onClick={() => void setupDefaultPacks()}
                  disabled={busy !== null}
                >
                  <span className="action-no">C1</span>
                  <span>
                    <strong>注册并锁定默认组合</strong>
                    <small>visual + narrative + technique</small>
                  </span>
                </button>
                <button
                  aria-label="刷新创作包"
                  className="action"
                  onClick={() => void refreshPackState()}
                  disabled={busy !== null}
                >
                  <span className="action-no">C2</span>
                  <span>
                    <strong>刷新状态</strong>
                    <small>current lock + compositions</small>
                  </span>
                </button>
              </div>
              <p className="project-meta">
                当前锁定：{" "}
                <strong>{packLock ? packLock.slice(0, 12) + "…" : "未锁定"}</strong>
              </p>
              {packCompositions.length === 0 ? (
                <p className="empty-hint">暂无组合修订</p>
              ) : (
                <ul className="job-list" aria-label="创作包组合列表">
                  {packCompositions.map((item) => (
                    <li key={item.composition_revision_id}>
                      <code>{item.status}</code> {item.name}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {view === "continuity" && (
        <section className="console-panel" aria-label="状态账本">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CONTINUITY LEDGER</p>
              <h3>时间化状态与冲突检查</h3>
            </div>
            <span className="panel-number">M2.12</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="写入样例状态账本"
                  className="action primary"
                  onClick={() => void buildContinuityLedger()}
                  disabled={busy !== null}
                >
                  <span className="action-no">T1</span>
                  <span>
                    <strong>写入样例状态账本</strong>
                    <small>outfit / injury / owner → snapshot</small>
                  </span>
                </button>
              </div>
              {!continuitySummary ? (
                <p className="empty-hint">尚未写入连续性状态。</p>
              ) : (
                <div className="overview-columns">
                  <div>
                    <h4>状态键</h4>
                    <ul className="job-list" aria-label="状态键列表">
                      {continuitySummary.sampleKeys.map((key) => (
                        <li key={key}>
                          <code>key</code> {key}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4>统计</h4>
                    <ul className="job-list" aria-label="账本统计">
                      <li>活动状态 {continuitySummary.stateCount}</li>
                      <li>阻断冲突 {continuitySummary.blockerCount}</li>
                      <li>警告 {continuitySummary.warningCount}</li>
                      <li>快照 {continuitySummary.snapshotCount}</li>
                    </ul>
                  </div>
                </div>
              )}
              <p className="empty-hint">
                同优先级区间重叠为 blocker；快照前必须无 blocker。有效状态按最高
                priority 解析。
              </p>
            </>
          )}
        </section>
      )}

      {view === "world" && (
        <section className="console-panel" aria-label="场景道具">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">LOCATIONS · PROPS · SPACE</p>
              <h3>场景包 · 空间关系 · 道具</h3>
            </div>
            <span className="panel-number">M2.11</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="创建样例场景与道具"
                  className="action primary"
                  onClick={() => void buildWorldSpace()}
                  disabled={busy !== null}
                >
                  <span className="action-no">W1</span>
                  <span>
                    <strong>创建并确认核心场景</strong>
                    <small>location pack + spatial + prop anchor</small>
                  </span>
                </button>
              </div>
              {!worldSummary ? (
                <p className="empty-hint">尚未创建场景世界。</p>
              ) : (
                <div className="overview-columns">
                  <div>
                    <h4>场景</h4>
                    <ul className="job-list" aria-label="场景列表">
                      {worldSummary.locationNames.map((name) => (
                        <li key={name}>
                          <code>location</code> {name}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4>统计</h4>
                    <ul className="job-list" aria-label="场景统计">
                      <li>场景 {worldSummary.locationCount}</li>
                      <li>道具 {worldSummary.propCount}</li>
                      <li>空间连接 {worldSummary.linkCount}</li>
                      <li>
                        核心门禁{" "}
                        <code>
                          {worldSummary.coreGateReady ? "ready" : "blocked"}
                        </code>
                      </li>
                    </ul>
                  </div>
                </div>
              )}
              <p className="empty-hint">
                核心场景（is_core）需 confirm 场景包后才可批量生成相关镜头。
              </p>
            </>
          )}
        </section>
      )}

      {view === "characters" && (
        <section className="console-panel" aria-label="角色声音">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CHARACTERS · VOICE · LOOK</p>
              <h3>角色 · 声音 · 定妆身份包</h3>
            </div>
            <span className="panel-number">M2.09–10</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="创建样例角色与声音"
                  className="action primary"
                  onClick={() => void buildCharacterCast()}
                  disabled={busy !== null}
                >
                  <span className="action-no">R1</span>
                  <span>
                    <strong>创建角色+定妆确认</strong>
                    <small>cast → looks → select → confirm</small>
                  </span>
                </button>
              </div>
              {!characterSummary ? (
                <p className="empty-hint">尚未创建角色档案。</p>
              ) : (
                <div className="overview-columns">
                  <div>
                    <h4>角色</h4>
                    <ul className="job-list" aria-label="角色列表">
                      {characterSummary.names.map((name) => (
                        <li key={name}>
                          <code>approved</code> {name}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4>统计</h4>
                    <ul className="job-list" aria-label="角色统计">
                      <li>角色 {characterSummary.characterCount}</li>
                      <li>关系 {characterSummary.relationshipCount}</li>
                      <li>声音档案 {characterSummary.voiceCount}</li>
                      <li>定妆候选 {characterSummary.lookCount}</li>
                      <li>
                        生产门禁{" "}
                        <code>
                          {characterSummary.gateReady ? "ready" : "blocked"}
                        </code>
                      </li>
                    </ul>
                  </div>
                </div>
              )}
              <p className="empty-hint">
                默认 mock 定妆图落盘 assets/images/looks；WORKFLOW_ENABLE_GROK_LOOKS=1
                时走 Grok image_gen。主角/反派未 confirm 身份包不可批量生成。
              </p>
            </>
          )}
        </section>
      )}

      {view === "scripts" && (
        <section className="console-panel" aria-label="分集剧本">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">EPISODE SCRIPT</p>
              <h3>分集 · 场景 · 台词 · 钩子</h3>
            </div>
            <span className="panel-number">M2.08</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="创建并批准样例剧本"
                  className="action primary"
                  onClick={() => void buildEpisodeScript()}
                  disabled={busy !== null}
                >
                  <span className="action-no">E1</span>
                  <span>
                    <strong>创建并批准样例剧本</strong>
                    <small>scene + dialogue + hook → approve</small>
                  </span>
                </button>
              </div>
              {!scriptSummary ? (
                <p className="empty-hint">尚未创建分集剧本。</p>
              ) : (
                <div className="overview-columns">
                  <div>
                    <h4>剧本</h4>
                    <ul className="job-list" aria-label="剧本摘要">
                      <li>
                        <code>{scriptSummary.status}</code> {scriptSummary.title}
                      </li>
                      <li>
                        episode · <code>{scriptSummary.episodeStatus}</code>
                      </li>
                      <li>id · {scriptSummary.scriptId.slice(0, 8)}…</li>
                    </ul>
                  </div>
                  <div>
                    <h4>结构</h4>
                    <ul className="job-list" aria-label="剧本结构统计">
                      <li>场景 {scriptSummary.sceneCount}</li>
                      <li>台词/旁白 {scriptSummary.lineCount}</li>
                      <li>钩子 {scriptSummary.hookCount}</li>
                    </ul>
                  </div>
                </div>
              )}
              <p className="empty-hint">
                台词使用稳定 line_id 与不可变修订；剧本不内嵌媒体提示词。
              </p>
            </>
          )}
        </section>
      )}

      {view === "package" && (
        <section className="console-panel" aria-label="故事包">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">STORY PACKAGE · SEASON</p>
              <h3>故事包与季时间线</h3>
            </div>
            <span className="panel-number">M2.07</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="构建并批准故事包"
                  className="action primary"
                  onClick={() => void buildSeasonPackage()}
                  disabled={busy !== null}
                >
                  <span className="action-no">K1</span>
                  <span>
                    <strong>构建并批准故事包</strong>
                    <small>rules + timeline + episodes → approve</small>
                  </span>
                </button>
                <button
                  aria-label="刷新故事包"
                  className="action"
                  onClick={() => void refreshSeasonPackageState()}
                  disabled={busy !== null}
                >
                  <span className="action-no">K2</span>
                  <span>
                    <strong>刷新概览</strong>
                    <small>season.overview</small>
                  </span>
                </button>
              </div>
              <div className="overview-columns">
                <div>
                  <h4>世界规则</h4>
                  {worldRules.length === 0 ? (
                    <p className="empty-hint">暂无规则</p>
                  ) : (
                    <ul className="job-list" aria-label="世界规则列表">
                      {worldRules.map((rule) => (
                        <li key={rule.id}>
                          <code>{rule.force_level}</code> {rule.category} · {rule.rule_text}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>时间线</h4>
                  {timelineBeats.length === 0 ? (
                    <p className="empty-hint">暂无 beats</p>
                  ) : (
                    <ul className="job-list" aria-label="季时间线列表">
                      {timelineBeats.map((beat) => (
                        <li key={beat.id}>
                          <code>#{beat.beat_no}</code> {beat.title} · {beat.summary}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>分集</h4>
                  {episodes.length === 0 ? (
                    <p className="empty-hint">暂无分集</p>
                  ) : (
                    <ul className="job-list" aria-label="分集列表">
                      {episodes.map((ep) => (
                        <li key={ep.id}>
                          <code>{ep.status}</code> E{ep.episode_no} {ep.title}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              <div>
                <h4>故事包修订</h4>
                {packageRevisions.length === 0 ? (
                  <p className="empty-hint">暂无故事包</p>
                ) : (
                  <ul className="job-list" aria-label="故事包修订列表">
                    {packageRevisions.map((rev) => (
                      <li key={rev.id}>
                        <code>{rev.status}</code> media_prompts=
                        {String(rev.contains_media_prompts)} · {rev.id.slice(0, 8)}…
                      </li>
                    ))}
                  </ul>
                )}
                <p className="empty-hint">
                  故事包仅含叙事结构引用，永不内嵌媒体提示词或镜头参数。
                </p>
              </div>
            </>
          )}
        </section>
      )}

      {view === "drafts" && (
        <section className="console-panel" aria-label="草稿修订">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">DRAFT GATE</p>
              <h3>草稿与正式修订</h3>
            </div>
            <span className="panel-number">M2.05</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="创建并晋级样例草稿"
                  className="action primary"
                  onClick={() => void createValidatePromoteDraft()}
                  disabled={busy !== null}
                >
                  <span className="action-no">D1</span>
                  <span>
                    <strong>创建并晋级样例草稿</strong>
                    <small>create → validate → promote</small>
                  </span>
                </button>
                <button
                  aria-label="刷新草稿"
                  className="action"
                  onClick={() => void refreshDraftState()}
                  disabled={busy !== null}
                >
                  <span className="action-no">D2</span>
                  <span>
                    <strong>刷新列表</strong>
                    <small>drafts + formal revisions</small>
                  </span>
                </button>
              </div>
              <div className="overview-columns">
                <div>
                  <h4>草稿</h4>
                  {drafts.length === 0 ? (
                    <p className="empty-hint">暂无草稿</p>
                  ) : (
                    <ul className="job-list" aria-label="草稿列表">
                      {drafts.map((draft) => (
                        <li key={draft.id}>
                          <code>{draft.status}</code> {draft.title} · {draft.schema_id}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4>正式修订</h4>
                  {revisions.length === 0 ? (
                    <p className="empty-hint">暂无正式修订</p>
                  ) : (
                    <ul className="job-list" aria-label="正式修订列表">
                      {revisions.map((rev) => (
                        <li key={rev.id}>
                          <code>
                            r{rev.revision_no}/{rev.status}
                          </code>{" "}
                          {rev.title}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {view === "generation" && (
        <section className="console-panel" aria-label="生成流水线">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">PLAN · EXECUTE · REVIEW</p>
              <h3>AI 生成流水线</h3>
            </div>
            <span className="panel-number">M2.06</span>
          </div>
          {!project ? (
            <p className="empty-hint">请先打开项目。</p>
          ) : (
            <>
              <div className="action-grid compact">
                <button
                  aria-label="跑通生成流水线"
                  className="action primary"
                  onClick={() => void runGenerationPipeline()}
                  disabled={busy !== null}
                >
                  <span className="action-no">G1</span>
                  <span>
                    <strong>跑通生成流水线</strong>
                    <small>plan → execute → review → gate</small>
                  </span>
                </button>
                <button
                  aria-label="刷新生成运行"
                  className="action"
                  onClick={() => void refreshGenerationState()}
                  disabled={busy !== null}
                >
                  <span className="action-no">G2</span>
                  <span>
                    <strong>刷新运行列表</strong>
                    <small>generation.list</small>
                  </span>
                </button>
              </div>
              {genRuns.length === 0 ? (
                <p className="empty-hint">暂无生成运行</p>
              ) : (
                <ul className="job-list" aria-label="生成运行列表">
                  {genRuns.map((item) => (
                    <li key={item.id}>
                      <code>{item.status}</code> {item.title} · iter {item.iteration}
                    </li>
                  ))}
                </ul>
              )}
              <p className="empty-hint">
                审阅通过只打开草稿门禁；正式修订仍需 draft.promote。
              </p>
            </>
          )}
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
