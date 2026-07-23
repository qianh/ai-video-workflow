# M0-09 Spike：music-downloader 定向输出与来源采集

## 目标

验证 `john-skills/music-downloader`：

1. 通过 `--output` 写入**任务暂存目录**
2. **不**写入默认 `~/Music/Downloads`
3. 采集来源元数据，导入状态为 `pending` 授权
4. 记录 Cookie 重试需用户授权

## 命令契约

```bash
python3 scripts/m0_09_music_downloader.py
# 或指定条目
python3 scripts/m0_09_music_downloader.py --item 'https://archive.org/details/testmp3testfile'
```

底层：

```bash
bash <music-downloader>/scripts/download.sh \
  --output <task-staging> \
  '<url-or-search>'
```

## 本机结论（2026-07-23）

| 检查 | 状态 |
|------|------|
| 定位 `download.sh` | ready |
| `yt-dlp` | ready（2025.12.08） |
| 暂存目录落盘 MP3 | **ready** |
| 默认 `~/Music/Downloads` 无新文件 | **ready** |
| 来源清单 + pending 授权 | **ready** |
| YouTube 直连 | **degraded**（JS challenge / 无格式；需升级 yt-dlp/EJS 或 Cookie） |
| Cookie 自动重试 | 脚本内置 chrome cookies；适配器必须先获用户授权 |

样例源：Internet Archive `testmp3testfile` → `artifacts/m0-09/staging/*.mp3`

## 适配器映射

- 能力：`music.download` / `sfx.download`
- 强制：`--output <task-staging>`，禁止省略（避免默认 `~/Music/Downloads`）
- 下载成功 → `SourceRecord` + 授权 `pending`
- Cookie：仅在权限清单声明且用户启用后允许 `--cookies-from-browser`
- 脚本缺陷：失败条目时进程仍可能 exit 0，wrapper 必须以暂存区音频文件为准

## 回退

1. YouTube 失败：换 Archive/Bilibili/直接 URL，或升级 yt-dlp 与 JS runtime
2. Cookie 验证：提示用户授权浏览器 Cookie 后重试
3. 默认目录风险：适配器层拒绝无 `--output` 调用
