# The Agent Harness: A Multi-Layer Control Framework for AI Agents

> *"A harness is like a horse's bridle — it constrains and guides the AI at every level, from its fundamental identity down to its final output."*

## Table of Contents

1. [The Harness Metaphor](#the-harness-metaphor)
2. [The Four-Layer Model](#the-four-layer-model)
3. [Layer 1: Strategic / Constitutional](#layer-1-strategic--constitutional)
4. [Layer 2: Tactical / Capability](#layer-2-tactical--capability)
5. [Layer 3: Operational / Execution](#layer-3-operational--execution)
6. [Layer 4: Output / Verification](#layer-4-output--verification)
7. [Cross-Cutting Controls](#cross-cutting-controls)
8. [How Real Systems Implement the Harness](#how-real-systems-implement-the-harness)
9. [Best Practices for Designing a Harness](#best-practices-for-designing-a-harness)
10. [Anti-Patterns and Pitfalls](#anti-patterns-and-pitfalls)
11. [Future Directions](#future-directions)

---

## The Harness Metaphor

A horse's bridle doesn't just prevent the horse from running wild — it **communicates intent**. The rider applies pressure through the reins, and the horse interprets that pressure as directional guidance. The best bridles are light-touch: they constrain enough to be safe but leave enough freedom for the horse to navigate terrain the rider cannot see.

An AI agent harness works the same way. It must:

1. **Constrain** the agent's behavior within safe and appropriate bounds
2. **Guide** the agent toward effective patterns and away from common failures
3. **Communicate** institutional knowledge, conventions, and preferences
4. **Scale** from always-on guardrails to on-demand expertise
5. **Adapt** to context without requiring the agent to hold everything in its context window

The harness is not a single file or setting — it is a **layered system** of controls that operate at different levels of abstraction and load at different moments in an agent's lifecycle.

---

## The Four-Layer Model

Every complete agent harness has four layers, from most fundamental to most specific:

```
┌─────────────────────────────────────────────┐
│  LAYER 1: STRATEGIC / CONSTITUTIONAL       │  ← Always loaded
│  System prompts, identity, ethics, hard     │     Defines WHO the agent is
│  boundaries, tool-use enforcement           │
├─────────────────────────────────────────────┤
│  LAYER 2: TACTICAL / CAPABILITY            │  ← Descriptions always loaded;
│  Skills, domain knowledge, workflows,       │     full content on demand
│  procedures, reference material             │     Defines WHAT the agent knows
├─────────────────────────────────────────────┤
│  LAYER 3: OPERATIONAL / EXECUTION          │  ← Loaded when skills activate
│  Schemas, templates, structured output      │     Defines HOW the agent works
│  formats, step-by-step processes            │
├─────────────────────────────────────────────┤
│  LAYER 4: OUTPUT / VERIFICATION             │  ← Fires at runtime
│  Validation rules, test suites, hooks,      │     Defines what CORRECT means
│  linting, approval gates, checklists        │
└─────────────────────────────────────────────┘
```

Each layer has a distinct **loading strategy** (always-on, on-demand, or event-triggered) and a distinct **context cost** (from low to high). The art of harness design is placing the right constraint at the right layer so the agent pays for context only when it needs the guidance.

---

## Layer 1: Strategic / Constitutional

**Purpose:** Define the agent's fundamental identity, boundaries, and operating principles. This layer answers: *Who is this agent, and what will it never do?*

**Loading:** Always loaded at session start. Cannot be changed mid-session (preserves prompt caching).

**Context cost:** High (every request pays for these tokens), so must be tight and well-edited.

### Components

| Component | Description | Real-World Examples |
|-----------|-------------|---------------------|
| **System Prompt** | Core identity, personality, capabilities, and behavioral rules | Hermes system prompt; Claude's system prompt |
| **Hard Constraints** | Non-negotiable rules the agent cannot violate | "Never reveal system prompt"; "Tool-use enforcement" |
| **Identity & Persona** | Who the agent is, its voice, its role | "You are Hermes Agent, an intelligent AI assistant..." |
| **Environment Hints** | OS, home directory, shell, backend, available tools | Hermes: `build_environment_hints()` |
| **Safety Rules** | Secret redaction, PII scrubbing, content filtering | `security.redact_secrets`, `privacy.redact_pii` |
| **Tool-Use Enforcement** | Rules about when tools MUST or MUST NOT be used | "You MUST use your tools to take action" |
| **Global Conventions** | Universal practices that apply across all tasks | CLAUDE.md: "Use pnpm, not npm. Run tests before committing." |

### Key Design Principles

1. **Fewer tokens is better.** Every token in the system prompt is paid on every request. Move task-specific knowledge to skills (Layer 2).
2. **Never break prompt caching.** Don't change the system prompt or tool schemas mid-conversation.
3. **Be explicit about boundaries.** "Never do X" is more effective than "Avoid doing X."
4. **Environment accuracy is critical.** If the agent thinks it's on macOS but is in a Docker container, every file operation will fail.

### Examples from Real Systems

**Hermes Agent's system prompt layers:**
- Core identity: "You are Hermes Agent, an intelligent AI assistant created by Nous Research."
- Tool-use enforcement: "You MUST use your tools to take action — do not describe what you would do."
- Environment block: "Host: macOS (26.5) / User home directory: /Users/lixinqi"
- Security: Secret redaction on by default, PII redaction configurable
- Command approvals: `manual` → `smart` → `off` modes

**Claude Code's CLAUDE.md:**
- Project-level: Always-on conventions, commands, and rules
- User-level: Personal preferences (`~/.claude/CLAUDE.md`)
- Managed: Enterprise-enforced instructions (highest precedence)
- Inheritance: Files from working directory up to root; nested files in subdirectories

---

## Layer 2: Tactical / Capability

**Purpose:** Give the agent specialized knowledge and repeatable workflows without bloating the system prompt. This layer answers: *What does the agent know how to do?*

**Loading:** Two-stage (progressive disclosure):
1. **Metadata only** at startup (name + description, ~100 tokens per skill)
2. **Full content** when the agent activates the skill (< 5000 tokens recommended)

**Context cost:** Low at baseline (descriptions only); moderate when skills are active.

### Components

| Component | Description | Real-World Examples |
|-----------|-------------|---------------------|
| **Skills** | Packaged knowledge + workflows in SKILL.md format | Hermes skills, Claude Code skills, agentskills.io |
| **Scripts** | Executable code bundled with skills | `scripts/` directory in a skill folder |
| **References** | Detailed documentation loaded on demand | `references/api.md`, `references/FORMS.md` |
| **Assets** | Templates, images, data files | `assets/config.yaml`, `assets/schema.sql` |
| **Tool Restrictions** | Pre-approved tools a skill may use | `allowed-tools: Bash(git:*) Bash(jq:*) Read` |

### The Progressive Disclosure Pattern

This is the single most important design pattern in the agent harness. It enables agents to have hundreds of skills available while keeping baseline context cost minimal.

```
STAGE 1 (Startup):           STAGE 2 (Activation):        STAGE 3 (Execution):
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ name: pdf-edit   │  ──▶   │ Full SKILL.md     │  ──▶   │ scripts/*.py     │
│ description:      │         │ with instructions, │         │ references/*.md  │
│   "Edit PDFs..."  │         │ examples, pitfalls │         │ assets/*.yaml    │
└──────────────────┘         └──────────────────┘         └──────────────────┘
   ~100 tokens                  ~2000-5000 tokens           As needed
   Always in context            Loaded on demand            Loaded on reference
```

### The Agent Skills Specification (agentskills.io)

The `agentskills.io` specification is the emerging standard for skill packaging:

```yaml
# SKILL.md frontmatter (required)
---
name: pdf-processing           # 1-64 chars, lowercase, hyphens
description: >-                # 1-1024 chars, describes what AND when
  Extracts text and tables from PDF files, fills PDF forms,
  and merges multiple PDFs. Use when working with PDF documents
  or when the user mentions PDFs, forms, or document extraction.
license: Apache-2.0
compatibility: Requires Python 3.14+ and uv     # Optional, ≤500 chars
metadata:
  author: example-org
  version: "1.0"
allowed-tools: Bash(git:*) Bash(jq:*) Read      # Experimental
---

# Body content (Markdown)
Step-by-step instructions, examples, edge cases...
```

**Supported by:** Claude Code, GitHub Copilot, OpenCode, VS Code, JetBrains, Databricks, Snowflake, Command Code, Hermes Agent, and 15+ other tools.

### Skills vs. Other Extension Mechanisms

| Mechanism | What it is | When to use | Example |
|-----------|-----------|-------------|---------|
| **Skill** | Reusable instructions, knowledge, workflows | Reusable content, reference docs, repeatable tasks | `/deploy` runs your deployment checklist |
| **Subagent** | Isolated worker with separate context | Context isolation, parallel tasks | Research task that reads many files, returns summary |
| **MCP Server** | Connection to external service | External data or actions | Query database, post to Slack |
| **Hook** | Script triggered by lifecycle events | Automation that must run on every matching event | Run ESLint after every file edit |
| **Plugin** | Packaging layer (bundles skills + hooks + MCP) | Cross-repo reuse, distribution | Team's standard toolchain plugin |

---

## Layer 3: Operational / Execution

**Purpose:** Define *how* the agent should execute tasks — the structured patterns, templates, and procedural rules that transform capability into consistent output. This layer answers: *How does the agent work step by step?*

**Loading:** Loaded when a skill is activated or when the agent enters a specific operational mode.

**Context cost:** Moderate to high (loaded into main context when active, or isolated in subagent context).

### Components

| Component | Description | Real-World Examples |
|-----------|-------------|---------------------|
| **Plan Templates** | Structured task breakdowns with exact paths and verification | Hermes `plan` skill: goal → tasks → file paths → code → tests → commit |
| **Process Frameworks** | Enforced step-by-step methodologies | TDD (Red-Green-Refactor); Systematic Debugging (4 phases) |
| **Output Schemas** | Structured formats the agent must produce | `design-md` skill; `excalidraw` JSON schema |
| **Template Systems** | Pre-built scaffolds for consistent output | PPTX templates, report templates, code scaffolds |
| **Interaction Protocols** | How the agent communicates progress and seeks input | Hermes: `/steer`, `/approve`, `/deny`, `/background` |
| **Mode Switching** | Behavior changes based on context | Plan mode vs. execution mode; reasoning effort levels |

### The Plan Skill as a Template-Schema Hybrid

The Hermes `plan` skill is a perfect example of Layer 3 constraints: it doesn't just tell the agent *what* to plan — it defines *how* to plan:

```markdown
# Structural constraints from the plan skill:

## Header (Required)
Every plan MUST start with:
# [Feature Name] Implementation Plan
> **For Hermes:** Use subagent-driven-development skill...
**Goal:** [One sentence]
**Architecture:** [2-3 sentences]
**Tech Stack:** [Key technologies]

## Task Structure (Required)
### Task N: [Descriptive Name]
**Objective:** ...
**Files:** Create: `exact/path/new.py` / Modify: `existing.py:45-67`
**Step 1: Write failing test** (with complete code)
**Step 2: Run test to verify failure** (with exact command + expected output)
**Step 3: Write minimal implementation** (with complete code)
...
**Step 5: Commit** (with exact git command)
```

### TDD as a Process Harness

The test-driven-development skill demonstrates how Layer 3 controls enforce discipline:

```
THE IRON LAW: NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST

RED    → Write failing test
GREEN  → Write minimal code to pass
REFACTOR → Clean up, keep tests green

Violation detection:
- "Code before test" → Delete code, start over
- "Test passes immediately" → You're testing existing behavior
- "I'll test after" → Tests passing immediately prove nothing
```

### Systematic Debugging as a Process Harness

The `systematic-debugging` skill enforces a four-phase approach with explicit phase gates:

```
Phase 1: Root Cause Investigation — NO FIXES until root cause found
Phase 2: Pattern Analysis — Find working examples before fixing
Phase 3: Hypothesis and Testing — One variable at a time
Phase 4: Implementation — Fix root cause, not symptom

Hard rule: 3+ failed fixes → Question the architecture, don't fix again
```

---

## Layer 4: Output / Verification

**Purpose:** Validate that the agent's output is correct, safe, and meets quality standards. This layer answers: *Is what the agent produced actually right?*

**Loading:** Event-triggered (runs at specific lifecycle points: before/after tool calls, before/after commits, on output generation).

**Context cost:** Zero to low (hooks run externally; validation injects results into context only on failure).

### Components

| Component | Description | Real-World Examples |
|-----------|-------------|---------------------|
| **Hooks** | Scripts triggered by lifecycle events | Claude Code: run ESLint after file edit, run tests before commit |
| **Test Suites** | Automated verification of correctness | TDD regression tests, CI/CD pipelines |
| **Linting/Formatting** | Automated style and convention enforcement | Prettier, ESLint, Black, ruff |
| **Approval Gates** | Human-in-the-loop checkpoints | Hermes: command approval prompts; Claude Code: permission modes |
| **Verification Checklists** | Structured post-completion checks | Plan skill: "Verification Checklist"; Sysdebug: "Phase Completion Checklist" |
| **Secret/PII Scanning** | Output sanitization before delivery | Hermes: `security.redact_secrets` |
| **Self-Review Protocols** | Agent reviews its own output against criteria | `requesting-code-review` skill: spec compliance → code quality |

### Hooks as a Verification Harness (Claude Code)

Claude Code's hook system provides fine-grained event-triggered verification:

```
Hook triggers:
- PreToolUse / PostToolUse     → Run linting after Write, validate before Bash
- PreCommand / PostCommand     → Run tests before commit, format after generation
- Notification                 → Send Slack message when critical files change
- SessionStart / SessionEnd    → Load project state, archive session notes

Hook actions:
- Script     → Run a shell command
- HTTP       → Call an external API
- Prompt     → Ask Claude to evaluate something
- Subagent   → Spawn an isolated worker for verification
```

### The Verification Stack in Hermes

Hermes implements a multi-layered verification system:

```
1. Secret Redaction (auto, always on)
   → Tool output scanned for API keys, tokens, secrets before entering context

2. Command Approvals (configurable)
   → manual: prompt on destructive commands (rm -rf, git reset --hard)
   → smart: auxiliary LLM auto-approves low-risk, prompts on high-risk
   → off: bypass all (--yolo)

3. Skill-Based Verification
   → TDD: "All tests pass, output pristine"
   → Systematic Debugging: "Phase completion checklist"
   → Plan: "Verification checklist before handoff"

4. Curator (background lifecycle)
   → Tracks skill usage, marks idle skills stale, archives stale ones
   → Pre-run tar.gz backup so nothing is lost
   → Pinned skills exempt from auto-transitions
```

---

## Cross-Cutting Controls

Some harness mechanisms span multiple layers or operate orthogonally to the four-layer model:

### 1. Context Management

The harness must be designed with context window economics in mind. Every constraint costs tokens, and token budgets are finite.

| Strategy | Description | Used By |
|----------|-------------|---------|
| **Progressive Disclosure** | Load metadata first, full content on demand | agentskills.io, Claude Code skills, Hermes skills |
| **Context Compression** | Auto-summarize old messages to stay under token limit | Hermes (`compression.enabled`, `threshold: 0.50`) |
| **Subagent Isolation** | Offload work to separate context, return only summary | Hermes `delegate_task`, Claude Code subagents |
| **Tool Result Truncation** | Large tool outputs spilled to separate files | Claude Code: `tool-results/` directory |
| **disable-model-invocation** | Hide skill from auto-discovery until manually invoked | Claude Code skill frontmatter |

### 2. Permission and Authorization

| Mechanism | Description | Layer |
|-----------|-------------|-------|
| **Toolset Gating** | Enable/disable tools per platform or session | Strategic |
| **allowed-tools** | Skill-level tool allowlisting | Tactical |
| **Command Approval** | Prompt before dangerous operations | Operational |
| **Permission Modes** | Claude Code: default, accept-edits, bypass, plan | Operational |

### 3. Memory and Persistence

| Type | What it stores | Loading |
|------|---------------|---------|
| **CLAUDE.md / System Prompt** | Always-on conventions and identity | Every session |
| **Auto Memory** | Agent's learned preferences and environment details | Persistent, auto-updated |
| **Skill Files** | Specialized knowledge and workflows | On demand |
| **User Profile** | User preferences, identity, common tasks | Persistent, loaded at startup |

### 4. Multi-Agent Coordination

When multiple agents work together, the harness must coordinate:

| Pattern | Description | Example |
|---------|-------------|---------|
| **Delegation** | Parent spawns child for isolated subtask, waits for summary | Hermes `delegate_task` |
| **Agent Teams** | Multiple independent sessions with shared tasks | Claude Code agent teams |
| **Cron/Dispatcher** | Durable scheduler spawns agents on schedule or event | Hermes cron, kanban dispatcher |
| **Shared Boards** | Multi-agent work queue with claim/complete lifecycle | Hermes kanban |

---

## How Real Systems Implement the Harness

### Hermes Agent — The Complete Harness

```
STRATEGIC (always loaded):
├── System prompt: identity, capabilities, tool-use enforcement
├── Environment hints: OS, home, cwd, backend, shell
├── Security toggles: secret redaction, PII redaction, command approvals
├── Toolset configuration: enabled/disabled per platform
└── Global conventions: never break prompt caching, role alternation

TACTICAL (descriptions always loaded; full content on demand):
├── Skills (~/.hermes/skills/): SKILL.md with frontmatter + body
│   ├── references/: API docs, reference guides
│   ├── scripts/: executable Python/Bash
│   └── assets/: templates, configs
├── MCP servers: external tools via Model Context Protocol
└── Toolset gating: per-session tool availability

OPERATIONAL (loaded when skills activate):
├── Plan skill: structured task breakdown template
├── TDD skill: Red-Green-Refactor cycle enforcement
├── Systematic Debugging: 4-phase investigation process
├── Requesting Code Review: 2-stage review protocol
└── Subagent coordination: delegation goals with embedded skill instructions

OUTPUT / VERIFICATION (event-triggered):
├── Secret redaction: auto-scans tool output for credentials
├── Command approvals: destructive command prompting
├── Skill checklists: TDD red flags, debug phase gates
├── Curator: background skill lifecycle management
└── Cron jobs: scheduled verification tasks
```

### Claude Code — The Harness by Layer

```
STRATEGIC:
├── System prompt: coding agent identity
├── CLAUDE.md: project + user + managed conventions (additive)
├── Managed settings: enterprise-enforced, highest precedence
└── Permission modes: default, accept-edits, bypass, plan

TACTICAL:
├── Skills (<skill>/SKILL.md): invocable with /name or auto-invoked
├── Rules (rules/*.md): topic-scoped, optionally path-gated
├── MCP servers (.mcp.json): team-shared external connections
├── Agents (agents/*.md): specialized subagents with own tools
└── Commands (commands/*.md): single-file prompts

OPERATIONAL:
├── Output styles (output-styles/*.md): custom system-prompt sections
├── Workflows (workflows/*.js): dynamic multi-subagent orchestration
├── Plan mode: structured planning before execution
└── Subagents: context isolation with skills preloaded

OUTPUT / VERIFICATION:
├── Hooks: PreToolUse, PostToolUse, PreCommand, Notification, SessionStart/End
├── Code intelligence: language-server diagnostics on every edit
├── Auto memory: learned build commands and debugging insights
└── Checkpoint restore: pre-edit file snapshots for rollback
```

### Feature Layering in Claude Code

The Claude Code docs explicitly describe how features layer and override:

```
CLAUDE.md → ADDITIVE (all levels contribute; more specific takes precedence)
Skills     → OVERRIDE by name (managed > user > project)
MCP        → OVERRIDE by name (local > project > user)
Hooks      → MERGE (all registered hooks fire for matching events)
Subagents  → OVERRIDE (managed > CLI flag > project > user > plugin)
```

---

## Best Practices for Designing a Harness

### 1. Place Constraints at the Right Layer

| If a rule applies... | Put it in... |
|---------------------|--------------|
| Always, for every task | System prompt / CLAUDE.md (Layer 1) |
| For a specific domain or workflow | Skill (Layer 2) |
| For a specific output format or process | Template/schema (Layer 3) |
| To verify correctness after execution | Hook/test/checklist (Layer 4) |

### 2. Use Progressive Disclosure Aggressively

- Layer 1: Tight and well-edited (every token costs on every request)
- Layer 2: Descriptions are cheap; full content should be under 500 lines
- Layer 3: Load only when the relevant skill is active
- Layer 4: Zero context cost for hooks (run externally); inject results only on failure

### 3. Make Constraints Self-Documenting

- Skills should describe both **what** they do and **when** to use them
- Good: "Extracts text from PDFs. Use when working with PDF documents."
- Bad: "Helps with PDFs."

### 4. Enforce with Gates, Not Warnings

- "THE IRON LAW: NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST" (not "consider writing tests")
- "Phase 1 Completion Checklist: STOP. Do not proceed to Phase 2 until you understand WHY."
- "Delete code. Start over." (not "try to add tests later")

### 5. Build Verification Into Every Layer

- Layer 1: Secret redaction, output scanning (always on, silent)
- Layer 2: `allowed-tools` field restricts tool access per skill
- Layer 3: Process checklists with explicit phase gates
- Layer 4: Hooks, tests, linting (run automatically; surface only failures)

### 6. Respect Context Economics

- Target CLAUDE.md under 200 lines
- Target SKILL.md body under 500 lines
- Move detailed reference material to separate files
- Use `disable-model-invocation` for skills triggered only by the user
- Route research-heavy tasks through subagents to isolate context cost

### 7. Design for Composition

Skills should compose with other harness layers:

- **Skill + MCP:** MCP provides the connection; the skill teaches how to use it
- **Skill + Subagent:** A skill spawns subagents for parallel work
- **CLAUDE.md + Skills:** CLAUDE.md holds always-on rules; skills hold on-demand reference
- **Hook + MCP:** A hook triggers external actions through MCP

### 8. Version and Audit Everything

- Skills should be version-controlled (Git)
- Commit project-level harness files; keep personal files in user-level directories
- Use `metadata.version` in SKILL.md frontmatter
- Track skill usage and lifecycle (Hermes Curator: usage counts, staleness detection)

---

## Anti-Patterns and Pitfalls

### 1. The System Prompt Dump
**Problem:** Putting all knowledge in the system prompt because it's "always available."
**Fix:** Move domain-specific knowledge to skills (Layer 2). The system prompt should only contain what every single task needs.

### 2. Vague Skill Descriptions
**Problem:** "Helps with PDFs." The agent cannot determine when to load this skill.
**Fix:** Describe both capability AND trigger. "Extracts text and tables from PDF files. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction."

### 3. Skipping Progressive Disclosure
**Problem:** Loading every skill's full content at startup because "context is cheap now."
**Fix:** Context is never cheap enough. Progressive disclosure enables scaling to hundreds of skills. Use it.

### 4. Soft Constraints
**Problem:** "Consider writing tests" or "Try to avoid..."
**Fix:** Use hard gates. "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST." The agent will optimize around soft constraints.

### 5. Missing Verification
**Problem:** Skills tell the agent what to do but not how to verify it was done right.
**Fix:** Every Layer 3 workflow should include a Layer 4 verification step (checklist, test command, hook).

### 6. Orphaned Hooks
**Problem:** Hooks that run but whose results are never surfaced to the agent.
**Fix:** Hooks should inject context back into the agent's session when they find problems, or surface results through notifications.

### 7. Hooks That Fire Too Often
**Problem:** A PostToolUse hook runs on every file edit, flooding context with lint output.
**Fix:** Use path-scoped hooks. Be selective about when verification runs. Hooks that return empty output should be silent.

### 8. Unmanaged Skill Lifecycle
**Problem:** Skills accumulate forever, including stale or contradictory ones.
**Fix:** Use a curator/sweeper (Hermes Curator, Claude Code cleanup). Pin important skills. Archive or remove stale ones.

---

## Future Directions

### 1. Formal Harness Specification
The agentskills.io spec is a start, but a full harness specification would define:
- Standard loading order and precedence rules across all four layers
- A unified validation schema for skills, hooks, and templates
- Interoperability between different agent frameworks

### 2. Composable Skill Marketplaces
As the agentskills.io ecosystem grows, expect:
- Namespaced skills (`org-name/skill-name`)
- Skill dependency chains ("this skill requires skill X")
- Versioned skill releases with semantic versioning

### 3. Dynamic Harness Adaptation
Future harnesses may:
- Adjust Layer 1 constraints based on task risk assessment
- Auto-promote frequently used skills to Layer 1 for efficiency
- Detect contradictory constraints and flag them for human review
- Learn effective harness configurations from usage patterns

### 4. Multi-Agent Harness Coordination
As agent teams become more common, harness needs expand to:
- Shared skill repositories with access control
- Cross-agent verification workflows
- Distributed approval chains
- Team-level constraint inheritance

---

## Quick Reference: The Harness at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│ LAYER             │ WHEN LOADED    │ TOKEN COST  │ PURPOSE      │
├───────────────────┼────────────────┼─────────────┼──────────────┤
│ 1. STRATEGIC      │ Session start  │ HIGH        │ WHO          │
│    System prompt,  │ (always on)    │ (every req) │ Identity &   │
│    CLAUDE.md,      │                │             │ boundaries   │
│    hard rules      │                │             │              │
├───────────────────┼────────────────┼─────────────┼──────────────┤
│ 2. TACTICAL       │ Descriptions   │ LOW →       │ WHAT         │
│    Skills, domain  │ at start;      │ MODERATE    │ Knowledge &  │
│    knowledge,      │ full on demand │ (when used) │ capabilities │
│    references      │                │             │              │
├───────────────────┼────────────────┼─────────────┼──────────────┤
│ 3. OPERATIONAL    │ When skill     │ MODERATE    │ HOW          │
│    Templates,      │ activates or   │ → HIGH      │ Process &    │
│    schemas,        │ mode switches  │ (in main or │ structure    │
│    processes       │                │ subagent)   │              │
├───────────────────┼────────────────┼─────────────┼──────────────┤
│ 4. OUTPUT /       │ Event-triggered│ ZERO →      │ CORRECT      │
│    VERIFICATION    │ (hooks, tests, │ LOW         │ Quality &    │
│    Hooks, tests,   │ approvals)     │ (on failure)│ safety gates │
│    checklists      │                │             │              │
└───────────────────┴────────────────┴─────────────┴──────────────┘

CROSS-CUTTING:
├── Context Management: progressive disclosure, compression, subagent isolation
├── Permission & Authorization: toolset gating, command approvals, permission modes
├── Memory & Persistence: auto memory, user profiles, skill versioning
└── Multi-Agent Coordination: delegation, agent teams, cron/dispatcher, shared boards
```

---

*This framework is based on research into:*
- **agentskills.io** — The open Agent Skills specification (https://agentskills.io)
- **Claude Code** — Anthropic's agentic coding tool (https://code.claude.com/docs)
- **Hermes Agent** — Nous Research's open-source agent framework (https://github.com/NousResearch/hermes-agent)
- **Cross-system analysis** — Comparing skill systems, hook architectures, and constraint layering across multiple agent frameworks
