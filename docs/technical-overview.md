# Agent-wiki 当前技术总览

> 本文档只描述当前实现。产品方向与长期边界以 `PROJECT_INTENT.md` 为准，现有技术结构不能自动成为未来路线。

Agent-wiki 是一个 Agent 驱动的个人知识资产系统。它把抖音视频/图文等外部内容沉淀进 Obsidian，让未来 AI 工作可以从本地知识资产出发。

当前运行结构由三部分组成：

- **Agent 运行说明与资产契约**：`SKILL.md`、`SCHEMA.md`、`rules/`、`templates/`，记录当前工具怎样运行和现有资产怎样写入。
- **手脚 Tools**：`deps/douyin/scripts/`、`scripts/`，负责下载、分析、写库和更新索引；不自动操作 vault Git。
- **辅助 Extension**：`chrome-extension/`，负责 Cookie、模型配置、任务入口和任务状态。
- **GitHub 服务**：`server/github_service.py`，负责 Device Flow、Keychain、官方 API、项目资产去重与刷新。
- **统一操作审计与诊断**：`server/operation_audit.py`，负责跨扩展、WebSocket、服务、子进程、外部接口和文件写入的关联、脱敏、持久化与查询。

Chrome 扩展不是主产品，也不直接拆解内容；真正的编排和执行在 Agent 本地工具链里。

## 统一操作时间线

每次扩展请求生成 `operationId`，任务分配后同时关联 `taskId`；重试、派生任务和 GitHub 批次子项用 `parentId` 建立父子关系。服务端和 `StatusWriter` 共用 `OperationAuditStore`，因此控制面、队列、worker、子进程、下载/媒体探测、模型阶段、写库和最终回复写入同一条有序时间线。服务重启会为未结束 operation 写入 `service_restart_recovery`，再由现有 inbox/GitHub 持久任务恢复执行。

```text
~/.agent-wiki/operations/
├── index.jsonl
└── by-id/<operationId>/
    ├── summary.json
    └── timeline.jsonl
```

事件包含 `timestamp`、`operationType`、`stage`、`state`、`durationMs`、脱敏参数摘要、结果摘要、结构化错误、相关任务/资产/批次和 artifact 引用。API Key、Cookie、Authorization、GitHub token、设备码/用户码、完整认证响应和模型 response ID 不写入该目录。

视频和派生现有 `run-artifacts/` 仍保存大 prompt、完整响应与详细产物；统一时间线只保存计数、长度、Token/成本摘要和文件引用，不复制第二套大日志。

## 扩展与服务版本对齐

扩展 manifest 是当前产品版本的单一来源；background 和 popup 都通过 `chrome.runtime.getManifest().version` 读取，不各自维护版本常量。WebSocket 服务启动时读取同一 manifest，并通过 `agent_ready`、`handshake_ack` 和 `status_snapshot.status.runtime` 暴露：

- 产品与产品版本
- 当前协议版本
- Git commit（可用时）和服务源码内容指纹
- 不含真实路径的部署状态枚举

扩展会显示扩展版本、服务版本、协议和服务源码标识。只有产品、版本、协议和部署状态全部匹配时，双方才允许配置、Cookie、模型检查、入库与派生写操作。旧服务缺少完整运行身份时仍可读取状态用于诊断，但 UI 会明确提示并暂停写操作；旧扩展连接新服务时也会被服务端握手门禁拒绝。

服务只按目录名识别已知旧源码路径风险，不把本地源码路径发给扩展，也不会创建旧路径兼容目录。部署判定保留启动时可见路径，因此通过旧目录名符号链接启动也会标记为 `legacy_path`；manifest、Git commit 和源码指纹仍从解析后的 canonical 仓库读取。若出现旧部署提示，应停止旧进程并从当前 `Agent-wiki` 仓库启动。

## 当前主链路

抖音视频入库走字节跳动火山方舟 Ark：

