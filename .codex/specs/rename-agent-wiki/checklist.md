# Agent-wiki Rename Acceptance Checklist

## Branch and Workflow

- [ ] Work is on `codex/rename-agent-wiki`.
- [ ] Each stage has a task-session result recorded in `progress.md`.
- [ ] Each completed stage has either a commit or a documented reason for batching.
- [ ] No task session performed out-of-scope edits.

## Rollback Readiness

- [ ] `main` remains unchanged until final user-approved merge.
- [ ] Starting `main` commit is recorded in `progress.md`.
- [ ] Starting rename branch commit is recorded in `progress.md`.
- [ ] Every completed stage commit is recorded in `progress.md`.
- [ ] Any failed committed stage is fixed by a follow-up commit or reverted with user-visible reporting.
- [ ] No `git reset`, force-push, branch deletion, or runtime-data deletion is performed without explicit user approval.

## Product Naming

- [ ] User-facing product name is `Agent-wiki`.
- [ ] Internal slug is `agent-wiki`.
- [ ] Chrome extension name is `Agent-wiki`.
- [ ] README, SKILL, setup output, launcher output, and protocol docs use the new name.

## Runtime Identity

- [ ] Default runtime directory is `~/.agent-wiki/`.
- [ ] Primary home env var is `AGENT_WIKI_HOME`.
- [ ] Task concurrency env var is `AGENT_WIKI_TASK_CONCURRENCY`.
- [ ] WebSocket client identifiers use `agent-wiki-*`.
- [ ] File bridge names use `agent-wiki.*`.
- [ ] User-Agent strings use `agent-wiki-*`.

## No Legacy Name in Active Tracked Files

- [ ] Search for old display name returns no active tracked-file hits.
- [ ] Search for old slug returns no active tracked-file hits.
- [ ] Search for old env var prefix returns no active tracked-file hits.
- [ ] Search for old runtime path returns no active tracked-file hits.
- [ ] Temporary spec artifacts have been removed, moved outside the repo, or sanitized before final acceptance.

## Runtime Migration

- [ ] Old runtime directory was inspected.
- [ ] New runtime directory was inspected.
- [ ] Backup was created before migration.
- [ ] Backup path was recorded in `progress.md`.
- [ ] Config migrated or regenerated.
- [ ] Cookie migrated if present.
- [ ] Inbox/status/logs/run-artifacts/response-memory migrated if present.
- [ ] Migration verification recorded.
- [ ] Old runtime directory was not deleted unless the user explicitly approved deletion after verification.

## Validation

- [ ] Python files touched by rename compile with `py_compile`.
- [ ] JavaScript files touched by rename pass `node --check` where applicable.
- [ ] `git diff --check` passes.
- [ ] Focused tests pass, or missing dependency blockers are recorded.
- [ ] Final `git status --short --branch` is clean after final commit.

## Final User Approval

- [ ] User reviewed final summary.
- [ ] User approved merge to `main`.
- [ ] User approved GitHub push if a remote is configured.
