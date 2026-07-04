# Obsidian Librarian

Obsidian Librarian 是一个 Agent 驱动的个人知识资产系统。它把抖音视频/图文等外部内容沉淀进 Obsidian，让未来 AI 工作可以从本地知识资产出发。

项目由三部分组成：

- **大脑 Harness**：`SCHEMA.md`、`SKILL.md`、`rules/`、`templates/`，告诉 Agent 这是什么、怎么写、什么不能碰。
- **手脚 Tools**：`deps/douyin/scripts/`、`scripts/`，负责下载、分析、写库、更新索引、git commit。
- **辅助 Extension**：`chrome-extension/`，负责 Cookie、模型配置、任务入口和任务状态。

Chrome 扩展不是主产品，也不直接拆解内容；真正的编排和执行在 Agent 本地工具链里。

## 当前主链路

抖音视频入库走普通豆包 / 火山方舟 Ark：

```text
下载视频
-> Files API 上传
-> 等待 file active
-> Responses API + input_video.file_id + store=true
-> 写入 Obsidian
-> 更新 index.md
-> git commit
```

扩展提交任务时只发送 URL、页面线索和入库意图：

- `knowledge_ingest`：知识入库，写入 `知识资产/知识入库/`
- `viral_breakdown`：爆款拆解，写入 `知识资产/创作模式/`
- 完整入库：一次任务同时执行上面两种意图

## Ark 视频策略

模型分工：

- Agent 模型：项目外部调用工具的决策模型，读取 Harness/Skill，负责知识库层面的判断与维护。
- `doubao-seed-2-0-lite-260428`：视频拆解工具的主分析模型，负责分片精拆、最终汇总、标题/摘要/标签候选、派生任务线索。
- `doubao-seed-2-0-mini-260428`：视频拆解工具的策略模型，只负责长视频 `1fps` 全片概览、分段 fps 决策和概览 JSON 修复。

- 默认质量档：`quality`
- 安全目标：`1250` 帧
- Ark 硬上限：约 `1280` 帧
- fps 范围：`0.2 - 5`
- `<= 250s`：保持 `5fps`
- `> 250s`：按 `1250 / 视频秒数` 下调 fps
- `> 10 分钟`：先全片概览，再自动切片精拆

长视频切片：

- 先用全片 `1fps` 做概览，提取粗内容、粗时间线和分段精拆策略
- 概览和策略由 mini 执行；如果 JSON 格式坏掉，mini 最多修复一次
- 每片 `240s`
- 重叠 `10s`
- 步长 `230s`
- 每片按策略使用 `2-5fps`
- 分片上传和精拆默认 `2` 路并发，避免长视频串行过慢
- 低置信、缺证据、JSON 无效或概览失败时，向 `5fps` 保守回退
- 修复失败和 fps 上调会写入 `~/.obsidian-librarian/logs/video-strategy-events.jsonl`
- 逐片分析后，再用全片概览和分片结果汇总为最终资产正文

## Responses 记忆

视频分析请求会使用 `store=true` 并保存返回的 `response_id`。

本地记忆位置：

```text
~/.obsidian-librarian/responses-memory/
```

记忆 key 按 `media_type + aweme_id/source_id + ingest_intent + model` 生成。`知识入库` 和 `爆款拆解` 分开记忆，避免上下文串味。

注意：

- `response_id` 不写入 Obsidian Markdown/frontmatter。
- `response_id` 不写入任务状态面板或策略日志，只保存在本地短期记忆索引。
- 本地短期记忆默认保存 `3` 天，避免超过 Ark Responses 默认保存期后继续复用失效上下文。
- 这是短期模型上下文，不是长期知识记忆。
- Files API 文件可用期和 Responses 记忆是两件事。

## 运行时目录

```text
~/.obsidian-librarian/
├── config.toml
├── cookie/
├── cache/
├── status/
├── logs/
├── responses-memory/
└── extension/
```

敏感信息只放在本地 runtime，不写入项目文档、知识库笔记或最终回复。

## 快速开始

准备环境：

```bash
python3 install/bootstrap.py
```

启动控制服务：

```bash
python3 server/launcher.py
```

安装扩展：

```text
chrome://extensions/
-> 开发者模式
-> 加载已解压的扩展程序
-> 选择 ~/.obsidian-librarian/extension/
```

在扩展里完成：

- 填普通 Ark API Key
- 填模型 ID
- 同步抖音 Cookie
- 识别或选择 Obsidian vault

Agent 或扩展提交抖音链接后，工具链会自动入库。

## Agent Plan 取舍

Agent Plan 已验证过小视频 inline base64 可以调用 `/api/plan/v3/responses`，但它没有可用的 Files API 链路：

- `/api/plan/v3/files`：404
- Agent Plan Key 调 `/api/v3/files`：401
- fake `file_id`：失败
- base64 小视频：成功

因此当前产品运行通道删除 Agent Plan，只保留普通 Ark。旧 `provider = "volcengine_agent_plan"` 会回落为 `doubao`，但旧 Agent Plan Key 不会自动迁移为普通 Ark Key。

## 验证

```bash
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py install/bootstrap.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
node --check chrome-extension/background.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

更多接口细节见：

- `docs/ark-video-understanding.md`
- `docs/websocket-protocol.md`
- `deps/douyin/SKILL.md`
