# WebSocket 控制面协议

> 扩展只做辅助控制台和任务入口。入库任务可以由扩展提交，但下载、
> 分析、写库和状态仍由 Agent 本地执行层完成；vault Git 不由入库任务自动操作。

## 连接

- URL: `ws://127.0.0.1:8765`
- 格式：JSON text message
- Origin：允许 Chrome 扩展和本地无 Origin 测试客户端；拒绝普通网页 Origin
- 敏感信息：服务端状态响应不得返回 API Key、Cookie、Bearer token
- 当前产品版本：`0.2.1`
- 当前协议版本：`1`

连接建立后可以先读取状态，但配置、Cookie、模型检查、入库和派生操作必须通过版本握手。新服务会拒绝旧扩展的写操作；新扩展连接缺少完整运行身份的旧服务时，只保留状态诊断并暂停同步与入库。

## 统一操作关联信封

当前扩展发出的每个有业务意义的请求都包含以下字段，服务端回复会继承同一组字段：

```json
{
  "operationId": "task_request-7b91...",
  "taskId": "",
  "parentId": "",
  "requestId": "task_request-1700000000000-abcd"
}
```

- `operationId`：一次用户可见操作的唯一标识。扩展生成；缺失时服务端补齐。
- `taskId`：已经存在的任务、GitHub 批次子项或授权流标识。新任务由服务端接受后分配，并写回公开状态。
- `parentId`：重试、派生子任务和 GitHub 批次子项的父 operation；没有父操作时为空字符串。
- `requestId`：控制面请求/回复匹配键，不替代 `operationId`。

服务端将关联信息写入 `~/.agent-wiki/operations/`。请求中的 API Key、Cookie、Authorization、GitHub token、`device_code`、`user_code`、完整认证响应和模型 response ID 在写审计前严格脱敏；`cookie_update.data` 只记录是否存在和字符数。普通 URL 会删除 token/key/secret/signature/code 等敏感 query。

## 扩展 -> Agent

### `handshake`

```json
{
  "type": "handshake",
  "client": "agent-wiki-extension",
  "product": "agent-wiki",
  "version": "0.2.1",
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

### `vault_discover`（旧扩展兼容）

旧消息不再自动识别、持久化或接管已有 vault，只返回应升级到正式知识库生命周期 API 的兼容状态。新 UI 使用 `vault_scan`。

```json
{
  "type": "vault_discover",
  "hint": "<home>/Library/Mobile Documents/iCloud~md~obsidian/Documents"
}
```

### `vault_pick`（旧扩展兼容）

旧消息不再把任意文件夹直接写成当前知识库。新 UI 通过系统选择器取得父目录后，把绝对路径作为 `vault_create.data.parentDirectory` 或 `vault_migration_preview.data.parentDirectory` 发送。

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

### `task_cancel` / `task_retry`

`task_cancel` 可取消排队或运行中的通用任务；运行中的子进程会收到终止信号，取消终态和错误批次子项都会持久化。`task_retry` 只接受已经结束的任务，复制原任务的非敏感来源线索生成新的 `taskId` 和 operation，并用 `parentId` 指向原 operation。

```json
{ "type": "task_cancel", "taskId": "20260716-004000-abcd" }
```

```json
{
  "type": "task_retry",
  "taskId": "20260716-004000-abcd",
  "parentId": "task_request-original-operation"
}
```

成功返回 `task_control_done`；任务不存在、仍在运行而请求重试、或已结束而请求取消时返回 `task_control_rejected`。弹窗任务卡对运行中任务显示取消，对失败/已取消任务显示重试。

### `operation_diagnostics_request`

按 operation 查询持久化摘要与完整结构化时间线。该消息是只读诊断请求，版本不匹配时仍允许发送。

```json
{
  "type": "operation_diagnostics_request",
  "targetOperationId": "task_request-7b91..."
}
```

返回 `operation_diagnostics`。`result.summary` 是当前投影，`result.events[]` 按 `sequence` 排序；`result.diagnostics` 给出本机索引、摘要和时间线位置。

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
  "version": "0.2.1",
  "protocolVersion": 1,
  "runtime": {
    "product": "agent-wiki",
    "productVersion": "0.2.1",
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
    "task_control",
    "operation_audit_v1",
    "operation_diagnostics",
    "derived_task_action",
    "github_device_flow",
    "github_repository_search",
    "github_star_import",
    "github_manual_refresh",
    "github_repository_dedupe"
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
    "productVersion": "0.2.1",
    "protocolVersion": 1,
    "sourceRevision": "3c7ea9e0158a",
    "buildId": "src-0123456789abcdef",
    "deployment": { "state": "current", "code": "source_checkout" }
  },
  "compatibility": {
    "state": "compatible",
    "canOperate": true,
    "message": "扩展、服务与协议版本一致。",
    "clientVersion": "0.2.1",
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
  "message": "扩展 v0.0.9 与服务 v0.2.1 不一致。",
  "runtime": { "product": "agent-wiki", "productVersion": "0.2.1", "protocolVersion": 1 }
}
```

