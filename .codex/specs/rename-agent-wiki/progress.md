# Agent-wiki 改名 Progress（进度记录）

## Current State（当前状态）

- Controller branch（控制分支）：`codex/rename-agent-wiki`
- Current phase（当前阶段）：Stage 4 Active Documentation and Reference Rewrite（有效文档和参考资料重写）已提交；准备派发 Stage 5 Tests and Fixtures（测试和测试夹具）
- Execution started（执行是否开始）：是，Stage 1-3 已开始并提交产品代码改名
- Runtime migration started（运行数据迁移是否开始）：否
- Final merge completed（最终合并是否完成）：否

## Rollback Baseline（回档基线）

- `main` baseline at spec review（规格检查时的主线基线）：`79c9506`（`Tighten derived candidate visibility rules`）
- Stage 0 `main` rollback baseline（Stage 0 主线回档基线）：`79c950657095dec02509cc61363bf3d3983363a3`（`Tighten derived candidate visibility rules`）
- Stage 0 rename branch pre-rename baseline（Stage 0 改名分支开始前基线）：`5079725ff07de6d6eac21ba4e2e1e53c0e9a82b5`（`Translate rename spec docs to Chinese`）
- Spec scaffold commit（规格脚手架提交）：`ebd111b`（`Add Agent-wiki rename spec`）
- Product rename implementation started（产品改名实现是否开始）：否
- Rollback before final merge（最终合并前的回档方式）：切回 `main`，或停止使用 `codex/rename-agent-wiki`
- Runtime backup created（运行数据备份是否创建）：否，因为 runtime migration（运行数据迁移）尚未开始
- Runtime deletion approved（是否批准删除运行数据）：否

## Stage Progress（阶段进度）

| Stage（阶段） | Name（名称） | Status（状态） | Session（会话） | Commit（提交） | Notes（备注） |
| --- | --- | --- | --- | --- | --- |
| 0 | Preflight and Inventory（执行前检查和盘点） | completed（已完成） | `019f47e1-5aae-72a0-8099-47f9f193d35e` | `fb7d012` | 仅更新本文件；未改产品代码 |
| 1 | User-Facing Display Rename（用户可见名称改名） | completed（已完成） | `019f47e8-860f-79d1-8512-749ddcd3e48a` | `9c38029` | 改用户可见展示名；未改 runtime/env/protocol |
| 2 | Internal Slug and Protocol Identifiers（内部短名和协议标识） | completed（已完成） | `019f47f0-396d-7343-a60c-9eb846be87fb` | `5f3c6d0` | 改内部短名、协议/client id、User-Agent、本地 Git bot；未改 runtime/env |
| 3 | Runtime Directory and Environment Variables（运行数据目录和环境变量） | completed（已完成） | `019f47f7-ef2f-7ca2-a3aa-52548ac01dfd` + controller verification（控制会话验证） | `3122d43` | 改默认 runtime 目录和 env var；未迁移真实运行数据 |
| 4 | Active Documentation and Reference Rewrite（有效文档和参考资料重写） | completed（已完成） | `019f47fd-f69a-7283-bac1-8d63d9b81466` + controller takeover（控制会话接手） | `fff9c9c` | 改 docs/references/codex-handoff 文档旧名；未改代码、测试、真实 runtime 数据 |
| 5 | Tests and Fixtures（测试和测试夹具） | pending（待处理） |  |  |  |
| 6 | Full Repository Legacy-Name Sweep（全仓库旧名称清扫） | pending（待处理） |  |  |  |
| 7 | Runtime Data Migration（运行数据迁移） | pending（待处理） |  |  |  |
| 8 | Final Validation and Merge Readiness（最终验证和合并准备） | pending（待处理） |  |  |  |

## Controller Log（控制会话记录）

