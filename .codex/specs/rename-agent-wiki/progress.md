# Agent-wiki 改名 Progress（进度记录）

## Current State（当前状态）

- Controller branch（控制分支）：`codex/rename-agent-wiki`
- Current phase（当前阶段）：Stage 7 Runtime Data Migration（运行数据迁移）已完成；准备进入 Stage 8 Final Validation and Merge Readiness（最终验证和合并准备）
- Active project directory（当前项目目录）：`agent-wiki/`
- Runtime migration started（运行数据迁移是否开始）：是
- Runtime migration completed（运行数据迁移是否完成）：是
- Final merge completed（最终合并是否完成）：否

## Rollback Baseline（回档基线）

- `main` baseline at spec review（规格检查时的主线基线）：`79c950657095dec02509cc61363bf3d3983363a3`（`Tighten derived candidate visibility rules`）
- Stage 0 rename branch pre-rename baseline（改名分支开始前基线）：`5079725ff07de6d6eac21ba4e2e1e53c0e9a82b5`（`Translate rename spec docs to Chinese`）
- Rollback before final merge（最终合并前的回档方式）：切回 `main`，或停止使用 `codex/rename-agent-wiki`
- Runtime backup created（运行数据备份是否创建）：是，备份路径为 `~/.agent-wiki-backups/pre-agent-wiki-runtime-20260710-130455`
- Runtime deletion approved（是否批准删除运行数据）：否

## Stage Progress（阶段进度）

| Stage（阶段） | Name（名称） | Status（状态） | Commit（提交） | Notes（备注） |
| --- | --- | --- | --- | --- |
| 0 | Preflight and Inventory（执行前检查和盘点） | completed（已完成） | `fb7d012` | 盘点旧身份命中；旧身份明细已在 Stage 6 消毒，不再保留旧字符串 |
| 1 | User-Facing Display Rename（用户可见名称改名） | completed（已完成） | `9c38029` | 改用户可见展示名 |
| 2 | Internal Slug and Protocol Identifiers（内部短名和协议标识） | completed（已完成） | `5f3c6d0` | 改内部短名、协议/client id、User-Agent、本地 Git bot |
| 3 | Runtime Directory and Environment Variables（运行数据目录和环境变量） | completed（已完成） | `3122d43` | 改默认 runtime 目录和 env var；未迁移真实运行数据 |
| 4 | Active Documentation and Reference Rewrite（有效文档和参考资料重写） | completed（已完成） | `fff9c9c` | 改 docs/references/codex-handoff 文档旧名；未改真实 runtime 数据 |
| 5 | Tests and Fixtures（测试和测试夹具） | completed（已完成） | `321f62d` | 改测试 env var、fixture skill name、运行路径引用；`pytest` 当前环境不可用 |
| 6 | Full Repository Legacy-Name Sweep（全仓库旧名称清扫） | completed（已完成） | `1698b0f` | 清理剩余旧身份字符串，并把项目目录改为 `agent-wiki/` |
| 7 | Runtime Data Migration（运行数据迁移） | completed（已完成，待记录提交号） |  | 已创建备份，并把运行数据复制到 `~/.agent-wiki/`；改名前运行目录保留 |
| 8 | Final Validation and Merge Readiness（最终验证和合并准备） | pending（待处理） |  | 合并前最终验收 |

## Validation Notes（验证记录）

- Stage 3:
  - Changed files（变更文件）：仅限 Stage 3 允许的 active code/current docs 文件
  - `git diff --check` -> 通过
  - Python py_compile（Python 编译检查）-> 通过
  - Shell syntax checks（Shell 语法检查）-> 通过
  - runtime/env legacy search（运行目录和环境变量旧身份搜索）-> 无命中
  - runtime migration（运行数据迁移）：未开始
- Stage 4:
  - Changed files（变更文件）：仅限 `agent-wiki/docs/`、`agent-wiki/references/`、`agent-wiki/codex-handoff/`
  - `git diff --check` -> 通过
  - Stage 4 scope legacy search（阶段范围旧身份搜索）-> 无命中
  - runtime migration（运行数据迁移）：未开始
- Stage 5:
  - Changed files（变更文件）：仅 `agent-wiki/tests/test_p0_static.py`
  - `git diff --check` -> 通过
  - Python py_compile（Python 编译检查）-> 通过
  - tests legacy search（测试旧身份搜索）-> 无命中
  - `python3 -m pytest agent-wiki/tests/test_p0_static.py` -> 未运行成功；当前环境报 `No module named pytest`

## Stage 6 Notes（Stage 6 记录）

- 临时 spec（规格）旧身份明细已消毒为摘要，以免最终旧身份搜索失败。
- ignored local historical workspace（被忽略的本地历史工作区）只报告，不修改、不删除。
- Stage 7 前不创建、复制、移动或删除真实 runtime data（运行数据）。
- Tracked-file legacy search（被跟踪文件旧身份搜索）-> 无命中。
- Ignored local historical workspace（被忽略的本地历史工作区）仍有 120 行旧身份命中；按约束只报告，不修改、不删除。
- Syntax checks（语法检查）：Python py_compile、Node `--check`、Shell `bash -n`、Chrome manifest JSON 检查均通过。

## Stage 7 Notes（Stage 7 记录）

- Pre-rename runtime directory（改名前运行数据目录）：存在，约 `1.0G`。
- New runtime directory（新运行数据目录）：`~/.agent-wiki/`，已创建，约 `1.0G`。
- Runtime backup（运行数据备份）：`~/.agent-wiki-backups/pre-agent-wiki-runtime-20260710-130455`，约 `1.0G`。
- Migration method（迁移方式）：先备份，再复制到新运行目录。
- Verified migrated items（已验证迁移项目）：`config.toml`、`cookie/douyin.txt`、`status/`、`inbox/`、`logs/`、`run-artifacts/`、`responses-memory/`、`extension/`、`service/`。
- Pre-rename runtime directory retained（改名前运行数据目录保留）：是；未获得用户明确批准前不删除。

## Blockers（阻塞点）

- `pytest` 当前环境不可用：`No module named pytest`。
