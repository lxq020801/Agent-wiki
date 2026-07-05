# Obsidian Librarian

Obsidian Librarian 是一个 Agent 驱动的个人知识资产系统。它把抖音视频/图文等外部内容沉淀进 Obsidian，让未来 AI 工作可以从本地知识资产出发。

项目由三部分组成：

- **大脑 Harness**：`SCHEMA.md`、`SKILL.md`、`rules/`、`templates/`，告诉 Agent 这是什么、怎么写、什么不能碰。
- **手脚 Tools**：`deps/douyin/scripts/`、`scripts/`，负责下载、分析、写库、更新索引、git commit。
- **辅助 Extension**：`chrome-extension/`，负责 Cookie、模型配置、任务入口和任务状态。

Chrome 扩展不是主产品，也不直接拆解内容；真正的编排和执行在 Agent 本地工具链里。

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

扩展提交任务时只发送 URL、页面线索和入库意图：

- `knowledge_ingest`：知识入库，写入 `知识资产/知识入库/`
- `viral_breakdown`：爆款拆解，写入 `知识资产/创作模式/`
- 协议层可提交多个 `ingest_intents`，一次任务同时执行上面两种意图；当前扩展首页只显示两个主入口

## 派生任务候选

`knowledge_ingest` 会让主分析模型输出结构化派生候选，例如 GitHub 项目、官方文档、API 文档或需要多源核验的网页研究。执行层不会机械派生；它会按评分机制筛选、去重、限流，并把高置信、低风险、可解析的 GitHub 项目候选自动交给派生工具执行；官方文档和网页研究当前先保留为候选/人工确认。

评分维度包括知识价值、父资产依赖度、证据强度、可执行性、时效核验必要性、新颖度、资产适配度、成本风险和歧义度。默认保留最多 `8` 个候选；每个父任务最多自动派生 `3` 个 GitHub 项目。缺目标、证据不确定、重复、需要登录/付费、URL 不安全或非 GitHub 类型的候选留在扩展任务详情里等待确认。

GitHub 候选不强制要求视频里出现链接；如果只有项目名，派生工具会用 GitHub API 搜索仓库、读取 README，并把 README/描述与视频上下文对齐。能唯一高置信匹配时自动补全目标并执行；匹配不唯一时降级为人工补链接。

完整候选记录写入：

```text
系统记录/派生任务候选/*.json
```

父资产 Markdown 只保留轻量指针：

- `derived_candidate_record`
- `derived_candidate_ids`

正文只展示人类可读的候选摘要表。完整评分、证据、去重状态、验收标准和父资产追溯信息都在系统记录 JSON 里，避免污染正式知识资产 frontmatter。候选阶段不写未来 `[[wikilink]]`；只有派生子资产真正生成后，工具才回写父子资产 `related`/`derived_from` 链接，让 Obsidian 图谱连接到真实存在的笔记。

## Ark 视频策略

模型分工：

- Agent 模型：项目外部调用工具的决策模型，读取 Harness/Skill，负责知识库层面的判断与维护。
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
- 修复失败和 fps 调整会写入 `~/.obsidian-librarian/logs/video-strategy-events.jsonl`
- 每次任务会写审计产物到 `~/.obsidian-librarian/run-artifacts/{task_id}/`，包括 mini 每段粗拆、mini 合成策略、修复前后策略、Lite 每段 prompt/输出、最终汇总 prompt/输出
- 逐片分析后，再用全片概览和分片结果汇总为最终资产正文

## Responses 记忆

视频分析请求会使用 `store=true` 并保存返回的 `response_id`。

本地记忆位置：

```text
~/.obsidian-librarian/responses-memory/
```

记忆 key 按 `media_type + aweme_id/source_id + ingest_intent + model + prompt_hash + flow_version + chunked` 生成。`知识入库` 和 `爆款拆解` 分开记忆，普通单文件链路和长视频分片链路也分开记忆，避免上下文串味。

注意：

- `response_id` 不写入 Obsidian Markdown/frontmatter。
- `response_id` 不写入任务状态面板或策略日志，只保存在本地短期记忆索引。
- 本地短期记忆默认保存 `3` 天，避免超过 Ark Responses 默认保存期后继续复用失效上下文。
- 这是短期模型上下文，不是长期知识记忆。
- Files API 文件可用期和 Responses 记忆是两件事。

## 运行时目录

```text
~/.obsidian-librarian/
├── config.toml
├── cookie/
├── cache/
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

启动控制服务：

```bash
python3 server/launcher.py
```

安装扩展：

```text
chrome://extensions/
-> 开发者模式
-> 加载已解压的扩展程序
-> 选择 ~/.obsidian-librarian/extension/
```

在扩展里完成：

- 填普通 Ark API Key
- 在首页直接提交当前内容或分享链接，入口只有 `知识入库`、`爆款拆解`
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
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py install/bootstrap.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
node --check chrome-extension/background.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

更多接口细节见：

- `docs/ark-video-understanding.md`
- `docs/websocket-protocol.md`
- `deps/douyin/SKILL.md`
