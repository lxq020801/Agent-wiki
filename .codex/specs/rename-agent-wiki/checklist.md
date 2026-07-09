# Agent-wiki 改名 Acceptance Checklist（验收清单）

## Branch and Workflow（分支和流程）

- [ ] 工作在 `codex/rename-agent-wiki` branch（分支）上进行。
- [ ] 每个 stage（阶段）都有 task-session result（任务会话结果）记录在 `progress.md`。
- [ ] 每个完成阶段都有 commit（提交/版本快照），或有明确记录说明为什么要合并提交。
- [ ] 没有 task session（任务会话）做超出范围的修改。

## Rollback Readiness（回档准备）

- [ ] `main`（主线）在用户最终批准 merge（合并）前保持不变。
- [ ] 起始 `main` commit（主线提交）记录在 `progress.md`。
- [ ] 起始 rename branch commit（改名分支提交）记录在 `progress.md`。
- [ ] 每个已完成 stage commit（阶段提交）记录在 `progress.md`。
- [ ] 任何已提交但失败的阶段，都通过 follow-up commit（后续修复提交）或 revert（反向提交/撤销）处理，并向用户说明。
- [ ] 未经用户明确批准，不执行 `git reset`（重置）、force-push（强制推送）、branch deletion（删除分支）或 runtime-data deletion（删除运行数据）。

## Product Naming（产品命名）

- [ ] 用户可见产品名是 `Agent-wiki`。
- [ ] internal slug（内部短名）是 `agent-wiki`。
- [ ] Chrome extension name（Chrome 扩展名称）是 `Agent-wiki`。
- [ ] README、SKILL、setup output（安装输出）、launcher output（启动器输出）和 protocol docs（协议文档）使用新名称。

## Runtime Identity（运行身份）

- [ ] 默认 runtime directory（运行数据目录）是 `~/.agent-wiki/`。
- [ ] 主要 home env var（主目录环境变量）是 `AGENT_WIKI_HOME`。
- [ ] task concurrency env var（任务并发环境变量）是 `AGENT_WIKI_TASK_CONCURRENCY`。
- [ ] WebSocket client identifiers（WebSocket 客户端标识）使用 `agent-wiki-*`。
- [ ] File bridge names（文件桥接名称）使用 `agent-wiki.*`。
- [ ] User-Agent strings（User-Agent 字符串）使用 `agent-wiki-*`。

## No Legacy Name in Active Tracked Files（当前有效跟踪文件无旧名称）

- [ ] 搜索旧 display name（展示名）时，没有 active tracked-file hits（当前有效跟踪文件命中）。
- [ ] 搜索旧 slug（短名）时，没有 active tracked-file hits。
- [ ] 搜索旧 env var prefix（环境变量前缀）时，没有 active tracked-file hits。
- [ ] 搜索旧 runtime path（运行路径）时，没有 active tracked-file hits。
- [ ] 最终验收前，temporary spec artifacts（临时规格产物）已经删除、移出 repository（仓库/版本库），或被改写到不含旧产品名。

## Runtime Migration（运行数据迁移）

- [ ] 已检查旧 runtime directory（运行数据目录）。
- [ ] 已检查新 runtime directory（运行数据目录）。
- [ ] 迁移前已创建 backup（备份）。
- [ ] backup path（备份路径）已记录在 `progress.md`。
- [ ] config（配置）已迁移或重新生成。
- [ ] cookie 如果存在，已迁移。
- [ ] inbox/status/logs/run-artifacts/response-memory（收件箱/状态/日志/运行产物/响应记忆）如果存在，已迁移。
- [ ] migration verification（迁移验证）已记录。
- [ ] 旧 runtime directory（运行数据目录）没有被删除，除非用户在验证后明确批准删除。

## Validation（验证）

- [ ] 改名涉及的 Python 文件通过 `py_compile`。
- [ ] 改名涉及的 JavaScript 文件在适用时通过 `node --check`。
- [ ] `git diff --check` 通过。
- [ ] focused tests（聚焦测试）通过，或 missing dependency blockers（缺失依赖阻塞）已记录。
- [ ] 最终 `git status --short --branch` 在 final commit（最终提交）后是干净的。

## Final User Approval（最终用户批准）

- [ ] 用户已 review（检查）最终总结。
- [ ] 用户已批准 merge（合并）到 `main`（主线）。
- [ ] 如果配置了 remote（远程仓库），用户已批准 push（推送）到 GitHub。
