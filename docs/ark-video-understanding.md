# Ark 视频理解接口口径

> 当前产品运行通道只使用字节跳动火山方舟 Ark API。Agent Plan 只保留为历史验证记录，不再作为运行路径。

## 主链路

1. 下载抖音视频到任务私有缓存 `cache/videos/<task_id>/`。任务结束后删除缓存，不把原视频复制进知识库。
2. 用 `ffprobe` 读取时长和基本媒体信息。
3. 使用系统固定 `5fps`，不做本地画面变化预扫描或动态 FPS 决策。
4. 若 `时长 × FPS <= 1250` 帧，整个视频只上传和分析一次。
5. 若超过 `1250` 帧安全目标，则用同一 FPS 做机械切片，分片并发分析后再汇总。
6. 解析最终资产正文与内部派生候选 JSON，写入干净的来源 Markdown。

Files API 上传必须在预处理阶段传入：

- `preprocess_configs.video.fps`
- `preprocess_configs.video.model`

然后等待文件 `status = active`，再用 Responses API 的 `input_video.file_id` 进行分析。

## 模型

- 默认主分析模型为 `doubao-seed-2-0-lite-260428`。
- 扩展中的 Mini 选项仍可作为主分析模型，用户选择后全链路都用 Mini。
- 不再有独立 Mini 粗拆、全片概览模型或分段策略模型。
- 单文件分析、切片分析和最终汇总使用同一个当前配置模型。
- 旧配置中的 `models.strategy`、`models.analyzer_fallback` 和协议字段 `strategyModel` 可被新服务无声忽略，但新配置不再写入它们。

## FPS 与机械切片

- 模型上传 FPS：系统固定 `5fps`。
- 不运行本地预扫描；质量档、目标帧数、FPS 范围和 `video_fps_mode` 已从正式配置中删除，旧键只会被忽略。
- 方舟硬上限：约 `1280` 帧。
- 项目安全目标：`1250` 帧。
- 切片长度固定为 `250` 秒。
- 相邻切片重叠：`10` 秒。
- 相邻切片起点步长：`240` 秒。
- 同一个视频的所有切片使用同一 FPS。
- 切片并发：默认 `2`，可配置为 `1-4`。

例如，`520.809` 秒视频选择 `5fps` 时的切片为：

```text
0-250s
240-490s
480-520.809s
```

切片只是服务商帧数上限的工程处理，不表示语义章节。

## 分析、重试与汇总

- 每个切片 prompt 只在原始分析要求后增加当前切片编号、真实时间范围和重叠时长。
- 不生成粗拆、语义分段、每段 FPS 建议或 `lite_brief`。
- Responses 连接中断、超时、`429` 和部分 `5xx` 会自动重试，默认最多 `3` 次。`400`、鉴权和文件类型错误不盲目重试。
- 同一 `task_id` 重跑时，若新流程的 `03-analysis/<intent>/part-xxx-output.md` 和 prompt hash 匹配，可复用已完成切片。
- 最终汇总负责去除 `10` 秒重叠内容，按时间顺序合并，生成一份正式资产正文。

## 派生候选与 Markdown

模型输出分为两部分：

1. 正式资产内容：`简洁概括`、`完整内容整理`、`明确标识的 AI 分析`。
2. 仅供程序使用的派生候选 JSON。

程序在写入 Markdown 前先解析并移除内部 JSON。后续状态如下：

```text
模型输出
  -> 解析正文与内部候选 JSON
  -> 写入干净的来源 Markdown
  -> 候选在任务系统中解析、去重、确认或执行
  -> 只有子资产真实存在后，才回写 related 和“相关资产”链接
```

- 候选数量没有固定上限。
- 不用数值分数、置信度或“必须是唯一主对象”作为可见或执行门槛。
- 当前只识别来源明确介绍的开源项目；普通产品、公司、官方/API 文档和网页研究不进入派生候选。
- GitHub 项目允许缺少 URL 或名称。无名称时必须有明确搜索查询，或同时具备组织和功能关键词；后续使用 GitHub 官方 API 解析，唯一可信匹配可继续，歧义则进入 `needs_target`。
- 候选、待确认、失败、取消和调试信息只保存在任务 JSON、`derived-actions/` 与 `run-artifacts/`，不写入 Markdown 正文或 frontmatter。
- 派生子资产与普通资产使用同一 `知识资产/` 目录和正文结构。

## 审计产物

每个任务的可排查产物位于：

```text
~/.agent-wiki/run-artifacts/<task_id>/
```

主要包含：

- `00-run-manifest.json`：任务与模型摘要。
- `01-prompts/`：原始入库 prompt。
- `01-sampling/evidence.json`：固定 FPS 请求与服务商返回事实。
- `01-sampling/chunk-plan.json`：只在必要时保存机械切片计划。
- `03-analysis/`：每个切片的 prompt、输出和摘要。
- `04-synthesis/`：最终汇总 prompt 和输出。
- `05-derive/`：派生候选解析、归一化、去重和公开投影。

任务状态和最终 Markdown 只保存审计目录/文件索引，不嵌入大段 prompt 或模型原始输出。审计中不得保存 API Key、Cookie、Bearer token 或 `response_id`。

## Responses 短期记忆

视频分析请求使用 `store=true`。返回的 `response_id` 只保存在本地 `~/.agent-wiki/responses-memory/`，默认保留 `3` 天，用于同视频同 prompt 的短期上下文续接。

`response_id` 不写入 Markdown、任务状态或日志；它也不是长期知识记忆。Files API 的文件可用期与 Responses 记忆是两件事。

## 工程边界

- Ark 默认托管 Files API 视频上限为 `512MB`；项目侧安全线为 `500MB`。
- 超过 `500MB` 当前直接失败。
- 长视频仍受下载时间、任务超时、模型上下文和供应商稳定性限制。
- 所有分片为任务临时文件，任务结束后清理。
