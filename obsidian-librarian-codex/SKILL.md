---
name: obsidian-librarian
description: "给 Codex 看的中文项目说明书：定义 obsidian-librarian 的 harness、工具、扩展三层形态、vault 宪法、当前运行口径与红线；用于抖音入库、配置/Cookie 同步和知识库维护。"
---

# Obsidian Librarian

> 这是给 AI 看的项目说明书，不是开发计划书，也不是项目回顾。

obsidian-librarian 是一个面向 Agent 的本地知识库系统。项目只有三个产物：

- **harness**：给 AI 的说明书、约束、边界、入库规则、失败处理
- **工具**：真正执行下载、分析、写库、索引、提交的脚本
- **扩展**：只负责把配置和 Cookie 这类辅助信息送进来，降低用户操作门槛

AI 是主执行面；Chrome 扩展只是辅助控制台。

## 这个项目是什么

用户把抖音链接交给 Agent，Agent 自动完成下载、拆解、写入知识库、更新索引、提交 git，再把结果回给用户。

## harness 的四层

这层是“给 AI 的脑子”：

| 层 | 名称 | 作用 | 主要文件 |
|---|---|---|---|
| L1 | 宪法层 | 定义 vault 的法律、红线、目录、frontmatter | `SCHEMA.md` |
| L2 | 说明书层 | 告诉 AI 这个项目是什么、能做什么、不能做什么 | `SKILL.md`、`references/agent-harness-*.md` |
| L3 | 操作层 | 定义具体工具怎么跑、输入输出怎么对齐 | `deps/douyin/SKILL.md`、`scripts/`、`templates/`、`rules/` |
| L4 | 校验层 | 定义检查、静态验证、提交前自检 | `tests/`、git 提交规则、secret 扫描 |

旧的中文 harness 思路以这四层为骨架；现在新增的 WebSocket、bootstrap、Ark 配置同步，只是把这个骨架落到可执行层。

## 权威顺序

按这个顺序理解项目，后面的文档只做补充，不抢主位：

1. `SCHEMA.md` - vault 宪法，定义能写什么、怎么写、什么不能碰。
2. `SKILL.md` - 本文档，定义 AI 如何理解这个项目、如何使用它、如何守边界。
3. `deps/douyin/SKILL.md` - 视频拆解工具层说明。
4. `templates/` 和 `rules/` - 输出骨架与校验规则。
5. `docs/CODEX_PROJECT_DIRECTION.md` - 当前实现口径附录，不是主宪法。
6. `references/` - 历史记录、研究材料、设计理由，只用于参考。

## 当前标准工作流

当用户在 Agent 会话里发来抖音链接时，默认按这个路径走：

1. 先跑 `python3 install/bootstrap.py`
2. 如果发现缺 `API Key` 或 `Cookie`，不要让用户把秘密贴进聊天；只提示去扩展里补
3. 知识库路径优先由 Agent 按“知识库发现协议”自动识别；失败时再让用户在扩展里选择/提供路径线索
4. 再跑 `python3 scripts/ingest_url.py "<douyin-url>"`，该入口固定走 `quality` 档
5. 工具链自动下载、分析、写入 `知识资产/视频分析/`、更新 `index.md`、执行 git commit
6. 最后只回用户：写入路径、结果摘要、是否提交成功

## 运行态

当前运行态默认落在：

```text
~/.obsidian-librarian/
├── config.toml      --- 扩展写入，Agent 读取
├── cookie/
├── cache/
├── status/
├── logs/
└── extension/
```

其中：

- `config.toml` 保存 Ark 配置、Agent 已确认的 vault 路径、分析参数
- `cookie/douyin.txt` 保存抖音 Cookie
- `status/` 保存运行状态
- `logs/` 保存诊断日志

目标 vault 优先由 Agent 自动发现：先读 Obsidian 本地 vault 登记和 iCloud Obsidian 目录，再查常见文档目录和用户提供的路径线索。只有识别失败时，扩展才作为兜底让用户选择或输入路径。

## 控制面