- 已为全量改名到 `Agent-wiki` 创建 spec scaffold（规格脚手架）。
- 已检查 spec safety posture（规格安全性），并补充明确 rollback rules（回档规则）。
- 已把 spec 文档改为中文说明，方便后续会话和用户共同阅读。
- Stage 0 已由任务会话完成，并由 controller 提交为 `fb7d012`（`Record Stage 0 rename inventory`）。
- Stage 1 已由任务会话完成，controller 补充一处 `derive_executor.py` 命令行展示文案，并提交为 `9c38029`（`Rename user-facing display name to Agent-wiki`）。
- Stage 2 已由任务会话完成，并由 controller 提交为 `5f3c6d0`（`Rename internal protocol identifiers to agent-wiki`）。
- Stage 3 retry 任务会话留下 runtime/env 改名结果，controller 验证并提交为 `3122d43`（`Rename runtime defaults and env vars to Agent-wiki`）。
- Stage 4 初始任务会话只做了前置检查，controller 接手文档改写，并提交为 `fff9c9c`（`Rewrite active docs for Agent-wiki rename`）。

## Inventory Notes（盘点记录）

### Stage 0 Preflight（执行前检查）

- Branch check（分支检查）：`git branch --show-current` -> `codex/rename-agent-wiki`
- Clean-tree check before inventory（盘点前工作树检查）：`git status --short` -> 无输出，工作树干净
- Baseline commands（基线命令）：
  - `git rev-parse main` -> `79c950657095dec02509cc61363bf3d3983363a3`
  - `git rev-parse HEAD` -> `5079725ff07de6d6eac21ba4e2e1e53c0e9a82b5`
  - `git log -1 --oneline main` -> `79c9506 Tighten derived candidate visibility rules`
  - `git log -1 --oneline HEAD` -> `5079725 Translate rename spec docs to Chinese`

### Stage 0 Inventory Commands（盘点命令）

Legacy regex（旧身份搜索表达式）：

```sh
obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian
```

Tracked files（被 Git 跟踪文件）：

```sh
git grep -n -I -E 'obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian'
git grep -l -I -E 'obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian'
git grep -n -I -F '<legacy-token>'
git grep -l -I -F '<legacy-token>'
```

Ignored local files（仅本地且被忽略文件；只读扫描，排除 `.git`、`.venv`、`__pycache__`、`*.pyc`）：

```sh
git status --short --ignored
grep -RIn --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude='*.pyc' -E 'obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian' obsidian-librarian obsidian-librarian-codex/logs obsidian-librarian-codex/deps/douyin/logs
grep -RIl --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude='*.pyc' -E 'obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian' obsidian-librarian obsidian-librarian-codex/logs obsidian-librarian-codex/deps/douyin/logs
```

### Stage 0 Counts（数量）

Tracked-file total（被跟踪文件总计）：

| Scope（范围） | Lines（行） | Files（文件） | Notes（备注） |
| --- | ---: | ---: | --- |
| All tracked files（全部被跟踪文件） | 220 | 50 | `git grep` total |
| Root metadata（根目录元数据） | 2 | 1 | `.gitignore` |
| Active product code/top docs（当前产品代码和顶层说明） | 83 | 22 | 排除 `docs/`、`references/`、`codex-handoff/`、`tests/` |
| Docs/history/spec（文档、历史内容、临时 spec） | 102 | 26 | 含 `.codex/specs/rename-agent-wiki/spec.md` 的 1 行临时旧名 |
| Tests（测试） | 33 | 1 | `obsidian-librarian-codex/tests/test_p0_static.py` |

Legacy token counts in tracked files（被跟踪文件中的旧 token 数量；不同 token 可在同一行重叠）：

| Legacy token（旧 token） | Lines（行） | Files（文件） | Primary category（主要类别） |
| --- | ---: | ---: | --- |
| `obsidian-librarian-codex` | 3 | 3 | internal slug / docs path（内部短名 / 文档路径） |
| `Obsidian Librarian Codex` | 0 | 0 | display name（展示名，当前无命中） |
| `Obsidian Librarian` | 16 | 14 | display name（展示名） |
| `obsidian-librarian` | 164 | 45 | internal slug / protocol / path（内部短名 / 协议 / 路径） |
| `OBSIDIAN_LIBRARIAN` | 42 | 9 | environment variable（环境变量） |
| `~/.obsidian-librarian` | 83 | 29 | runtime path（运行路径） |
| `.obsidian-librarian` | 96 | 34 | runtime path / file bridge prefix（运行路径 / 文件桥接前缀） |

