# CLI 与能力适配器协议

> 文档状态：初稿  
> 对应文档：[PRD](./PRD.md) · [领域模型](./DOMAIN_MODEL.md) · [技术架构](./ARCHITECTURE.md)  
> 协议代号：AWAP（AI Workflow Adapter Protocol）  
> 协议版本：1.0-draft  
> 最后更新：2026-07-21

## 1. 目的

AWAP 用统一契约连接工作站与 Codex、Grok、FFmpeg、CosyVoice3、MuseTalk、`music-downloader` 以及未来工具。

协议解决：

1. 工作流只声明所需能力，不写死供应商。
2. 每个工具可独立探测、估算、执行、取消和诊断。
3. 输入输出可验证，媒体文件能够可靠落盘。
4. 任务进度、错误、重试和降级具有一致语义。
5. 免费订阅、本地模型和付费 API 在调度前可被硬性区分。
6. 第三方适配器无法通过任意 Shell 或未声明环境变量扩大权限。

## 2. 协议边界

AWAP 定义 sidecar 与适配器进程之间的协议，不等同于 React 与 sidecar 的应用 IPC。

```mermaid
flowchart LR
    UI[React] --> RUST[Rust Core]
    RUST --> PY[Python Sidecar]
    PY -->|AWAP NDJSON| A[Adapter Process]
    A --> CLI[CLI / Model Runtime]
    CLI --> FILES[Task Staging Files]
```

内置 Python 适配器可以使用同一接口在进程内运行；第三方适配器必须使用独立进程和 AWAP NDJSON。业务层不得感知这一区别。

## 3. 设计原则

### 3.1 能力优先

工作流请求 `image.generate`，路由器再选择 Grok 或未来实现。供应商品牌只能出现在适配器配置和生成清单中。

### 3.2 Schema 优先

每项能力拥有版本化 JSON Schema。适配器不能用自由文本代替协议字段；无法结构化的原始输出必须由适配器解析后再返回。

### 3.3 文件交付优先

媒体结果以文件描述符返回，不通过 stdout 传输二进制或 Base64。

### 3.4 失败显式化

“进程退出码为 0”不代表能力成功。成功必须同时满足协议响应、输出文件存在和能力专属验证。

### 3.5 最小权限

适配器只收到本次任务需要的环境变量、输入文件和输出暂存目录。项目根目录不默认暴露给第三方插件。

### 3.6 费用先决

每个执行计划必须在启动前确定 `cost_class`。未知费用不能按免费处理。

## 4. 协议传输

### 4.1 NDJSON

stdin/stdout 使用 UTF-8 NDJSON，一行一个完整 JSON 对象：

```json
{"awap":"1.0","type":"request","id":"01J...","method":"probe","params":{}}
{"awap":"1.0","type":"response","id":"01J...","result":{"status":"ready"}}
```

- stdout 只允许协议消息。
- stderr 用于日志，可包含多行文本。
- 单条消息默认上限 1 MiB。
- 媒体、长提示词和大 JSON 使用文件引用。
- 协议行必须以 `\n` 结束。

### 4.2 会话

sidecar 启动适配器后先执行握手：

```json
{
  "awap": "1.0",
  "type": "request",
  "id": "01JHELLO",
  "method": "hello",
  "params": {
    "host": {
      "app_version": "0.1.0",
      "protocol_min": "1.0",
      "protocol_max": "1.0",
      "platform": "aarch64-apple-darwin"
    },
    "session": {
      "session_id": "01JSESSION",
      "locale": "zh-CN",
      "timezone": "Asia/Shanghai"
    }
  }
}
```

响应必须包含适配器 ID、版本、协议范围和能力摘要。协议范围无交集时立即退出，不允许带病运行。

### 4.3 消息类型

| 类型 | 方向 | 用途 |
|---|---|---|
| `request` | host → adapter | 调用方法 |
| `response` | adapter → host | 方法成功 |
| `error` | adapter → host | 方法失败 |
| `event` | adapter → host | 进度、日志摘要、产物发现 |
| `cancel` | host → adapter | 请求取消执行 |

