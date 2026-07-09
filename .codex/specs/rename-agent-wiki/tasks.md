# Agent-wiki 改名 Tasks（任务清单）

## Execution Rule（执行规则）

一个 stage（阶段）对应一个 task session（任务会话），通常也对应一个 commit（提交/版本快照）。controller session（控制会话）负责安排顺序，并更新 `progress.md`。

## Stage 0：Preflight and Inventory（执行前检查和盘点）

- [ ] 确认当前 branch（分支）是 `codex/rename-agent-wiki`。
- [ ] 确认 working tree（工作树/真实项目目录）在编辑前是干净的。
- [ ] 记录当前 `main` commit（主线提交），作为 rollback baseline（回档基线）。
- [ ] 记录产品改名开始前的 rename branch commit（改名分支提交）。
- [ ] 盘点 tracked active files（被 Git 跟踪的当前有效文件）里的所有旧名称出现位置。
- [ ] 把出现位置分类为 display name（展示名）、internal slug（内部短名）、runtime path（运行路径）、environment variable（环境变量）、protocol/client id（协议/客户端标识）、docs/history（文档/历史内容）、tests（测试）、local-only ignored files（仅本地且被忽略的文件）。
- [ ] 编辑前产出 replacement map（替换映射表）。

验收：

- inventory commands（盘点命令）和 counts（数量）记录到 `progress.md`。
- rollback baseline commits（回档基线提交）记录到 `progress.md`。
- 除非 controller（控制会话）明确要求，本阶段不改产品文件。

## Stage 1：User-Facing Display Rename（用户可见名称改名）

- [ ] 把 README 标题和产品描述改为 `Agent-wiki`。
- [ ] 修改 Chrome extension（Chrome 扩展）的展示名和 popup title（弹窗标题）。
- [ ] 修改 launcher（启动器）和 setup（安装/初始化）里的用户可见输出。
- [ ] 修改高层 docs（文档）和 SKILL headings（技能标题）。

验收：

- 用户能看到的文字都使用 `Agent-wiki`。
- 除非机械上无法避免，本阶段不混入 internal slug（内部短名）和 runtime path（运行路径）修改。

## Stage 2：Internal Slug and Protocol Identifiers（内部短名和协议标识）

- [ ] 把 active internal slug（当前有效内部短名）替换为 `agent-wiki`。
- [ ] 更新 WebSocket client identifiers（WebSocket 客户端标识）。
- [ ] 更新 file bridge prefixes（文件桥接前缀）。
- [ ] 更新 User-Agent strings（User-Agent 字符串）。
- [ ] 更新 ingestion scripts（摄取脚本）使用的 local git bot identity（本地 Git 机器人身份）。
- [ ] 更新 Chrome extension（Chrome 扩展）和 background identifiers（后台标识）中绑定产品短名的部分。

验收：

- active protocol strings（当前有效协议字符串）使用 `agent-wiki`。
- third-party names（第三方名称）保持不动。

## Stage 3：Runtime Directory and Environment Variables（运行数据目录和环境变量）

- [ ] 把默认 runtime directory（运行数据目录）替换为 `~/.agent-wiki/`。
- [ ] 把 `*_HOME` 变量替换为 `AGENT_WIKI_HOME`。
- [ ] 把 task concurrency variable（任务并发变量）替换为 `AGENT_WIKI_TASK_CONCURRENCY`。
- [ ] 按需更新 config loader（配置加载器）、server（服务端）、installer（安装器）、analyzer（分析器）、strategy（策略）、executor（执行器）、status writer（状态写入器）、downloader docs（下载器文档）和 setup scripts（安装脚本）。

验收：

- 新代码不再读取旧 runtime env vars（运行环境变量）。
- 新代码不再默认使用旧 runtime directory（运行数据目录）。
- tests（测试）已更新为使用新环境变量。

## Stage 4：Active Documentation and Reference Rewrite（有效文档和参考资料重写）

