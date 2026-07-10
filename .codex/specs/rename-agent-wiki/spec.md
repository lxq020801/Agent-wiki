# Agent-wiki 全量产品改名 Spec（规格说明）

## 状态

- Spec（规格）状态：草稿
- Execution（执行）状态：未开始
- Controller branch（控制分支）：`codex/rename-agent-wiki`
- 目标产品展示名：`Agent-wiki`
- 目标内部 slug（内部短名）：`agent-wiki`
- 目标 runtime directory（运行数据目录）：`~/.agent-wiki/`
- 目标 environment variable prefix（环境变量前缀）：`AGENT_WIKI`

## 目的

把当前产品从旧身份全量改名为 `Agent-wiki`，范围从用户能看到的文字，到内部运行标识。

这是 full rename（全量改名），不是 compatibility migration（兼容迁移）。最终的 active repository state（当前有效仓库状态）必须统一使用 `Agent-wiki` / `agent-wiki`。

## 用户已确定的不可变决定

用户选择的是严格全量改名：

- 代码里不保留旧 runtime（运行时）兼容逻辑。
- active project files（当前有效项目文件）里不保留旧产品名。
- 旧 runtime data（运行数据）会在最后由 Codex 迁移到新的运行数据目录。
- Codex 在移动或改写本地运行数据前，必须先备份用户数据。

## 重要临时例外

改名过程中，这个 spec directory（规格目录）曾临时记录旧身份，用作 search target（搜索目标）和 migration target（迁移目标）。

在最终 merge（合并）到 `main`（主线）之前，这个目录必须做以下三选一：

- 从 repository（仓库/版本库）中删除；
- 移到 repository（仓库/版本库）外；
- 改写到不再包含旧产品身份字符串。

否则最终的“没有旧名称残留”验证不能通过。

## 产品身份约定

所有 active code（有效代码）、docs（文档）、tests（测试）、UI strings（界面文字）、protocol identifiers（协议标识）、file prefixes（文件前缀）、runtime paths（运行路径）和 setup instructions（安装说明）都必须收敛到：

| 角色 | 最终值 |
| --- | --- |
| Product display name（产品展示名） | `Agent-wiki` |
| Internal slug（内部短名） | `agent-wiki` |
| Runtime directory（运行数据目录） | `~/.agent-wiki/` |
| Environment variable home（主目录环境变量） | `AGENT_WIKI_HOME` |
| Task concurrency variable（任务并发环境变量） | `AGENT_WIKI_TASK_CONCURRENCY` |
| WebSocket client prefix（WebSocket 客户端前缀） | `agent-wiki-*` |
| File bridge prefix（文件桥接前缀） | `agent-wiki.*` |
| User-Agent prefix（User-Agent 前缀） | `agent-wiki-*` |
| Local git bot name（本地 Git 机器人名称） | `Agent-wiki` |
| Local git bot email（本地 Git 机器人邮箱） | `agent-wiki@local` |

## 范围

改名适用于以下 active project files（当前有效项目文件）：

- `agent-wiki/`
- 根目录项目元数据，例如 `.gitignore`
- Chrome extension（Chrome 扩展）的元数据和 UI 文字
- Python 脚本和 server code（服务端代码）
- tests（测试）
- docs（文档）、references（参考资料）、runbooks（操作手册）、handoff files（交接文件）、setup guides（安装指南）

不要修改 third-party package names（第三方包名），除非那段文字描述的是本产品自己的集成身份。

## Runtime Migration Scope（运行数据迁移范围）

实现结束后，Codex 需要检查本地 runtime state（运行数据状态），并把旧运行数据目录的数据迁移到 `~/.agent-wiki/`。

迁移必须加保护：

1. 打印 source path（来源路径）和 destination path（目标路径）。
2. 先备份 source directory（来源目录）。
3. 如果 destination（目标目录）里有更新的文件，不要直接覆盖，必须先报告。
4. 尽量保留 permissions（权限）。
5. 迁移后验证关键文件，尤其是 config（配置）、cookie、inbox（收件箱）、status（状态）、logs（日志）、run artifacts（运行产物）和 response memory（响应记忆）。

runtime migration（运行数据迁移）是最后阶段任务，不能和代码改名阶段混在一起做。

