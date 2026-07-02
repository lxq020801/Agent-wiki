# 知识库宪法 (SCHEMA.md)

> Agent 进入 vault 第一件事就是读本文档。它定义了知识库的所有法律。
> 违反本文档中的「安全红线」视为错误。

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
├── SCHEMA.md                 ← 本文件，Layer 1 宪法，每次会话必读
├── index.md                  ← 知识库总索引，每次写入后必须更新
├── templates/                ← 4 种资产类型的 Markdown 骨架模板
├── raw/                      ← 原始抓取物（视频字幕、网页HTML、GitHub README）
│   ├── videos/  web/  github/
├── 知识资产/                  ← agent 产出的结构化笔记（正式产出区）
│   ├── 视频分析/  GitHub项目/  网页剪藏/  代码模块/
├── 系统记录/                  ← agent 自动生成：维护报告/、回收站/、变更日志/
├── .obsidian/                ← 【红线】agent 严禁读取或修改此目录
└── .git/
```

> `raw/` 是原始证据层，agent 只能新增、不得修改已有文件。`知识资产/` 是正式产出。`系统记录/` 存放管理数据。

---

## 二、资产类型与模板

知识库支持 4 种资产类型，入库时必须使用 `templates/` 中的对应模板：

| type 值 | 模板文件 | 用途 |
|----------|----------|------|
| `video_analysis` | `templates/video_analysis.md` | 视频内容拆解：观点、工具、方法、派生线索 |
| `github_project` | `templates/github_project.md` | GitHub 仓库的中文化评估：功能、用法、风险 |
| `web_clip` | `templates/web_clip.md` | 网页/文章的关键信息提取与结构化 |
| `code_module` | `templates/code_module.md` | 代码模块的能力说明书、接口契约、复刻步骤 |

> 模板定义了必备章节，缺失章节视为不完整资产。SCHEMA.md 只定义跨类型的通用规范。

---

## 三、通用 Frontmatter 规范

所有资产文件必须包含以下 frontmatter（`tags` 必须从第四章标签体系选取）：

```yaml
---
id: 20260617-video-001     # {日期}-{type}-{序号}，全局唯一
type: video_analysis       # 资产类型（4种之一）
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
| `type` | 是 | `video_analysis` / `github_project` / `web_clip` / `code_module` |
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

---

## 四、标签体系

所有标签必须先在此登记，再在资产中使用。新增标签时 agent 必须同步更新本章。

**平台类：** `douyin` `bilibili` `youtube` `github` `zhihu` `weixin` `xiaohongshu` `hackernews` `arxiv` `medium` `substack` `twitter`

**领域类：** `ai-agent` `video-analysis` `code-generation` `knowledge-management` `web-scraping` `api-design` `prompt-engineering` `llm` `rag` `mcp` `tool-use` `browser-automation`

**类型类：** `tutorial` `reference` `case-study` `tool` `library` `framework` `opinion` `news` `paper` `sop`

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

## 视频分析
- [[20260617-douyin-video-download|抖音视频下载]] — Cookie鉴权链路分析 `#douyin` `#video-analysis`

## GitHub项目 / 网页剪藏 / 代码模块
- [[20260616-openai-agents-sdk|OpenAI Agents SDK]] — 官方Agent SDK评估 `#ai-agent` `#library`
```

**更新规则：** 按资产类型分组，组内倒序。每条 `[[文件名|标题]] — 摘要 \`#tag\``。资产标记 `deprecated`/`archived` 时移入「已归档」分组。每次更新顶部日期和总数。

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

**提交频率：** 每次写入操作后立即 commit。一次写入 = 一次 commit。

**提交信息格式：**
- `ingest({type}): {title}` — 入库资产
- `maintenance: {报告描述}` — 维护操作
- `index: 更新 index.md（新增 N 条）` — 索引更新

**回滚：** `git log --oneline -10` 查看历史 → `git reset --soft <hash>` 回滚。

**备份策略：** 本地 vault 为 git 仓库，建议设置 GitHub private repo 作为远程备份，维护扫描后执行 `git gc`。

---

> 📜 本文档是知识库最高法律，agent 每次会话开始时应完整阅读。
> SCHEMA.md 的修订需人工审核，agent 不得自行修改。