Semantic classification counts（语义分类数量；这些维度会重叠，不与 total 相加）：

| Category（类别） | Lines（行） | Notes（备注） |
| --- | ---: | --- |
| display name（展示名） | 16 | README、SKILL、Chrome manifest/popup、launcher、server docstrings/output |
| internal slug（内部短名） | 164 | package/skill slug、repo/path mention、local bot email、docs references |
| runtime path（运行路径） | 91 | `~/.obsidian-librarian`、`Path.home() / ".obsidian-librarian"`、`$HOME/.obsidian-librarian` 等 |
| environment variable（环境变量） | 42 | `OBSIDIAN_LIBRARIAN_HOME`、`OBSIDIAN_LIBRARIAN_TASK_CONCURRENCY` |
| protocol/client id（协议/客户端标识） | 25 | `obsidian-librarian-extension`、`obsidian-librarian-background`、`obsidian-librarian-derive`、`obsidian-librarian.`、`obsidian-librarian@local`、`com.obsidian-librarian` |
| docs/history（文档/历史内容） | 102 | `docs/`、`references/`、`codex-handoff/`、临时 spec |
| tests（测试） | 33 | `tests/test_p0_static.py` |
| local-only ignored files（仅本地且被忽略文件） | 120 lines / 27 files | 主要在 ignored `obsidian-librarian/` historical workspace；未修改、未删除 |

High-hit tracked files（高命中文件，供后续阶段拆分）：

| File group（文件组） | Notable files（代表文件） |
| --- | --- |
| Active product code/top docs（当前产品代码和顶层说明） | `README.md` 10 行；`deps/douyin/SKILL.md` 8 行；`install/bootstrap.py` 7 行；`SKILL.md` 7 行；`deps/douyin/scripts/ingest.py` 6 行；`deps/douyin/scripts/config_loader.py` 6 行 |
| Docs/history/spec（文档/历史内容/临时 spec） | `references/chrome-extension-setup.md` 16 行；`codex-handoff/RUNBOOK.md` 14 行；`references/websocket-server-setup.md` 12 行；`docs/2026-07-04-video-chain-checkpoint.md` 9 行；`.codex/specs/rename-agent-wiki/spec.md` 1 行 |
| Ignored local-only（仅本地 ignored） | `obsidian-librarian/SKILL.md` 21 行；`obsidian-librarian/deps/douyin/SKILL.md` 20 行；`obsidian-librarian/references/chrome-extension-setup.md` 16 行；`obsidian-librarian/references/websocket-server-setup.md` 12 行 |

### Stage 0 Replacement Map（替换映射表）

