# 文件命名规范

> 本文档服从 SCHEMA.md 第五章「命名规范」的全部约束。
> 状态说明：本文档包含为兼容当前实现保留的遗留规则。只有代码和测试支持的部分才是当前事实，不得据此定义未来产品方向。

## 命名格式

```
{YYYYMMDD}-{slug}.md
```

| 组成部分 | 说明 |
|----------|------|
| `YYYYMMDD` | 入库日期，与 frontmatter 中 `ingested` 日期一致。如 `20260617` |
| `slug` | 英文 slug，反映核心主题。全部小写、连字符分隔、≤60 字符、仅含 `a-z` `0-9` `-` |

## Slug 规则

1. **全部小写**：`Douyin-Video` → `douyin-video`
2. **连字符分隔**：`video download` → `video-download`
3. **≤60 字符**：超长省略，保留关键信息
4. **仅含 `a-z` `0-9` `-`**：禁止中文、空格、下划线、特殊符号
5. **反映核心主题**：不用无意义的数字串或通用词
6. **禁止拼音**：使用英文关键词翻译，不使用拼音（如不用 `douyin-shi-pin-xia-zai`）

## 正确示例

| 笔记标题 | 文件名 |
|----------|--------|
| 抖音视频下载：Cookie 鉴权链路分析 | `20260617-douyin-video-download-cookie-auth.md` |
| OpenAI Agents SDK 评估报告 | `20260616-openai-agents-sdk.md` |
| React 19 新特性解读 | `20260615-react-19-new-features.md` |
| Transformer 架构深入理解 | `20260614-transformer-architecture-deep-dive.md` |
| LangChain 源码分析 | `20260610-langchain-source-code-analysis.md` |

## 禁止示例

| ❌ 错误 | 问题 | ✅ 正确 |
|---------|------|---------|
| `vid-20250617-transformer.md` | 禁止 type 前缀 | `20250617-transformer-architecture.md` |
| `20260617.md` | 缺少 slug | `20260617-topic-slug.md` |
| `抖音视频下载.md` | 中文文件名 | `douyin-video-download.md` |
| `Video Download.md` | 空格和大写 | `video-download.md` |
| `20260617-dou-yin-shi-pin-xia-zai.md` | 拼音 slug | `20260617-douyin-video-download.md` |

## 目录结构

文件存放位置必须遵循 SCHEMA.md 第一章定义的 vault 目录结构：

```
vault/
├── SCHEMA.md                 ← 当前结构和字段契约
├── index.md                  ← 知识库总索引
├── templates/                ← 来源模板与资产模板的 Markdown 骨架
├── raw/                      ← 原始抓取物
│   ├── videos/
│   ├── images/
│   ├── web/
│   └── github/
├── 知识资产/                  ← agent 产出的结构化笔记（正式产出区）
│   ├── 知识入库/
│   ├── 创作模式/
│   ├── GitHub项目/
│   ├── 网页剪藏/
│   └── 代码模块/
├── 系统记录/                  ← agent 自动生成
│   ├── 维护报告/
│   ├── 回收站/
│   └── 变更日志/
├── .obsidian/                ← 【红线】agent 严禁读取或修改
└── .git/
```

## 命名约束

- **新建资产**：按 `{YYYYMMDD}-{slug}.md` 格式，存入 `知识资产/{对应子目录}/`。
- **同名冲突**：禁止覆盖。在 slug 末尾追加 `-2`、`-3` 等序号区分。
- **附件文件**（图片、PDF 等）：存放于 `assets/{YYYYMMDD}-{slug}/` 目录下，以保持与笔记的对应关系。
- **文件名不可修改**：入库后文件名即固定（与 frontmatter `id` 耦合），不得重命名。
