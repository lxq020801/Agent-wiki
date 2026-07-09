# Agent-wiki Rename Tasks

## Execution Rule

One stage equals one task session and normally one commit. The controller session owns sequencing and updates `progress.md`.

## Stage 0: Preflight and Inventory

- [ ] Confirm current branch is `codex/rename-agent-wiki`.
- [ ] Confirm working tree is clean before edits.
- [ ] Inventory all legacy-name occurrences in tracked active files.
- [ ] Categorize occurrences into display name, internal slug, runtime path, environment variable, protocol/client id, docs/history, tests, and local-only ignored files.
- [ ] Produce a replacement map before editing.

Acceptance:

- Inventory commands and counts are recorded in `progress.md`.
- No product files are changed in this stage unless the controller requests it.

## Stage 1: User-Facing Display Rename

- [ ] Rename README title and product description to `Agent-wiki`.
- [ ] Rename Chrome extension display name and popup title text.
- [ ] Rename launcher and setup user-facing output.
- [ ] Rename high-level docs and SKILL headings.

Acceptance:

- User-visible strings say `Agent-wiki`.
- Internal slug and runtime path changes are not mixed into this stage unless mechanically unavoidable.

## Stage 2: Internal Slug and Protocol Identifiers

- [ ] Replace active internal slug usage with `agent-wiki`.
- [ ] Update WebSocket client identifiers.
- [ ] Update file bridge prefixes.
- [ ] Update User-Agent strings.
- [ ] Update local git bot identity used by ingestion scripts.
- [ ] Update Chrome extension and background identifiers tied to the product slug.

Acceptance:

- Active protocol strings use `agent-wiki`.
- Third-party names remain untouched.

## Stage 3: Runtime Directory and Environment Variables

- [ ] Replace runtime directory defaults with `~/.agent-wiki/`.
- [ ] Replace `*_HOME` variable with `AGENT_WIKI_HOME`.
- [ ] Replace task concurrency variable with `AGENT_WIKI_TASK_CONCURRENCY`.
- [ ] Update config loader, server, installer, analyzer, strategy, executor, status writer, downloader docs, and setup scripts as needed.

Acceptance:

- New code no longer reads old runtime env vars.
- New code no longer defaults to old runtime directory.
- Tests are updated to use new env vars.

## Stage 4: Active Documentation and Reference Rewrite

- [ ] Update active docs, protocol docs, setup guides, runbooks, and handoff files.
- [ ] Update references that describe current product behavior.
- [ ] Update archived references only if they are tracked and would violate final no-legacy-name validation.
- [ ] Decide whether historical context is removed, rewritten, or moved outside the repo.

Acceptance:

- Active docs consistently say `Agent-wiki`.
- Any remaining legacy-name mentions are explicitly justified as temporary and scheduled for final cleanup.

## Stage 5: Tests and Fixtures

- [ ] Update test environment variables.
- [ ] Update expected strings, fixture skill names, runtime paths, file bridge names, and user agent assertions.
- [ ] Run focused tests if dependencies are available.

Acceptance:

- Tests reflect the new product identity.
- Syntax checks pass.

## Stage 6: Full Repository Legacy-Name Sweep

- [ ] Run tracked-file searches for all legacy spellings.
- [ ] Remove or rewrite any remaining old names in active tracked files.
- [ ] Decide what to do with this spec directory before final acceptance.
- [ ] Check ignored local folders separately and report them without deleting unless instructed.

Acceptance:

- Active tracked files contain no old product identity strings.
- Search output is recorded in `progress.md`.

## Stage 7: Runtime Data Migration

- [ ] Inspect whether the old runtime directory exists.
- [ ] Inspect whether `~/.agent-wiki/` already exists.
- [ ] Create a timestamped backup before moving or copying data.
- [ ] Migrate config, cookie, status, inbox, logs, run artifacts, extension files, service files, and response memory.
- [ ] Verify migrated files exist.

Acceptance:

- Runtime data is present under `~/.agent-wiki/`.
- Backup path is recorded.
- No old runtime path is required by code.

## Stage 8: Final Validation and Merge Readiness

- [ ] Run syntax checks for Python and JavaScript files touched by rename.
- [ ] Run tests if `pytest` is available.
- [ ] Run `git status`.
- [ ] Run legacy-name search after removing or relocating temporary spec artifacts.
- [ ] Produce final summary and merge recommendation.

Acceptance:

- Working tree is clean after final commit.
- Branch is ready for user approval to merge into `main`.

## Dependency Order

```text
Stage 0
  -> Stage 1
  -> Stage 2
  -> Stage 3
  -> Stage 4
  -> Stage 5
  -> Stage 6
  -> Stage 7
  -> Stage 8
```

Do not run Stage 7 before code no longer depends on old runtime names.
