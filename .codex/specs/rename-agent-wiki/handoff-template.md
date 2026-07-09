# Agent-wiki Rename Task Handoff Template

Use this template when the controller creates a new task session.

```text
You are working on the same project:

Project path:
/Users/lixinqi/Documents/agent 知识库

Controller branch:
codex/rename-agent-wiki

This is a controlled multi-session product rename. Do not improvise outside the assigned stage.

Before editing:
1. Read `.codex/specs/rename-agent-wiki/spec.md`.
2. Read `.codex/specs/rename-agent-wiki/tasks.md`.
3. Read `.codex/specs/rename-agent-wiki/checklist.md`.
4. Read `.codex/specs/rename-agent-wiki/progress.md`.
5. Check `git status --short --branch`.
6. Confirm you are on `codex/rename-agent-wiki`.

Assigned stage:
<STAGE_NUMBER_AND_NAME>

Stage objective:
<OBJECTIVE>

Allowed edit scope:
<FILES_OR_DIRECTORIES>

Forbidden:
- Do not merge to main.
- Do not push to GitHub.
- Do not delete runtime data.
- Do not use git reset, force-push, or branch deletion.
- Do not start runtime migration unless assigned Stage 7.
- Do not create another child session.
- Do not modify unrelated behavior.
- If your stage fails or becomes unclear, stop and report. The controller owns rollback decisions.

Required final report:
1. Files changed.
2. What changed and why.
3. Checks run and exact results.
4. Any remaining legacy-name hits relevant to this stage.
5. Whether this stage is ready for controller commit.
6. Any rollback, backup, or data-migration risk noticed.
7. Suggested commit message.
```

The controller replaces placeholders before dispatching a task session.