## Multi-Session Execution Model（多会话执行模型）

这套 spec 是为 controller-driven relay（控制会话驱动的接力执行）设计的：

1. controller session（控制会话）读取这套 spec。
2. controller session 为下一个未完成阶段创建一个 task session（任务会话）。
3. task session 只执行被分配的那个阶段。
4. task session 汇报 changed files（改动文件）、checks（检查）、blockers（阻塞点）和建议的 commit message（提交说明）。
5. controller session 验证结果，更新 `progress.md`，如果合适就提交该阶段，然后再创建下一个 task session。
6. child task sessions（子任务会话）不能再创建自己的子会话，除非 controller（控制会话）明确允许。

这样既能支持自动接力执行，也能减少 context drift（上下文漂移）。

## Branch and Commit Policy（分支和提交规则）

- 使用一个长期 rename branch（改名分支）：`codex/rename-agent-wiki`。
- 不要每个阶段都创建新 branch（分支），除非 controller 明确判断某个高风险实验需要隔离。
- 每个完成的阶段通常应该产生一个 commit（提交/版本快照）。
- 所有 checklist（验收清单）通过前，不要 merge（合并）到 `main`（主线）。
- 用户确认 GitHub 仓库设置和可见性之前，不要 push（推送）到 GitHub。

## Rollback and Recovery Plan（回档和恢复方案）

最终 merge（合并）前，`main`（主线）是安全返回点。改名工作只存在于 `codex/rename-agent-wiki`；放弃或暂停这个 branch（分支）时，必须保证 `main` 不受影响。

每个 stage commit（阶段提交）都是 checkpoint（检查点）：

1. Stage 0 开始前，记录 `main` 的起始 commit（提交）。
2. 每个完成阶段的 commit 都要记录到 `progress.md`。
3. 如果某个阶段提交后发现问题，优先用新的 fix commit（修复提交）或针对该阶段 commit 执行 `git revert`（反向提交/撤销某次提交）。
4. 未经用户明确批准，不要使用 `git reset`（重置）、force-push（强制推送）、删除 branch（分支）或删除 runtime data（运行数据）。

runtime data rollback（运行数据回档）和 code rollback（代码回档）是两件事：

1. runtime migration（运行数据迁移）只能在 Stage 7 做。
2. Stage 7 在复制或移动数据前，必须创建并记录 timestamped backup（带时间戳的备份）。
3. 迁移应该先 copy（复制），再 verify（验证）。
4. Stage 7 不得删除旧 runtime directory（运行数据目录），除非用户在验证后明确批准删除。

如果后续 branch 已经 push（推送）或已经创建 pull request（拉取请求/合并请求），恢复方案通常应该是关闭 pull request，或者 revert merge commit（反向撤销合并提交）。不要默认使用 force-push（强制推送）。

## Global Safety Rules（全局安全规则）

- 没有明确阶段指令时，不要做大范围破坏性清理。
- 没有 verified backup（已验证备份）时，不要删除用户 runtime data（运行数据）。
- 改名时不要顺手改 unrelated behavior（无关行为）。
- 对结构化文件，优先使用 structured parsers（结构化解析器）或精确替换，不要粗暴文本乱替换。
- 每个阶段后都要运行 relevant checks（相关检查）。
- 如果 tests（测试）因为缺依赖无法运行，必须报告准确缺失的 dependency（依赖），并且只有 controller 接受风险后才继续。

## Final Acceptance Definition（最终验收定义）

改名只有在以下条件全部满足时才算完成：

1. active project code and docs（当前有效代码和文档）一致使用 `Agent-wiki` / `agent-wiki`。
2. active tracked files（当前有效、被 Git 跟踪的文件）里没有旧产品名。
3. 新 runtime paths（运行路径）和 environment variables（环境变量）已经全局使用。
4. tests（测试）和 syntax checks（语法检查）已通过，或依赖阻塞已经被明确记录并解决。
5. runtime data migration（运行数据迁移）到 `~/.agent-wiki/` 已备份并验证。
6. 临时 spec directory（规格目录）不会导致旧名称搜索失败。
7. 最终 branch（分支）干净，并准备好 merge（合并）。
