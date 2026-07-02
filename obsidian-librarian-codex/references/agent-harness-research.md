# AI Agent Constraint & Harness Systems: Research Summary

> Research conducted June 17, 2026. Covering Anthropic and OpenAI's approaches to controlling agent behavior across multiple levels — from high-level task boundaries down to output formatting rules.

---

## 1. Anthropic's Approach

Anthropic provides a **multi-layered constraint architecture** for agents spanning project-level configuration, runtime environment containment, model-level schema enforcement, and long-running session management.

### 1.1 CLAUDE.md — Project-Level Instruction File

**CLAUDE.md** is Anthropic's canonical pattern for persistent project-level agent instructions. It lives at `.claude/CLAUDE.md` in a project repository and is loaded into every Claude Code session automatically.

- **Source**: [Claude Code Docs — Best Practices](https://code.claude.com/docs/en/best-practices) (section "Write an effective CLAUDE.md")
- **Purpose**: Stores coding conventions, build commands, architecture overview, environment setup, and style preferences that every agent session should know.
- **Key pattern**: The CLAUDE.md is the *single source of truth* for how the agent should behave in a given project. It acts as a persistent system prompt augmentation that follows the project.
- **Related**: The `.claude/` directory also houses skills, hooks, MCP server configs, and subagent definitions — all layered constraints.
- **Community examples**: Multiple open-source tools have emerged for generating/maintaining CLAUDE.md files aligned with Anthropic's best practices (e.g., ClaudeForge, claude-code-mastery).

### 1.2 System Prompt / Agent Design Philosophy

Anthropic's core philosophy, articulated in **"Building Effective Agents"** (Dec 2024):

- **Start simple** — don't build an agent when a single LLM call suffices.
- **Composable patterns > complex frameworks**: Augmented LLM (LLM + retrieval + tools + memory) → Workflows (prompt chaining, routing, parallelization) → Autonomous agents.
- **Direct API use recommended** over opaque frameworks. "Incorrect assumptions about what's under the hood are a common source of customer error."
- **Context window is the scarcest resource** — all best practices flow from managing it aggressively.
- **Source**: [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)

### 1.3 Tool Use Guidelines

Anthropic's tool use system is comprehensive and deeply documented:

| Feature | Description | Source |
|---------|-------------|--------|
| **Tool definition** | JSON Schema-based tool definitions with `name`, `description`, `input_schema` | [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) |
| **Strict tool use** | `strict: true` enforces JSON Schema compliance via grammar-constrained sampling — guarantees type-safe parameters every time | [Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use) |
| **Server vs client tools** | Server tools: Anthropic executes. Client tools: application executes and returns results | [Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) |
| **Parallel tool use** | Multiple tools can be called in a single response | [Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use) |
| **Tool Runner (SDK)** | Higher-level SDK abstraction for tool execution loop | [Tool Runner](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner) |
| **Tool search** | Dynamic tool discovery for large tool ecosystems | [Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search) |
| **Built-in tools** | Web search, web fetch, code execution, advisor, memory, bash, computer use, text editor | Various doc pages |

**Key insight**: Strict mode is not just for correctness — it's a *reliability guarantee* for production agents. Without it, agents may return `"2"` instead of `2`, breaking downstream systems.

### 1.4 Structured Outputs

Anthropic provides two complementary structured output features:

1. **JSON outputs** (`output_config.format`): Constrains Claude's entire response to a specific JSON schema. Integrates with Pydantic (Python), Zod (TypeScript), and native class schemas in Java/Ruby/PHP/C#/Go.
2. **Strict tool use** (`strict: true` on tool definitions): Grammar-constrained sampling ensures tool inputs always match schema.

- **Source**: [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- **Key guarantee**: "Always valid: No more JSON.parse() errors. Type safe. No retries needed for schema violations."
- **Models**: Available on Claude Opus 4.5+, Sonnet 4.5+, Haiku 4.5+, and newer models.
- **HIPAA**: Eligible with caveats (no PHI in schema definitions).

### 1.5 Agent Containment Architecture

Anthropic's most detailed writing on agent constraint systems is **"How We Contain Claude Across Products"** (May 2026):

- **Source**: [How We Contain Claude](https://www.anthropic.com/engineering/how-we-contain-claude)
- **Three types of risk**: User misuse, model misbehavior, external attackers
- **Three defense components**:
  1. **Environment (hard boundary)**: Process sandboxes, VMs, filesystem boundaries, egress controls. "If credentials never enter the sandbox, they can't be exfiltrated."
  2. **Model-level (probabilistic)**: System prompts, classifiers, probes, training modifications. "These shape only what the agent *tends* to do, not what it is theoretically capable of doing."
  3. **Orchestration (supervision)**: Human-in-the-loop approval, auto mode with automated safer approvals, permission modes.
- **Key insight**: Claude Code's reference devcontainer exists precisely so agents can run unattended — tight perimeters enable relaxed oversight.

### 1.6 Long-Running Agent Harness

Anthropic explicitly uses the term **"harness"** in **"Effective Harnesses for Long-Running Agents"** (Nov 2025):

- **Source**: [Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- **Core problem**: Agents that must work across multiple context windows (sessions) with no memory between them.
- **Two-agent harness pattern**:
  1. **Initializer agent**: Sets up environment — scaffold script, `claude-progress.txt` log, feature list (200+ items, initially marked "failing"), initial git commit.
  2. **Coding agent**: Each session reads progress file + git history, makes incremental progress, leaves environment in "clean state" (mergeable to main), updates progress log.
- **Failure modes addressed**: Agent trying to one-shot everything (context overflow), agent declaring "done" prematurely after seeing progress.
- **Key design principle**: The harness bridges context windows by requiring agents to leave structured, machine-readable artifacts.

### 1.7 Agent Skills System

- **Source**: Skills section in docs (Overview, Quickstart, Best Practices, Enterprise, API integration)
- Skills are reusable capability modules that agents can load.
- Combined with MCP (Model Context Protocol) for connecting to external tools and data sources.

### 1.8 Context Management & Verification Gates

- **Compaction**: Summarizes conversation to free context space.
- **Prompt caching**: Reduces latency and cost for repeated content.
- **Verification gates**: Tests, builds, screenshots, linters that serve as "checks Claude can run" — the difference between a session you watch and one you walk away from.
- **Stop hooks**: Deterministic gates that block turn completion until a check passes.
- **`/goal` conditions**: Session-level assertions checked by a separate evaluator after every turn.

---

## 2. OpenAI's Approach

OpenAI provides a **layered SDK-based framework** for agent development with structured outputs at the API level and guardrails/handoffs at the orchestration level.

### 2.1 Agents SDK

The **OpenAI Agents SDK** is a production-grade, open-source framework for multi-agent workflows:

- **Source**: [Agents SDK Overview](https://platform.openai.com/docs/guides/agents-sdk) | [GitHub (Python)](https://github.com/openai/openai-agents-python) | [GitHub (JS/TS)](https://github.com/openai/openai-agents-js)
- **Core concepts**:
  - **Agents**: LLMs configured with instructions, tools, guardrails, and handoffs
  - **Sandbox Agents**: Agents preconfigured with container environments for long-running work
  - **Handoffs / Agents as tools**: Delegation between specialist agents
  - **Tools**: Functions, MCP servers, hosted tools (web search, file search, code execution)
  - **Guardrails**: Configurable safety checks for input/output validation
  - **Human-in-the-loop**: Built-in mechanisms for involving humans
  - **Sessions**: Automatic conversation history management
  - **Tracing**: Built-in observability for debugging and optimization
- **Design philosophy**: "Use the Responses API when one model call + tools is enough. Use the Agents SDK when your application owns orchestration, tool execution, approvals, and state."

### 2.2 Structured Outputs

OpenAI's structured outputs system guarantees JSON Schema adherence:

- **Source**: [Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- **Two forms**:
  1. **`text_format`** (response format): Model's final text output follows a schema. Use when structuring model's response to user.
  2. **Function calling with `strict: true`**: Tool call arguments follow schema.
- **Key features**:
  - Pydantic (Python) and Zod (TypeScript) integration via `responses.parse()`
  - Explicit refusal handling — safety refusals are programmatically detectable
  - Comparison with JSON mode: Structured Outputs guarantees *schema adherence*, not just valid JSON
- **Models**: GPT-4o-mini, GPT-4o-2024-08-06, and later (including GPT-5.x)

### 2.3 Function Calling Rules

- **Source**: [Function Calling](https://platform.openai.com/docs/guides/function-calling)
- **Flow**: Define tools → model returns tool call → execute on application side → return output to model → final response
- **Key features**:
  - **Namespaces**: Group related tools by domain (CRM, billing, shipping) for cleaner organization
  - **Tool search**: Defer rarely-used tools, load only when model needs them (GPT-5.4+)
  - **`strict: true`**: Enforce schema on function parameters
  - **Best practices**: Clear function names/descriptions, use enums/object structure to make invalid states unrepresentable, include examples and edge cases in descriptions
- **Deferred tools**: Tools marked `defer_loading: true` are loaded on-demand via tool search

### 2.4 Guardrails System

- **Source**: [Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- **Three types**:
  1. **Input guardrails**: Run on initial user input (only for first agent in chain)
  2. **Output guardrails**: Run on final agent output (only for last agent in chain)
  3. **Tool guardrails**: Run on every custom function-tool invocation (input guardrails before, output guardrails after)
- **Execution modes**:
  - **Parallel** (default): Guardrail runs concurrently with agent — best latency but agent may consume tokens before cancellation
  - **Blocking**: Guardrail completes before agent starts — prevents token consumption and tool side effects
- **Tripwire mechanism**: Guardrails produce `GuardrailFunctionOutput`; if `tripwire_triggered` is true, an exception is raised
- **Cost optimization pattern**: Use cheap model for guardrail, expensive model for main task

### 2.5 Agent Orchestration & Handoffs

- Agents can hand off to other specialist agents
- "Agents as tools" pattern: one agent wraps another as a callable tool
- Orchestration controls which agent owns the reply at each step
- Sessions persist state across handoffs

### 2.6 Sandbox / Container-Based Agents

- Similar to Anthropic's containment approach: agents run in configurable containers
- Support for Unix local sandboxes, Docker sandboxes
- Workspace manifests: declare files, git repos, packages that the sandbox needs
- Preconfigured for long-horizon autonomous work

---

## 3. The "Agent Harness" Concept

The term **"agent harness"** (or "AI harness") is used explicitly by Anthropic as an industry concept:

### Anthropic's Definition
From **"Effective Harnesses for Long-Running Agents"** (Nov 2025):
> "The Claude Agent SDK is a powerful, general-purpose agent harness adept at coding..."

A harness is the **runtime infrastructure** that:
- Manages agent lifecycle across multiple context windows
- Provides initialization, progress tracking, and session-to-session continuity
- Enforces verification gates (tests, builds, checks)
- Bridges the gap between discrete LLM sessions

### Key Harness Patterns

| Pattern | Description | Source |
|---------|-------------|--------|
| **Initializer + Coding agent** | First session sets up environment and feature list; subsequent sessions make incremental progress | [Effective Harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| **Progress artifacts** | Structured files (`claude-progress.txt`, feature lists with pass/fail states, git history) that let fresh sessions orient quickly | Same |
| **Verification gates** | Tests, builds, screenshots, linters as automated "stop conditions" | [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices) |
| **Permission modes** | Level-based permissions that constrain what agents can do without approval | [Claude Code Docs](https://code.claude.com/docs/en/permission-modes) |
| **Stop hooks** | Deterministic scripts that block turn completion until checks pass | Same |
| **Sandbox containment** | Process/VMs/filesystem boundaries as "hard" constraints; model-level controls as "soft" constraints | [How We Contain Claude](https://www.anthropic.com/engineering/how-we-contain-claude) |

---

## 4. Comparative Summary

| Dimension | Anthropic | OpenAI |
|-----------|-----------|--------|
| **Project-level instructions** | CLAUDE.md (file-based, auto-loaded) | Agent `instructions` parameter (programmatic) |
| **System prompt philosophy** | Start simple, composable patterns, direct API over frameworks | Layered SDK: Responses API → Agents SDK |
| **Tool constraints** | `strict: true` (grammar-constrained sampling), server/client distinction | `strict: true` (function calling), tool search for scale |
| **Output formatting** | `output_config.format` (JSON schema), Pydantic/Zod integration | `text_format` (JSON schema), `responses.parse()`, Pydantic/Zod |
| **Guardrails** | Stop hooks, verification gates, permission modes, adversarial review | Input/output/tool guardrails with tripwire mechanism, blocking/parallel modes |
| **Containment** | Sandboxes, VMs, egress controls, reference devcontainer | Sandbox agents (Unix local, Docker), workspace manifests |
| **Long-running agents** | Initializer + coding agent harness, progress artifacts | Sandbox agents with container state, sessions |
| **Agent orchestration** | Subagents, handoffs, plan mode | Handoffs, agents-as-tools, manager/routing patterns |
| **Observability** | Context window tracking, session management | Built-in tracing, evaluation loops |
| **Human-in-the-loop** | Permission prompts, auto mode, stop hooks | Human-in-the-loop SDK integration |

---

## 5. Key References

### Anthropic
1. **Building Effective Agents** (Dec 2024): https://www.anthropic.com/engineering/building-effective-agents
2. **How We Contain Claude** (May 2026): https://www.anthropic.com/engineering/how-we-contain-claude
3. **Effective Harnesses for Long-Running Agents** (Nov 2025): https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
4. **Claude Code Best Practices** (CLAUDE.md): https://code.claude.com/docs/en/best-practices
5. **Structured Outputs**: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
6. **Strict Tool Use**: https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use
7. **Tool Use Overview**: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
8. **Claude Agent SDK**: https://code.claude.com/docs/en/agent-sdk
9. **Equipping Agents with Agent Skills**: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
10. **Writing Tools for Agents**: https://www.anthropic.com/engineering/writing-tools-for-agents

### OpenAI
1. **Agents SDK Overview**: https://platform.openai.com/docs/guides/agents-sdk
2. **Agents SDK GitHub (Python)**: https://github.com/openai/openai-agents-python
3. **Agents SDK Docs (Guardrails)**: https://openai.github.io/openai-agents-python/guardrails/
4. **Structured Outputs**: https://platform.openai.com/docs/guides/structured-outputs
5. **Function Calling**: https://platform.openai.com/docs/guides/function-calling
6. **Agent Definitions**: https://openai.github.io/openai-agents-python/agents/
7. **Handoffs**: https://openai.github.io/openai-agents-python/handoffs/

### Industry Concept
- **"Agent Harness"** is most explicitly defined by Anthropic in their engineering blog. The industry has not yet converged on a single term, but the concept encompasses the full runtime infrastructure that constrains, guides, and manages agent behavior across sessions. Related terms: "agent scaffolding," "agent runtime," "orchestration layer," "agent containment."
