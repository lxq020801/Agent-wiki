# 标签体系规范

> 本文档是 SCHEMA.md 第四章「标签体系」的详细说明。所有标签必须先在 SCHEMA.md 中登记，再在资产中使用。
> 状态说明：本文档包含为兼容当前实现保留的遗留标签与维护规则。只有代码和测试支持的部分才是当前事实，不得据此实现尚不存在的维护命令。

## 核心原则

1. **裸标签**：标签不加 `#` 前缀。`#` 仅用于 index.md 中展示，frontmatter 中为裸字符串。
2. **无层级**：标签平铺，不使用 `/` 或 `>` 分隔符。
3. **全小写 + 连字符**：单词间用 `-` 连接，如 `video-analysis`、`knowledge-management`。
4. **先登记后使用**：需要新标签时，agent 必须先更新 SCHEMA.md 第四章对应分类，再用于资产。
5. **最少 1 个标签**：每个资产至少 1 个标签（SCHEMA 要求 ≥1）。

## 四大分类

### 一、平台类（platform）

标识内容来源平台：

| 标签 | 说明 |
|------|------|
| `douyin` | 抖音 |
| `bilibili` | B 站 |
| `youtube` | YouTube |
| `github` | GitHub |
| `zhihu` | 知乎 |
| `weixin` | 微信公众号 |
| `xiaohongshu` | 小红书 |
| `hackernews` | Hacker News |
| `arxiv` | arXiv |
| `medium` | Medium |
| `substack` | Substack |
| `twitter` | Twitter / X |

### 二、领域类（domain）

标识内容涉及的技术领域：

| 标签 | 说明 |
|------|------|
| `ai-agent` | AI Agent 相关 |
| `video-analysis` | 视频分析与处理 |
| `image-analysis` | 图文/图片理解与拆解 |
| `code-generation` | 代码生成 |
| `knowledge-management` | 知识管理 |
| `creative-pattern` | 创作模式与表达样本 |
| `web-scraping` | 网页抓取 |
| `api-design` | API 设计 |
| `prompt-engineering` | 提示工程 |
| `llm` | 大语言模型 |
| `rag` | 检索增强生成 |
| `mcp` | Model Context Protocol |
| `tool-use` | 工具使用 |
| `browser-automation` | 浏览器自动化 |

### 三、类型类（asset-type）

标识资产的内容形态：

| 标签 | 说明 |
|------|------|
| `knowledge-asset` | 知识资产 |
| `tutorial` | 教程与指南 |
| `reference` | 参考资料 |
| `case-study` | 案例分析 |
| `tool` | 工具 |
| `library` | 代码库 |
| `framework` | 框架 |
| `opinion` | 观点与评论 |
| `news` | 新闻资讯 |
| `paper` | 学术论文 |
| `sop` | 标准操作流程 |

### 四、质量类（quality）

标识资产的验证状态（通常与其他标签搭配使用）：

| 标签 | 说明 |
|------|------|
| `verified` | 已验证可运行或多方印证 |
| `unverified` | 未独立验证 |
| `outdated` | 内容已过时 |
| `incomplete` | 内容不完整 |
| `needs-review` | 需要复核 |

## 正确示例

```yaml
# 抖音知识入库 — 平台 + 资产用途 + 来源形态
tags: [douyin, knowledge-asset, case-study, video-analysis]

# 抖音爆款拆解 — 平台 + 创作模式 + 来源形态
tags: [douyin, creative-pattern, case-study, image-analysis]

# GitHub 项目评估 — 平台 + 领域 + 类型 + 质量
tags: [github, ai-agent, library, verified]

# 网页剪藏 — 平台 + 领域 + 类型
tags: [zhihu, prompt-engineering, opinion]

# 代码模块说明书
tags: [code-generation, tool, sop, verified]
```

## 禁止做法

| ❌ 错误 | ✅ 正确 | 原因 |
|---------|---------|------|
| `#douyin` | `douyin` | 禁止 `#` 前缀 |
| `ai/ml/llm` | `llm` 或 `ai-agent` | 禁止层级分隔符 `/` |
| `AI-Agent` | `ai-agent` | 必须全小写 |
| `抖音` | `douyin` | 禁止中文标签 |
| 自创标签 `my-tag` | 先登记到 SCHEMA.md 再使用 | 禁止使用未登记标签 |

## 标签维护流程

1. 发现需要新标签 → 在 SCHEMA.md 第四章对应分类中追加。
2. 更新 SCHEMA.md 后，在本文件中同步说明（如有必要）。
3. 运行 `/tag-audit` 扫描存量资产，确认无未登记标签。
