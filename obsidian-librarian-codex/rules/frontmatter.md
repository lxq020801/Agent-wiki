# Frontmatter 字段规范

> 本文档服从 SCHEMA.md 第三章「通用 Frontmatter 规范」的全部约束。任何冲突以 SCHEMA.md 为准。

## 必填字段（15 个）

所有资产文件必须包含以下 15 个字段，缺一不可：

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | string | `{YYYYMMDD}-{type}-{序号}`，全局唯一 | 资产主键，与文件名日期部分一致。type 缩写：`video` / `github` / `web` / `code` |
| `type` | enum | `video_analysis` / `image_post_analysis` / `github_project` / `web_clip` / `code_module` | 来源模板类型，决定由哪个工具/模板生成 |
| `asset_family` | enum | `knowledge_asset` / `creative_pattern` / `github_project` / `code_module` / `idea_asset` | 资产用途，决定主目录和召回用途 |
| `source_media` | enum | `douyin_video` / `douyin_image_post` / `webpage` / `github` / `manual` / `other` | 来源形态 |
| `ingest_intent` | enum | `knowledge_ingest` / `viral_breakdown` / `manual` | 本次入库意图 |
| `title` | string | ≤60字，中文优先 | 资产标题，禁止纯英文（无中文来源需翻译） |
| `source_url` | string | 有效 URL 或 `"manual"` | 原始来源链接；无来源（如纯原创）填 `"manual"` |
| `ingested` | string | `YYYY-MM-DD` | 首次入库日期（非时间戳） |
| `updated` | string | `YYYY-MM-DD` | 最后编辑日期，**每次修改必须刷新** |
| `tags` | array | ≥1 个，须在 SCHEMA.md 第四章登记 | 裸标签（无 `#` 前缀），全部小写、连字符分隔 |
| `summary` | string | ≤80字 | 一句话摘要，简洁描述资产核心内容 |
| `confidence` | enum | `high` / `medium` / `low`，默认 `medium` | 来源可信度。官方/论文/已验证 → `high`；可信未验证 → `medium`；来源不明 → `low` |
| `weight` | int | 0–100，新建默认 100 | 100=全新入库；80–99=近期维护；50–79=>30天未更新；1–49=>90天未更新；0=已归档 |
| `status` | enum | `active` / `deprecated` / `archived` | `active`=正常；`deprecated`=已过时（weight 自动降至 30）；`archived`=归档 |
| `related` | array | `[[笔记名]]` 列表，无则 `[]` | 关联笔记的 wikilink 列表，须指向存在的笔记 |

## 完整示例

```yaml
---
id: 20260617-video-001
type: video_analysis
asset_family: knowledge_asset
source_media: douyin_video
ingest_intent: knowledge_ingest
title: "抖音视频下载：Cookie 鉴权链路分析"
source_url: "https://www.douyin.com/video/xxxxx"
ingested: 2026-06-17
updated: 2026-06-17
tags: [douyin, video-analysis, case-study]
summary: "分析抖音视频下载的 Cookie 鉴权机制与常见反爬策略"
confidence: medium
weight: 100
status: active
related: []
---
```

## 类型专属可选字段

以下字段按资产类型选用，非必填：

| type | 可选字段 | 说明 |
|------|----------|------|
| `video_analysis` | `platform`, `author`, `duration` | 平台、作者、时长（秒） |
| `image_post_analysis` | `platform`, `author`, `image_count` | 平台、作者、图片数量 |
| `github_project` | `repo`, `language`, `stars`, `license` | 仓库全名、主语言、star 数、许可证 |
| `web_clip` | `domain`, `author`, `publish_date` | 域名、作者、发布日期 |
| `code_module` | `language`, `dependencies`, `source_path` | 语言、依赖列表、源码路径 |

派生候选只允许轻量引用字段：

| 字段 | 适用类型 | 说明 |
|------|----------|------|
| `derived_candidate_record` | `video_analysis` / `image_post_analysis` | 指向 `系统记录/派生任务候选/*.json` 的相对路径 |
| `derived_candidate_ids` | `video_analysis` / `image_post_analysis` | 候选 ID 列表，只放 `dt-...` 字符串 |

禁止把派生候选完整对象写入资产 frontmatter。以下运行态字段只能存在于系统记录 JSON：`scores`、`evidence`、`reason`、`dedupe`、`parent_task_id`、`execution_status`、`candidate_status`、`target_type`、`derived_kind`、`acceptance_criteria`。

## 字段约束速查

- **`id`**：永不修改，入库后即为资产永久主键。
- **`asset_family`**：长期资产用途，优先用于目录、召回和维护。
- **`source_media`**：来源形态，不得拿来替代资产用途。
- **`ingest_intent`**：记录入口意图；扩展只能提交意图，最终写库仍由 Agent/工具执行。
- **`title`**：禁止纯英文，无中文来源须翻译后填入。≤60字。
- **`tags`**：禁止自创标签，所有标签必须在 SCHEMA.md 第四章登记。裸标签无 `#` 前缀。
- **`summary`**：≤80字，必填，不得为空。
- **`confidence`**：默认 `medium`。来源官方/论文/经验证可运行 → `high`；来源不明/仅线索 → `low`。
- **`weight`**：新建默认 100（全新）。agent 每周扫描自动根据 `updated` 距今天数调整。
- **`status`**：变更时必须同步刷新 `updated` 并更新 `index.md`。
- **`related`**：每个 wikilink 必须指向存在的笔记，格式见 `rules/wikilinks.md`。
- **`source_url`**：无外部来源的纯原创笔记填 `"manual"`，不得留空或填占位符。
