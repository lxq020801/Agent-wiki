# Agent-wiki 改名 Task Handoff Template（任务交接模板）

controller（控制会话）创建新的 task session（任务会话）时，使用这个模板。

```text
你正在同一个项目中工作：

Project path（项目路径）：
/Users/lixinqi/Documents/agent 知识库

Controller branch（控制分支）：
codex/rename-agent-wiki

这是一次受控的 multi-session product rename（多会话产品改名）。
不要在被分配的 stage（阶段）之外自由发挥。

Before editing（编辑前）：
1. 阅读 `.codex/specs/rename-agent-wiki/spec.md`。
2. 阅读 `.codex/specs/rename-agent-wiki/tasks.md`。
3. 阅读 `.codex/specs/rename-agent-wiki/checklist.md`。
4. 阅读 `.codex/specs/rename-agent-wiki/progress.md`。
5. 检查 `git status --short --branch`。
6. 确认当前在 `codex/rename-agent-wiki` branch（分支）上。

Assigned stage（分配阶段）：
<STAGE_NUMBER_AND_NAME>

Stage objective（阶段目标）：
<OBJECTIVE>

Allowed edit scope（允许编辑范围）：
<FILES_OR_DIRECTORIES>

Forbidden（禁止事项）：
- 不要 merge（合并）到 main（主线）。
- 不要 push（推送）到 GitHub。
- 不要删除 runtime data（运行数据）。
- 不要使用 git reset（重置）、force-push（强制推送）或 branch deletion（删除分支）。
- 除非分配的是 Stage 7，否则不要开始 runtime migration（运行数据迁移）。
- 不要创建另一个 child session（子会话）。
- 不要修改 unrelated behavior（无关行为）。
- 如果你的阶段失败或变得不清楚，停止并报告。rollback decisions（回档决策）由 controller（控制会话）负责。

Required final report（必须汇报）：
1. Files changed（改动文件）。
2. What changed and why（改了什么，为什么改）。
3. Checks run and exact results（运行了哪些检查，准确结果是什么）。
4. Any remaining legacy-name hits relevant to this stage（本阶段相关的剩余旧名称命中）。
5. Whether this stage is ready for controller commit（本阶段是否准备好由控制会话提交）。
6. Any rollback, backup, or data-migration risk noticed（发现的回档、备份或数据迁移风险）。
7. Suggested commit message（建议提交说明）。
```

controller（控制会话）在派发 task session（任务会话）前替换占位符。
