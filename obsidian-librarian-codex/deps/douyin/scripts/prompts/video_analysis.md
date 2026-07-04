# 旧版视频拆解提示词

本文件只保留为旧任务兼容说明。当前执行链不应直接读取本文件，而应按任务意图选择：

- `video_knowledge_ingest.md`：知识入库，生成 `knowledge_asset`
- `video_viral_breakdown.md`：爆款拆解，生成 `creative_pattern`

如果你是 Agent，请不要把“短视频复刻”当作默认目标。默认入口是知识资产化，只有用户或扩展明确选择 `viral_breakdown` 时，才进入创作模式拆解。
