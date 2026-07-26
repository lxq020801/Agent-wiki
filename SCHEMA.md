# 知识资产结构契约 (SCHEMA.md)

> 本文档记录当前知识库的目录、字段和写入规则，是现有工具的数据契约，不是 Agent-wiki 的产品基准线或开发路线。
> 在项目仓库中，产品方向以 `PROJECT_INTENT.md` 为准；具体行为是否已经实现，以代码和测试为准。本文档中的未实现描述不得自动解释为开发任务。
> 入库工具不把本文档复制到用户 vault；它在项目仓库中描述当前结构与写入兼容。
> 违反本文档中当前适用的安全红线和数据约束视为错误。

---

## 零、首次写入

> 首次写入只创建当前资产需要的目录和索引，不把项目仓库里的规则、模板或 Schema 副本夹带到用户 vault。

1. **创建写入目录**：按实际产物创建 `raw/` 和 `知识资产/` 下需要的目录；不得读取或修改 `.obsidian/`。
2. **创建空白 index.md**：若 `index.md` 不存在，创建内容为：

   ```markdown
   # 知识库索引
   > 最后更新：{当前日期} | 资产总数：0
   ```

3. **不管理 Git**：不得自动执行 `git init`、`git add` 或 `git commit`，也不得修改已有历史。用户已经配置的 Git 仓库保持原样，由用户或独立备份工具管理。

---

## 一、目录结构

```
vault/
├── index.md                  ← 知识库总索引，每次写入后必须更新
├── raw/                      ← 原始抓取物（图文图片、网页HTML、GitHub README）
│   ├── images/  web/  github/
├── 知识资产/                  ← agent 产出的结构化笔记（正式产出区）
│   └── *.md                  ← 所有正式知识资产直接存放于此
├── 系统记录/                  ← 可选的人工维护区；普通入库不会夹带写入规则或候选文件
├── .obsidian/                ← 【红线】agent 严禁读取或修改此目录
└── .git/                     ← 可选；存在时也不由入库工具自动操作
```

> `raw/` 是原始证据层，agent 只能新增、不得修改已有文件。`知识资产/` 是正式产出。`系统记录/` 存放管理数据。
> 视频文件不进入 `raw/`：抖音视频只下载到运行时的任务私有缓存目录（`cache/videos/<task_id>/`），任务结束（成功、失败或取消）即删除；视频资产通过 `source_url`、`source_id` 等元数据追溯原始内容。
> 旧版 `知识资产/知识入库/` 中的资产继续作为兼容数据读取，但新资产不再写入该目录；当前工具不会自动移动或迁移旧资产。

---

## 二、双轴资产模型

知识库不再只按“来源形态”建模，而是采用双轴模型：

### A. 来源维度：`source_media`

| source_media 值 | 含义 |
|---|---|
| `douyin_video` | 抖音视频 |
| `douyin_image_post` | 抖音图文/多图 |
| `webpage` | 网页/文章 |
| `github` | GitHub 仓库 |
| `manual` | 用户或 Agent 手动创建 |
| `other` | 其他来源，必须在正文说明 |

### B. 资产用途维度：`asset_family`

| asset_family 值 | 写入目录 | 用途 |
|---|---|---|
| `knowledge_asset` | `知识资产/` | 当前所有正式资产；内容可以是知识、工具、项目、方法、步骤或风险 |
| `idea_asset` | `知识资产/` | 兼容字段；用户灵感、问题、假设、方案草稿 |

### C. 来源模板：`type`

`type` 保留为兼容字段，用来表示本次资产由哪个工具/模板生成，不再承担长期资产用途分类。

| type 值 | 模板文件 | 来源/工具含义 |
|----------|----------|------|
| `video_analysis` | `templates/video_analysis.md` | 视频输入生成的资产 |
| `image_post_analysis` | `templates/image_post_analysis.md` | 图文/多图输入生成的资产 |
| `github_project` | 统一三段式正文 | GitHub 仓库输入生成的资产 |
| `web_clip` | 统一三段式正文 | 网页/文章输入生成的资产 |

> 当前正式入库统一使用 `asset_family: knowledge_asset` 并写入 `知识资产/`。`type`、`source_media`、来源字段和关系字段负责保留来源与形成方式。缺失必备章节视为不完整资产。

### D. 派生任务候选与新资产

> 以下执行规则描述当前已实现行为，不代表可以据此扩展新的派生类型。

视频/图文知识入库当前只为明确介绍的开源项目生成 GitHub 派生候选。官方文档、API 文档、网页研究、普通产品、公司和泛概念不进入派生候选。候选只是运行态决策记录，不是正式知识资产：

- 不参与 `asset_family` / `type` / `source_media` 分类。
- 不进入 `index.md`。
- 不写入 `知识资产/`，直到后续被确认并真正执行为新知识资产。
- 完整候选记录、评分和调试数据写入 runtime `run-artifacts/`，不作为普通入库的额外 vault 文件。
- 父资产正文不展示候选 JSON、调试记录或未完成的派生状态；这些内容只保存在任务状态和运行日志中。

