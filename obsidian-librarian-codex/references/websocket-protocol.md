# WebSocket Control Protocol

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
  "client": "obsidian-librarian-extension",
  "version": "0.1.0"
}
```

### `config_update`

The server writes `~/.obsidian-librarian/config.toml` in the format consumed by
`deps/douyin/scripts/config_loader.py`.

```json
{
  "type": "config_update",
  "data": {
    "apiKey": "sk-...",
    "vaultPath": "/Users/lixinqi/Documents/agent 知识库",
    "model": "doubao-seed-2-0-lite-260428",
    "quality": "balanced"
  }
}
```

### `cookie_update`

The server writes `~/.obsidian-librarian/cookie/douyin.txt` with user-only file
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
