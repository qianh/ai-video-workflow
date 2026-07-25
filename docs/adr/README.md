# 架构决策记录（ADR）

本目录记录 V1 关键技术决策。格式统一为：背景 → 决策 → 证据 → 替代方案 → 影响 → 回退 → 后续验证。

| ID | 标题 | 状态 |
|----|------|------|
| [ADR-001](./ADR-001-stdio-ndjson-rpc.md) | Rust 与 Python stdio NDJSON RPC | 已验证 |
| [ADR-002](./ADR-002-python-sidecar-packaging.md) | Python Sidecar 打包（PyInstaller） | 已验证（签名待复验） |
| [ADR-003](./ADR-003-sqlite-single-writer.md) | 项目 SQLite 唯一写入者 | 已决策，M1 实现 |
| [ADR-004](./ADR-004-plugin-isolation.md) | 插件隔离级别 | 已决策 |
| [ADR-005](./ADR-005-global-asset-import.md) | 全局资产导入策略 | 已决策 |
| [ADR-006](./ADR-006-grok-media-adapter.md) | Grok 媒体能力适配 | 已验证（视频降级） |
| [ADR-007](./ADR-007-local-model-components.md) | 本地模型组件管理 | 已决策（组件未装） |
| [ADR-008](./ADR-008-ffmpeg-timeline-compiler.md) | FFmpeg 时间线编译器 | 已验证（软字幕默认） |

Spike 原始证据见 [`docs/spikes/`](../spikes/)。
