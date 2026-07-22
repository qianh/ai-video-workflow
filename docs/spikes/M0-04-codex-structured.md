# M0-04 Spike：Codex CLI 非交互结构化输出

## 目标

验证 Codex 可作为 V1 `text.structured_generate` / `text.review` 后端：

- 非交互执行（`codex exec`）
- Schema 约束最终输出（`--output-schema`）
- 结果可落盘并独立校验（`--output-last-message`）
- 至少连续 10 次 Schema 校验通过

## 命令契约（已验证）

```bash
codex exec \
  --ephemeral \
  --skip-git-repo-check \
  --sandbox read-only \
  --color never \
  --output-schema spikes/m0-04/shot_card.schema.json \
  --output-last-message artifacts/m0-04/run-01.json \
  -C <task-workdir> \
  "<prompt>"
```

关键参数：

| 参数 | 作用 |
|------|------|
| `--output-schema` | 约束最终响应形状 |
| `--output-last-message` | 只落盘最后一条助手消息，便于 wrapper 解析 |
| `--ephemeral` | 不持久化会话文件 |
| `--sandbox read-only` | 结构化文本生成默认不写仓库 |
| `-C` | 任务工作目录隔离 |

可选：

- `--json`：事件 JSONL，后续可用于进度映射，不作为成功结果来源
- `--model`：固定模型做回归

## 验收脚本

```bash
python3 scripts/m0_04_codex_structured.py --runs 10
```

产物：

- `artifacts/m0-04/run-NN.json`：每次结构化结果
- `artifacts/m0-04/summary.json`：连续通过计数与结论

## 适配器映射建议

- 能力码：`text.structured_generate`、`text.review`
- wrapper 职责：构造 schema 文件、调用 `codex exec`、读取 last message、Schema 二次校验、写 AWAP 清单
- 禁止：把 stdout 人话日志当成功结果；禁止项目外写入
- 会话：每次任务新建 ephemeral 调用；审阅阶段使用独立调用，不复用执行会话

## 回退

若连续成功率低于门槛：

1. 固定更强模型并缩短 schema
2. 对失败响应做一次受限重试（不改 schema）
3. 切换到 Grok 文本路径（同样走 structured wrapper）

## 证据

以最近一次 `artifacts/m0-04/summary.json` 为准。