```text
下载视频
-> Files API 上传
-> 等待 file active
-> Responses API + input_video.file_id + store=true
-> 生成派生任务候选与自动资格
-> 串行写入来源资产与 index.md
-> 来源资产成功后，高置信 GitHub 派生候选自动进入派生队列
-> 返回写入与派生状态（Git 由独立流程管理）
```

扩展提交任务时只发送 URL 和页面线索。服务端固定按 `ingest_intent: knowledge_ingest` 入队；每个任务生成一份来源知识资产并写入 `知识资产/知识入库/`。

普通入库、派生执行、GitHub 首次写入与确认刷新共享同一套跨进程 vault 写锁。任务可并发下载和分析，但资产、索引与父子关系的读改写按 vault 串行，避免不同 worker 互相覆盖 `index.md`。派生重试发现子资产已存在时会补齐索引和真实父子关系，不重复生成资产。

## 派生任务候选

`knowledge_ingest` 会让主分析模型输出结构化派生候选。筛选核心是对象是否为来源的主要介绍对象，并结合证据强度、可执行性、资产用途、歧义和安全性判断；顺带提及留在来源正文或审计记录。所有符合条件的高置信 GitHub 主要项目都可进入派生执行，不按候选数量截断。

评分维度包括知识价值、父资产依赖度、证据强度、可执行性、时效核验必要性、新颖度、资产适配度、成本风险和歧义度。候选和自动派生都不设固定数量上限。缺目标、证据不确定、重复、需要登录/付费、URL 不安全或不是主要介绍对象的线索会被压到 `suppressed` 审计记录里；“案例”等单一关键词不会直接否决候选。

GitHub 候选不强制要求视频里出现链接；如果只有项目名，派生工具会用 GitHub API 搜索仓库、读取 README，并把 README/描述与视频上下文对齐。能唯一高置信匹配时自动补全目标并执行；匹配不唯一时降级为人工补链接。

完整候选记录写入 runtime `run-artifacts/`，不作为普通入库的额外 vault 文件。

候选的确认、忽略、已入队和 child task 关联状态写入本地 sidecar：

```text
~/.agent-wiki/derived-actions/{parent_task_id}.json
```

每次派生还会写运行审计节点，方便回看程序为什么派生、为什么过滤、为什么选中某个目标：

```text
~/.agent-wiki/run-artifacts/{task_id}/05-derive/
~/.agent-wiki/run-artifacts/{child_task_id}/05-derive-executor/
```

`05-derive/` 记录分析正文输入、JSON 候选、Markdown fallback 候选、归一化候选、最终保留/过滤结果和公开投影；`05-derive-executor/` 记录派生子任务输入、GitHub/网页目标解析、来源材料、Lite prompt、原始输出、清洗后输出、写库结果和父子链接结果。

父资产正文只展示人类可读的派生状态表，frontmatter 不保存候选指针或完整对象。完整评分、证据、去重状态、验收标准、父资产追溯信息和调试节点都在 runtime artifacts 里。候选阶段不写未来 `[[wikilink]]`；只有派生子资产真正生成后，工具才回写父子资产 `related`/`derived_from` 链接，并把父资产对应状态行更新为真实子资产链接。

## Ark 视频策略

模型分工：

- Agent 模型：项目外部调用工具的决策模型，读取 `PROJECT_INTENT.md`、`AGENTS.md` 和 `SKILL.md`，决定何时调用当前工具；未来检索和维护方式不由本文档规定。
- `doubao-seed-2-0-lite-260428`：视频拆解工具的主分析模型，负责分片精拆、最终汇总、标题/摘要/标签候选、派生候选 JSON。
- `doubao-seed-2-0-mini-260428`：视频拆解工具的策略模型，负责长视频 `2fps` 全片/分片概览、分段 fps 决策、给 Lite 的精拆说明和概览 JSON 修复。

