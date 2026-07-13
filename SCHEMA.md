# 知识资产结构契约 (SCHEMA.md)

> 本文档记录当前知识库的目录、字段和写入规则，是现有工具的数据契约，不是 Agent-wiki 的产品基准线或开发路线。
> 在项目仓库中，产品方向以 `PROJECT_INTENT.md` 为准；具体行为是否已经实现，以代码和测试为准。本文档中的未实现描述不得自动解释为开发任务。
> 本文档被复制到用户 vault 时，`PROJECT_INTENT.md` 不一定随之复制；这不改变本文档的范围，它仍只负责该 vault 的结构与写入兼容。
> 违反本文档中当前适用的安全红线和数据约束视为错误。

---

## 零、首次初始化

> 当 agent 首次进入 vault，发现目录结构不完整或不存在时，自动执行以下初始化流程。

### 初始化流程

1. **检测缺失目录**：对比第一章定义的目录结构，检查 vault 根目录下是否存在 `templates/`、`raw/`、`知识资产/`、`系统记录/`、`.obsidian/` 等核心目录。
2. **创建完整目录树**：按第一章定义的目录结构，创建所有缺失目录（`.obsidian/` 仅检查是否存在，不自动创建或修改其内容）。
3. **创建空白 index.md**：若 `index.md` 不存在，创建内容为：

   ```markdown
   # 知识库索引
   > 最后更新：{当前日期} | 资产总数：0
   ```

4. **Git 初始化**：若 `.git/` 不存在，执行 `git init`。
5. **创建 .gitignore**：若 `.gitignore` 不存在，创建包含以下内容的文件：

   ```
   .obsidian/workspace.json
   .obsidian/workspace-mobile.json
   .trash/
   .DS_Store
   ```

6. **首次提交**：执行 `git add -A && git commit -m "初始化知识库目录结构"`。
7. **报告用户**：向用户汇报初始化结果（创建了哪些目录、是否初始化了 git）。

> 初始化完成后，agent 正常进入工作流程。此流程仅在新 vault 或目录缺失时执行，已完整的 vault 跳过。

---

## 一、目录结构

```
vault/
├── SCHEMA.md                 ← 本文件，当前结构和字段契约
├── index.md                  ← 知识库总索引，每次写入后必须更新
├── templates/                ← 来源模板与资产模板的 Markdown 骨架
├── raw/                      ← 原始抓取物（视频字幕、网页HTML、GitHub README）
│   ├── videos/  images/  web/  github/
├── 知识资产/                  ← agent 产出的结构化笔记（正式产出区）
│   ├── 知识入库/  创作模式/  GitHub项目/  网页剪藏/  代码模块/
├── 系统记录/                  ← agent 自动生成：维护报告/、回收站/、变更日志/、派生任务候选/
├── .obsidian/                ← 【红线】agent 严禁读取或修改此目录
└── .git/
```

> `raw/` 是原始证据层，agent 只能新增、不得修改已有文件。`知识资产/` 是正式产出。`系统记录/` 存放管理数据。

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
| `knowledge_asset` | `知识资产/知识入库/` | 知识、工具、项目、方法、步骤、风险、派生线索 |
| `creative_pattern` | `知识资产/创作模式/` | 爆款基因、文案结构、叙事节奏、画面/剪辑特征、可迁移方法 |
| `github_project` | `知识资产/GitHub项目/` | GitHub 仓库的中文化评估：功能、用法、风险 |
| `code_module` | `知识资产/代码模块/` | 代码模块的能力说明书、接口契约、复刻步骤 |
| `idea_asset` | `知识资产/知识入库/` | 用户灵感、问题、假设、方案草稿 |

### C. 来源模板：`type`

`type` 保留为兼容字段，用来表示本次资产由哪个工具/模板生成，不再承担长期资产用途分类。

| type 值 | 模板文件 | 来源/工具含义 |
|----------|----------|------|
| `video_analysis` | `templates/video_analysis.md` | 视频输入生成的资产 |
| `image_post_analysis` | `templates/image_post_analysis.md` | 图文/多图输入生成的资产 |
| `github_project` | `templates/github_project.md` | GitHub 仓库输入生成的资产 |
| `web_clip` | `templates/web_clip.md` | 网页/文章输入生成的资产 |
| `code_module` | `templates/code_module.md` | 代码模块输入生成的资产 |

> 目录按 `asset_family` 分区；来源信息写入 frontmatter。缺失必备章节视为不完整资产。

### D. 派生任务候选与派生资产

> **状态：当前遗留实现兼容。** 以下候选目录、字段和执行规则用于描述现有代码，不代表长期派生目标，不得据此扩展新功能。未来调整派生策略时，应与代码、模板和测试一起重新设计。

视频/图文知识入库可以生成派生任务候选，例如 GitHub 项目、官方文档、API 文档或网页研究。候选只是运行态决策记录，不是正式知识资产：

- 不参与 `asset_family` / `type` / `source_media` 分类。
- 不进入 `index.md`。
- 不写入 `知识资产/`，直到后续被确认并真正执行为 GitHub 项目、网页剪藏等资产。
- 完整候选记录写入 `系统记录/派生任务候选/*.json`。
- 父资产 frontmatter 只允许保留 `derived_candidate_record` 和 `derived_candidate_ids` 这种轻量引用。

