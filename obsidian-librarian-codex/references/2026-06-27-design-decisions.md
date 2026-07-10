# 关键设计决策（2026-06-27）

> 当前实现口径的决策记录，保留给未来的 AI 看；若与 `SKILL.md` 冲突，以 `SKILL.md` 为准。

## 1. 为什么从文件桥改为 WebSocket

**原始方案**：扩展写 Downloads → Python 轮询（bridge_poller）
**问题**：
- 用户能看到 Downloads 里的文件（不够无感）
- 轮询有延迟，不是实时响应
- 需要额外维护 bridge_poller 进程

**最终方案**：WebSocket 实时通信
- 扩展直接连 Agent 的 WebSocket 服务器
- 配置/cookie 实时同步
- 任务进度实时推送
- 断线自动重连

## 2. 扩展与 Agent 的职责边界

| 职责 | 扩展 | Agent |
|------|------|-------|
| 抓 Cookie | ✅ | ❌ |
| 配置 API Key | ✅ | ❌ |
| 显示状态 | ✅ | ❌ |
| 视频下载 | ❌ | ✅ |
| 火山 API 调用 | ❌ | ✅ |
| 写 Vault | ❌ | ✅ |
| 任务进度推送 | ❌ | ✅（WebSocket） |

## 3. Chrome 扩展权限选择

**必须权限**：
- `cookies`：抓抖音 cookie
- `storage`：保存配置
- `downloads`：v0.1 曾用于文件桥，v0.2 后移除

**不需要权限**（曾误加）：
- `downloads`：WebSocket 方案不需要
- `activeTab`：如果不需要页面内容提取
- `scripting`：如果不需要注入脚本

## 4. WebSocket 连接策略

**扩展端**：
- popup.js 连接（用户打开扩展时）
- background.js 连接（后台常驻，用于接收通知）
- 断线后 3 秒重连

**Agent 端**：
- 启动时监听 `ws://127.0.0.1:8765`
- 支持多客户端连接（广播）
- 自动清理断开的客户端

## 5. 错误处理模式

**扩展 → Agent 消息**：
- 发送后等待确认（config_synced / cookie_synced）
- 超时未确认则显示警告
- 不阻塞用户操作

**Agent → 扩展推送**：
- 任务完成/失败时推送通知
- 扩展未连接时消息丢失（v0.1 不缓存）
- v0.x 可添加消息队列

## 6. 开发调试技巧

**扩展调试**：
1. Chrome → 扩展 → 开发者模式 → 背景页 → Console
2. 查看 `chrome-extension://<id>/popup/popup.html` 的 Console
3. Network 面板看 WebSocket 连接

**Agent 调试**：
1. 前台运行 `python3 server/websocket_server.py` 看日志
2. 用 `websockets` CLI 测试连接
3. 检查 `~/.agent-wiki/logs/` 日志

## 7. 常见坑

| 坑 | 原因 | 解决 |
|---|------|------|
| 扩展显示「Agent 未连接」| Agent 服务没启动 | 运行 `python3 server/launcher.py` |
| Cookie 抓取失败 | 没登录抖音 | 先打开抖音网页版登录 |
| 配置保存后没同步 | WebSocket 断线 | 检查网络，重连后自动同步 |
| 任务没触发 | 扩展没发 task_request | v0.1 需手动发链接给 Agent |
| 视频下载失败 | Cookie 失效 | 重新抓取 Cookie |

## 8. 版本演进路径

```
v0.1（当前）:
  - WebSocket 通信
  - Agent 手动启动
  - 抖音视频拆解
  - 扩展控制塔

v0.2:
  - 扩展直接触发拆解（task_request）
  - Agent 自动启动（launchd/系统服务）
  - 网页内容提取

v0.3:
  - 多平台支持（B站、小红书）
  - 知识库搜索/召回
  - 快捷指令（iOS Shortcuts）
```