- 默认质量档：`quality`
- 安全目标：`1250` 帧
- Ark 硬上限：约 `1280` 帧
- 模型上传 fps：自动模式和固定模式都限制为 `2 - 5fps`
- 本地预扫描：仅自动模式运行，固定 `1fps`，只测画面变化，不调用模型
- 自动模式：按本地变化证据选择 `2/3/4/5fps`；预扫描失败保守回退 `5fps`
- 固定模式：支持 `fixed_2`、`fixed_3`、`fixed_5`，不运行本地预扫描
- `> 10 分钟`：进入长视频模式，先概览，再自动切片精拆
- 任意视频若所选 fps 会超过 `1250` 帧安全目标，也会切片，以保留采样密度

长视频切片：

- `10 分钟 < duration <= 615s` 时，用全片 `2fps` 做概览，最多约 `1230` 帧，给 `1250` 帧安全目标留 20 帧余量
- `duration > 615s` 时，概览阶段也切片：每片用 `2fps` 粗拆，再合并生成全片分段策略；之后仍按正常长视频流程精拆
- 这让任意更长的视频都能沿同一套策略扩展：只是概览切片数量增加；实际仍受 `500MB` 文件安全上限、下载耗时、任务超时和模型上下文窗口限制
- 概览和策略由 mini 执行；mini 的核心产物不是最终知识笔记，而是给 Lite 的拆解作战说明：信息主要由什么承载、低 fps 是否会漏视觉/OCR/操作证据、精拆时该重点看什么
- fps 只服务视觉、字幕/OCR、操作、动作和短暂画面证据；知识密度、观点密度和论证复杂度进入 `lite_brief`，不再单独把片段推到 `4/5fps`
- 如果 JSON 格式坏掉，mini 最多修复一次
- 策略评估粒度为每片 `240s`、重叠 `10s`、步长 `230s`
- 精拆前把相邻同 fps 的策略片合并成按帧预算装满的分析片（`1250/fps` 秒，最长 `600s`，重叠 `10s`）：方舟按单次调用总帧数动态压缩，单片的视频 token 基本恒定，装满帧预算可按片数比例省钱
- 每片按策略使用 `2-5fps`
- 分片上传和精拆默认 `2` 路并发，避免长视频串行过慢
- 扩展里的“任务并发”控制同时处理多少个入库任务，不改变单个长视频内部的分片并发
- JSON 无效、缺段或缺必填字段属于结构兜底；低置信属于保守 fps 调整；两者在状态和日志里分开记录
- 修复失败和 fps 调整会写入 `~/.agent-wiki/logs/video-strategy-events.jsonl`
- 每次任务会写审计产物到 `~/.agent-wiki/run-artifacts/{task_id}/`，包括 mini 每段粗拆、mini 合成策略、修复前后策略、Lite 每段 prompt/输出、最终汇总 prompt/输出
- Responses 连接中断、超时、5xx/429 等可恢复错误会按调用级别自动重试，默认最多 `3` 次；400、鉴权、文件类型错误不会盲目重试
- 长视频精拆支持分片级断点复用：同一个 `task_id` 重跑时，如果 `03-lite/{intent}/part-xxx-output.md` 已存在且 prompt hash 匹配，会直接复用该分片，不再重复上传和分析
- 逐片分析后，再用全片概览和分片结果汇总为最终资产正文

## Responses 记忆

视频分析请求会使用 `store=true` 并保存返回的 `response_id`。

本地记忆位置：

```text
~/.agent-wiki/responses-memory/
```

记忆 key 按 `media_type + aweme_id/source_id + ingest_intent + model + prompt_hash + flow_version + chunked` 生成。不同来源、模型、prompt 和普通单文件/长视频分片链路使用独立记忆，避免上下文串味。

注意：

- `response_id` 不写入 Obsidian Markdown/frontmatter。
- `response_id` 不写入任务状态面板或策略日志，只保存在本地短期记忆索引。
- 本地短期记忆默认保存 `3` 天，避免超过 Ark Responses 默认保存期后继续复用失效上下文。
- 这是短期模型上下文，不是长期知识记忆。
- Files API 文件可用期和 Responses 记忆是两件事。

