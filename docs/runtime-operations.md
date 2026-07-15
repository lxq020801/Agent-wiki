# 本地运行与诊断

本文只说明当前已经实现的本地控制服务运维入口。默认运行目录是 `~/.agent-wiki/`；测试或隔离运行可以通过 `AGENT_WIKI_HOME` 指向其他目录。

## 服务命令

```bash
python3.11 server/launcher.py start
python3.11 server/launcher.py status
python3.11 server/launcher.py restart
python3.11 server/launcher.py stop
```

无参数调用和 `foreground` 子命令都以前台方式运行，用于开发调试：

```bash
python3.11 server/launcher.py foreground
```

`start` 在启动前执行以下检查：

- 找到 Python 3.11+，并确认控制面依赖可导入；
- 报告抖音入库 venv 是否可用；
- 读取 `[server]` 配置，拒绝非回环监听地址和无效端口；
- 检查端口是否已被占用；
- 检查私有服务状态、已有进程的启动标识和运行源码位置；
- 报告旧 `~/.agent-wiki/service/` 部署、旧 `~/.obsidian-librarian/` 运行目录和旧环境变量，但不修改它们。

端口被未知进程占用时启动会失败，不会尝试停止该进程。`stop` 只在以下信息全部一致时发送信号：

- `run/control-plane.pid` 与 `run/control-plane.json` 中的 PID；
- Agent-wiki 服务标识和状态格式版本；
- 操作系统报告的进程启动标识；
- Python 可执行文件和 `server/service_entry.py` 入口路径。

状态文件、PID 文件和日志权限为 `0600`，`run/` 与 `logs/` 目录权限为 `0700`。服务状态还保存当前源码 commit、`git describe` 版本、dirty 标记、源码根目录、监听地址和启动时间。`status --json` 可输出不含配置秘密的结构化状态。服务停止时返回码为 `3`，不代表诊断执行失败。

## 统一操作诊断

框架级结构化操作时间线位于：

```text
~/.agent-wiki/operations/
├── index.jsonl                         # 所有事件的轻量索引
└── by-id/<operationId>/
    ├── summary.json                    # 当前状态、关联和诊断路径
    └── timeline.jsonl                  # 按 sequence 排序的完整事件时间线
```

任务状态、GitHub 批次/子项、知识库生命周期响应和控制面回复会公开 `operationId` 或 `diagnostics`。也可以通过 WebSocket `operation_diagnostics_request.targetOperationId` 查询。弹窗关闭不会删除时间线；服务重启时未结束 operation 会记录恢复节点。

统一时间线只保存严格脱敏后的排错摘要和 artifact 路径。视频/派生的大 prompt、完整模型响应与详细中间产物继续位于 `run-artifacts/`；不要把两类目录混为一套，也不要将 `operations/`、`run-artifacts/` 或其他 runtime 文件提交到仓库。

## Doctor

```bash
python3.11 server/launcher.py doctor
python3.11 server/launcher.py doctor --json
```

`doctor` 是只读诊断，检查：

- Python 3.11+、控制面依赖和抖音 venv 依赖；
- `ffmpeg` 与 `ffprobe`；
- `config.toml` 是否存在、TOML 是否有效、权限是否私有；
- Ark 凭据是否已配置，但不输出凭据；
- Cookie 文件是否存在且权限私有，但不读取 Cookie 内容；
- vault 是否存在以及顶层 `.obsidian/`、`SCHEMA.md`、`index.md`、`知识资产/` 标记，不读取 `.obsidian/` 内容；
- 服务端口、托管进程和运行源码位置；
- `~/.agent-wiki/extension/` 与仓库扩展源码是否一致；
- 旧部署、旧运行目录和旧环境变量是否残留。

存在 `FAIL` 时命令返回 `1`；只有 `WARN` 不改变成功返回码。

## 缓存报告

```bash
python3.11 server/launcher.py cache report
python3.11 server/launcher.py cache report --json
python3.11 server/launcher.py cache clean --dry-run
```

报告覆盖 `cache/`、`run-artifacts/` 和 `responses-memory/`，不跟随符号链接。`cache clean` 强制要求 `--dry-run`，只汇总假如清理 `cache/` 时涉及的普通文件数量和大小；当前代码没有真实删除入口。`run-artifacts/` 和 `responses-memory/` 始终只报告。

## 安全恢复

状态损坏、PID 与元数据不一致、进程启动标识变化或入口脚本不匹配时，launcher 会拒绝发送信号。先运行 `status` 和 `doctor` 检查；不要通过按端口或进程名批量终止进程。旧部署和旧运行目录只会被报告，迁移或删除需要单独确认。