- [ ] 更新 active docs（有效文档）、protocol docs（协议文档）、setup guides（安装指南）、runbooks（操作手册）和 handoff files（交接文件）。
- [ ] 更新描述当前产品行为的 references（参考资料）。
- [ ] 如果 archived references（归档参考资料）是 tracked（被 Git 跟踪）的，并且会导致最终旧名称验证失败，就更新它们。
- [ ] 决定 historical context（历史上下文）是删除、改写，还是移出 repository（仓库/版本库）。

验收：

- active docs（有效文档）一致使用 `Agent-wiki`。
- 任何剩余旧名称都必须明确说明是临时保留，并安排到最终清理阶段。

## Stage 5：Tests and Fixtures（测试和测试夹具）

- [ ] 更新 test environment variables（测试环境变量）。
- [ ] 更新 expected strings（预期字符串）、fixture skill names（测试夹具技能名）、runtime paths（运行路径）、file bridge names（文件桥接名）和 user agent assertions（User-Agent 断言）。
- [ ] 如果依赖可用，运行 focused tests（聚焦测试）。

验收：

- tests（测试）反映新的产品身份。
- syntax checks（语法检查）通过。

## Stage 6：Full Repository Legacy-Name Sweep（全仓库旧名称清扫）

- [ ] 对所有 legacy spellings（旧名称写法）运行 tracked-file searches（被 Git 跟踪文件搜索）。
- [ ] 删除或重写 active tracked files（当前有效且被 Git 跟踪的文件）里剩余的旧名称。
- [ ] 决定最终验收前如何处理这个 spec directory（规格目录）。
- [ ] 单独检查 ignored local folders（被忽略的本地目录），只报告，不删除，除非用户明确要求。

验收：

- active tracked files（当前有效且被 Git 跟踪的文件）里没有旧产品身份字符串。
- search output（搜索输出）记录到 `progress.md`。

## Stage 7：Runtime Data Migration（运行数据迁移）

- [ ] 检查旧 runtime directory（运行数据目录）是否存在。
- [ ] 检查 `~/.agent-wiki/` 是否已经存在。
- [ ] 移动或复制数据前，创建 timestamped backup（带时间戳的备份）。
- [ ] 只有确认 backup（备份）创建成功后，才复制或迁移数据。
- [ ] 迁移 config（配置）、cookie、status（状态）、inbox（收件箱）、logs（日志）、run artifacts（运行产物）、extension files（扩展文件）、service files（服务文件）和 response memory（响应记忆）。
- [ ] 验证迁移后的文件存在。
- [ ] 除非用户在验证后明确批准删除，否则保留旧 runtime directory（运行数据目录）。

验收：

- runtime data（运行数据）已经出现在 `~/.agent-wiki/`。
- backup path（备份路径）已记录。
- 没有在未经用户明确批准的情况下删除旧 runtime directory（运行数据目录）。
- 代码不再需要旧 runtime path（运行路径）。

## Stage 8：Final Validation and Merge Readiness（最终验证和合并准备）

- [ ] 对改名涉及的 Python 和 JavaScript 文件运行 syntax checks（语法检查）。
- [ ] 如果 `pytest` 可用，运行 tests（测试）。
- [ ] 运行 `git status`。
- [ ] 移除或迁移临时 spec artifacts（规格产物）后，再运行 legacy-name search（旧名称搜索）。
- [ ] 确认 rollback records（回档记录）、stage commits（阶段提交）和 runtime backup records（运行数据备份记录）完整。
- [ ] 输出 final summary（最终总结）和 merge recommendation（合并建议）。

验收：

- final commit（最终提交）后，working tree（工作树/真实项目目录）是干净的。
- branch（分支）已经准备好，让用户批准 merge（合并）到 `main`（主线）。

## Dependency Order（依赖顺序）

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

代码不再依赖旧 runtime names（运行时名称）之前，不要执行 Stage 7。
