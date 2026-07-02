# Ark 视频理解接口口径

> 本文记录 P0 采用的豆包 / 火山方舟视频拆解链路。以 Ark 官方文档和 MCP 查询结果为准。

## P0 链路

1. 下载抖音视频到本地 mp4。
2. 用 Ark Files API 上传本地视频：
   - `POST /api/v3/files`
   - `purpose = "user_data"`
   - `preprocess_configs.video.fps`
   - `preprocess_configs.video.model`
3. 轮询文件状态，直到 `status = "active"`。
4. 用 Ark Responses API 分析：
   - `POST /api/v3/responses`
   - content 使用 `{"type": "input_video", "file_id": "..."}`
   - 同一条 input 里附加 `{"type": "input_text", "text": "..."}`
   - 使用流式输出，降低长任务超时风险。

## 关键限制

- Files API 默认托管空间：视频最大 512 MB。
- TOS Bucket 可支持最大 2 GB 视频，但 P0 不接入 TOS。
- base64 / URL 直传视频只适合 50 MB 以内，不是 P0 主链路。
- base64 视频请求体不能超过 64 MB。
- `fps` 范围：0.2 到 5。
- 使用 `file_id` 时，`fps` 必须在上传预处理时设置；Responses 阶段再传 fps 无效。
- `preprocess_configs.video.model` 是上传预处理策略字段，应随当前视频理解模型传入。
- 不传 `preprocess_configs.video.model` 时，Ark 会使用旧模型抽帧策略，可能把长视频理解上限从 1280 帧退回 640 帧。
- 文件状态官方为 `processing` / `active` / `failed`。
- Files API 文件默认保存 7 天，可通过 `expire_at` 调整到 1-30 天。
- Seed 2.0 策略最多约 1280 帧；超出时 Ark 会按时长均匀抽取上限帧数。

## 项目策略

- P0 固定走 `quality` 档，目标帧数 1250。
- 扩展不展示 quality / fps / target frames。
- `balanced` 只保留为内部调试兼容，不属于用户产品路径。
- P0 不做长视频切片、不做 TOS、不缓存 `file_id`。
- 超过 512 MB 直接失败；长视频先依赖 fps 动态计算和 Ark 均匀抽帧。

## 资料来源

- Ark 视频理解文档
- Ark Files API OpenAPI spec
- Ark Responses API OpenAPI spec
- Ark Tokenization API OpenAPI spec
- 项目旧资料 `references/douyin-ingest-implementation.md`
