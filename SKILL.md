---
name: agent-wiki
description: "给 Agent 看的当前运行说明：记录已经实现的抖音入库、配置与 Cookie 同步、知识库写入方式和安全边界；产品方向以 PROJECT_INTENT.md 为准。"
---

# Agent-wiki

> 这是给 Agent 看的当前工具运行说明，不是产品基准线、开发计划或项目回顾。

产品方向先读 `PROJECT_INTENT.md`，开发文档的权威边界先读 `AGENTS.md`。本文只说明当前已经实现的工具怎样运行；其中的现有行为不能自动变成未来路线。

当前实现以 Agent 为主要执行面，工具负责下载、分析、写库、索引和提交，Chrome 扩展负责提供配置、Cookie、任务入口和状态等辅助能力。

## 文档职责和阅读顺序

1. `PROJECT_INTENT.md`：唯一长期产品基准。
2. `AGENTS.md`：开发 AI 的入口和文档权威边界。
3. 本文与 `deps/douyin/SKILL.md`：当前工具的运行说明。
4. `SCHEMA.md`、`templates/` 和 `rules/`：当前知识资产的结构与写入契约。
5. `docs/`、代码和测试：当前技术事实；发生差异时以代码和测试核实实际行为。

## 当前标准工作流

当用户在 Agent 会话里发来抖音链接时，统一按“知识入库”路径走。扩展和命令行只提交来源线索，执行层固定记录 `ingest_intent = knowledge_ingest`，同一来源生成一份独立的来源知识资产。

1. 先跑 `python3 install/bootstrap.py`
2. 如果发现缺 `API Key` 或 `Cookie`，不要让用户把秘密贴进聊天；只提示去扩展里补
3. 首次使用先由扩展扫描 Obsidian 根位置并创建 `<Obsidian 根>/<用户名称>/` 空白 Agent-wiki 知识库；不得自动接管已有 vault
4. 再跑 `python3 scripts/ingest_url.py "<douyin-url>"`，该入口固定走 `quality` 档
5. 工具链自动下载、分析、写入 `知识资产/知识入库/` 并更新 `index.md`；不自动初始化、暂存或提交 vault Git
6. 最后只回用户：写入路径、结果摘要和派生状态

## 资产模型

当前采用双轴模型：

- **来源维度**：内容从哪里来，例如 `douyin_video`、`douyin_image_post`、`webpage`、`github`、`manual`
- **资产用途维度**：它被沉淀成什么，例如 `knowledge_asset`、`github_project`、`code_module`、`idea_asset`

目录按用途分区，不按来源分区：

```text
知识资产/
├── 知识入库/   --- 来源知识、工具、方法、步骤、风险
├── GitHub项目/ --- 已执行的 GitHub 派生资产
├── 网页剪藏/   --- 已执行的网页派生或手动资产
└── 代码模块/   --- 代码模块资产
```

frontmatter 必须同时记录 `asset_family`、`source_media`、`ingest_intent` 和 `source_url`。抖音视频和图文来源统一记录 `asset_family: knowledge_asset` 与 `ingest_intent: knowledge_ingest`，并保留可追溯的来源字段。

## 运行态

当前运行态默认落在：

```text
~/.agent-wiki/
├── config.toml      --- 扩展写入，Agent 读取
├── vault-registry.json --- 当前库的用户名称、规范化路径和稳定身份
├── vault-migrations/   --- 迁移预览、校验和回退记录
├── cookie/
├── cache/
├── run/             --- 托管服务 PID 与进程身份元数据
├── status/
├── logs/
├── operations/      --- 跨入口结构化操作时间线、索引与诊断摘要
├── run-artifacts/   --- 视频/派生的大 prompt、完整响应与详细产物
└── extension/
```

其中：

- `config.toml` 保存 Ark 配置、Agent 已确认的 vault 路径、分析参数
- `cookie/douyin.txt` 保存抖音 Cookie
- `status/` 保存运行状态
- `logs/` 保存诊断日志
- `operations/index.jsonl` 和 `operations/by-id/<operationId>/` 保存严格脱敏的统一操作索引、摘要与事件时间线；大 prompt/完整响应仍只放 `run-artifacts/`

首次使用只自动扫描 Obsidian 根位置，用于建议新库的父目录；不会自动选择、改写或迁移扫描到的旧 vault。空白新库只创建索引、必要资产目录和 `.agent-wiki-vault.json` 稳定身份标记。路径变化后同时匹配用户名称和身份重连；多路径命中时等待用户确认。已有知识库只能通过迁移预览、复制、完成校验后切换，来源始终保留。

## 控制面

需要配置同步或 Cookie 同步时，启动 WebSocket 控制服务：

```bash
python3.11 server/launcher.py start
python3.11 server/launcher.py status
```

停止或重启服务时使用 `stop` / `restart`。launcher 只有在 PID、私有元数据、进程启动标识、Python 路径和服务入口全部一致时才发送信号；端口被未知进程占用时只报告，不尝试清理。`python3.11 server/launcher.py doctor` 可检查本地环境，且不会读取 Cookie 内容或 `.obsidian/` 内容。缓存命令只提供报告与 `cache clean --dry-run` 预览，不执行删除。

控制面接受这几类消息：

