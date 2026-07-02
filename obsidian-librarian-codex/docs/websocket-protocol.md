# WebSocket 控制面协议

> 扩展只做辅助控制台。入库任务仍由 Agent 会话触发，不从扩展启动。

## 连接

- URL: `ws://127.0.0.1:8765`
- 格式：JSON text message
- Origin：允许 Chrome 扩展和本地无 Origin 测试客户端；拒绝普通网页 Origin
- 敏感信息：服务端状态响应不得返回 API Key、Cookie、Bearer token

## 扩展 -> Agent

### `handshake`

```json
{
  "type": "handshake",
  "client": "obsidian-librarian-extension",
  "version": "0.1.0"
}
```

### `status_request`

请求当前状态快照。

```json
{ "type": "status_request" }
```

### `config_update`

同步模型配置。`vaultPath` 只是线索，服务端必须校验后才写入配置。
扩展不发送质量档；服务端固定 `[analysis].default_quality = "quality"`。

```json
{
  "type": "config_update",
  "data": {
    "provider": "doubao",
    "apiKey": "sk-...",
    "model": "doubao-seed-2-0-lite-260428",
    "vaultPath": "/Users/xxx/Obsidian"
  }
}
```

### `vault_discover`

让 Agent 按知识库发现协议识别 vault。`hint` 可为空。

```json
{
  "type": "vault_discover",
  "hint": "/Users/xxx/Library/Mobile Documents/iCloud~md~obsidian/Documents"
}
```

### `vault_pick`

让本地 Agent 弹系统文件夹选择器，拿到真实绝对路径后校验并写入配置。
当前只支持 macOS。

```json
{ "type": "vault_pick" }
```

### `model_check`

轻量模型健康检查，按 provider 选择健康检查端点：`doubao` 走 Ark
`/tokenization`，`volcengine_agent_plan` 走 Agent Plan `/responses`。该检查只验证
API Key、endpoint、模型 ID 是否基本可用；这不等价于视频拆解端到端验证。

```json
{
  "type": "model_check",
  "data": {
    "provider": "doubao",
    "apiKey": "sk-...",
    "model": "doubao-seed-2-0-lite-260428"
  }
}
```

### `cookie_update`

扩展抓取抖音 Cookie 后，用 Netscape cookie 文件文本同步给 Agent。

```json
{
  "type": "cookie_update",
  "platform": "douyin",
  "data": "netscape_cookie_file_text"
}
```

### `task_request`

P0 拒绝扩展直接触发入库。

## Agent -> 扩展

### `agent_ready`

```json
{
  "type": "agent_ready",
  "version": "0.1.0",
  "capabilities": [
    "config_sync",
    "cookie_sync",
    "vault_discovery",
    "model_health_check"
  ]
}
```

### `status_snapshot`

```json
{
  "type": "status_snapshot",
  "status": {
    "vault": { "state": "ready", "path": "/...", "source": "obsidian_registry" },
    "model": { "state": "ready", "provider": "doubao", "model": "..." },
    "cookie": { "state": "ready", "platform": "douyin" }
  },
  "timestamp": "2026-07-02T10:00:00"
}
```

### `vault_status` / `model_status` / `config_synced` / `cookie_synced`

分别确认知识库识别、模型健康检查、配置落盘、Cookie 落盘。

## 边界

视频入库只从 Agent 侧启动：

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

该入口固定走 `quality` 档。扩展不展示、不保存、不发送拆解质量选项。