## 运行时目录

```text
~/.agent-wiki/
├── config.toml
├── cookie/
├── cache/
├── run/
│   ├── control-plane.pid
│   └── control-plane.json
├── status/
├── logs/
├── operations/
├── run-artifacts/
├── responses-memory/
└── extension/
```

视频下载使用任务私有缓存 `cache/videos/<task_id>/`：任务结束（成功、失败或取消）即删除该目录，视频不复制进知识库，资产只保留 `source_url` 等元数据。图文图片缓存仍位于 `cache/images/`，原始图片继续写入 vault 的 `raw/images/`。

敏感信息只放在本地 runtime，不写入项目文档、知识库笔记或最终回复。

## 快速开始

准备环境：

```bash
./agent-wiki install
```

隔离测试使用临时 `AGENT_WIKI_HOME` 时必须同时传 `--vault <临时目录> --skip-websocket-check`。显式 vault 即使是空目录也只初始化该目录；路径无效或指向 `.obsidian` 内部时直接失败，不会回退到 Obsidian 自动发现，也不会探测当前默认端口上的服务。

启动托管控制服务：

```bash
./agent-wiki start
./agent-wiki status
./agent-wiki doctor
```

服务日志写到 `~/.agent-wiki/logs/control-plane.log`。PID 与包含源码 commit/版本、启动标识和入口路径的元数据以私有权限写到 `run/`。停止服务前会重新核对这些身份字段，不会因为端口占用或相同进程名而停止其他进程。诊断、前台兼容运行和缓存 dry-run 见 `docs/runtime-operations.md`。

安装扩展：

```text
chrome://extensions/
-> 开发者模式
-> 加载已解压的扩展程序
-> 选择 ~/.agent-wiki/extension/
```

在扩展里按首次引导完成：

- 确认 Agent 本地服务已连接
- 填写用户自己的 Ark API Key、Endpoint 和可用模型 ID
- 通过系统文件夹选择器选择知识库
- 在已经登录抖音网页版的 Chrome 中同步 Cookie
- 按需完成 GitHub Device Flow 登录；该步骤可跳过

配置完成后可在首页提交当前抖音内容或分享链接进行 `知识入库`。模型调用、OAuth、Stars 和真实入库仍由用户在自己的账号与网络环境中验收。任务队列并发和长视频分片并发默认均为 2、范围均为 1-4。

Agent 或扩展提交抖音链接后，工具链会自动入库。

## Agent Plan 取舍

Agent Plan 已验证过小视频 inline base64 可以调用 `/api/plan/v3/responses`，但它没有可用的 Files API 链路：

- `/api/plan/v3/files`：404
- Agent Plan Key 调 `/api/v3/files`：401
- fake `file_id`：失败
- base64 小视频：成功

因此当前产品运行通道删除 Agent Plan，只保留普通 Ark。旧 `provider = "volcengine_agent_plan"` 会回落为 `doubao`，但旧 Agent Plan Key 不会自动迁移为普通 Ark Key。

## 验证

```bash
python3.11 scripts/release_audit.py
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py server/runtime_manager.py server/service_entry.py server/launcher.py install/bootstrap.py scripts/release_audit.py
python3.11 tests/test_runtime_manager.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
python3.11 tests/test_runtime_version_protocol.py
python3.11 tests/test_ci_integration.py
python3.11 tests/test_release_audit.py
python3.11 tests/test_github_service.py
python3.11 tests/test_github_protocol.py
node tests/test_extension_runtime_version.js
node tests/test_extension_contract.js
node tests/test_github_extension_contract.js
node --check chrome-extension/background.js
node --check chrome-extension/runtime-version.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

公开发布前还需运行 `python3.11 scripts/release_audit.py --history`。

更多接口细节见：

- `docs/ark-video-understanding.md`
- `docs/websocket-protocol.md`
- `deps/douyin/SKILL.md`
