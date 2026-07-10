# WebSocket Control Protocol

> 历史归档 / 非权威资料：本文记录 2026-06-27 附近的旧控制面设想。
> 当前协议以 `docs/websocket-protocol.md` 为准；当前扩展可以提交
> `task_request`，但只提交 `ingest_intent` 和页面线索，业务编排仍由
> Agent 本地执行层完成。

P0 uses WebSocket only as a control plane between the Chrome extension and the
Agent runtime. It syncs configuration and Douyin Cookie immediately. It does not
start ingest jobs.

## Connection

- URL: `ws://127.0.0.1:8765`
- Format: JSON text messages
- Reconnect: extension retries after disconnect

## Extension -> Agent

### `handshake`

```json
{
  "type": "handshake",
  "client": "agent-wiki-extension",
  "version": "0.1.0"
}
```

### `config_update`

The server writes `~/.agent-wiki/config.toml` in the format consumed by
`deps/douyin/scripts/config_loader.py`.

```json
{
  "type": "config_update",
  "data": {
    "apiKey": "sk-...",
    "vaultPath": "/path/to",
    "model": "doubao-seed-2-0-lite-260428",
    "quality": "balanced"
  }
}
```

### `cookie_update`

The server writes `~/.agent-wiki/cookie/douyin.txt` with user-only file
permissions.

```json
{
  "type": "cookie_update",
  "platform": "douyin",
  "data": "netscape_cookie_file_text"
}
```

### `task_request`

Deferred. P0 rejects this message:

```json
{
  "type": "task_rejected",
  "reason": "extension_task_trigger_deferred",
  "message": "P0 ingest runs from Agent via scripts/ingest_url.py"
}
```

## Agent -> Extension

### `agent_ready`

```json
{
  "type": "agent_ready",
  "version": "0.1.0",
  "capabilities": ["config_sync", "cookie_sync"]
}
```

### `config_synced`

```json
{
  "type": "config_synced",
  "timestamp": "2026-06-27T10:00:00"
}
```

### `cookie_synced`

```json
{
  "type": "cookie_synced",
  "platform": "douyin",
  "timestamp": "2026-06-27T10:00:00"
}
```

## Boundary

Douyin ingest starts only when the Agent runs:

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

Task queues, extension one-click ingest, and progress push are deferred beyond
P0.
