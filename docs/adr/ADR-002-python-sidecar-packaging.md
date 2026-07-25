# ADR-002：Python Sidecar 打包方案

> 状态：已验证（开发/测试产物），签名包待复验  
> 日期：2026-07-25  
> 关联计划：M0-02、M0-03；M1 发布链路

## 背景

核心业务运行在 Python sidecar 中，需与 Tauri 主程序一起分发。要求：

- Apple Silicon（`aarch64-apple-darwin`）可独立启动
- 开发态可用源码模块快速迭代
- 发布态不依赖用户本机 Python
- 与 Rust 监督器通过 stdio NDJSON 通信（ADR-001）
- 模型权重不塞进应用包，避免体积失控

## 决策

1. **发布产物**：PyInstaller **onefile** 可执行文件  
   `workflow-sidecar-aarch64-apple-darwin`  
   构建入口：[`scripts/build-sidecar.sh`](../../scripts/build-sidecar.sh)
2. **开发态**：Rust 启动 `python3 -m workflow_sidecar`，设置 `PYTHONPATH` 指向 `services/sidecar/src`
3. **环境覆盖**：
   - `WORKFLOW_SIDECAR_PYTHON`：开发用解释器
   - `WORKFLOW_SIDECAR_BINARY`：强制使用已打包二进制
4. **测试方法开关**：仅通过 `WORKFLOW_SIDECAR_ENABLE_TEST_METHODS=1` 启用；正式 release 关闭
5. **目标架构**：V1 只保证 `aarch64-apple-darwin`；缺少对应 sidecar 的目标构建应失败
6. **模型与组件**：CosyVoice / MuseTalk 等不打入 sidecar，走独立组件管理（ADR-007）

## 已验证证据

| 项 | 结果 |
|---|---|
| PyInstaller onefile 产出 arm64 二进制 | 通过 |
| Rust 集成测试（源码模块） | 通过 |
| Rust 集成测试（打包二进制 + 测试方法 env） | 通过 |
| 协议 1000 条消息 / 取消 / 崩溃恢复 | 通过 |

## 替代方案

### 要求用户安装 Python + venv

开发友好，发布脆弱；普通创作者机器难保证版本与依赖。不采用。

### conda / 嵌入式 CPython 目录树

可调试性更好，但签名/公证路径更复杂，分发体积与路径管理成本高。V1 不采用。

### 把业务迁回纯 Rust

类型与性能好，但 AI 编排、适配器与数据层生态在 Python 更成熟。不采用。

## 影响

- CI/本地发布必须先 `build-sidecar.sh` 再打 Tauri 包
- onefile 冷启动略慢，可接受；若过慢可改为 onedir
- 签名与公证时主程序与 sidecar 必须同一链路处理
- 诊断包应记录 sidecar 版本与启动模式（源码 / 二进制）

## 回退

1. onefile 签名失败 → 改为 onedir 外置二进制  
2. 启动过慢 → onedir + 预热  
3. 架构扩展 → 按三元组增加二进制并在构建门禁校验  

## 后续验证

- 签名、公证后的 `.app` 内 sidecar 路径与可执行权限  
- 无开发工具链机器上的冷启动与崩溃恢复  
