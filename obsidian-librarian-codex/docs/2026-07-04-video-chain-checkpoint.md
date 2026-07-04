# 2026-07-04 视频拆解链路 Checkpoint

本文件用于在继续改长视频策略前保存当前状态。它只记录可回溯的工程信息，不记录 Cookie、API Key、真实日志或其他敏感内容。

## 项目定位

当前项目按三件套理解：

- 大脑 Harness：`SKILL.md`、`SCHEMA.md`、`rules/`、`templates/`，负责告诉 Agent 这是什么项目、知识资产怎么写、什么能碰、什么不能碰。
- 手脚 Tools：`deps/douyin/scripts/`、`scripts/`、`server/`，负责下载、上传、分析、写库、状态同步。
- 辅助 Extension：`chrome-extension/`，负责 Cookie 同步、模型配置、知识库状态、任务入口和任务进度展示。

Obsidian Vault 是长期事实源。扩展是轻入口，不负责业务主编排。

## 当前已落地能力

- 抖音视频和图文任务可以通过扩展或 Agent 入口提交。
- 入库意图已收敛为两类：
  - `knowledge_ingest`：知识入库，沉淀知识、工具、项目、方法、步骤、风险。
  - `viral_breakdown`：爆款拆解，沉淀创作模式、文案结构、节奏、画面和可迁移方法。
- 扩展侧已提供任务列表和状态显示。
- 服务端保留普通豆包 Ark 通道，运行时不再走 Agent Plan。
- Agent Plan 只在文档中作为历史验证路线记录。
- 视频主链路为：Files API 上传 -> 等待文件 `active` -> Responses API 使用 `file_id` 分析。
- Responses 记忆已加入：
  - 请求使用 `store=true`。
  - 保存返回的 `response_id`。
  - 本地记忆目录：`~/.obsidian-librarian/responses-memory/`。
  - 记忆 key 由 `media_type + source_id/aweme_id + ingest_intent + model` 组成。
  - `response_id` 不写入 Obsidian frontmatter 或正文。
- 长视频基础切片已加入：
  - 当前阈值：`duration > 600s` 触发切片。
  - 当前片长：`240s`。
  - 当前重叠：`10s`。
  - 当前流程：每段独立上传和分析，最后做文本汇总。

## 当前阈值共识

- Ark 官方视频抽帧 fps 范围按 `0.2-5` 处理。
- 项目安全帧数目标按 `1250` 处理，给官方 `1280` 帧上限留冗余。
- 短视频优先效果：
  - `<=250s`：`5fps`。
  - `250-600s`：按 `1250 / duration` 动态降 fps，最低约 `2.08fps`。
  - `>600s`：进入长视频策略。
- 用户确认：长视频以 `2fps` 作为精拆下限，效果优先，时间第二，成本第三。

## 下一步要做的策略升级

目标是把当前“固定切片 + 每段 5fps”升级为“全片概览 + 分段自适应精拆”：

1. 对 `>600s` 视频先做一次全片 `1fps` 概览。
2. 概览不只是分类，还要粗略拆出：
   - 视频大概说了什么。
   - 粗时间线和章节。
   - 重要概念、例子、结论。
   - 哪些片段需要高密度精拆。
3. 概览阶段输出分段策略：
   - 每段推荐 `2-5fps`。
   - 给出证据、风险、重点关注内容和置信度。
   - 不做死板规则，例如“访谈一定 2fps”；让模型按画面变化、字幕/OCR、操作密度、信息风险来判断。
4. 每个 240 秒片段按自己的 fps 重新上传和分析。
5. 最终汇总时同时使用全片概览和各段精拆结果。
6. 如果概览失败、策略 JSON 无效或置信度低，默认向高 fps 保守回退。

## 已验证命令

最近一次已通过的验证命令：

```bash
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py deps/douyin/scripts/status_writer.py server/websocket_server.py install/bootstrap.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
node --check chrome-extension/background.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

## 运行时同步位置

当前开发目录：

```text
/Users/lixinqi/Documents/agent 知识库/obsidian-librarian-codex
```

当前服务运行目录：

```text
~/.obsidian-librarian/service/
```

当前扩展加载目录：

```text
~/.obsidian-librarian/extension/
```

常用同步命令：

```bash
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude 'logs' --exclude 'deps/douyin/.venv' ./ ~/.obsidian-librarian/service/
rsync -a --delete --exclude '.DS_Store' chrome-extension/ ~/.obsidian-librarian/extension/
```

## 注意事项

- 不提交 `logs/`、`.DS_Store`、运行时 Cookie、真实密钥。
- 不把 `response_id` 写入 vault Markdown。
- 同一个来源视频可以分别生成知识资产和创作模式资产，但两条链路的 Responses 记忆要隔离。
- 扩展只提交任务意图和页面线索；最终分类、下载、分析、写库由 Agent/工具完成。
