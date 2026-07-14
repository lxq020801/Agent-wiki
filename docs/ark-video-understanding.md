# Ark 视频理解接口口径

> 当前产品运行通道只使用字节跳动火山方舟 Ark API。Agent Plan 只保留为历史验证记录，不再作为运行路径。

## 主链路

1. 下载抖音视频到本地 mp4。
2. 通过普通 Ark Files API 上传：
   - `POST /api/v3/files`
   - `purpose = user_data`
   - 上传时传 `preprocess_configs.video.fps`
   - 上传时传 `preprocess_configs.video.model`
3. 轮询文件状态，直到 `status = active`。
4. 调 Ark Responses API：
   - `POST /api/v3/responses`
   - content 使用 `{"type": "input_video", "file_id": "..."}`
   - 同一条 input 附加 `{"type": "input_text", "text": "..."}`
   - 视频分析请求显式 `store = true`
5. 保存返回的 `response_id` 到本地短期记忆索引。

## 模型分工

- Agent 模型：不在本工具内部配置。它读取 `PROJECT_INTENT.md`、`AGENTS.md` 和 `SKILL.md`，决定何时调用当前工具；未来检索和维护方式不由本工具规定。
- 主分析模型：默认 `doubao-seed-2-0-lite-260428`，负责正式视频精拆、最终汇总、标题/摘要/标签候选和派生候选 JSON。
- 策略模型：默认 `doubao-seed-2-0-mini-260428`，负责长视频 `1fps` 全片/分片概览、分段 fps 决策、给 Lite 的精拆说明和策略 JSON 修复。

## 派生候选边界

Ark 视频理解只负责从 `knowledge_ingest` 输出中给出结构化派生候选。执行层会二次解析、评分、去重、限制数量，并只把高置信、低风险、可解析的 GitHub 项目候选自动入队。弱证据、缺 URL 的官方文档/网页研究和泛研究想法默认不进入可见候选，只保留在审计记录中。

人工确认、忽略和自动入队状态写入 `~/.agent-wiki/derived-actions/{parent_task_id}.json`，再由 WebSocket 状态快照合并回 `derivedTasks`。只有通过可见候选闸门的项目才进入这里，避免把 UI 操作状态和泛研究想法塞进父资产 Markdown。

当前允许的候选目标类型：

- `github_project`：明确 GitHub 仓库或开源库。可以在只有项目名时由派生执行器通过 GitHub API + README 解析；解析后会再次查 vault，避免重复写同一项目资产。
- `official_doc`：官方文档、API 文档、官方报告、官方博客。当前需要明确 URL 且与父结论强相关才进入可见候选；否则只进审计。
- `web_research`：需要多源核验的事实、案例或趋势。当前必须是父结论强依赖且有明确 URL 的高价值核验才进入可见候选；普通案例背景和泛行业研究默认压到审计。

候选不是正式资产。完整记录写入 `系统记录/派生任务候选/*.json`；父资产只写轻量 `derived_candidate_record` 和 `derived_candidate_ids`。派生记录不得包含 API Key、Cookie、Bearer token、`response_id`。

派生链路同时写 runtime 审计节点：

- `run-artifacts/{task_id}/05-derive/`：记录分析正文输入、JSON 候选、Markdown fallback 候选、归一化候选、保留/过滤结果和公开投影。
- `run-artifacts/{child_task_id}/05-derive-executor/`：记录子任务输入、目标解析、来源材料、Lite prompt、原始输出、清洗后输出、写库结果和父子链接结果。

这些节点只用于排查和复盘，不写入 Obsidian 正文。

## fps 和帧数

- 官方 fps 范围：`0.2 - 5`。
- 项目默认质量档：`quality`。
- 项目安全目标：`1250` 帧。
- Ark 硬上限：约 `1280` 帧。
- `<= 250s`：保持 `5fps`。
- `> 250s`：普通单文件视频按 `1250 / 视频秒数` 向下调整 fps。
- `> 600s`：不继续依赖单文件低 fps，进入长视频“概览 + 切片精拆”策略。

