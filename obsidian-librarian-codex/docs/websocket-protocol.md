# WebSocket 控制面协议

> 扩展只做辅助控制台和任务入口。入库任务可以由扩展提交，但下载、
> 分析、写库、状态和 git commit 仍由 Agent 本地执行层完成。

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

轻量模型健康检查。当前只支持普通豆包 / 火山方舟 Ark API，固定请求
`/api/v3/tokenization`。该检查只验证 API Key、endpoint、模型 ID 是否基本可用；
这不等价于视频拆解端到端验证。

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

提交一个抖音入库任务。扩展只发送入库意图、URL 和页面线索，不发送 Cookie/API Key。
服务端写入 `~/.obsidian-librarian/inbox/{task_id}.json`，再由本地任务队列调用
`deps/douyin/scripts/ingest.py --task ...`。
任务执行支持有限并发，默认同时处理 2 个任务；可通过
`OBSIDIAN_LIBRARIAN_TASK_CONCURRENCY` 调整，当前上限为 4。

`ingest_intent` 是资产用途意图：

- `knowledge_ingest`：知识入库，写入 `知识资产/知识入库/`，生成 `knowledge_asset`
- `viral_breakdown`：爆款拆解，写入 `知识资产/创作模式/`，生成 `creative_pattern`

如果一次任务需要同时产出两份资产，扩展发送 `ingest_intents` 数组。服务端仍只创建一个队列任务；执行层下载一次、普通 Ark 上传/预处理一次，然后用不同 prompt 生成两份笔记。

视频超过 10 分钟时，执行层会先做全片概览，再自动切片精拆，并在任务进度中出现：

- `chunking_plan`
- `overview_uploading`
- `overview_uploaded`
- `analyzing_overview`
- `overview_strategy_decided`
- `chunk_uploading`
- `chunk_uploaded`
- `analyzing_chunk`
- `chunk_done`
- `synthesizing_chunks`
- `synthesizing_done`

```json
{
  "type": "task_request",
  "requestId": "1700000000000-abcd",
  "source": "extension_inline_button",
  "taskType": "douyin_ingest",
  "ingest_intent": "knowledge_ingest",
  "ingest_intents": ["knowledge_ingest", "viral_breakdown"],
  "url": "https://www.douyin.com/video/7390000000000000000",
  "pageTitle": "页面标题",
  "pageUrl": "https://www.douyin.com/",
  "awemeId": "7390000000000000000",
  "detectedBy": "active-feed:data-e2e-vid"
}
```

### `task_status_request`

请求最近任务状态。

```json
{ "type": "task_status_request" }
```

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
    "model_health_check",
    "extension_task_ingest",
    "task_status"
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
    "cookie": { "state": "ready", "platform": "douyin" },
    "tasks": {
      "running": 1,
      "failed": 0,
      "done": 3,
      "items": [
        {
          "id": "20260702-233000-abcd",
          "stageLabel": "分析中",
          "progressPercent": 74,
          "elapsedSec": 92
        }
      ]
    }
  },
  "timestamp": "2026-07-02T10:00:00"
}
```

### `task_accepted` / `task_rejected` / `task_status_snapshot`

`task_accepted` 表示任务已进入队列；`task_rejected` 表示 URL 或环境不满足；
`task_status_snapshot` 返回最近任务列表。

### `vault_status` / `model_status` / `config_synced` / `cookie_synced`

分别确认知识库识别、模型健康检查、配置落盘、Cookie 落盘。

## 边界

抖音入库可以从 Agent 会话或扩展按钮提交。无论入口在哪里，业务执行都固定走
Agent 本地执行层，并固定使用 `quality` 档。扩展可以提交 `ingest_intent`，但不展示、
不保存、不发送拆解质量、fps 或抽帧参数。
