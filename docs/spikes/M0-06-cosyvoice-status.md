# M0-06 Spike 状态：CosyVoice3

## 探测（2026-07-22）

| 检查 | 结果 |
|------|------|
| `import cosyvoice` | 失败（未安装） |
| 本机常见路径 `*cosyvoice*` | 未找到模型/仓库 |
| MuseTalk（M0-07 依赖） | 未找到 |

## 结论

**blocked / unavailable** — 需要先安装 CosyVoice3 运行时与模型后，再跑 TTS + 声音克隆验收。

## 下一步（安装后）

1. 固定组件目录与版本清单
2. 生成普通话样句到任务暂存目录
3. 使用授权参考音频完成一次克隆
4. 记录 RTF / 内存 / 设备（M3 Pro）证据
5. 输出 `ready|degraded|unavailable`

在完成前，M0-07 MuseTalk 与 M4 语音路径保持挂起。