完整评分、证据、去重状态、执行建议、验收标准、父资产追溯信息必须留在系统记录 JSON，不得塞进资产 frontmatter。

高置信、低风险、可解析的 GitHub 项目候选可以自动进入 `derived_ingest` 派生执行队列。`official_doc` 和 `web_research` 只有在目标明确、证据强、父资产强依赖时才进入可见候选；普通补充研究只保留在审计记录。派生工具执行完成后才生成正式资产，并回写真实存在的 Obsidian wikilink：

- `github_project` -> `type: github_project` / `asset_family: github_project` / `source_media: github`
- `official_doc` / `web_research` -> `type: web_clip` / `asset_family: knowledge_asset` / `source_media: webpage`，并写 `derived_kind`
- 父资产 `related` 追加子资产链接；子资产 `derived_from` 和 `related` 回链父资产
- 候选阶段禁止写未来 `[[wikilink]]`，避免死链

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
| `asset_family` | 是 | `knowledge_asset` / `creative_pattern` / `github_project` / `code_module` / `idea_asset` |
| `source_media` | 是 | `douyin_video` / `douyin_image_post` / `webpage` / `github` / `manual` / `other` |
| `ingest_intent` | 是 | `knowledge_ingest` / `viral_breakdown` / `manual` / `derived_ingest` |
| `title` | 是 | ≤60字，中文优先 |
| `source_url` | 是 | 原始链接，无来源填 `"manual"` |
| `ingested` | 是 | `YYYY-MM-DD` |
| `updated` | 是 | 每次编辑刷新 |
| `tags` | 是 | ≥1个，须在第四章登记 |
| `summary` | 是 | ≤80字 |
| `confidence` | 是 | `high` / `medium` / `low`（默认 `medium`） |
| `weight` | 是 | 0–100 |
| `status` | 是 | `active` / `deprecated` / `archived` |
| `related` | 是 | `[[笔记名]]` 列表，无则 `[]` |

可选派生引用字段：

| 字段 | 必填 | 约束 |
|------|------|------|
| `derived_candidate_record` | 否 | 指向 `系统记录/派生任务候选/*.json`，无候选时为空字符串 |
| `derived_candidate_ids` | 否 | 候选 ID 列表，只放 `dt-...` 字符串，不放完整对象 |

---

## 四、标签体系

所有标签必须先在此登记，再在资产中使用。新增标签时 agent 必须同步更新本章。

**平台类：** `douyin` `bilibili` `youtube` `github` `webpage` `zhihu` `weixin` `xiaohongshu` `hackernews` `arxiv` `medium` `substack` `twitter`

**领域类：** `ai-agent` `video-analysis` `image-analysis` `code-generation` `knowledge-management` `creative-pattern` `web-scraping` `api-design` `prompt-engineering` `llm` `rag` `mcp` `tool-use` `browser-automation` `derived-asset` `official-doc` `web-research` `project`

**类型类：** `knowledge-asset` `tutorial` `reference` `case-study` `tool` `library` `framework` `opinion` `news` `paper` `sop`

**质量类：** `verified` `unverified` `outdated` `incomplete` `needs-review`

> 规则：tag 必须小写、使用连字符。agent 不得使用未登记标签。需要新标签时，先在上述对应分类中追加再使用。

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

## 创作模式
- [[20260617-douyin-image-post-template|抖音图文结构拆解]] — 图文表达结构样本 `#douyin` `#creative-pattern`

## GitHub项目 / 网页剪藏 / 代码模块
- [[20260616-openai-agents-sdk|OpenAI Agents SDK]] — 官方Agent SDK评估 `#ai-agent` `#library`
```

**更新规则：** 按资产用途分组，组内倒序。每条 `[[文件名|标题]] — 摘要 \`#tag\``。资产标记 `deprecated`/`archived` 时移入「已归档」分组。每次更新顶部日期和总数。

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
6. **Git 提交前自查** — 每次 commit 前检查是否违反上述红线，违规变更不得提交。

---

## 十、Git 备份规则

> **状态：当前运行与备份做法。** 本章不是知识资产 Schema，也不是长期产品要求；只有当前工具或用户明确启用的行为才适用，后续应迁入对应运行说明。

**提交频率：** 每次写入操作后立即 commit。一次写入 = 一次 commit。

**提交信息格式：**
- `ingest({asset_family}): {title}` — 入库资产
- `maintenance: {报告描述}` — 维护操作
- `index: 更新 index.md（新增 N 条）` — 索引更新

**回滚：** `git log --oneline -10` 查看历史 → `git reset --soft <hash>` 回滚。

**备份策略：** 本地 vault 为 git 仓库，建议设置 GitHub private repo 作为远程备份，维护扫描后执行 `git gc`。

---

> 本文档是当前知识资产结构和字段的公开契约，不决定产品方向或未来开发顺序。
> SCHEMA.md 的修订需人工审核；未经用户明确批准，agent 不得自行修改。