| Old（旧） | New（新） | Category（类别） | Notes（备注） |
| --- | --- | --- | --- |
| `Obsidian Librarian` | `Agent-wiki` | display name（展示名） | 用户可见名称、标题、输出文案 |
| `Obsidian Librarian Codex` | `Agent-wiki` | display name（展示名） | 当前 tracked files 无命中；保留为后续 sweep 目标 |
| `obsidian-librarian-codex` | `agent-wiki` | internal slug / path（内部短名 / 路径） | 包含当前项目目录名和文档路径提及；实际目录重命名由后续阶段/controller 决策 |
| `obsidian-librarian` | `agent-wiki` | internal slug（内部短名） | skill name、client prefix、file prefix、user agent、bot identity 的基础短名 |
| `~/.obsidian-librarian/` | `~/.agent-wiki/` | runtime path（运行路径） | Stage 7 前只改代码/文档引用，不迁移数据 |
| `.obsidian-librarian` | `.agent-wiki` 或 `agent-wiki.*` | runtime path / file bridge（运行路径 / 文件桥接） | runtime dirname 用 `.agent-wiki`；Chrome/file bridge prefix 按 spec 使用 `agent-wiki.*`，不要保留前导点 |
| `OBSIDIAN_LIBRARIAN_HOME` | `AGENT_WIKI_HOME` | environment variable（环境变量） | 旧 env var 不保留兼容读取 |
| `OBSIDIAN_LIBRARIAN_TASK_CONCURRENCY` | `AGENT_WIKI_TASK_CONCURRENCY` | environment variable（环境变量） | server/task concurrency override |
| `OBSIDIAN_LIBRARIAN` | `AGENT_WIKI` | environment variable prefix（环境变量前缀） | 通用前缀替换 |
| `obsidian-librarian-extension` | `agent-wiki-extension` | protocol/client id（协议/客户端标识） | WebSocket client id |
| `obsidian-librarian-background` | `agent-wiki-background` | protocol/client id（协议/客户端标识） | WebSocket/background client id |
| `obsidian-librarian-derive` | `agent-wiki-derive` | protocol/User-Agent（协议/User-Agent） | derive executor User-Agent |
| `obsidian-librarian-model-health` | `agent-wiki-model-health` | protocol/internal alarm（协议/内部 alarm） | Chrome alarm/storage key style identifier |
| `obsidian-librarian.` | `agent-wiki.` | file bridge prefix（文件桥接前缀） | spec 目标为 `agent-wiki.*` |
| `obsidian-librarian@local` | `agent-wiki@local` | local git bot email（本地 Git 机器人邮箱） | ingest local git identity |
| `com.obsidian-librarian` | `com.agent-wiki` | launchd/protocol id（launchd/协议标识） | references/setup 中出现；后续 docs/runtime 阶段确认是否仍 active |

### Stage 0 Risks and Uncertainties（风险和不确定项）

- `.codex/specs/rename-agent-wiki/` 按 spec 是临时例外；本次只记录旧名命中，最终 merge 前必须删除、移出仓库或改写到无旧名。
- `obsidian-librarian/` 是 ignored local-only historical workspace，含 120 行旧名命中；本阶段只报告，不删除、不改写。
- `.venv`、`__pycache__`、`*.pyc` 未纳入 ignored 文本盘点，避免把依赖和缓存当作产品改名范围。
- `obsidian-librarian-codex` 当前也是被跟踪项目目录名；是否在后续阶段实际重命名目录，需要 controller 明确阶段边界。
- runtime migration 尚未开始；`~/.obsidian-librarian/` 只能在 Stage 7 备份并验证后迁移。

## Validation Notes（验证记录）

- Stage 0:
  - `git diff --name-only` -> `.codex/specs/rename-agent-wiki/progress.md`
  - `git diff --check` -> 通过，无输出
  - `git status --short --branch` -> `## codex/rename-agent-wiki` plus `M .codex/specs/rename-agent-wiki/progress.md`
  - 未运行测试；Stage 0 仅为盘点和进度记录，未改产品代码。
- Stage 3:
  - Changed files（变更文件）：仅限 Stage 3 允许的 14 个 active code/current docs 文件
  - `git diff --check` -> 通过，无输出
  - `python3 -m py_compile ...` -> 通过，无输出
  - `bash -n obsidian-librarian-codex/deps/douyin/setup.sh obsidian-librarian-codex/setup-extension.sh` -> 通过，无输出
  - allowed-file legacy search（允许文件旧名搜索）：`OBSIDIAN_LIBRARIAN|~/.obsidian-librarian|\.obsidian-librarian|\$HOME/.obsidian-librarian` -> 无命中
  - runtime migration（运行数据迁移）：未开始，未创建/复制/删除真实 runtime 目录
- Stage 4:
  - Changed files（变更文件）：仅限 `obsidian-librarian-codex/docs/`、`obsidian-librarian-codex/references/`、`obsidian-librarian-codex/codex-handoff/` 下 25 个文档文件
  - `git diff --check` -> 通过，无输出
  - Stage 4 scope legacy search（阶段范围旧名搜索）：`obsidian-librarian-codex|Obsidian Librarian Codex|Obsidian Librarian|obsidian-librarian|OBSIDIAN_LIBRARIAN|~/.obsidian-librarian` -> 无命中
  - runtime migration（运行数据迁移）：未开始，未创建/复制/删除真实 runtime 目录

## Blockers（阻塞点）

暂无记录。