### 4.4 并发

- 一个适配器进程可以声明 `max_inflight`。
- V1 默认每个适配器进程同时只执行一个 `execute`。
- `describe`、`health` 等只读方法是否可并发由清单声明。
- 消息通过请求 ID 关联，不依赖返回顺序。

## 5. 适配器清单

### 5.1 文件位置

每个适配器包根目录必须包含 `adapter.json`。清单本身不能包含秘密值。

### 5.2 示例

```json
{
  "$schema": "https://awap.local/schemas/adapter-manifest-1.0.json",
  "manifest_version": "1.0",
  "id": "builtin.grok-cli",
  "name": "Grok CLI",
  "version": "0.1.0",
  "publisher": "builtin",
  "runtime": {
    "kind": "executable",
    "entrypoint": "grok-adapter",
    "protocol": "stdio-ndjson",
    "min_host_version": "0.1.0"
  },
  "discovery": {
    "executables": ["grok"],
    "version_args": ["--version"]
  },
  "capabilities": [
    {"code": "text.structured_generate", "version": "1.0"},
    {"code": "image.generate", "version": "1.0", "probe_required": true},
    {"code": "video.image_to_video", "version": "1.0", "probe_required": true}
  ],
  "permissions": {
    "network": "inherited-tool-only",
    "read_inputs": true,
    "write_staging": true,
    "project_root": false,
    "environment": ["GROK_*", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"]
  },
  "limits": {
    "max_inflight": 1,
    "default_timeout_seconds": 900,
    "max_stderr_bytes": 10485760
  },
  "cost": {
    "class": "subscription",
    "requires_budget_authorization": false
  }
}
```

### 5.3 标识规则

- `id` 使用反向域名或 `builtin.<name>`，发布后不可更改。
- `version` 使用语义化版本。
- 能力版本与适配器版本分别管理。
- 同一设备允许多个适配器版本共存，但一项安装记录只指向一个版本。

### 5.4 Runtime 类型

| `kind` | 说明 |
|---|---|
| `builtin-python` | 工作站内置、可进程内执行的受信实现 |
| `executable` | 实现 AWAP 的独立可执行程序 |
| `python-process` | 独立 Python 环境启动的 AWAP 进程 |
| `wrapper` | 封装不支持 AWAP 的现有 CLI |

第三方插件不允许使用 `builtin-python`。

### 5.5 权限声明

权限至少覆盖：

- 网络访问级别。
- 输入文件读取。
- 暂存目录写入。
- 项目根目录访问。
- 浏览器 Cookie 访问。
- 环境变量白名单。
- 可执行子进程白名单。

清单请求不等于自动授权；安装时用户看到实际权限摘要，主机可以拒绝或缩小范围。

## 6. 能力目录

### 6.1 通用命名

能力代码使用 `<domain>.<action>`：

| 能力 | 用途 |
|---|---|
| `text.structured_generate` | 根据 Schema 生成结构化文本 |
| `text.review` | 审阅、诊断与建议 |
| `image.generate` | 文生图 |
| `image.edit` | 参考图编辑 |
| `video.image_to_video` | 图生视频 |
| `speech.tts` | 文本转语音 |
| `speech.voice_clone` | 授权样本声音克隆 |
| `video.lip_sync` | 音频驱动口型 |
| `media.probe` | 媒体探测 |
| `media.proxy` | 创建代理和缩略图 |
| `media.render` | 时间线或片段渲染 |
| `music.download` | 下载音乐素材 |
| `sfx.download` | 下载音效素材 |
| `quality.media_check` | 确定性媒体质检 |

### 6.2 能力支持级别

适配器对每项能力报告：

| 状态 | 含义 |
|---|---|
| `declared` | 清单声明，尚未探测 |
| `ready` | 已探测且可执行 |
| `degraded` | 可执行但缺少可选功能或性能不达默认值 |
| `unavailable` | 当前不可用 |
| `blocked_auth` | 未登录或凭据失效 |
| `blocked_quota` | 订阅额度或频率受限 |
| `incompatible` | 工具版本或设备不兼容 |

