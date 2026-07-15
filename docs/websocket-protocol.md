# WebSocket 控制面协议

> 扩展只做辅助控制台和任务入口。入库任务可以由扩展提交，但下载、
> 分析、写库和状态仍由 Agent 本地执行层完成；vault Git 不由入库任务自动操作。

## 连接

- URL: `ws://127.0.0.1:8765`
- 格式：JSON text message
- Origin：允许 Chrome 扩展和本地无 Origin 测试客户端；拒绝普通网页 Origin
- 敏感信息：服务端状态响应不得返回 API Key、Cookie、Bearer token
- 当前产品版本：`0.1.1`
- 当前协议版本：`1`

连接建立后可以先读取状态，但配置、Cookie、模型检查、入库和派生操作必须通过版本握手。新服务会拒绝旧扩展的写操作；新扩展连接缺少完整运行身份的旧服务时，只保留状态诊断并暂停同步与入库。

## 扩展 -> Agent

### `handshake`

```json
{
  "type": "handshake",
  "client": "agent-wiki-extension",
  "product": "agent-wiki",
  "version": "0.1.1",
  "protocolVersion": 1
}
```

`version` 必须来自扩展 `manifest.json`，不能在 background/popup 中另写常量。服务端以 `handshake_ack.compatibility` 返回校验结果。

### `status_request`

请求当前状态快照。

```json
{ "type": "status_request" }
```

### `config_update`

同步模型配置。`vaultPath` 只是线索，服务端必须校验后才写入配置。
扩展不发送质量档；服务端固定 `[analysis].default_quality = "quality"`。
`videoAnalysis.strategyModel` 是可选字段，缺省使用 `doubao-seed-2-0-mini-260428`。
`server.taskConcurrency` 控制任务队列同时处理多少个入库任务，范围 `1-4`，缺省为 `2`。
`videoAnalysis.chunkConcurrency` 控制单个长视频内几个切片并发分析，范围 `1-4`，缺省为 `2`。
Endpoint 必须是可信 HTTPS 地址，不能包含账号密码，也不能是 Agent Plan endpoint；非法地址必须返回 `config_rejected`，不能静默回退。

```json
{
  "type": "config_update",
  "data": {
    "llm": {
      "provider": "doubao",
      "apiKey": "sk-...",
      "endpoint": "https://ark.cn-beijing.volces.com/api/v3"
    },
    "videoAnalysis": {
      "modelPreset": "lite",
      "analyzerModel": "doubao-seed-2-0-lite-260428",
      "strategyModel": "doubao-seed-2-0-mini-260428",
      "chunkConcurrency": 2
    },
    "server": {
      "taskConcurrency": 2
    },
    "vaultPath": "<home>/Obsidian"
  }
}
```

旧版 flat 字段仍兼容读取：`provider/apiKey/model/strategyModel/taskConcurrency/serverTaskConcurrency/videoChunkConcurrency/endpoint`。

### `vault_discover`

让 Agent 按知识库发现协议识别 vault。`hint` 可为空。

```json
{
  "type": "vault_discover",
  "hint": "<home>/Library/Mobile Documents/iCloud~md~obsidian/Documents"
}
```

### `vault_pick`

让本地 Agent 弹系统文件夹选择器，拿到真实绝对路径后校验并写入配置。
当前只支持 macOS。

```json
{ "type": "vault_pick" }
```

### `model_check`

轻量模型健康检查。当前只支持字节跳动火山方舟 Ark API，固定请求
`/api/v3/tokenization`。该检查只验证 API Key、endpoint、模型 ID 是否基本可用；
这不等价于视频拆解端到端验证。