| 方向 | 消息 | 用途 |
|---|---|---|
| 扩展 -> Agent | `handshake` | 连接检查 |
| 扩展 -> Agent | `config_update` | 写完整 `config.toml` |
| 扩展 -> Agent | `status_request` | 拉取 Agent / vault / 模型 / Cookie 状态 |
| 扩展 -> Agent | `vault_scan` / `vault_create` | 扫描 Obsidian 根位置并创建独立空白知识库 |
| 扩展 -> Agent | `vault_switch` / `vault_candidate_confirm` | 按稳定身份切换或确认歧义候选 |
| 扩展 -> Agent | `vault_migration_preview` / `vault_migration_execute` / `vault_migration_rollback` | 预览、复制校验、切换和回退已有知识库 |
| 扩展 -> Agent | `model_check` | 轻量检查模型配置是否可连接 |
| 扩展 -> Agent | `cookie_update` | 写 Douyin Cookie 文件 |
| 扩展 -> Agent | `task_request` | 提交知识入库来源和页面线索 |
| 扩展 -> Agent | `task_status_request` | 拉取任务进度 |
| 扩展 -> Agent | `task_cancel` / `task_retry` | 取消运行/排队任务，或从终态创建有关联的新重试任务 |
| 扩展 -> Agent | `operation_diagnostics_request` | 按 operationId 查询持久化诊断时间线 |
| 扩展 -> Agent | `github_*` | GitHub Device Flow、仓库搜索、Stars 导入与手动刷新 |
| Agent -> 扩展 | `agent_ready` | 服务已连接 |
| Agent -> 扩展 | `status_snapshot` | 当前状态快照 |
| Agent -> 扩展 | `vault_lifecycle_status` | 统一知识库生命周期结果 |
| Agent -> 扩展 | `model_status` | 模型检查结果 |
| Agent -> 扩展 | `config_synced` | 配置已写入 |
| Agent -> 扩展 | `cookie_synced` | Cookie 已写入 |
| Agent -> 扩展 | `task_accepted` / `task_rejected` | 任务进入队列或被拒绝 |
| Agent -> 扩展 | `task_status_snapshot` | 任务进度快照 |
| Agent -> 扩展 | `task_control_done` / `task_control_rejected` / `operation_diagnostics` | 任务控制和诊断查询结果 |
| Agent -> 扩展 | `github_status` / `github_*_results` | GitHub 登录、列表、批量进度与刷新结果 |

`task_request` 只能提交 URL 和页面线索，不能提交 Cookie、API Key、质量档或业务编排步骤。

扩展请求和服务回复携带统一 `operationId/taskId/parentId`。任务状态、GitHub 批次与知识库生命周期结果会公开 operation 或诊断位置；服务重启后仍可从 `operations/` 查询。任何 Cookie、API Key、Authorization、GitHub token、设备码/用户码或完整认证响应都不得进入该时间线。

## 扩展只做什么

扩展只做辅助，不做主编排：

- 抓取 Cookie
- 保存普通 Ark API Key 和 Endpoint
- 保存视频拆解设置：Lite/Mini 主分析模型、任务队列并发、长视频分片并发
- 展示知识库根扫描、新建、切换、迁移预览和候选确认流程
- 显示连接状态
- 提供任务入口：`知识入库`
- 展示任务进度和结果状态
- 提供 GitHub 网页授权、Stars 选择导入和手动刷新确认

扩展不负责：

- 取代 Agent
- 充当业务调度器
- 决定最终资产分类、目录、标签、派生任务
- 展示或发送质量档、fps、抽帧参数

## 工具层怎么用

视频入库的主入口是：

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

它会自动：

1. 跑 bootstrap
2. 读 `~/.agent-wiki/config.toml`
3. 用 Cookie 下载视频
4. 固定按 `quality` 档调 Ark 做视频分析
5. 按 `SCHEMA.md` 写入 vault 的 `知识资产/知识入库/`
6. 更新 `index.md`
7. 保持已有 Git 历史不动，版本控制由用户或独立备份流程管理

## 写入规则

工具链写库时必须满足：

- frontmatter 服从 `SCHEMA.md`
- 标题、标签、索引都要中文优先
- 不写真实密钥、Cookie、token、session、日志原文
- 写完必须更新 `index.md`
- 不自动执行 `git init`、`git add` 或 `git commit`

## 红线

1. 不要把 `.obsidian/` 当普通目录处理
2. 不要把秘密写进任何 markdown、frontmatter、日志、回复
3. 不要让用户为了使用而去开终端、填配置文件、手动跑脚本
4. 不要把扩展写成主产品
5. 不要把历史资料当当前口径

## 已经被替代的旧说法

历史上的文件桥、Downloads 轮询、扩展直接执行入库，属于旧演进记录，不是当前实现口径。
如果看到这类内容，把它当历史资料，不要当成现在的正确答案。

## 需要时再读的当前资料

- `SCHEMA.md`：当前知识资产结构和字段契约
- `deps/douyin/SKILL.md`：当前抖音视频与图文工具说明
- `docs/technical-overview.md`：当前技术结构概览
- `docs/websocket-protocol.md`：当前控制面协议

## 验证

如果你要确认这套说明书和工具链是否还对得上，跑：

```bash
python3.11 tests/test_p0_static.py
```

如果要看当前配置是否能落盘、WebSocket 是否能写入、Cookie 权限是否正常，就优先看 `server/websocket_server.py` 和 `deps/douyin/scripts/config_loader.py` 的真实字段名，不要凭旧记忆猜。

GitHub 联动的 mock 测试使用：

```bash
python3.11 tests/test_github_backend.py
python3.11 tests/test_github_service.py
python3.11 tests/test_github_protocol.py
python3.11 tests/test_no_vault_git.py
node tests/test_github_extension_contract.js
```
