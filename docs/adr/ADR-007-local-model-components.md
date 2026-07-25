# ADR-007：本地模型组件管理

> 状态：已决策；CosyVoice3 / MuseTalk 本机尚未安装（M0-06 blocked）  
> 日期：2026-07-25  
> 关联计划：M0-06、M0-07；M4 语音/口型  
> 证据：[M0-06 status](../spikes/M0-06-cosyvoice-status.md)

## 背景

CosyVoice3、MuseTalk 等体积大、版本敏感，不能：

- 打进 PyInstaller sidecar（ADR-002）  
- 与应用版本强绑死、无法单独升级  
- 静默删除导致项目无法复现  

## 决策

### 组件分类

| 类型 | 示例 | 管理方式 |
|------|------|----------|
| 应用内置 | 核心 sidecar、Schema、迁移 | 随应用发布 |
| 系统发现 | Codex、Grok、FFmpeg、yt-dlp | PATH/探测，不负责安装模型权重 |
| 应用管理 | CosyVoice3、MuseTalk、权重与专用 runtime | 组件管理器下载/校验/切换 |

### 状态机

`not_installed → downloading → verifying → installing → ready`  

异常：`failed`、`incompatible`、`update_available`、`disabled`

### 规则

1. **应用更新与模型组件更新分离**  
2. 更新前检查是否有运行中任务  
3. **版本共存**：新版本安装成功并用户确认前，不删除旧版本  
4. 卸载 / 清理必须 **用户确认**；系统只给建议，不自动删用户文件  
5. 启动时握手校验：sidecar 协议版本、组件兼容矩阵  
6. 下载后 **哈希与来源校验** 才进入 `ready`  
7. 组件路径记入全局设备配置，不进项目相对路径业务表的“可执行文件”字段  

### 当前本机

- CosyVoice3：未安装 → M0-06 **blocked**  
- MuseTalk：未找到 → M0-07 挂起  
- 不阻断进入 M1；阻断的是 M4 语音/口型相关验收  

## 替代方案

### 全部打进 .app

安装简单，更新与签名体积不可接受。不采用。

### 每次任务临时 pip 安装

不可复现、不安全。禁止。

### 完全云端 TTS/口型

可作降级供应商，但仍需同一组件状态模型。

## 影响

- `global.db` 保存组件版本与健康状态  
- UI 设置页展示组件矩阵与探测结果  
- 项目生成清单记录组件版本，保证可追溯  
- M0 阶段对未装组件如实标记 unavailable，不伪造 ready  

## 回退

1. CosyVoice 不可用 → 寻找替代中文 TTS，或暂停口型链路  
2. MuseTalk 不可用 → 简化口型 / 静态嘴型降级（产品已允许）  
3. 组件损坏 → 回退到上一 ready 版本目录  

## 后续验证

- 安装 CosyVoice3 后完成普通话 TTS + 授权克隆样例  
- MuseTalk 动漫近景口型或明确降级  
- 卸载确认与磁盘回收流程  
