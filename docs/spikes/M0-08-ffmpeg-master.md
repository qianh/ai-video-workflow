# M0-08 Spike：FFmpeg 竖屏母版渲染

## 目标

验证本机 FFmpeg 可从测试资产生成：

- 竖屏 **1080×1920**
- **90 秒以内**
- **ASS 字幕源**接入成片
- **人声 + 配乐混音**
- **H.264 + AAC** 母版

## 命令契约

```bash
python3 scripts/m0_08_ffmpeg_master.py
```

流水线：

1. 合成 fixture：`still.png`、`voice.wav`、`music.wav`、`captions.ass`
2. `zoompan` Ken Burns → 1080×1920 + `amix` 混音 → `staged.mp4`
3. 将 ASS 软复用为 `mov_text` 字幕轨 → `master.mp4`
4. FFprobe 校验分辨率、时长、音视频/字幕轨

## 本机结论（2026-07-22）

| 项 | 状态 |
|----|------|
| FFmpeg | 8.0.1 (Homebrew) |
| 竖屏运镜 + 混音 | **ready** |
| 软字幕（ASS→mov_text） | **ready** |
| 烧录字幕（ass/subtitles/drawtext） | **unavailable**（构建无 libass/freetype） |
| 验收 | **accepted** |

样片：`artifacts/m0-08/master.mp4`（8s / 1080×1920 / h264+aac+mov_text）

## 适配器建议

- 能力：`media.proxy`、`media.render`、`quality.media_check`
- 只暴露预定义 filtergraph 模板，禁止 UI 透传任意滤镜串
- V1 默认软字幕轨；需要硬烧录时安装带 libass 的 FFmpeg 并在探测后启用
- 输出仅写任务暂存目录

## 回退

1. 无 libass：软字幕 + 播放器叠加（当前默认）
2. 需要硬烧录：切换/构建 `ffmpeg --enable-libass --enable-libfreetype`
3. 编码失败：优先 `libx264` + `aac`，VideoToolbox 作为可选加速路径