### 6.3 特性标志

能力可以报告细粒度特性，例如：

```json
{
  "code": "image.generate",
  "version": "1.0",
  "status": "ready",
  "features": {
    "reference_images": true,
    "image_edit": false,
    "seed": false,
    "aspect_ratios": ["9:16", "1:1"],
    "max_reference_images": 4,
    "output_formats": ["png", "webp"]
  }
}
```

工作流必须依据特性做降级，不能只判断能力代码存在。

## 7. 标准方法

### 7.1 `hello`

协商协议和会话。不得访问外部工具或执行昂贵探测。

### 7.2 `describe`

返回清单解析结果、配置 Schema、能力 Schema 和限制。结果可按适配器版本缓存。

### 7.3 `discover`

查找本机可执行文件、模型目录和运行环境。

输入允许主机提供候选路径，但适配器必须规范化并验证。输出绝对路径只保存到设备级 `AdapterInstallation`。

### 7.4 `probe`

执行实际可用性检测。探测分级：

- `quick`：安装、版本、登录和本地依赖。
- `capability`：执行最小真实能力样例。
- `diagnostic`：针对失败进行更深检查。

探测不得产生付费调用，除非请求中存在预算授权。订阅额度调用要明确标记是否消耗额度。

### 7.5 `estimate`

返回预计耗时、输出大小、资源、费用类别和是否需要确认：

```json
{
  "duration_seconds": {"min": 20, "typical": 60, "max": 300},
  "output_bytes": {"typical": 12000000},
  "resources": {"memory_mb": 2048, "exclusive": ["local-ml"]},
  "cost": {"class": "local", "amount": 0, "currency": "CNY"},
  "confidence": "low"
}
```

### 7.6 `prepare`

验证输入、解析引用、选择工具版本并返回不可变执行计划。`prepare` 不执行生成。

执行计划必须包含：

- 规范化输入哈希。
- 适配器和工具版本。
- 模型或组件版本。
- 需要的文件与环境变量名。
- 输出文件约定。
- 超时、资源和费用分类。
- 可脱敏的命令预览。

### 7.7 `execute`

执行已准备计划。请求必须带计划哈希，防止准备后配置被静默修改。

### 7.8 `cancel`

对指定执行 ID 请求软取消。响应表示“已收到”，不表示进程已经结束。最终结果通过 `execution.finished` 事件或 `execute` 终态响应返回。

### 7.9 `validate`

对现有输出执行能力专属校验。主机还会执行独立的基础文件校验，适配器不能绕过。

### 7.10 `diagnose`

返回脱敏诊断、修复建议和用户可执行动作。不得返回 Cookie、Token 或完整环境值。

### 7.11 `shutdown`

停止接收新任务，完成或取消当前任务后退出。超过宽限时间由主机终止。

## 8. 文件引用协议

### 8.1 FileRef

```json
{
  "file_id": "input-character-front",
  "path": "inputs/character-front.png",
  "media_type": "image/png",
  "sha256": "...",
  "size_bytes": 1488221,
  "role": "character_reference",
  "read_only": true
}
```

- 路径相对于任务工作目录。
- 输入目录只读。
- 输出只能写入 `outputs/`。
- 临时文件只能写入 `work/`。
- 适配器不能返回工作目录之外的输出路径。

### 8.2 OutputArtifact

```json
{
  "artifact_id": "candidate-1",
  "path": "outputs/candidate-1.png",
  "media_type": "image/png",
  "role": "candidate_image",
  "metadata": {
    "width": 1080,
    "height": 1920
  }
}
```

主机在登记资产前重新计算哈希、大小和媒体属性，不信任适配器填报值。

### 8.3 原子性

适配器必须先写 `work/`，完成后移动到 `outputs/`。`execute` 成功时，所有声明输出必须已经关闭文件句柄并可读取。

## 9. 结构化文本能力

### 9.1 请求

