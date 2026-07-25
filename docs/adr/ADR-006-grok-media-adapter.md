# ADR-006：Grok 媒体能力适配

> 状态：已验证（文本/图/编辑 ready；视频 unavailable）  
> 日期：2026-07-25  
> 关联计划：M0-05；M2 定妆；M3 图片/视频适配器  
> 证据：[M0-05 spike](../spikes/M0-05-grok-media.md)

## 背景

V1 首选 Grok 作为图片与（可选）视频生成通道，同时用 Grok/Codex 做结构化文本。需要固定：

- 非交互调用方式  
- 成功标准（落盘 + 可解码）  
- 能力独立探测  
- 失败与降级语义  

## 决策

### 调用契约

| 能力 | CLI 方式 | 成功标准 |
|------|----------|----------|
| 文本结构化 | `grok -p --json-schema --output-format json` | 解析 `structuredOutput` |
| 图片生成 | `grok -p --tools image_gen --yolo` | 复制到任务目录 + PIL/FFprobe 可读 |
| 图片编辑 | `grok -p --tools image_edit --yolo` | 同上 |
| 图生视频 | `grok -p --tools image_to_video --yolo` | 视频落盘 + ffprobe 有视频流 |

### 规则

1. **能力状态机独立**：`ready | degraded | unavailable`，互不推断  
2. **Session 路径不可信**：工具常写 `~/.grok/sessions/...`，wrapper **必须立即复制**到任务 `outputs/`  
3. **人话 stdout 不是成功结果**；结构化字段 / 文件校验才是  
4. **图片生成失败** → 阻断依赖定妆/分镜出图的生产路径  
5. **视频 unavailable** → **不阻断 V1**，降级为关键帧动态（FFmpeg 运镜，ADR-008）  
6. 探测脚本：`scripts/m0_05_grok_media.py`

### 本机探测结论（2026-07-22）

| 能力 | 状态 | 说明 |
|------|------|------|
| text.structured_generate | ready | json-schema |
| image.generate | ready | 1024² JPEG |
| image.edit | ready | 可编辑落盘 |
| video.image_to_video | unavailable | ZDR 需 `output.upload_url` |

## 替代方案

### 直接调 xAI HTTP API

可控性高，但要自管鉴权、额度与上传 URL；当前 OAuth CLI 已够用。可作为并行后端，接口保持 AWAP 不变。

### 其他图片供应商作为主路径

可替换；适配器层必须可插拔，不把 Grok 写死进领域模型。

### 视频失败则整体停工

与产品门禁不符。不采用。

## 影响

- M2 定妆可先复用 M0 验证的图片 wrapper  
- UI 必须展示各能力探测状态  
- 清单记录模型、工具、耗时与来源路径  
- 额度/成本需进入任务诊断，不进入自动艺术评分  

## 回退

1. 图片长期失败 → 换供应商或暂停相关里程碑  
2. 视频恢复 → 探测转 ready 后启用，无需改领域模型  
3. ZDR 限制 → 配置合规 `upload_url` 或继续 static_motion  

## 后续验证

- 批量定妆稳定性与目录配额  
- 签名环境无交互权限下的 headless 调用  
