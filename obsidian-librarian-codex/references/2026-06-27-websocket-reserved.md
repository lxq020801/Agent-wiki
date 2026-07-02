# WebSocket 预留实现记录（2026-06-27）

## 背景

用户要求扩展实现「立刻响应式回传」，不要文件桥、不要轮询。经过讨论，选择 WebSocket 作为长期方案（v0.x），但 v0.1 先保留文件桥保证可用。

## 决策过程

### 用户诉求

- 「不要做文件下载，不要做自动轮询，需要立刻响应式的回传」
- 后期 Agent 会把任务进度传回扩展通知用户
- 用户不是官方开发者，不走 Native Messaging（门槛太高）

### 方案对比

| 方案 | 原理 | 优点 | 缺点 | 用户门槛 |
|------|------|------|------|----------|
| WebSocket | 扩展主动连 Agent（ws://127.0.0.1:8765） | 实时双向，Agent 可主动推 | Agent 需常驻 | 低 |
| HTTP POST | 扩展 POST 到 Agent | 简单 | 单向，每次建连 | 低 |
| Native Messaging | Chrome 官方机制 | 安全，按需启动 | 需系统级注册 | 高 |
| 文件桥 | 扩展写 Downloads | 无服务 | 有延迟，用户可见文件 | 极低 |

**结论**：WebSocket 是最佳长期方案，v0.1 先保留文件桥，扩展端预留 WebSocket 代码。

## 实现内容

### 扩展端变更（popup.js + background.js）

**popup.js**：
- 添加 `connectWebSocket()` 函数
- 添加 `sendToAgent()` 函数
- 添加 `handleAgentMessage()` 函数处理 Agent 推送
- 支持消息类型：handshake、config_update、cookie_update
- 接收消息类型：task_update、task_complete、task_error、config_synced、cookie_synced
- 连接状态自动更新（红灯/绿灯）

**background.js**：
- 添加 WebSocket 连接管理
- 断线后 3 秒自动重连
- 接收 Agent 推送并显示系统通知
- 支持消息类型：task_complete、task_error、agent_ready

**manifest.json**：
- 保留必要权限：storage、cookies、activeTab、scripting
- 保留 host_permissions：抖音域名

**popup.html**：
- 添加 `connection-status` 状态灯
- 添加 `status-row` 容器

**popup.css**：
- 添加 `.status-row` 样式
- 添加 `.status-dot.offline` 样式

### 协议文档

编写 `docs/websocket-protocol.md`：
- 连接信息：ws://127.0.0.1:8765
- 消息格式：JSON，必须包含 type 字段
- 扩展 → Agent 消息：handshake、config_update、cookie_update、task_request
- Agent → 扩展消息：agent_ready、config_synced、cookie_synced、task_update、task_complete、task_error
- 状态码定义：queued、downloading、uploading、analyzing、done、error

## 验证结果

- ✅ manifest.json：权限正确
- ✅ popup.js：WebSocket 客户端、消息发送/接收、任务状态更新
- ✅ background.js：WebSocket 连接管理、Agent 消息处理、系统通知
- ✅ popup.html：连接状态 UI
- ✅ popup.css：连接状态样式
- ✅ websocket-protocol.md：完整协议文档

## 当前状态

- 扩展端 WebSocket 客户端已就绪
- v0.1 阶段 Agent 端 WebSocket 服务器未实现
- 扩展显示「Agent 未连接」（红灯）
- Agent 实现 WebSocket 服务器后自动变绿

## 后续工作

1. **Agent 端 WebSocket 服务器**：实现 `ws://127.0.0.1:8765` 监听
2. **消息处理**：接收扩展消息，处理 config_update、cookie_update
3. **任务推送**：任务进度、完成、错误时主动推送给扩展
4. **系统通知**：Agent 推送任务完成通知到扩展

## 关键文件

- `chrome-extension/popup/popup.js` — 扩展端 WebSocket 客户端
- `chrome-extension/background.js` — 后台 WebSocket 连接管理
- `chrome-extension/manifest.json` — 扩展权限配置
- `docs/websocket-protocol.md` — 通信协议文档
