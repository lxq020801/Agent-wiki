# Agent-wiki Full Product Rename Spec

## Status

- Spec status: draft
- Execution status: not started
- Controller branch: `codex/rename-agent-wiki`
- Target product display name: `Agent-wiki`
- Target internal slug: `agent-wiki`
- Target runtime directory: `~/.agent-wiki/`
- Target environment variable prefix: `AGENT_WIKI`

## Purpose

Rename the product from its old identity to `Agent-wiki` from user-facing text down to internal runtime identifiers.

This is a full rename, not a compatibility migration. The final active repository state must use `Agent-wiki` / `agent-wiki` naming throughout.

## Non-Negotiable User Decision

The user chose the strict rename option:

- Do not keep legacy runtime compatibility in code.
- Do not leave legacy product names in active project files.
- The old runtime data will be migrated to the new runtime directory by Codex at the end.
- Codex must back up user runtime data before moving or rewriting local runtime files.

## Important Temporary Exception

During the rename, this spec directory may mention legacy names as search targets and migration targets.

Before final merge to `main`, this directory must be either:

- deleted from the repository, or
- moved outside the repository, or
- rewritten so it contains no legacy product names.

Otherwise final "no legacy name remains" validation cannot pass.

## Product Identity Contract

All active code, docs, tests, UI strings, protocol identifiers, file prefixes, runtime paths, and setup instructions must converge on:

| Role | Final Value |
| --- | --- |
| Product display name | `Agent-wiki` |
| Internal slug | `agent-wiki` |
| Runtime directory | `~/.agent-wiki/` |
| Environment variable home | `AGENT_WIKI_HOME` |
| Task concurrency variable | `AGENT_WIKI_TASK_CONCURRENCY` |
| WebSocket client prefix | `agent-wiki-*` |
| File bridge prefix | `agent-wiki.*` |
| User-Agent prefix | `agent-wiki-*` |
| Local git bot name | `Agent-wiki` |
| Local git bot email | `agent-wiki@local` |

## Scope

The rename applies to active project files under:

- `obsidian-librarian-codex/`
- root project metadata such as `.gitignore`
- Chrome extension metadata and UI text
- Python scripts and server code
- tests
- docs, references, runbooks, handoff files, setup guides

The rename does not modify third-party package names unless the text describes this product's integration identity.

## Runtime Migration Scope

At the end of implementation, Codex should inspect local runtime state and migrate data from the old runtime directory to `~/.agent-wiki/`.

Migration must be guarded:

1. Print source and destination paths.
2. Back up the source directory first.
3. Do not overwrite newer destination files without reporting.
4. Preserve permissions where possible.
5. Verify key files after migration, especially config, cookie, inbox, status, logs, run artifacts, and response memory.

Runtime migration is a final-stage task and must not be mixed with code rename stages.

## Multi-Session Execution Model

This spec is designed for a controller-driven relay:

1. The controller session reads this spec set.
2. The controller creates one task session for the next incomplete stage.
3. The task session executes exactly that stage.
4. The task session reports changed files, checks, blockers, and recommended commit message.
5. The controller verifies the result, updates `progress.md`, commits the stage if appropriate, then creates the next task session.
6. Child task sessions must not create their own child sessions unless the controller explicitly instructs them to.

This keeps automatic execution possible while preventing context drift.

## Branch and Commit Policy

- Use one long-lived rename branch: `codex/rename-agent-wiki`.
- Do not create a branch per stage unless the controller explicitly decides a risky experiment needs isolation.
- Each completed stage should normally produce one commit.
- Do not merge to `main` until all checklist items pass.
- Do not push to GitHub until the user confirms repository setup and visibility.

## Global Safety Rules

- Never use broad destructive cleanup without an explicit stage instruction.
- Never delete user runtime data without a verified backup.
- Never change unrelated behavior while renaming.
- Prefer structured parsers or targeted replacements where files have structured formats.
- Run relevant checks after each stage.
- If tests cannot run because dependencies are missing, report the exact missing dependency and continue only if the controller accepts the risk.

## Final Acceptance Definition

The rename is complete only when:

1. Active project code and docs consistently use `Agent-wiki` / `agent-wiki`.
2. Old product names are absent from active tracked files.
3. New runtime paths and environment variables are used everywhere.
4. Tests and syntax checks have passed or documented dependency blockers are resolved.
5. Runtime data migration to `~/.agent-wiki/` has been backed up and verified.
6. The temporary spec directory no longer causes legacy-name search failures.
7. The final branch is clean and ready to merge.
