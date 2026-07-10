# Codex 侧当前实现附录

> 这不是主说明书，也不是项目蓝图。主权威仍然是 `SKILL.md` + `SCHEMA.md`。

## 当前口径

agent-wiki 的主结构是：

- **harness**：给 AI 的说明书、边界、约束
- **工具**：下载、分析、写库、索引、提交
- **扩展**：抓 Cookie、同步配置、辅助控制

Chrome 扩展是辅助，不是主产品。

## 实现阶段口径

P0 只表示实现顺序，不决定知识库宪法和资产模型。当前优先跑通这几件事：

1. 扩展同步 `config.toml`
2. 扩展同步 Douyin Cookie
3. Agent 会话或扩展页面入口提交抖音/图文入库任务
4. 根据 `ingest_intent` 写入 `知识资产/知识入库/` 或 `知识资产/创作模式/`
5. 更新 `index.md`
6. 提交 git

不做：

- 网页剪藏
- 多平台支持
- 系统级常驻服务

## 资产模型

当前使用双轴模型：

- 来源维度：`source_media = douyin_video | douyin_image_post | webpage | github | manual | other`
- 资产用途维度：`asset_family = knowledge_asset | creative_pattern | github_project | code_module | idea_asset`

抖音入口当前只开放两类意图：

- `knowledge_ingest`：知识入库
- `viral_breakdown`：爆款拆解

扩展可提供“完整入库”按钮，但它不是新的资产类型，只是一次任务同时提交
`ingest_intents = [knowledge_ingest, viral_breakdown]`。执行层应复用同一来源素材，
视频链路复用同一个 Ark `file_id`，最后写出两篇互相可关联的资产。

## 当前运行方式

1. 先跑 `python3 install/bootstrap.py`
2. 扩展负责把配置、Cookie、任务意图和页面线索写进 `~/.agent-wiki/`
3. Agent 会话入口从 `python3 scripts/ingest_url.py "<douyin-url>" --intent knowledge_ingest` 开始
4. 扩展入口由 `server/websocket_server.py` 写入 inbox，再由本地执行层运行 `ingest.py --task`

## 配置字段

`server/websocket_server.py` 写出的 TOML 必须被 `deps/douyin/scripts/config_loader.py` 直接读懂。核心字段是：

- `[ark].api_key`
- `[ark].endpoint`
- `[models].analyzer`
- `[models].strategy`
- `[models].analyzer_fallback`
- `[analysis].default_quality`
- `[analysis].balanced_target_frames`
- `[analysis].quality_target_frames`
- `[analysis].fps_min`
- `[analysis].fps_max`
- `[analysis].file_active_timeout_sec`
- `[douyin].cookie_path`
- `[vault].path`
- `[vault].relative_root`

## 历史说明

旧的文件桥、Downloads 轮询、扩展直接执行业务编排，属于早期演进资料。它们保留在 `references/` 里，只能当历史看，不再当当前实现口径。

## 看哪里

- `docs/websocket-protocol.md`：控制面协议
- `references/2026-06-27-design-decisions.md`：为什么会改成现在这样
- `references/2026-06-27-extension-rewrite.md`：扩展的现状和边界
- `references/architecture-decisions.md`：更早的决策记录
