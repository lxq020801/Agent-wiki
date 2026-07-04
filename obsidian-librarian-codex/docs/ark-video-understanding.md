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

## fps 和帧数

- 官方 fps 范围：`0.2 - 5`。
- 项目默认质量档：`quality`。
- 项目安全目标：`1250` 帧。
- Ark 硬上限：约 `1280` 帧。
- `<= 250s`：保持 `5fps`。
- `> 250s`：按 `1250 / 视频秒数` 向下调整 fps。
- `> 600s`：不继续依赖单文件低 fps，自动切片。

关键阈值：

- `250s / 4m10s`：5fps 到达 1250 安全目标。
- `625s / 10m25s`：fps 会降到约 2fps。
- `6250s / 104m10s`：0.2fps 到达 1250 安全目标。
- `6400s / 106m40s`：0.2fps 到达 1280 硬上限。

## 长视频切片

触发条件：

- 视频时长 `> 10 分钟`。

切片策略：

- 每片 `240s`。
- 重叠 `10s`。
- 步长 `230s`。
- 单片在 `5fps` 下约 `1200` 帧，低于项目安全目标 `1250`，距离 Ark 硬上限约留 `80` 帧。

处理流程：

1. 用 `ffmpeg -c copy` 生成临时 mp4 切片，尽量避免重新编码。
2. 每片独立走 Files API 上传、等待 active。
3. 每片独立用 Responses 分析。
4. 每个入库意图独立维护 `previous_response_id` 链。
5. 所有片段拆完后，再用 text-only Responses 汇总成最终资产正文。
6. 临时切片目录结束后清理。

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

## Responses 记忆

用途：

- 让同一视频、同一入库意图的后续补拆可以接上模型上下文。

实现：

- 请求时传 `store = true`。
- 如果本地存在未过期记忆，传 `previous_response_id`。
- 记忆默认保存在 `~/.obsidian-librarian/responses-memory/`。
- key 使用 `media_type + source_id/aweme_id + ingest_intent + model`。
- `knowledge_ingest` 和 `viral_breakdown` 分开记忆，避免上下文串味。

边界：

- `response_id` 不写入 Obsidian frontmatter。
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