完整评分、证据、去重状态、执行建议、验收标准、父资产追溯信息必须留在运行审计，不得塞进资产 frontmatter。

候选必须包含直接开源证据以及项目用途、能力或用法证据。项目名称和 URL 均允许缺失，但此时必须提供组织、功能、画面文字等可区分线索和明确的 GitHub 搜索查询。只有 GitHub 官方 API 返回唯一可信匹配时才可以自动进入 `derived_ingest` 派生执行队列；歧义或未找到时等待确认。派生工具执行完成后才生成与其他资产结构一致的正式资产，并回写真实存在的 Obsidian wikilink：

- `github_project` -> `type: github_project` / `asset_family: knowledge_asset` / `source_media: github`
- 父资产 `related` 追加子资产链接；子资产 `derived_from` 和 `related` 回链父资产
- 候选阶段禁止写未来 `[[wikilink]]`，避免死链
- 候选数量不设固定上限；筛选依据是来源是否确实介绍了一个开源项目，而不是按数量截断
- 只有子资产真实写入成功后，才在父子资产元数据中建立可解析的真实关系；失败、歧义或未执行候选不得写入正文或伪装成已完成关系

---

## 三、通用 Frontmatter 规范

所有资产文件必须包含以下 frontmatter（`tags` 必须从第四章标签体系选取）：

```yaml
---
id: 20260617-knowledge-001 # {日期}-{用途缩写}-{序号}，全局唯一
type: video_analysis       # 来源模板类型
asset_family: knowledge_asset
source_media: douyin_video
ingest_intent: knowledge_ingest
title: "标题（≤60字）"     # 中文优先
source_url: "https://..."  # 原始来源URL，无则填 "manual"
source_id: "739..."        # 官方接口来源 ID；可获得时优先记录
ingested: 2026-06-17       # 入库日期
updated: 2026-06-17        # 最后更新日期（每次编辑必须刷新）
tags: [douyin, video]      # 至少1个，须在第四章登记
summary: "一句话（≤80字）"  # 必填摘要
confidence: medium          # high / medium / low
weight: 100                 # 100=新，<50=旧，0=归档
status: active              # active / deprecated / archived
related: []                 # 关联的 [[笔记名]] 列表
---
```

| 字段 | 必填 | 约束 |
|------|------|------|
| `id` | 是 | `{YYYYMMDD}-{type}-{序号}`，全局唯一 |
| `type` | 是 | 来源模板类型：`video_analysis` / `image_post_analysis` / `github_project` / `web_clip` / `code_module` |
| `asset_family` | 是 | 当前正式入库统一为 `knowledge_asset`；`idea_asset` 仅作兼容保留 |
| `source_media` | 是 | `douyin_video` / `douyin_image_post` / `webpage` / `github` / `manual` / `other` |
| `ingest_intent` | 是 | `knowledge_ingest` / `manual` / `derived_ingest` |
| `title` | 是 | ≤60字，中文优先 |
| `source_url` | 是 | 原始链接，无来源填 `"manual"` |
| `source_id` | 条件必填 | 来源平台官方接口可提供 ID 时记录；不得用浏览器页面标题替代 |
| `ingested` | 是 | `YYYY-MM-DD` |
| `updated` | 是 | 每次编辑刷新 |
| `tags` | 是 | ≥1个，须在第四章登记 |
| `summary` | 是 | ≤80字 |
| `confidence` | 是 | `high` / `medium` / `low`（默认 `medium`） |
| `weight` | 是 | 0–100 |
| `status` | 是 | `active` / `deprecated` / `archived` |
| `related` | 是 | `[[笔记名]]` 列表，无则 `[]` |

### 来源笔记正文

成功入库的来源笔记正文使用三部分：`简洁概括`、`完整内容整理`、`AI 分析`。完整内容整理忠实保留来源表达和必要上下文；AI 分析必须明确标识、只依据当前来源，并用限定语区分推断与来源事实。不得为了固定栏目重复摘要或填充空内容。

正式笔记不得写模型名、Token、成本、响应 ID、质量档、抽帧参数等运行噪声。这些数据保留在任务状态和 `run-artifacts/` 审计记录中。原始完整标题或文案应保留在正文来源元数据中，正式标题和文件名保持简洁。

---

## 四、标签体系

所有标签必须先在此登记，再在资产中使用。新增标签时 agent 必须同步更新本章。

**平台类：** `douyin` `bilibili` `youtube` `github` `webpage` `zhihu` `weixin` `xiaohongshu` `hackernews` `arxiv` `medium` `substack` `twitter`

**领域类：** `ai-agent` `video-analysis` `image-analysis` `code-generation` `knowledge-management` `web-scraping` `api-design` `prompt-engineering` `llm` `rag` `mcp` `tool-use` `browser-automation` `official-doc` `web-research`

**类型类：** `knowledge-asset` `tutorial` `reference` `case-study` `tool` `library` `framework` `opinion` `news` `paper` `sop`

**质量类：** `verified` `unverified` `outdated` `incomplete` `needs-review`