关键阈值：

- `250s / 4m10s`：5fps 到达 1250 安全目标。
- `625s / 10m25s`：fps 会降到约 2fps。
- `1230s / 20m30s`：超长视频分界线，给 1fps 全片概览的 1250 帧安全目标留 20 秒余量。

## 长视频概览与切片

触发条件：

- 视频时长 `> 10 分钟`。

策略：

- `10 分钟 < duration <= 1230s` 时，上传全片，使用 `1fps` 做概览。
- 超长视频指 `duration > 1230s / 20m30s` 的视频。超长视频的概览阶段也切片：每片用 `1fps` 粗拆，再把所有粗概览合成为全片分段策略。
- 超长视频后续仍走正常长视频精拆流程，因此时长可以继续按切片数量扩展；工程上仍受 `500MB` 文件安全上限、下载耗时、任务超时和模型上下文窗口限制。
- 全片概览上传的 `preprocess_configs.video.model` 使用策略模型，Responses 推理也使用策略模型。
- 概览 prompt 要输出粗内容、粗时间线、重要概念、待确认点、每个固定切片的 `2-5fps` 精拆建议，以及给 Lite 的 `lite_brief` 精拆说明。
- fps 决策不按死板类型判断，而按实际信息承载方式判断：画面变化、字幕/OCR、操作、动作和短暂视觉证据决定采样；概念密度、知识密度和论证复杂度进入 `lite_brief`，不单独推高 fps。
- 程序校验概览 JSON。JSON 无效、缺段或缺必填字段时，策略模型最多做一次文本修复，不重新上传视频；修复仍失败时，坏掉的片段按 `5fps` 兜底，整份 JSON 不可用时全段 `5fps` 兜底。
- 结构坏掉叫结构兜底；置信度低、超过安全帧数或缺少视觉/OCR/操作证据时叫 fps 调整。两类事件分开记录，避免把“策略保守”误读成“JSON 坏了”。
- 如果模型建议 `4/5fps`，必须有明确视觉/OCR/操作/动作证据；纯静态口播/访谈即使知识密度高，也会被程序压回较低 fps，并把重点交给 Lite 的语义拆解。
- 每片 `240s`。
- 重叠 `10s`。
- 步长 `230s`。
- 单片在 `5fps` 下约 `1200` 帧，低于项目安全目标 `1250`，距离 Ark 硬上限约留 `80` 帧。
- 分片上传和精拆默认 `2` 路并发，可在扩展“视频拆解设置”里调整为 `1-4`。扩展里的任务队列并发只控制同时处理多少个入库任务，不改变单个长视频内部的分片并发。

处理流程：

1. 判断全片概览是否会超过 `1250` 帧安全目标；超过则标记为超长视频。
2. 未超过时，全片用 `1fps` 走 Files API 上传、等待 active。
3. 超过时，复用固定切片，每片 `1fps` 上传分析，先得到分片粗概览。
4. 策略模型通过 Responses 生成长视频概览和分段精拆策略；超长视频则先把分片粗概览合成为同一份策略 JSON。
5. 如果策略 JSON 不能解析、缺少分段或缺必填字段，策略模型用 `previous_response_id` 接上上轮上下文修复一次。
6. 用 `ffmpeg -c copy` 生成临时 mp4 切片，尽量避免重新编码；超长视频概览和后续精拆共用同一批切片。
7. 每片按策略 fps 独立走 Files API 上传、等待 active。
8. 每片由主分析模型用 Responses 分析，prompt 中带上全片概览和本段精拆重点。
9. 每个入库意图可以接入上次同视频同意图的 `previous_response_id`，但当前任务内的分片彼此并发，不串行依赖上一片输出。
10. 所有片段拆完后，再由主分析模型用全片概览和分片结果做 text-only Responses 汇总。
11. 临时切片目录结束后清理。

