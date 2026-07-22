# M0-05 Spike：Grok 文本与媒体落盘

## 目标

独立探测 Grok 四项能力，并为每项给出 `ready` / `degraded` / `unavailable`：

| 能力码 | 探测方式 |
|--------|----------|
| `text.structured_generate` | `grok -p --json-schema` |
| `image.generate` | `grok -p --tools image_gen` |
| `image.edit` | `grok -p --tools image_edit` |
| `video.image_to_video` | `grok -p --tools image_to_video` |

成功媒体必须复制到任务目录并通过 PIL / FFprobe 解码。

## 命令契约

### 文本

```bash
grok -p '...' \
  --json-schema '<schema>' \
  --output-format json \
  --max-turns 2
```

解析 `structuredOutput`，不要把人话 stdout 当成功结果。

### 图片生成

```bash
grok -p 'Use image_gen ... report absolute path' \
  --yolo \
  --tools image_gen \
  --output-format json \
  --json-schema '{"type":"object","required":["image_path","ok"],...}'
```

工具默认写入 Grok session 目录；wrapper 必须立刻复制到任务 `outputs/`。

### 图片编辑

```bash
grok -p 'Use image_edit on <local-file> ...' \
  --yolo \
  --tools image_edit \
  --output-format json
```

### 图生视频

```bash
grok -p 'Use image_to_video on <local-file> ...' \
  --yolo \
  --tools image_to_video \
  --output-format json
```

## 本机探测结论（2026-07-22）

| 能力 | 状态 | 证据 |
|------|------|------|
| 文本结构化 | **ready** | `--json-schema` → `structuredOutput` |
| 图片生成 | **ready** | 1024×1024 JPEG，PIL/FFprobe 可读 |
| 图片编辑 | **ready** | 基于生成图编辑后 JPEG 可读 |
| 图生视频 | **unavailable** | API 400：Zero Data Retention 需 `output.upload_url` |

V1 门禁：图片生成失败必须阻断；视频不可用不阻断，降级为关键帧动态。

## 适配器映射建议

- wrapper 始终：任务工作目录、复制落盘、独立校验、写清单
- 不要假设 session 路径可长期保留
- 视频适配器在探测为 unavailable 时对 UI 显示降级，不隐藏错误
- 文本与媒体能力状态机独立，互不推断

## 复跑

```bash
python3 scripts/m0_05_grok_media.py
```

产物：`artifacts/m0-05/summary.json` 与 `images/`。

## 回退

1. 图片失败：停止依赖 Grok 图片的生产路径，改供应商或暂停 M2 定妆。
2. 编辑失败、生成成功：定妆用多次 generate 替代 edit。
3. 视频 unavailable：`static_motion`（FFmpeg 运镜）作为默认。