```json
{
  "capability": "text.structured_generate",
  "capability_version": "1.0",
  "input": {
    "instructions": "生成第 1 集的结构化剧本草稿",
    "context_files": [
      {"path": "inputs/story-package.json", "sha256": "..."}
    ],
    "output_schema": {
      "$ref": "schemas/episode-script-1.0.json"
    },
    "locale": "zh-CN"
  },
  "options": {
    "model": null,
    "reasoning_effort": "medium",
    "timeout_seconds": 600
  }
}
```

### 9.2 响应

结果写入 `outputs/result.json` 并返回文件引用。适配器必须完成 JSON 解析和 Schema 校验；原始模型文本可作为诊断附件，但不能充当成功结果。

### 9.3 纠正循环

结构无效时允许适配器内部进行有限纠正，但每次模型调用都必须进入生成清单。超过限制返回 `OUTPUT_SCHEMA_INVALID`，由任务策略决定是否重试。

## 10. 图片能力

### 10.1 `image.generate`

标准输入：

- 正向提示词。
- 负向提示词。
- 画幅与目标尺寸。
- 角色/场景参考图。
- 风格圣经摘要。
- 候选数量。
- 随机种子（可选）。

标准输出：一个或多个 `candidate_image` 文件。

### 10.2 `image.edit`

除生成输入外，必须指定源图和编辑蒙版（若工具支持）。不支持蒙版时能力特性必须报告 `mask_edit=false`。

### 10.3 成功验证

- 文件可解码。
- 尺寸与画幅在允许误差内。
- 不为空白或纯色文件。
- 无工具错误占位图。
- 候选数量符合响应。

角色一致性属于后续质检，不属于适配器基础成功条件。

## 11. 视频能力

### 11.1 `video.image_to_video`

输入包括源图、期望动作、镜头运动、时长、画幅、帧率偏好和是否保持首尾帧。

输出为视频候选，不默认包含最终配音。

### 11.2 成功验证

- FFprobe 可读取。
- 至少包含视频轨道。
- 时长、画幅和帧率位于允许范围。
- 可解码首帧、中间帧和末帧。
- 不包含工具报错画面或零时长轨道。

### 11.3 降级

若能力不可用，路由器可以改为 `static_motion` 生产计划；适配器本身不得伪造成功的视频结果。

## 12. TTS 与声音克隆

### 12.1 `speech.tts`

输入包括文本、语言、声音档案、语速、情绪、音量、发音规则和期望格式。

输出至少包含：

- 音频文件。
- 实际时长。
- 采样率和声道。
- 可选句级/词级时间信息。

### 12.2 `speech.voice_clone`

除 TTS 输入外，必须提供：

- 授权参考音频。
- `authorization_record_id`。
- 参考文本（已知时）。

缺少授权记录返回 `VOICE_AUTHORIZATION_REQUIRED`，不可降级为无约束克隆。

### 12.3 CosyVoice3 映射

- `speech.tts` 使用固定或已有声音档案。
- `speech.voice_clone` 使用零样本参考音频。
- 情绪、语速和发音能力根据实际组件版本探测。
- 模型进程可以复用，但每次任务输入输出必须隔离。

## 13. 口型同步

### 13.1 `video.lip_sync`

输入：源图片或视频、配音音频、面部区域设置、输出帧率和粘贴回原图策略。

输出：带音频或独立无声口型视频，以及实际处理区域和警告。

### 13.2 MuseTalk 映射

- MLX 版是 Apple Silicon 首选实现。
- 输入在执行前标准化。
- 只用于 `lip_sync_level=precise`。
- 失败可由工作流降级到 `simplified`，不能由适配器返回低质量占位结果。

## 14. FFmpeg 媒体能力

### 14.1 禁止任意命令模板

UI 和项目数据不能直接提供 FFmpeg 参数字符串。适配器接收结构化操作，由可信构建器生成参数数组。

### 14.2 操作类型

- `probe`
- `thumbnail`
- `proxy`
- `normalize_audio`
- `waveform`
- `ken_burns`
- `concatenate`
- `mix_audio`
- `render_captions`
- `render_timeline`
- `mux`
- `validate_delivery`

### 14.3 生成清单