需要配置同步或 Cookie 同步时，启动 WebSocket 控制服务：

```bash
python3 server/launcher.py
```

当前 P0 只接受这几类消息：

| 方向 | 消息 | 用途 |
|---|---|---|
| 扩展 -> Agent | `handshake` | 连接检查 |
| 扩展 -> Agent | `config_update` | 写完整 `config.toml` |
| 扩展 -> Agent | `status_request` | 拉取 Agent / vault / 模型 / Cookie 状态 |
| 扩展 -> Agent | `vault_discover` | 按知识库发现协议自动识别 vault |
| 扩展 -> Agent | `vault_pick` | 通过本地 Agent 弹系统文件夹选择器 |
| 扩展 -> Agent | `model_check` | 轻量检查模型配置是否可连接 |
| 扩展 -> Agent | `cookie_update` | 写 Douyin Cookie 文件 |
| Agent -> 扩展 | `agent_ready` | 服务已连接 |
| Agent -> 扩展 | `status_snapshot` | 当前状态快照 |
| Agent -> 扩展 | `vault_status` | vault 识别结果 |
| Agent -> 扩展 | `model_status` | 模型检查结果 |
| Agent -> 扩展 | `config_synced` | 配置已写入 |
| Agent -> 扩展 | `cookie_synced` | Cookie 已写入 |
| Agent -> 扩展 | `task_rejected` | 扩展直触发任务被拒绝 |

`task_request`、任务队列、进度看板都不是 P0。收到后要拒绝，不要在扩展里直接起入库。

## 扩展只做什么

扩展只做辅助，不做主编排：

- 抓取 Cookie
- 保存 API Key、模型 ID，显示/兜底选择 vault 路径
- 显示连接状态
- 作为用户的最小操作面

扩展不负责：

- 直接触发入库任务
- 取代 Agent
- 充当业务调度器

## 工具层怎么用

视频入库的主入口是：

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

它会自动：

1. 跑 bootstrap
2. 读 `~/.obsidian-librarian/config.toml`
3. 用 Cookie 下载视频
4. 固定按 `quality` 档调 Ark 做视频分析
5. 按 `SCHEMA.md` 写入 vault
6. 更新 `index.md`
7. 只提交这次改动过的文件

## 写入规则

工具链写库时必须满足：

- frontmatter 服从 `SCHEMA.md`
- 标题、标签、索引都要中文优先
- 不写真实密钥、Cookie、token、session、日志原文
- 写完必须更新 `index.md`
- 必要时执行 git 提交

## 红线

1. 不要把 `.obsidian/` 当普通目录处理
2. 不要把秘密写进任何 markdown、frontmatter、日志、回复
3. 不要让用户为了使用而去开终端、填配置文件、手动跑脚本
4. 不要把扩展写成主产品
5. 不要把历史资料当当前口径

## 预留位

先留位置，不在当前阶段承诺实现：

- 网页入库 / 网页剪藏
- 多平台来源
- 任务进度看板
- 快捷指令
- 知识库召回 / 搜索增强

## 已经被替代的旧说法

历史上的文件桥、Downloads 轮询、扩展直接触发任务，属于旧演进记录，不是当前实现口径。
如果看到这类内容，把它当历史资料，不要当成现在的正确答案。

## 需要时再读的资料

- `SCHEMA.md`：vault 宪法
- `docs/websocket-protocol.md`：当前控制面协议
- `docs/CODEX_PROJECT_DIRECTION.md`：当前实现附录
- `references/agent-harness-framework.md`：四层 harness 理论
- `references/agent-harness-research.md`：Anthropic / OpenAI / 业界调研
- `references/2026-06-27-design-decisions.md`：为什么当前口径会变成这样

## 验证

如果你要确认这套说明书和工具链是否还对得上，跑：

```bash
python3.11 tests/test_p0_static.py
```

如果要看当前配置是否能落盘、WebSocket 是否能写入、Cookie 权限是否正常，就优先看 `server/websocket_server.py` 和 `deps/douyin/scripts/config_loader.py` 的真实字段名，不要凭旧记忆猜。
