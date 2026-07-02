# Codex 交接包（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本目录是 2026-06-27 从 Hermes 迁移到 Codex 时生成的旧交接包，只用于追溯背景。
> 它包含当时的临时 bug、旧 P0、旧协议示例和已废弃方向，不能作为当前 Agent 使用说明书或开发依据。
>
> 当前权威入口请读项目根目录：
> `SKILL.md`、`SCHEMA.md`、`docs/CODEX_PROJECT_DIRECTION.md`、`docs/websocket-protocol.md`、
> `docs/ark-video-understanding.md`、`deps/douyin/SKILL.md`。

## 文件清单

| 文件 | 用途 | 大小 |
|------|------|------|
| `HANDOFF.md` | 项目总览、当前状态、下一步建议 | 4.9K |
| `CHAT_SUMMARY.md` | 会话摘要、需求变化、技术结论 | 2.3K |
| `DECISIONS.md` | 关键技术决策、替代方案、风险 | 3.8K |
| `OPEN_TASKS.md` | 未完成任务（P0-P3） | 3.8K |
| `FILE_MAP.md` | 核心文件清单、状态、需关注项 | 3.7K |
| `RUNBOOK.md` | 运行、调试、验证命令 | 5.7K |
| `TEST_RESULTS.md` | 验证结果、失败项、错误信息 | 3.4K |
| `SENSITIVE_DATA.md` | 敏感信息排除说明 | 2.9K |
| `sanitized-chat.jsonl` | 脱敏会话记录 | 2.4K |
| `project-snapshot.zip` | 项目源码快照（脱敏） | 173K |

## 总览

- **文件数**: 10
- **总大小**: 205K
- **项目路径**: `~/.hermes/skills/obsidian-librarian/`
- **交接包路径**: `~/.hermes/skills/obsidian-librarian/codex-handoff/`

## 快速开始

以下内容为当时迁移建议，已过期；当前不要按这里执行。

1. 读 `HANDOFF.md` — 了解项目全貌
2. 读 `OPEN_TASKS.md` — 了解待办事项
3. 读 `RUNBOOK.md` — 了解如何运行
4. 解压 `project-snapshot.zip` — 获取源码
5. 修复 `server/websocket_server.py` — 最优先

## 关键信息

- **阻塞 bug**: WebSocket 服务器 toml 导入崩溃
- **最优先**: 修复 bug → 端到端测试 → 任务推送
- **用户偏好**: 简洁直接、先加日志、不接触命令行

---

> 生成时间: 2026-06-27
> 生成者: Hermes Agent
> 接收者: Codex