保存 FFmpeg/FFprobe 版本、规范化操作计划和脱敏命令预览。完整命令可以进入本地技术日志，但不得包含秘密或未规范化外部路径。

## 15. 音乐与音效下载

### 15.1 `music.download` 与 `sfx.download`

输入支持：

- 搜索词或歌名。
- 直接 URL。
- 播放列表 URL。
- 输出格式偏好。
- Cookie 使用是否获用户授权。

### 15.2 music-downloader 映射

V1 封装 `john-skills/skills/music-downloader/scripts/download.sh`：

- 搜索词映射到脚本的名称输入。
- URL 直接传给脚本。
- 输出目录固定到任务暂存区。
- 不允许脚本默认写入 `~/Music/Downloads`。
- Cookie 重试必须在适配器权限清单中声明，并由用户启用。
- 下载后提取来源 URL、标题、现有元数据和文件信息。

### 15.3 授权状态

下载成功只创建 `pending` 授权记录。用户确认后改为 `confirmed_by_user`。适配器不得自行将下载素材标记为可发布。

## 16. 进度事件

### 16.1 标准事件

```json
{
  "awap": "1.0",
  "type": "event",
  "event": "execution.progress",
  "data": {
    "execution_id": "01JEXEC",
    "phase": "generating",
    "completed": 3,
    "total": 10,
    "unit": "frames",
    "percent": 30,
    "message": "正在生成候选"
  }
}
```

### 16.2 阶段

通用阶段：

`starting → preparing → running → validating → finalizing → finished`

能力可以添加细分阶段，但必须同时映射到一个通用阶段。

### 16.3 未知进度

无法获得百分比时省略 `percent`，使用阶段和心跳。不得伪造线性进度。

### 16.4 产物发现

适配器可发送 `execution.artifact_discovered`，用于提前预览候选；该事件不代表产物已验证或已正式登记。

## 17. 取消与终止

取消顺序：

1. 向适配器发送 `cancel`。
2. 适配器向底层工具发送软终止或调用取消接口。
3. 等待能力定义的宽限时间。
4. 主机终止适配器进程组。
5. 标记任务 `cancelled` 或 `failed`。

已完整生成的输出保留在暂存区并作为清理建议或可选候选，不自动删除。

适配器必须让所有子进程加入可终止的进程组，避免取消后留下孤儿 CLI。

## 18. 错误协议

### 18.1 Error 对象

```json
{
  "awap": "1.0",
  "type": "error",
  "id": "01JREQ",
  "error": {
    "code": "AUTH_REQUIRED",
    "category": "auth",
    "message": "Grok CLI 尚未登录",
    "retryable": false,
    "user_action": {
      "type": "run_login",
      "label": "打开登录说明"
    },
    "diagnostic_id": "01JDIAG",
    "details": {}
  }
}
```

### 18.2 标准错误码

| 错误码 | 可重试 | 含义 |
|---|---:|---|
| `INVALID_INPUT` | 否 | 输入 Schema 或业务参数错误 |
| `UNSUPPORTED_FEATURE` | 否 | 缺少所需能力特性 |
| `TOOL_NOT_FOUND` | 否 | 未找到 CLI/运行时 |
| `TOOL_VERSION_INCOMPATIBLE` | 否 | 版本不兼容 |
| `AUTH_REQUIRED` | 否 | 未登录 |
| `AUTH_EXPIRED` | 否 | 登录已失效 |
| `QUOTA_EXHAUSTED` | 条件 | 订阅额度不足 |
| `RATE_LIMITED` | 是 | 临时频率限制 |
| `NETWORK_UNAVAILABLE` | 是 | 网络不可用 |
| `TIMEOUT` | 是 | 执行超时 |
| `PROCESS_CRASHED` | 条件 | 外部进程异常退出 |
| `OUTPUT_NOT_FOUND` | 条件 | 声称成功但未落盘 |
| `OUTPUT_INVALID` | 条件 | 媒体或结构无法验证 |
| `OUTPUT_SCHEMA_INVALID` | 条件 | 结构化文本不符合 Schema |
| `INSUFFICIENT_DISK` | 否 | 磁盘空间不足 |
| `RESOURCE_EXHAUSTED` | 是 | 内存/GPU 等资源不足 |
| `PERMISSION_DENIED` | 否 | 文件/进程权限不足 |
| `BUDGET_AUTHORIZATION_REQUIRED` | 否 | 付费调用未授权 |
| `VOICE_AUTHORIZATION_REQUIRED` | 否 | 声音克隆缺授权 |
| `CANCELLED` | 否 | 用户取消 |
| `INTERNAL_ADAPTER_ERROR` | 条件 | 适配器内部错误 |

