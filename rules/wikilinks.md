# Wikilink 规范

> 本文档服从 SCHEMA.md 的相关约束。wikilink 中的文件名必须遵循命名规范 `{YYYYMMDD}-{slug}`。

## 基本格式

```
[[文件名|显示文本]]
```

| 组成部分 | 说明 |
|----------|------|
| `文件名` | 不含 `.md` 扩展名，使用 `{YYYYMMDD}-{slug}` 格式。大小写不敏感（但推荐全小写以匹配文件名） |
| `显示文本` | 可选。省略时直接显示文件名；填写时显示自定义文本 |

## 书写规则

| 场景 | 示例 | 说明 |
|------|------|------|
| 标准引用 | `[[20260617-douyin-video-download-cookie-auth]]` | 直接引用目标笔记 |
| 别名引用 | `[[20260617-douyin-video-download-cookie-auth\|抖音视频下载]]` | 自定义显示文本为笔记标题 |
| 标题锚点 | `[[20260617-douyin-video-download-cookie-auth#鉴权机制]]` | 跳转到笔记内某个标题 |
| 块引用 | `[[20260617-douyin-video-download-cookie-auth#^block-id]]` | 引用笔记内特定段落块 |
| 嵌入引用 | `![[20260617-douyin-video-download-cookie-auth]]` | 将目标笔记的完整内容嵌入当前页 |

## Frontmatter 中的 wikilink

`related` 字段使用 wikilink 数组关联相关笔记：

```yaml
related:
  - "[[20260616-openai-agents-sdk|OpenAI Agents SDK]]"
  - "[[20260615-mcp-protocol-analysis|MCP 协议分析]]"
  - "[[20260614-browser-automation-tools|浏览器自动化工具]]"
```

## 交叉引用约束

1. **必须指向存在的笔记**：`related` 及正文中的所有 wikilink 目标文件必须真实存在。死链在 `/link-check` 扫描时会被标记为错误。
2. **双向链接**：被引用的笔记应在开头用 `## 被引用` 章节反向列出引用者。
3. **枢纽笔记**：被引用数 ≥ 2 的笔记标记为「枢纽笔记」，在 `index.md` 中高亮标注。
4. **禁止孤岛**：新笔记入库 24 小时内必须建立至少一条双向链接（入链或出链）。

## 禁止做法

| ❌ 错误 | ✅ 正确 | 原因 |
|---------|---------|------|
| `[[vid-20250617-xxx]]` | `[[20250617-topic-slug]]` | 禁止旧的 type 前缀格式 |
| `[[Transformer架构]]` | `[[20250617-transformer-architecture\|Transformer架构]]` | 禁止中文文件名 |
| `[[20250617-xxx.md]]` | `[[20250617-xxx]]` | 禁止 `.md` 扩展名 |
| `[[20250617-xxx|]]` | `[[20250617-xxx]]` | 禁止空的显示文本 |

## 自动化

- **`/link-check`**：扫描所有 wikilink 有效性，生成断链报告。
- **`lint`**：每次提交前运行 wikilink 完整性校验，死链阻断提交。
- **`backlink`**：新增引用时自动更新目标笔记的「被引用」章节。