只读的 `status_request`、`task_status_request` 和 `operation_diagnostics_request` 不受该门禁影响，便于诊断旧部署。

### `status_snapshot`

```json
{
  "type": "status_snapshot",
  "status": {
    "runtime": {
      "product": "agent-wiki",
      "productVersion": "0.2.1",
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
      "cancelled": 0,
      "done": 3,
      "items": [
        {
          "id": "20260702-233000-abcd",
          "operationId": "task_request-7b91...",
          "parentId": "",
          "diagnostics": {
            "index": "/Users/.../.agent-wiki/operations/index.jsonl",
            "timeline": "/Users/.../.agent-wiki/operations/by-id/task_request-7b91.../timeline.jsonl"
          },
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

每个任务项固定公开 `operationId`、`parentId` 和 `diagnostics`。GitHub 导入批次及 `items[]` 也公开各自的 `operationId`/`parentId`，因此弹窗关闭或服务重启后仍可从一个 operation 还原父批次与子项失败。

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

### 知识库生命周期消息

知识库生命周期使用统一响应类型 `vault_lifecycle_status`，请求名和输入如下：

| 请求 `type` | `data` 必填字段 | `data` 可选字段 | 含义 |
| --- | --- | --- | --- |
| `vault_scan` | 无 | `userName`, `parentHints[]` | 扫描 Obsidian 根位置和带身份标记的 Agent-wiki 库；不会切换到普通旧库 |
| `vault_create` | `userName`，以及 `obsidianRoot` / `parentDirectory` 二选一 | 无 | 在所选父目录下创建 `<userName>/` 空白新库并切换 |
| `vault_switch` | `vaultPath` | `expectedVaultId` | 只切换到身份标记有效的 Agent-wiki 库；普通旧库返回 `migration_required` |
| `vault_candidate_confirm` | `candidateId`, `action` | `userName`, `obsidianRoot`, `parentDirectory` | 确认扫描候选；`action` 为 `create`、`switch` 或 `migrate` |
| `vault_migration_preview` | `sourcePath`, `userName`，以及 `obsidianRoot` / `parentDirectory` 二选一 | 无 | 生成复制计划、内容摘要和冲突清单，不写目标、不切换 |
| `vault_migration_execute` | `migrationId` | 无 | 重新核对来源摘要，复制到 staging，逐文件校验后才切换 |
| `vault_migration_rollback` | `migrationId` | 无 | 回退当前库选择；来源和已迁移目标都保留 |

`obsidianRoot` 与 `parentDirectory` 在后端都表示新知识库目录的直接父目录；字段名区分自动扫描到的 Obsidian 根位置和用户手动选择的父目录。二者不能同时出现。新库路径由后端规范化为绝对路径 `<父目录>/<userName>`，名称校验和目录名拼接只在生命周期模块中完成。

统一回包：

```json
{
  "type": "vault_lifecycle_status",
  "requestId": "vault-...",
  "result": {
    "contractVersion": 1,
    "ok": true,
    "operation": "create",
    "state": "created",
    "requiresUserAction": false,
    "message": "A new empty Agent-wiki vault was created and activated.",
    "activeVault": {
      "vaultId": "5e9c...",
      "userName": "Alice",
      "vaultPath": "/Users/alice/Obsidian/Alice",
      "identityMarker": ".agent-wiki-vault.json"
    },
    "obsidianRoots": [],
    "vaultCandidates": [],
    "migration": null
  },
  "timestamp": "2026-07-15T10:00:00"
}
```

所有响应固定包含 `contractVersion`、`ok`、`operation`、`state`、`requiresUserAction`、`message`、`activeVault`、`obsidianRoots`、`vaultCandidates`、`migration`。失败时可额外包含稳定的 `errorCode`。

字段边界：

- `obsidianRoots[].obsidianRoot` 是新库的父目录；`suggestedVaultPath` 是按当前 `userName` 计算的建议绝对路径。
- `vaultCandidates[].vaultPath` 是具体知识库目录，不是 Obsidian 根目录。
- `activeVault.vaultId` 与库根目录的 `.agent-wiki-vault.json` 一致；安全重连同时匹配 `userName` 和 `vaultId`。
- `vaultCandidates[].kind` 为 `agent_wiki_vault`、`existing_obsidian_vault` 或 `obsidian_root`；普通旧库只支持 `migrate`，不能直接 `switch`。
- 同一名称和身份出现多个路径时返回 `state = "ambiguous"`，不自动切换；UI 用 `vault_candidate_confirm` 确认一个候选。
- `migration` 包含 `migrationId`、`sourceVault`、`targetVault`、`copyMode`、`sourcePreserved`、`fileCount`、`directoryCount`、`totalBytes`、`sourceDigest`、`excludedNames`、`conflicts`、`canExecute` 与 `rollbackAvailable`。

常见 `state` 包括 `first_use`、`root_selection_required`、`root_ready`、`created`、`ready`、`disconnected`、`reconnected`、`ambiguous`、`switched`、`migration_required`、`migration_ready`、`migration_conflict`、`migration_stale`、`migrated`、`rollback_blocked`、`rolled_back` 和 `error`。

空白初始化只创建 `index.md`、`raw/`、`知识资产/知识入库/` 和 `.agent-wiki-vault.json`；不复制旧内容、仓库 `rules/` / `templates/` / `SCHEMA.md`，不创建 `.obsidian/` 或 `.git/`。迁移复制用户内容，但任何层级的 `.obsidian/`、`.git/` 和来源身份标记都不读取或复制；目标使用新的唯一身份，避免在保留来源副本时产生重复身份。

旧响应 `vault_status` 仅保留给旧扩展兼容。新 UI 使用上述正式生命周期消息。`model_status`、`config_synced` 和 `cookie_synced` 分别确认模型健康检查、配置落盘和 Cookie 落盘。

## GitHub 联动消息

所有 `github_*` 请求都必须先通过版本握手。服务端响应不得包含 access token、device code、Authorization header 或完整 OAuth 响应。

### 登录与设置

- `github_status_request` -> `github_status`：校验 Keychain token 并返回配置状态、登录账号摘要、`autoStar`、非敏感的 `activeAuthorization`，以及持久化的 `activeImport`、`recentImports[]` 和 `recentTasks[]`。弹窗重新打开时用它恢复授权状态、活动批次、最近批次和资产创建/自动 Star 事件；响应永远不包含 device code 或 token。
- `github_auth_start` -> `github_auth_state`：返回 `flowId`、`userCode`、GitHub 官方 `verificationUri`、轮询间隔和过期时间。
- 本地服务收到 `github_auth_start` 后独立进行 token 轮询，不依赖扩展弹窗持续打开；授权完成后把 token 写入 Keychain，并向仍在线的扩展广播 `github_auth_state`。
- `github_auth_poll`：传 `flowId`；返回等待、成功、拒绝或超时状态。token 由服务端直接写入 macOS Keychain。
- `github_auth_cancel`：取消内存中的授权流程。
- `github_logout`：删除 Keychain token。

初始 `status_snapshot` 在版本握手通过前不会包含 `activeAuthorization`。后台轮询遇到短暂网络错误会广播带 `transient: true` 和 `flowId` 的 `github_error` 后继续退避重试；拒绝、过期和主动取消才会终止对应授权流程。
- `github_settings_update`：只接受布尔值 `autoStar`；默认关闭。

### 仓库搜索与 Stars

```json
{
  "type": "github_repository_search",
  "requestId": "github-search-...",
  "query": "langgraph",
  "page": 1,
  "perPage": 20
}
```

`github_repository_results` 返回分页、总数和公开仓库摘要。已入库项额外带 `ingested` 与相对 `assetPath`。限流统一返回 `github_error`，其中 `code = "rate_limited"`，并可带 `retryAfter`。

`github_stars_request` / `github_stars_results` 使用同样的仓库公开投影，并返回 `hasNext`。私有仓库不会进入列表。

### Stars 批量导入

```json
{
  "type": "github_import_stars",
  "repositories": [
    {"id": 123, "fullName": "owner/repo"}
  ]
}
```

服务端重新向 GitHub API 按 ID 读取每个仓库，不信任扩展提交的元数据。`github_import_accepted` 返回持久批次；后续 `github_import_progress` 包含 `total`、`completed`、`succeeded`、`existing`、`failed`、`cancelled`、`items[]` 和兼容用的终态 `results[]`。`items[]` 始终保留每个子任务的 `taskId`、`state`、仓库身份和非敏感结果，因此弹窗关闭后仍能恢复 queued/running/terminal 明细。

`github_import_status` 接受 `batchId` 并返回同形的 `github_import_progress`。本地服务重启时会把中断的 running 父任务和子任务恢复为 queued，并继续未完成项；UI 在 `github_status.activeImport` 出现后会主动读取一次批次状态。`github_import_cancel` 只取消尚未开始的项，不回滚已经成功写入的资产。

### 手动刷新

`github_refresh_check` 接受 `{id, fullName}`，只比较资料，不写资产。无变化返回 `state = "no_changes"`；有变化返回 `state = "confirmation_required"`、`refreshId` 和字段摘要。

只有 `github_refresh_confirm` 能使用 `refreshId` 一次性应用这批变化。`github_refresh_cancel` 删除待确认快照。确认 15 分钟后失效，必须重新检查。

### 去重与自动 Star

所有入口共享 repository ID 优先、规范化 `owner/repo` 兜底的登记表。正式 GitHub 派生复用统一适配器的 README 清理和三段正文校验，并在资产与索引成功写入后调用同一登记/事件钩子。自动 Star 只发生在正式派生成功之后；Stars 导入不重复 Star，GitHub Star API 失败也不改变知识入库成功状态。GitHub 首次写入、Stars 导入和确认刷新都不会执行自动 Git 操作。

## 边界

抖音入库可以从 Agent 会话或扩展按钮提交。无论入口在哪里，业务执行都固定走
Agent 本地执行层，并固定使用 `quality` 档。扩展不提交可选入库意图，也不展示、
不保存、不发送拆解质量、fps 或抽帧参数。