> 规则：tag 必须小写、使用连字符。优先使用内容主题标签；平台和媒体形态标签只作辅助。agent 不得使用未登记标签。需要新标签时，先在上述对应分类中追加再使用。

---

## 五、命名规范

文件名格式：`{YYYYMMDD}-{slug}.md`

**Slug 规则：** 全部小写、连字符分隔、不超过 60 字符、仅含 `a-z` `0-9` `-`。Slug 须反映核心主题，不用无意义数字串。

**正确示例：** `20260617-douyin-video-download-cookie-auth.md`

**禁止：** `20260617.md`（无slug）、`抖音 视频 下载.md`（空格）、`20260617-抖音视频下载.md`（中文slug）

---

## 六、index.md 维护规则

**更新义务：** agent 在每次完成资产入库后，必须更新 `index.md`。不得跳过。

**index.md 格式：**

```markdown
# 知识库索引
> 最后更新：2026-06-17 | 资产总数：42

## 知识入库
- [[20260617-douyin-video-download|抖音视频下载]] — Cookie鉴权链路分析 `#douyin` `#knowledge-asset`
- [[20260616-openai-agents-sdk|OpenAI Agents SDK]] — 官方Agent SDK评估 `#ai-agent` `#library`
```

**更新规则：** 所有活跃资产统一列入「知识入库」，组内倒序。每条 `[[文件名|标题]] — 摘要 \`#tag\``。来源和形成方式不另建索引栏目。资产标记 `deprecated`/`archived` 时移入「已归档」分组。顶部资产总数只统计索引中存在、目标 Markdown 真实存在、且具有有效资产 frontmatter 的非归档资产；孤立文件和断链不得计数。

---

## 七、质量标准

| 维度 | 值 | 含义 |
|------|-----|------|
| **confidence** | `high` | 来源官方/论文，或已验证可运行，或多方印证 |
| | `medium` | 来源可信但未独立验证（**默认**） |
| | `low` | 来源不明、不完整、仅为线索 |
| **weight** | 100 | 全新入库 |
| | 80–99 | 近期维护，信息较新 |
| | 50–79 | 超过 30 天未更新 |
| | 1–49 | 超过 90 天未更新，可能过时 |
| | 0 | 已归档，不参与检索 |
| **status** | `active` | 正常资产，参与检索和引用 |
| | `deprecated` | 已过时，保留标记，weight 自动降至 30 |
| | `archived` | 移入归档，不参与检索 |

> status 变更必须同步更新 `updated` 和 `index.md`。每次维护扫描后 agent 自动调整 weight。

---

## 八、维护规则

> **状态：早期未落地设想。** 本章不是当前数据契约，也不代表已经存在这些命令或自动任务。不得据此自动执行或开发维护功能；维护模块真正进入开发范围时重新设计。

### 每周自动扫描（cron 或用户触发）

1. **去重检测：** 扫描标题/URL重复 → 生成合并建议 → **不自动合并** → 等用户确认
2. **过时检查：** `updated` 超 90 天的 `active` 资产 → 降 weight 至 49 → 生成过时清单
3. **链接修复：** 检查 `[[wiki_link]]` 有效性 → 断链生成修复报告 → 等用户确认
4. **index.md 一致性：** 验证条目数 = 实际文件数 → 不一致则**自动修复**（例外，不需要确认）
5. **标签审计：** 扫描未登记标签 → 报告 → 等用户决定追加或修正

### 按需维护命令

`/dedup` `/staleness` `/link-check` `/tag-audit` — 单步检查 | `/health` — 快速统计摘要

### 维护原则

> **报告先行，确认后执行。** 任何可能改变资产的操作，必须先输出报告、获得用户确认。只读扫描不需要确认。index.md 一致性修复是唯一例外，可自动执行并在报告中注明。

---

## 九、安全红线

违反任一条视为 agent 执行错误：

1. **禁止修改 `.obsidian/`** — 不得读取、写入、修改该目录下的任何文件。
2. **禁止永久删除** — 不得执行 `rm`。废弃文件移至 `系统记录/回收站/` 并记录原因。
3. **禁止写入敏感凭据** — API Key、Token、Cookie、密码等 **绝对不得** 写入任何 Markdown 或 frontmatter。只能写环境变量名（如 `OPENAI_API_KEY`），不写真实值。
4. **维护操作报告先行** — 修改/删除操作必须先输出报告、获得确认。只读类不需要。
5. **禁止修改 raw/ 已有文件** — raw 是原始证据层，agent 只能新增，不得修改或删除已有内容。
6. **写入前自查** — 每次写入前检查是否违反上述红线，违规内容不得进入 vault。

---

## 十、Git 边界

知识入库和派生入库不自动初始化、暂存或提交 Git。已有 `.git/` 和历史保持不动；需要 Git 备份时，由用户或独立备份流程显式执行，不能把版本控制副作用绑定到单次入库。

---

> 本文档是当前知识资产结构和字段的公开契约，不决定产品方向或未来开发顺序。
> SCHEMA.md 的修订需人工审核；未经用户明确批准，agent 不得自行修改。
