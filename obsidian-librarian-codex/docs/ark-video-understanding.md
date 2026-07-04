# Ark 视频理解接口口径

> 当前产品运行通道只使用普通豆包 / 火山方舟 Ark API。Agent Plan 只保留为历史验证记录，不再作为运行路径。

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

- Agent 模型：不在本工具内部配置。它读取 Harness/Skill，决定何时调用工具、如何维护知识库、是否做旧笔记去重或相似判断。
- 主分析模型：默认 `doubao-seed-2-0-lite-260428`，负责正式视频精拆、最终汇总、标题/摘要/标签候选和派生任务线索。
- 策略模型：默认 `doubao-seed-2-0-mini-260428`，只负责长视频最高 `1fps` 的动态概览、分段 fps 决策和策略 JSON 修复。

## fps 和帧数

- 官方 fps 范围：`0.2 - 5`。
- 项目默认质量档：`quality`。
- 项目安全目标：`1250` 帧。
- Ark 硬上限：约 `1280` 帧。
- `<= 250s`：保持 `5fps`。
- `> 250s`：按 `1250 / 视频秒数` 向下调整 fps。
- `> 600s`：不继续依赖单文件低 fps，进入长视频“概览 + 切片精拆”策略。

关键阈值：

- `250s / 4m10s`：5fps 到达 1250 安全目标。
- `625s / 10m25s`：fps 会降到约 2fps。
- `6250s / 104m10s`：0.2fps 到达 1250 安全目标。
- `6400s / 106m40s`：0.2fps 到达 1280 硬上限。

## 长视频概览与切片

触发条件：

- 视频时长 `> 10 分钟`。

策略：

- 先上传全片，使用最高 `1fps` 的动态 fps 做概览；概览 fps 会按 `1250 / 视频秒数` 下调，但不低于官方最低 `0.2fps`。
- 如果视频长到 `0.2fps` 也会超过 `1250` 帧安全目标，则跳过全片概览，写入策略日志，并按 `5fps` 分片精拆兜底。
- 全片概览上传的 `preprocess_configs.video.model` 使用策略模型，Responses 推理也使用策略模型。
- 概览 prompt 要输出粗内容、粗时间线、重要概念、待确认点，以及每个固定切片的 `2-5fps` 精拆建议。
- fps 决策不按死板类型判断，而按画面变化、字幕/OCR 密度、操作密度、动作细节、概念密度、低 fps 漏细节风险和置信度评分。
- 程序校验概览 JSON。JSON 无效、缺段或缺必填字段时，策略模型最多做一次文本修复，不重新上传视频；修复仍失败时，坏掉的片段按 `5fps` 兜底，整份 JSON 不可用时全段 `5fps` 兜底。
- 缺证据、置信度低或风险高时，程序向更高 fps 保守回退，最保守为 `5fps`。
- 每片 `240s`。
- 重叠 `10s`。
- 步长 `230s`。
- 单片在 `5fps` 下约 `1200` 帧，低于项目安全目标 `1250`，距离 Ark 硬上限约留 `80` 帧。
- 分片上传和精拆默认 `2` 路并发，可在扩展“视频拆解设置”里调整为 `1-4`。扩展里的任务队列并发只控制同时处理多少个入库任务，不改变单个长视频内部的分片并发。

处理流程：

1. 全片用最高 `1fps` 的动态 fps 走 Files API 上传、等待 active；超过概览安全帧数时跳过这一步并进入保守分片。
2. 策略模型通过 Responses 生成长视频概览和分段精拆策略。
3. 如果策略 JSON 不能解析、缺少分段或缺必填字段，策略模型用 `previous_response_id` 接上上轮上下文修复一次。
4. 用 `ffmpeg -c copy` 生成临时 mp4 切片，尽量避免重新编码。
5. 每片按策略 fps 独立走 Files API 上传、等待 active。
6. 每片由主分析模型用 Responses 分析，prompt 中带上全片概览和本段精拆重点。
7. 每个入库意图可以接入上次同视频同意图的 `previous_response_id`，但当前任务内的分片彼此并发，不串行依赖上一片输出。
8. 所有片段拆完后，再由主分析模型用全片概览和分片结果做 text-only Responses 汇总。
9. 临时切片目录结束后清理。

策略日志：

- 路径：`~/.obsidian-librarian/logs/video-strategy-events.jsonl`
- 记录：JSON 修复、修复失败、低置信/缺证据/高风险导致的 fps 上调、最终 fps 计划。
- 不记录：API Key、Cookie、Bearer token、`response_id`。

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
- `strategy_focus`

## Responses 记忆

用途：

- 让同一视频、同一入库意图的后续补拆可以接上模型上下文。

实现：

- 请求时传 `store = true`。
- 如果本地存在未过期记忆，传 `previous_response_id`。
- 记忆默认保存在 `~/.obsidian-librarian/responses-memory/`。
- 本地记忆默认保存 `3` 天，匹配 Ark Responses 默认存储周期，避免过期后继续复用。
- key 使用 `media_type + source_id/aweme_id + ingest_intent + model + prompt_hash + flow_version + chunked`。
- `knowledge_ingest` 和 `viral_breakdown` 分开记忆，避免上下文串味。

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
- 项目旧资料：`references/douyin-ingest-implementation.md`
