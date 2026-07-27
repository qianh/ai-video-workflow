# AI Video Workflow

面向 AI 连续漫剧生产的 macOS 桌面工作站。当前代码完成 M0-01～M0-03 的最小纵向链路：React → Tauri/Rust → Python Sidecar。

## 当前能力

- Tauri 2 + React + TypeScript 桌面骨架。
- 版本化 stdio NDJSON 请求、响应和事件。
- Rust Sidecar 监督器、超时、取消和崩溃检测。
- Sidecar 崩溃后的安全恢复：失败请求不重放，下一请求自动启动新进程。
- Python 源码开发模式和 PyInstaller arm64 单文件构建。
- M0 状态页，可执行 Ping、进度、取消和受控崩溃恢复验证。
- **M1 底座**：`global.db` / `project.db` 迁移、项目创建/打开/关闭/最近列表、持久任务队列（租约/重试/暂停/取消）。
- 环境合并：项目 `.env.local` > 全局 `env` 文件 > 进程环境；`env.summary` 不回显密钥。
- 迁移前自动轻量快照（`project.db` → `snapshots/`）；支持 `snapshot.create` / `list`。
- 桌面 UI 含主导航壳：项目总览 / 项目 / 故事 / 任务中心 / 链路诊断。
- **M2 起步**：故事来源导入、章节切分、可定位事件与事件边（主线分支）。
- **Creative Pack**：注册/发布修订、组合解析、固定套件评测、项目锁定（不自动覆盖旧锁定语义）。
- **草稿门禁**：`draft → validate → promote`，无绕过 Schema 的正式修订入口。

项目 RPC：`project.create` / `open` / `close` / `current` / `list_recent` / `overview`。  
故事 RPC：`story.import_source` / `list_sources` / `split_chapters` / `list_chunks` / `create_event` / `list_events` / `create_edge` / `list_edges` / `list_branches` / `create_branch` / `fork_branch` / `set_primary` / `archive_branch`。  
创作包 RPC：`pack.register` / `publish_revision` / `list` / `compose` / `evaluate` / `lock` / `current_lock` / `list_compositions`。  
草稿 RPC：`draft.create` / `update` / `validate` / `promote` / `get` / `list` / `list_schemas`；`revision.list`。  
任务 RPC：`job.enqueue` / `get` / `list` / `claim` / `complete` / `fail` / `cancel` / `pause` / `resume` / `reclaim_expired`。  
环境 RPC：`env.summary`、`env.resolve`（必须传 `allow_keys`）。  
快照 RPC：`snapshot.create`、`snapshot.list`。  
日志/诊断 RPC：`log.write`、`log.tail`、`diagnostics.create_pack`（脱敏 JSONL，打包不含 `.env.local`/媒体原件）。  
路径 RPC：`fs.resolve`、`fs.hash`（仅项目相对路径，拒绝越界）。  
全局库默认路径：`~/.ai-video-workflow/global.db`（可用 `WORKFLOW_GLOBAL_DB` 覆盖）。  
全局 env 文件：与 `global.db` 同目录的 `env`。

## 环境要求

- macOS Apple Silicon。
- Node.js 22+ 与 npm。
- Rust 1.88+，安装 `aarch64-apple-darwin` target。
- Python 3.11+ 与 `uv`。

仓库通过 [`.cargo/config.toml`](./.cargo/config.toml) 将 Rust 默认目标锁定为 `aarch64-apple-darwin`。

## 初始化

```bash
rustup target add aarch64-apple-darwin

cd apps/desktop
npm install

cd ../../services/sidecar
uv sync --extra dev
```

## 本地开发

```bash
cd apps/desktop
npm run tauri dev
```

开发构建直接使用 `python3 -m workflow_sidecar`。可通过以下环境变量覆盖：

- `WORKFLOW_SIDECAR_PYTHON`：Python 可执行文件。
- `WORKFLOW_SIDECAR_BINARY`：直接启动指定的已打包 Sidecar。

受控计数和崩溃方法只在开发/测试启动配置中启用。

## 测试

```bash
# Python：单元、stdio 集成、1000 条消息与覆盖率
cd services/sidecar
uv run --extra dev pytest

# React：组件、Tauri bridge 与覆盖率
cd ../../apps/desktop
npm test
npm run build

# Rust：协议、1000 条消息、取消与崩溃恢复
cd src-tauri
cargo test
```

Python 与 React 覆盖率门禁均为 80%。

## 打包 Sidecar

```bash
./scripts/build-sidecar.sh aarch64-apple-darwin
```

输出：

```text
apps/desktop/src-tauri/binaries/workflow-sidecar-aarch64-apple-darwin
```

使用打包产物复跑 Rust 集成测试：

```bash
cd apps/desktop/src-tauri
WORKFLOW_SIDECAR_TEST_BINARY="$PWD/binaries/workflow-sidecar-aarch64-apple-darwin" cargo test
```

## 构建桌面应用

```bash
cd apps/desktop
npm run tauri build -- --debug --no-bundle
```

正式 DMG 仍需要开发者签名、公证和发布配置；这些不属于当前本地 M0 验证范围。

## 协议与恢复语义

- stdout 只能输出一行一个 JSON envelope，日志只能写 stderr。
- 单条消息上限为 1 MiB，大内容后续使用文件引用。
- 未知版本、方法、无效参数和重复请求 ID 返回稳定错误码。
- 取消按请求 ID 执行，目标任务返回 `CANCELLED`。
- Sidecar 退出时，所有等待中的请求明确失败。
- 监督器不会自动重放失败请求；下一请求会启动新进程。

架构决策见 [docs/adr/](./docs/adr/README.md)（ADR-001～008）。

## 供应商 Spike

### M0-04 Codex 结构化输出

```bash
python3 scripts/m0_04_codex_structured.py --runs 10
```

要求本机已登录 `codex` CLI。验收：连续 10 次 `--output-schema` 结果通过校验。  
说明见 [docs/spikes/M0-04-codex-structured.md](./docs/spikes/M0-04-codex-structured.md)。

### M0-05 Grok 文本与媒体

```bash
python3 scripts/m0_05_grok_media.py
```

要求本机已登录 `grok` CLI。对文本 / 生图 / 改图 / 图生视频分别给出 `ready|degraded|unavailable`，媒体必须落盘并可解码。  
说明见 [docs/spikes/M0-05-grok-media.md](./docs/spikes/M0-05-grok-media.md)。

### M0-08 FFmpeg 竖屏母版

```bash
python3 scripts/m0_08_ffmpeg_master.py
```

合成测试资产并输出 1080×1920、90s 内、含字幕混音的 H.264/AAC 样片。  
说明见 [docs/spikes/M0-08-ffmpeg-master.md](./docs/spikes/M0-08-ffmpeg-master.md)。

### M0-09 music-downloader

```bash
python3 scripts/m0_09_music_downloader.py
```

强制写入任务暂存目录，并断言默认 `~/Music/Downloads` 无新增文件。  
说明见 [docs/spikes/M0-09-music-downloader.md](./docs/spikes/M0-09-music-downloader.md)。
