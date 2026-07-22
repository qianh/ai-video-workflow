# ADR-001：Rust 与 Python 使用 stdio NDJSON RPC

> 状态：已验证，待签名包复验  
> 日期：2026-07-22  
> 关联计划：M0-01、M0-02、M0-03

## 背景

桌面工作站需要让 Tauri/Rust 监督 Python 业务进程，同时避免在本机开放 HTTP 端口。协议必须支持短请求、长任务事件、按请求取消、进程崩溃检测和应用重启后的后续恢复。

## 决策

V1 使用 stdin/stdout 上的一行一个 JSON 对象：

```json
{"v":1,"type":"request","id":"req_01","method":"system.ping","params":{}}
{"v":1,"type":"response","id":"req_01","result":{"status":"ok"}}
{"v":1,"type":"event","event":"request.progress","data":{"request_id":"req_02","current":1,"total":20}}
```

Rust 是进程监督者和请求关联方；Python 是协议服务端。stderr 只用于受限日志，不能进入协议流。

## 关键语义

### 请求和事件

- 协议版本固定为 `v = 1`，未知版本拒绝。
- 请求 ID 在单次应用会话中唯一。
- 单条消息最大 1 MiB。
- 响应通过 ID 关联等待者；事件通过 Tauri event 转发给 React。
- 未知事件可以忽略，UI 不能把事件当作唯一事实源。

### 取消

主机发送 `request.cancel` 并携带目标请求 ID。目标执行协程被取消并返回 `CANCELLED`；取消动作自身返回是否找到运行中请求。

### 崩溃与恢复

- stdout EOF 或协议污染会将 Sidecar 标记为不可用，并使所有等待请求明确失败。
- 失败请求不会自动重放，避免重复生成、重复写入或重复付费。
- 下一次新请求检查进程状态并启动新 Sidecar。
- 后续持久化 `Job` 依靠幂等键决定是否重新调度，不由 IPC 层猜测。

## 已验证证据

| 层 | 验证内容 | 结果 |
|---|---|---|
| Python 协议 | envelope、版本、大小、错误码 | 通过 |
| Python stdio | 1000 条连续消息、错误后继续、受控崩溃 | 通过 |
| Python 运行时 | 进度、取消、重复 ID、参数错误 | 通过 |
| Rust 协议 | 响应、事件、远端错误和无效消息 | 通过 |
| Rust 监督器 | 1000 条消息、进度转发、取消、崩溃后新进程 | 通过 |
| React | 状态、Ping、进度、取消、恢复和错误展示 | 通过 |

Python 测试覆盖率为 96%；React 测试的语句、函数、行和分支覆盖率均达到 80% 门禁。

## 替代方案

### 本机 HTTP

优点是调试和生态成熟；缺点是端口冲突、防火墙提示、额外鉴权和本机服务暴露。V1 不采用。

### Unix Domain Socket

支持多客户端和更明确的连接模型，但需要处理 socket 路径、陈旧文件和权限，当前单 UI/单 Sidecar 没有收益。暂不采用。

### Tauri Shell 插件直接调用每个 CLI

实现短，但会把业务编排和外部命令权限推向 WebView/Rust，并难以形成统一持久任务模型。不采用。

## 影响

- Python stdout 必须保持绝对干净。
- 大型文本和媒体不能经过 IPC，必须使用受控文件引用。
- Rust 需要维护 Sidecar 生命周期、等待请求表和事件广播。
- 应用正式签名和公证时，主程序与 PyInstaller Sidecar 必须按同一发布链路处理。

## 后续验证

- 签名、公证后的 `.app` 内 Sidecar 路径和执行权限。
- 应用退出期间的优雅停机宽限时间。
- 持久化 `Job` 引入后的跨应用重启恢复与幂等对账。