### 18.3 错误分类

`input`、`auth`、`quota`、`network`、`tool`、`output`、`resource`、`permission`、`cost`、`cancel`、`internal`。

路由器主要依据标准错误码，不解析供应商自然语言错误。

### 18.4 供应商错误映射

适配器保存原始退出码和脱敏错误摘要，再映射标准错误码。无法识别时使用 `INTERNAL_ADAPTER_ERROR`，不得把未知错误标为可重试。

## 19. 费用与额度

### 19.1 CostClass

| 类别 | 含义 |
|---|---|
| `local` | 本地运行，无单次现金费用 |
| `subscription` | 使用已有订阅额度 |
| `free_tier` | 免费额度，可能有限制 |
| `paid_api` | 按量付费 |
| `unknown` | 无法确定，必须阻断 |

### 19.2 调度规则

- `local`、`subscription` 可按项目路由执行。
- `free_tier` 需要显示额度和限制。
- `paid_api` 必须存在有效预算授权。
- `unknown` 不能自动执行。
- 适配器运行中发现费用类别变化时立即停止，并返回预算错误。

### 19.3 订阅额度

无法精确读取订阅余额时，适配器报告 `quota_visibility=unknown`。系统可以依据实际限流错误暂停，但不能展示虚构余额。

## 20. 输入指纹与幂等

### 20.1 ExecutionKey

```text
sha256(
  capability code + capability version
  + normalized input
  + input file hashes
  + adapter version
  + tool/model identity
  + normalized options
)
```

### 20.2 幂等语义

- `prepare` 对相同输入应返回相同计划哈希，除非探测状态或工具版本变化。
- `execute` 不保证模型输出确定性。
- 主机用幂等键避免重复调度，不用它假定媒体结果可复现。
- 满意结果必须保存实际文件。

## 21. 超时、资源和并发

### 21.1 超时层级

- 握手超时。
- 无输出心跳超时。
- 单阶段超时。
- 总执行超时。
- 取消宽限超时。

适配器可以建议默认值，项目只能在允许范围内覆盖。

### 21.2 资源声明

```json
{
  "resources": {
    "cpu_weight": 2,
    "memory_mb_typical": 4096,
    "memory_mb_peak": 7000,
    "gpu": "metal",
    "exclusive_groups": ["local-ml"],
    "disk_temp_bytes": 2000000000
  }
}
```

调度器根据当前设备、其他任务和磁盘空间决定是否入队。适配器不能自行无上限并发。

## 22. 日志与脱敏

### 22.1 日志字段

- 时间。
- 级别。
- 适配器 ID/版本。
- execution/job/attempt ID。
- 阶段。
- 消息。
- 标准错误码。

### 22.2 禁止记录

- Token、Cookie 和完整密钥。
- `.env.local` 内容。
- 未经选择的声音样本内容。
- 完整浏览器配置。
- 项目外私人文件内容。

### 22.3 命令预览

命令以可执行名和参数数组记录。敏感参数保存 `<redacted:VAR_NAME>`，提示词优先保存文件哈希与项目内清单引用。

## 23. 内置适配器映射

### 23.1 Codex CLI

本机已确认支持 `codex exec` 非交互模式。V1 映射：

- `text.structured_generate`
- `text.review`

要求：

- 使用任务独立工作目录。
- 禁止项目外写入。
- 输出由 wrapper 解析和 Schema 校验。
- 版本和实际模型写入生成清单（能够获取时）。

### 23.2 Grok CLI

