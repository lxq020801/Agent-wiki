# Codex 侧当前实现附录

> 这不是主说明书，也不是项目蓝图。主权威仍然是 `SKILL.md` + `SCHEMA.md`。

## 当前口径

obsidian-librarian 的主结构是：

- **harness**：给 AI 的说明书、边界、约束
- **工具**：下载、分析、写库、索引、提交
- **扩展**：抓 Cookie、同步配置、辅助控制

Chrome 扩展是辅助，不是主产品。

## 当前 P0

P0 只做这几件事：

1. 扩展同步 `config.toml`
2. 扩展同步 Douyin Cookie
3. Agent 会话里直接发抖音链接时，完成入库
4. 写入 `知识资产/视频分析/`
5. 更新 `index.md`
6. 提交 git

不做：

- 扩展直接触发入库
- 任务队列看板
- 网页剪藏
- 多平台支持
- 系统级常驻服务

## 当前运行方式

1. 先跑 `python3 install/bootstrap.py`
2. 扩展只负责把配置和 Cookie 写进 `~/.obsidian-librarian/`
3. 真正入库从 `python3 scripts/ingest_url.py "<douyin-url>"` 开始
4. `server/websocket_server.py` 只负责控制面同步和状态确认

## 配置字段

`server/websocket_server.py` 写出的 TOML 必须被 `deps/douyin/scripts/config_loader.py` 直接读懂。核心字段是：

- `[ark].api_key`
- `[ark].endpoint`
- `[models].analyzer`
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

旧的文件桥、Downloads 轮询、扩展直触发任务，属于早期演进资料。它们保留在 `references/` 里，只能当历史看，不再当当前实现口径。

## 看哪里

- `docs/websocket-protocol.md`：控制面协议
- `references/2026-06-27-design-decisions.md`：为什么会改成现在这样
- `references/2026-06-27-extension-rewrite.md`：扩展的现状和边界
- `references/architecture-decisions.md`：更早的决策记录