策略日志：

- 路径：`~/.agent-wiki/logs/video-strategy-events.jsonl`
- 记录：JSON 修复、修复失败、低置信/缺证据/高风险导致的 fps 上调、最终 fps 计划。
- 不记录：API Key、Cookie、Bearer token、`response_id`。

审计产物：

- 路径：`~/.agent-wiki/run-artifacts/{task_id}/`
- 保存：任务 manifest、原始入库 prompt、mini 每段粗拆 prompt/输出、mini 合成策略 prompt/输出、修复 prompt/输出、最终 normalized strategy、Lite 每段 prompt/输出、最终汇总 prompt/输出。
- 任务状态和最终 Markdown 只写审计目录/文件索引，不把大段中间文本塞进状态 JSON。
- 审计产物会做本地脱敏：不写 API Key、Cookie、Bearer token、`response_id`。

片段元数据会保留在运行结果里：

- `part_index`
- `start_sec`
- `end_sec`
- `overlap_sec`
- `file_id`
- `fps`
- `target_frames`
- `actual_frames_estimate`
- `usage`
- `strategy_confidence`
- `strategy_scores`
- `strategy_fallback_applied`
- `strategy_fallback_reason`
- `strategy_validation_fallback`
- `strategy_fps_adjusted`
- `strategy_fps_adjust_reason`
- `strategy_lite_brief`
- `strategy_focus`

## Responses 记忆

用途：

- 让同一视频、同一入库意图的后续补拆可以接上模型上下文。

实现：

- 请求时传 `store = true`。
- 如果本地存在未过期记忆，传 `previous_response_id`。
- 记忆默认保存在 `~/.agent-wiki/responses-memory/`。
- 本地记忆默认保存 `3` 天，匹配 Ark Responses 默认存储周期，避免过期后继续复用。
- key 使用 `media_type + source_id/aweme_id + ingest_intent + model + prompt_hash + flow_version + chunked`。
- 不同来源、模型、prompt 和单文件/分片链路使用独立记忆，避免上下文串味。

边界：

- `response_id` 不写入 Obsidian frontmatter。
- `response_id` 不写入任务状态文件或策略日志。
- `response_id` 不等于长期知识记忆。
- `previous_response_id` 只续模型上下文，不负责 `file_id` 保活。
- Files API 文件可用期和 Responses 记忆是两件事。

## 文件大小

- Ark 默认托管 Files API 视频上限：`512MB`。
- 项目侧安全线：`500MB`。
- 超过 500MB 当前直接失败。
- TOS Bucket 可作为未来路线支持更大文件，本轮不接入。

未来接 TOS 时需要处理：

- Bucket 与 Ark 同地域，例如 `cn-beijing`。
- 使用可下载的预签名 GET URL。
- URL 过期时间、对象 key 稳定性、查询参数要可追踪。

## Agent Plan 历史记录

已验证：

- Agent Plan Key -> `POST /api/plan/v3/files`：404。
- Agent Plan Key -> `POST /api/v3/files`：401。
- Agent Plan Key -> `POST /api/plan/v3/responses` + fake `file_id`：失败。
- Agent Plan Key -> `POST /api/plan/v3/responses` + base64 小视频：成功。

当前取舍：

- 产品运行通道不再支持 Agent Plan。
- 扩展、服务端、配置、健康检查、analyzer 都只保留普通 Ark。
- 旧 `provider = "volcengine_agent_plan"` 会回落为 `doubao`。
- 旧 Agent Plan Key 不会自动迁移成普通 Ark Key。

## 资料来源

- Ark 视频理解文档：https://www.volcengine.com/docs/82379/1895586
- Ark Files API 文档：https://www.volcengine.com/docs/82379/1885708
- Ark Responses API 文档：https://www.volcengine.com/docs/82379/1569618
- Ark Agent Plan 对比文档：https://www.volcengine.com/docs/82379/2160841