本机已确认支持单次提示、JSON/streaming JSON 输出和模型选择。V1 映射：

- `text.structured_generate`
- `text.review`
- `image.generate`（探测后启用）
- `image.edit`（探测后启用）
- `video.image_to_video`（探测后启用）

每项媒体能力独立探测，不能因文本能力可用就推断媒体能力可用。

### 23.3 FFmpeg/FFprobe

- `media.probe`
- `media.proxy`
- `media.render`
- `quality.media_check`

使用内置可信 wrapper，不直接开放 CLI 给插件或 UI。

### 23.4 CosyVoice3

- `speech.tts`
- `speech.voice_clone`

作为应用管理的本地组件，具体情绪、方言和时间戳能力由探测结果报告。

### 23.5 MuseTalk 1.5 MLX

- `video.lip_sync`

能力报告必须包含 Apple Silicon、内存和输入尺寸限制。

### 23.6 music-downloader

- `music.download`
- `sfx.download`

作为 yt-dlp 封装运行，下载结果永远先进入待确认素材状态。

## 24. 合规测试套件

每个适配器在启用前运行通用测试：

1. 清单 Schema 有效。
2. 握手与协议版本协商正确。
3. stdout 无非协议内容。
4. 输入 Schema 错误能被拒绝。
5. 输出路径无法逃逸暂存目录。
6. 未声明环境变量不会被注入。
7. 取消不会留下子进程。
8. 超时能够产生标准错误。
9. 错误中不泄露秘密。
10. 输出文件由主机独立验证。
11. `paid_api` 无授权时无法执行。
12. 执行后能够生成完整清单。

能力专属测试使用微小输入，不评价艺术质量。

## 25. 兼容性与版本演进

### 25.1 协议兼容

- 主版本变化允许破坏性修改。
- 次版本只增加可选字段和方法。
- 适配器必须忽略未知可选字段。
- 必填字段变化需要新能力版本。

### 25.2 能力兼容

工作流声明可接受的能力版本范围。适配器升级后若能力 Schema 不兼容，旧版本可并存，不能自动迁移正在生产的项目。

### 25.3 清单签名

V1 内置适配器随应用签名。第三方插件市场不在 V1 范围；本地加载插件时显示未验证来源，并记录文件哈希。

## 26. 实现目录建议

```text
packages/
├── awap-schemas/
│   ├── protocol/
│   ├── capabilities/
│   └── manifests/
├── adapter-sdk-python/
├── adapter-conformance/
└── adapters/
    ├── codex-cli/
    ├── grok-cli/
    ├── ffmpeg/
    ├── cosyvoice3/
    ├── musetalk-mlx/
    └── music-downloader/
```

Schema 是协议的权威来源，Python 类型和 TypeScript 类型由 Schema 生成，不手工维护三份定义。

## 27. 协议验收标准

1. 新适配器可只依赖能力 Schema 接入，不修改故事或分镜领域代码。
2. 文本、图片、视频、TTS 和口型使用一致生命周期。
3. 任意适配器崩溃不会导致 sidecar 主进程退出。
4. 适配器不能写入任务暂存区之外。
5. 未声明的环境变量不会进入适配器进程。
6. 媒体成功必须经过文件落盘与主机独立验证。
7. 供应商错误能映射为稳定错误码。
8. 取消和超时不会遗留孤儿进程。
9. 未授权付费调用在进程启动前被阻止。
10. 每次执行可生成完整、脱敏、可追溯的生成清单。

## 28. 待实现验证

1. Codex CLI 最稳定的结构化输出与会话隔离方式。
2. Grok 图片、图片编辑和视频能力的实际调用与产物发现方式。
3. Grok streaming JSON 中进度和工具调用的稳定映射。
4. CosyVoice3 是否直接实现 AWAP 进程或由 wrapper 封装本地服务。
5. MuseTalk MLX 的取消粒度和模型释放机制。
6. `music-downloader` Cookie 重试的权限提示与元数据补全。
7. macOS 下可靠终止完整子进程组的实现。
8. JSON Schema 到 Python/TypeScript 类型生成工具链。