```json
{
  "type": "model_check",
  "data": {
    "llm": {
      "provider": "doubao",
      "apiKey": "sk-...",
      "endpoint": "https://ark.cn-beijing.volces.com/api/v3"
    },
    "videoAnalysis": {
      "modelPreset": "lite",
      "analyzerModel": "doubao-seed-2-0-lite-260428",
      "strategyModel": "doubao-seed-2-0-mini-260428",
      "chunkConcurrency": 2
    },
    "server": {
      "taskConcurrency": 2
    }
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

提交一个抖音入库任务。扩展只发送 URL 和页面线索，不发送 Cookie/API Key。
服务端写入 `~/.agent-wiki/inbox/{task_id}.json`，再由本地任务队列调用
`deps/douyin/scripts/ingest.py --task ...`。
任务执行支持有限并发，默认同时处理 `2` 个任务；扩展可通过
`config_update.server.taskConcurrency` 调整，范围 `1-4`。也可用旧字段
`config_update.taskConcurrency` / `config_update.serverTaskConcurrency` 兼容调整。
长视频内部切片并发通过 `config_update.videoAnalysis.chunkConcurrency` 调整，范围 `1-4`。
也可用
`AGENT_WIKI_TASK_CONCURRENCY` 作为启动时覆盖值。

抖音任务固定使用 `ingest_intent: knowledge_ingest`：写入 `知识资产/知识入库/`，生成一份 `knowledge_asset` 来源笔记。该字段由服务端写入任务和状态，扩展不再发送可选入库意图。

视频超过 10 分钟时，执行层会先做全片概览，再自动切片精拆，并在任务进度中出现：

- `chunking_plan`
- `overview_uploading`
- `overview_uploaded`
- `analyzing_overview`
- `repairing_overview_strategy`
- `overview_strategy_repaired`
- `overview_strategy_decided`
- `chunk_uploading`
- `chunk_uploaded`
- `analyzing_chunk`
- `chunk_done`
- `synthesizing_chunks`
- `synthesizing_done`
- `derived_candidates_ready`

长视频状态会额外带：

- `audit_artifacts`：本次任务的审计产物目录和文件索引，实际文件位于 `~/.agent-wiki/run-artifacts/{task_id}/`
- `overview_strategy_decided.fps_plan[].validation_fallback`：JSON/结构问题导致的兜底
- `overview_strategy_decided.fps_plan[].fps_adjusted`：程序根据置信度、安全帧数或视觉证据规则调整 fps
- `overview_strategy_decided.fps_plan[].lite_brief`：mini 给 Lite 的本段精拆说明摘要
- `chunk_progress[*].chunk_done.artifact`：Lite 分片输出文件

```json
{
  "type": "task_request",
  "requestId": "1700000000000-abcd",
  "source": "extension_popup",
  "taskType": "douyin_ingest",
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

### `derived_task_action`

对某个父任务下的派生候选执行人工操作。扩展只发送候选 ID、动作和可选目标 URL；服务端负责状态机校验、父资产存在性校验、URL 安全清洗、幂等入队和任务状态合并。

允许动作：

- `confirm`：确认执行派生。只有 `candidate` / `auto_ready` / `needs_target` 且父资产已写入时可执行。
- `ignore`：忽略候选。忽略动作不校验输入框 URL，也不会创建子任务；结果写入 `~/.agent-wiki/derived-actions/{parent_task_id}.json`，后续自动派生不会再次入队。

`official_doc` / `web_research` 或其他缺目标候选必须提供公开 HTTPS URL。URL 不能包含账号密码、localhost/private IP，也会删除 token/key/secret/signature 等敏感 query。

```json
{
  "type": "derived_task_action",
  "requestId": "derived-1700000000000-abcd",
  "taskId": "20260705-170000-abcd",
  "derivedTaskId": "dt-xxxx",
  "action": "confirm",
  "targetUrl": "https://github.com/langchain-ai/langgraph"
}
```

## Agent -> 扩展

### `agent_ready`

```json
{
  "type": "agent_ready",
  "version": "0.1.1",
  "protocolVersion": 1,
  "runtime": {
    "product": "agent-wiki",
    "productVersion": "0.1.1",
    "protocolVersion": 1,
    "sourceRevision": "3c7ea9e0158a",
    "buildId": "src-0123456789abcdef",
    "deployment": {
      "state": "current",
      "code": "source_checkout"
    }
  },
  "capabilities": [
    "config_sync",
    "cookie_sync",
    "vault_discovery",
    "model_health_check",
    "extension_task_ingest",
    "task_status",
    "derived_task_action"
  ]
}
```

`sourceRevision` 是可用时的短 Git commit；`buildId` 是服务源码内容指纹，Git 不可用时仍可比较两次运行是否来自同一份服务代码。两者都不包含源码目录。`deployment` 只返回枚举状态，不返回本地路径：

- `current/source_checkout`：当前 Git checkout
- `current/packaged_source`：不带 Git 元数据的源码副本
- `legacy_path/legacy_source_path`：从已知旧目录名启动，扩展必须暂停写操作并提示从当前仓库启动

不得通过新建旧目录、复制当前代码到旧目录或建立旧路径符号链接来消除提示。服务使用启动时可见路径判断旧目录风险，旧目录名符号链接不会被 canonical path 掩盖；manifest、Git commit 和源码指纹仍从 canonical 路径读取。

### `handshake_ack`

```json
{
  "type": "handshake_ack",
  "runtime": {
    "product": "agent-wiki",
    "productVersion": "0.1.1",
    "protocolVersion": 1,
    "sourceRevision": "3c7ea9e0158a",
    "buildId": "src-0123456789abcdef",
    "deployment": { "state": "current", "code": "source_checkout" }
  },
  "compatibility": {
    "state": "compatible",
    "canOperate": true,
    "message": "扩展、服务与协议版本一致。",
    "clientVersion": "0.1.1",
    "clientProtocolVersion": 1
  }
}
```

`compatibility.state` 可能为 `compatible`、`legacy_client`、`product_mismatch`、`version_mismatch` 或 `protocol_mismatch`。除 `compatible` 外，服务端拒绝控制面写操作。

### `protocol_rejected`

未握手或版本校验未通过的客户端发送写操作时，服务端返回：

```json
{
  "type": "protocol_rejected",
  "reason": "version_mismatch",
  "message": "扩展 v0.0.9 与服务 v0.1.1 不一致。",
  "runtime": { "product": "agent-wiki", "productVersion": "0.1.1", "protocolVersion": 1 }
}
```

只读的 `status_request` 和 `task_status_request` 不受该门禁影响，便于诊断旧部署。

### `status_snapshot`

```json
{
  "type": "status_snapshot",
  "status": {
    "runtime": {
      "product": "agent-wiki",
      "productVersion": "0.1.1",
      "protocolVersion": 1,
      "sourceRevision": "3c7ea9e0158a",
      "buildId": "src-0123456789abcdef",
      "deployment": { "state": "current", "code": "source_checkout" }
    },
    "vault": { "state": "ready", "path": "/...", "source": "obsidian_registry" },
    "llm": { "state": "ready", "provider": "doubao", "model": "...", "endpoint": "https://ark.cn-beijing.volces.com/api/v3" },
    "videoAnalysis": {
      "modelPreset": "lite",
      "analyzerModel": "doubao-seed-2-0-lite-260428",
      "strategyModel": "doubao-seed-2-0-mini-260428",
      "chunkConcurrency": 2
    },
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

## 扩展兼容行为

扩展用同一套纯函数同时校验 background 和 popup 收到的 `agent_ready`、`handshake_ack`、`status_snapshot.status.runtime`：

| 场景 | 显示 | 控制面行为 |
| --- | --- | --- |
| 产品、扩展版本、服务版本、协议和部署状态都匹配 | 服务版本、协议、源码 commit/指纹，绿色“版本一致” | 正常同步和入库 |
| 扩展版本与服务版本不同 | 中文说明两端版本 | 暂停同步和入库 |
| 协议版本不同 | 中文说明两端协议版本 | 暂停同步和入库 |
| 缺 `runtime`、版本、协议或源码标识 | “检测到旧服务” | 保留状态读取，暂停同步和入库 |
| `deployment.state = legacy_path` | “服务由旧源码路径启动” | 暂停同步和入库 |

扩展只读取运行身份白名单字段。服务端即使返回额外的 `path`、`apiKey` 或任意文本，也不会进入版本状态或持久化的 `agentRuntime`。

### `task_accepted` / `task_rejected` / `task_status_snapshot`

`task_accepted` 表示任务已进入队列；`task_rejected` 表示 URL 或环境不满足；
`task_status_snapshot` 返回最近任务列表。

任务状态项可包含派生候选公开投影。这里返回的是摘要，不是完整系统记录：

```json
{
  "id": "20260705-170000-abcd",
  "stageLabel": "派生候选已生成",
  "derivedTasks": [
    {
      "id": "dt-xxxx",
      "name": "LangGraph",
      "targetType": "github_project",
      "taskKind": "github_project_ingest",
      "targetUrl": "https://github.com/langchain-ai/langgraph",
      "searchQuery": "LangGraph GitHub repository",
      "decision": "candidate",
      "status": "candidate",
      "candidateStatus": "candidate",
      "score": 88,
      "reason": "父视频用它解释 Agent Harness 状态图。"
    }
  ],
  "derivedSummary": {
    "candidate": 1,
    "rejected": 0,
    "existing_related": 0,
    "needs_target": 0,
    "suppressed": 0,
    "raw": 1,
    "unique": 1,
    "duplicate": 0,
    "retained": 1
  },
  "derivedAuditArtifacts": {
    "dir": "run-artifacts/20260705-170000-abcd",
    "files": {
      "derive_input": "run-artifacts/20260705-170000-abcd/05-derive/00-input.json",
      "derive_public_candidates": "run-artifacts/20260705-170000-abcd/05-derive/05-public-candidates.json"
    }
  }
}
```

完整评分、证据、验收标准、去重信息、父资产追溯信息和 prompt/source material 不通过 WebSocket 全量返回；它们写入 runtime 的 `run-artifacts/{task_id}/05-derive/` / `run-artifacts/{child_task_id}/05-derive-executor/`，不作为普通入库的额外 vault 文件。

### `derived_task_action_done` / `derived_task_action_rejected`

派生候选操作的确认或拒绝回包。`confirm` 成功后会返回 `childTaskId`；重复确认已经入队的候选时，服务端返回同一个 `childTaskId`，不会覆盖已存在的子任务状态。

```json
{
  "type": "derived_task_action_done",
  "requestId": "derived-1700000000000-abcd",
  "action": "confirm",
  "parentTaskId": "20260705-170000-abcd",
  "candidateId": "dt-xxxx",
  "childTaskId": "20260705-170000-abcd-derive-dt-xxxx",
  "timestamp": "2026-07-05T19:00:00"
}
```

```json
{
  "type": "derived_task_action_rejected",
  "requestId": "derived-1700000000000-abcd",
  "parentTaskId": "20260705-170000-abcd",
  "candidateId": "dt-xxxx",
  "reason": "target_url_required",
  "message": "这个候选需要先补充目标 URL",
  "timestamp": "2026-07-05T19:00:00"
}
```

### `vault_status` / `model_status` / `config_synced` / `cookie_synced`

分别确认知识库识别、模型健康检查、配置落盘、Cookie 落盘。

## 边界

抖音入库可以从 Agent 会话或扩展按钮提交。无论入口在哪里，业务执行都固定走
Agent 本地执行层，并固定使用 `quality` 档。扩展不提交可选入库意图，也不展示、
不保存、不发送拆解质量、fps 或抽帧参数。
