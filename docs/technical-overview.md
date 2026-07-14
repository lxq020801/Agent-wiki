# Agent-wiki 当前技术总览

> 本文档只描述当前实现。产品方向与长期边界以 `PROJECT_INTENT.md` 为准，现有技术结构不能自动成为未来路线。

Agent-wiki 是一个 Agent 驱动的个人知识资产系统。它把抖音视频/图文等外部内容沉淀进 Obsidian，让未来 AI 工作可以从本地知识资产出发。

当前运行结构由三部分组成：

- **Agent 运行说明与资产契约**：`SKILL.md`、`SCHEMA.md`、`rules/`、`templates/`，记录当前工具怎样运行和现有资产怎样写入。
- **手脚 Tools**：`deps/douyin/scripts/`、`scripts/`，负责下载、分析、写库、更新索引、git commit。
- **辅助 Extension**：`chrome-extension/`，负责 Cookie、模型配置、任务入口和任务状态。

Chrome 扩展不是主产品，也不直接拆解内容；真正的编排和执行在 Agent 本地工具链里。

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
-> 写入 Obsidian
-> 高置信 GitHub 派生候选自动进入派生队列
-> 更新 index.md
-> git commit
```

扩展提交任务时只发送 URL 和页面线索。服务端固定按 `ingest_intent: knowledge_ingest` 入队；每个任务生成一份来源知识资产并写入 `知识资产/知识入库/`。

## 派生任务候选

`knowledge_ingest` 会让主分析模型输出结构化派生候选，但派生链路默认收紧：只有父资产核心结论明显依赖、证据强、目标可执行的线索才会进入可见候选。执行层会按评分机制筛选、去重、限流，并把高置信、低风险、可解析的 GitHub 项目候选自动交给派生工具执行；弱证据、缺 URL 的官方文档/网页研究和“以后可能有用”的泛研究想法只保留在审计记录中，不进入扩展候选列表。

评分维度包括知识价值、父资产依赖度、证据强度、可执行性、时效核验必要性、新颖度、资产适配度、成本风险和歧义度。默认最多保留 `3` 个可见强候选；每个父任务最多自动派生 `3` 个 GitHub 项目。缺目标、证据不确定、重复、需要登录/付费、URL 不安全、非关键官方文档或泛网页研究会被压到 `suppressed` 审计记录里。

GitHub 候选不强制要求视频里出现链接；如果只有项目名，派生工具会用 GitHub API 搜索仓库、读取 README，并把 README/描述与视频上下文对齐。能唯一高置信匹配时自动补全目标并执行；匹配不唯一时降级为人工补链接。

完整候选记录写入：

```text
系统记录/派生任务候选/*.json
```

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

父资产 Markdown 只保留轻量指针：

- `derived_candidate_record`
- `derived_candidate_ids`

正文只展示人类可读的候选摘要表。完整评分、证据、去重状态、验收标准、父资产追溯信息和调试节点都在系统记录 JSON / runtime artifacts 里，避免污染正式知识资产 frontmatter。候选阶段不写未来 `[[wikilink]]`；只有派生子资产真正生成后，工具才回写父子资产 `related`/`derived_from` 链接，让 Obsidian 图谱连接到真实存在的笔记。

## Ark 视频策略

模型分工：

- Agent 模型：项目外部调用工具的决策模型，读取 `PROJECT_INTENT.md`、`AGENTS.md` 和 `SKILL.md`，决定何时调用当前工具；未来检索和维护方式不由本文档规定。
- `doubao-seed-2-0-lite-260428`：视频拆解工具的主分析模型，负责分片精拆、最终汇总、标题/摘要/标签候选、派生候选 JSON。
- `doubao-seed-2-0-mini-260428`：视频拆解工具的策略模型，负责长视频 `1fps` 全片/分片概览、分段 fps 决策、给 Lite 的精拆说明和概览 JSON 修复。

- 默认质量档：`quality`
- 安全目标：`1250` 帧
- Ark 硬上限：约 `1280` 帧
- fps 范围：普通视频 `0.2 - 5`，长视频概览最高 `1fps`，长视频分片精拆 `2 - 5fps`
- `<= 250s`：保持 `5fps`
- `> 250s`：普通单文件视频按 `1250 / 视频秒数` 下调 fps
- `> 10 分钟`：进入长视频模式，先概览，再自动切片精拆
- 超长视频：`> 1230s / 20m30s`，给全片 `1fps` 概览的 `1250` 帧安全目标留 20 秒余量

长视频切片：

- `10 分钟 < duration <= 1230s` 时，用全片 `1fps` 做概览，提取粗内容、粗时间线和分段精拆策略
- 超长视频把概览阶段也切片：每片用 `1fps` 粗拆，再合并生成全片分段策略；之后仍按正常长视频流程精拆
- 这让任意更长的视频都能沿同一套策略扩展：只是概览切片数量增加；实际仍受 `500MB` 文件安全上限、下载耗时、任务超时和模型上下文窗口限制
- 概览和策略由 mini 执行；mini 的核心产物不是最终知识笔记，而是给 Lite 的拆解作战说明：信息主要由什么承载、低 fps 是否会漏视觉/OCR/操作证据、精拆时该重点看什么
- fps 只服务视觉、字幕/OCR、操作、动作和短暂画面证据；知识密度、观点密度和论证复杂度进入 `lite_brief`，不再单独把片段推到 `4/5fps`
- 如果 JSON 格式坏掉，mini 最多修复一次
- 每片 `240s`
- 重叠 `10s`
- 步长 `230s`
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
├── run-artifacts/
├── responses-memory/
└── extension/
```

敏感信息只放在本地 runtime，不写入项目文档、知识库笔记或最终回复。

## 快速开始

准备环境：

```bash
python3 install/bootstrap.py
```

启动托管控制服务：

```bash
python3.11 server/launcher.py start
python3.11 server/launcher.py status
```

服务日志写到 `~/.agent-wiki/logs/control-plane.log`。PID 与包含源码 commit/版本、启动标识和入口路径的元数据以私有权限写到 `run/`。停止服务前会重新核对这些身份字段，不会因为端口占用或相同进程名而停止其他进程。诊断、前台兼容运行和缓存 dry-run 见 `docs/runtime-operations.md`。

安装扩展：

```text
chrome://extensions/
-> 开发者模式
-> 加载已解压的扩展程序
-> 选择 ~/.agent-wiki/extension/
```

在扩展里完成：

- 填普通 Ark API Key
- 在首页直接提交当前内容或分享链接进行 `知识入库`
- 在设置页选择拆解模型：`Lite` 或 `Mini`
- 调整任务队列并发（默认 2，范围 1-4）
- 调整长视频分片并发（默认 2，范围 1-4；调高更容易发热）
- 同步抖音 Cookie
- 识别或选择 Obsidian vault

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
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py server/runtime_manager.py server/service_entry.py install/bootstrap.py
python3.11 tests/test_runtime_manager.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
python3.11 tests/test_runtime_version_protocol.py
node tests/test_extension_runtime_version.js
node --check chrome-extension/background.js
node --check chrome-extension/runtime-version.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

更多接口细节见：

- `docs/ark-video-understanding.md`
- `docs/websocket-protocol.md`
- `deps/douyin/SKILL.md`
