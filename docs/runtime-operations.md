# 本地运行与诊断

本文只说明当前已经实现的本地安装、服务生命周期和诊断入口。默认运行目录是 `~/.agent-wiki/`；测试或隔离运行可以通过 `AGENT_WIKI_HOME` 指向其他目录。

## 统一命令入口

在仓库根目录使用：

```bash
./agent-wiki install
./agent-wiki start
./agent-wiki status
./agent-wiki restart
./agent-wiki stop
./agent-wiki doctor
./agent-wiki autostart status
./agent-wiki uninstall
```

根级入口只负责找到 Python 3.11+ 并路由命令。`install` 复用 `install/bootstrap.py`，服务命令复用 `server/runtime_manager.py`，没有第二套安装或进程管理逻辑。

## 安装

```bash
./agent-wiki install
```

该命令可以重复执行。它会：

- 创建 `~/.agent-wiki/` 下的运行目录和权限为 `0600` 的配置模板；
- 在 `deps/douyin/.venv/` 准备隔离 Python 环境，并从仓库 requirements 安装运行依赖；
- 把当前 Chrome 扩展同步到 `~/.agent-wiki/extension/`；
- 检查 Python 3.11+、`ffmpeg`、`ffprobe` 和本地 WebSocket 状态；
- 保留已有配置和知识库选择，不覆盖用户数据。

缺少 Python 3.11+ 或 FFmpeg 时，命令会给出中文处理建议。安装器不会安装 Homebrew、不会修改系统 Python，也不会使用 `--break-system-packages`。测试或离线检查可以显式使用 `--skip-install-deps` 和 `--skip-websocket-check`。

安装完成后，在 Chrome 的 `chrome://extensions/` 开启开发者模式，选择“加载已解压的扩展程序”，并加载 `~/.agent-wiki/extension/`。首次打开扩展时按以下顺序完成配置：

1. Agent：运行 `./agent-wiki start` 并确认连接。
2. Ark API：由用户填写自己的 API Key、Endpoint 和可用模型 ID，再执行连接测试。
3. 知识库：通过 macOS 原生文件夹选择器明确选择目标目录。
4. 抖音 Cookie：由已经登录抖音网页版的用户同步。
5. GitHub（可选）：需要 Stars 导入时再完成 Device Flow 授权。

这些步骤不会替用户完成真实 OAuth、Stars 读取、模型调用或内容入库；它们仍需要在用户自己的账号、网络和第三方服务环境中验收。

## 服务生命周期

```bash
./agent-wiki start
./agent-wiki status
./agent-wiki status --json
./agent-wiki restart
./agent-wiki stop
```

`start` 在启动前执行以下检查：

- 找到 Python 3.11+，并确认控制面依赖可导入；
- 报告抖音入库 venv 是否可用；
- 读取 `[server]` 配置，拒绝非回环监听地址和无效端口；
- 检查端口是否已被占用；
- 检查私有服务状态、已有进程的启动标识和运行源码位置；
- 报告旧运行接线，但不修改它们。

端口被未知进程占用时启动会失败，不会尝试停止该进程。`stop` 只在以下信息全部一致时发送信号：

- `run/control-plane.pid` 与 `run/control-plane.json` 中的 PID；
- Agent-wiki 服务标识和状态格式版本；
- 操作系统报告的进程启动标识；
- Python 可执行文件和 `server/service_entry.py` 入口路径；
- 回环监听地址和端口。

状态文件、PID 文件和日志权限为 `0600`，`run/` 与 `logs/` 目录权限为 `0700`。状态包含源码 commit、`git describe` 版本、dirty 标记、源码根目录、Python、监听地址和启动时间。服务停止时 `status` 返回码为 `3`，不代表状态检查本身崩溃。

根级 CLI 当前面向用户公开后台服务生命周期，不公开前台运行子命令。内部 launcher 仍保留前台模式供开发调试，但不应作为用户安装后的首选入口。

## macOS 开机启动

开机启动默认关闭，只能由用户显式启用：

```bash
./agent-wiki autostart enable
./agent-wiki autostart status
./agent-wiki autostart status --json
./agent-wiki autostart disable
```

当前实现只管理以下确定性资源：

```text
label: com.agent-wiki.control-plane
plist: ~/Library/LaunchAgents/com.agent-wiki.control-plane.plist
```

plist 使用 Python 标准库 `plistlib` 结构化生成，`ProgramArguments` 是参数数组，不拼接 shell 命令。它记录 Python、源码目录、源码版本、运行目录和回环监听地址。源码路径含空格时不需要额外转义。

启用和禁用都先验证 label、管理标记、参数结构、源码路径、Python 与运行目录。发现以下情况时会安全失败，不覆盖 plist、不卸载同名 job，也不停止未知进程：

- plist 损坏、是符号链接或结构不属于当前 Agent-wiki；
- launchd 中存在没有对应已验证 plist 的同名服务；
- 已有启动项指向不同源码、Python、运行目录或版本；
- 源码目录已经移动或缺失。

对仍符合 Agent-wiki 确定性管理格式、但源码已移动的 plist，可以从原运行用户执行 `autostart disable` 清理，再从新源码目录重新 `enable`。`disable` 不会停止当前已经运行的服务；需要停止时另行执行 `./agent-wiki stop`。

## Doctor

```bash
./agent-wiki doctor
./agent-wiki doctor --json
```

`doctor` 是只读诊断，按 `PASS`、`WARN`、`FAIL` 输出：

- Python 3.11+、控制面依赖和抖音 venv 依赖；
- `ffmpeg` 与 `ffprobe`；
- `config.toml` 是否存在、TOML 是否有效、权限是否私有；
- Ark 凭据是否已配置，但不输出凭据；
- Cookie 文件是否存在且权限私有，但不读取 Cookie 内容；
- vault 是否存在以及顶层标记，不读取 `.obsidian/` 内容；
- 服务端口、托管进程、运行源码位置和版本是否一致；
- `~/.agent-wiki/extension/` 与仓库扩展源码是否一致；
- 旧部署、旧运行目录和旧环境变量是否残留。

存在 `FAIL` 时命令返回 `1`；只有 `WARN` 不改变成功返回码。用户可见文本和 JSON 会脱敏常见 API Key、Cookie、token、Authorization 赋值和 URL 查询参数。

开机启动诊断由 `./agent-wiki autostart status` 提供，显示 plist、label、加载状态、Python、源码路径、源码版本、运行目录和监听地址。

## 安全卸载

```bash
./agent-wiki uninstall
./agent-wiki uninstall --json
```

卸载只执行两类操作：

1. 通过现有进程身份校验停止服务；身份无法验证时不发送信号。
2. 通过 launchd 所有权和结构校验移除本版本管理的开机启动 plist；未知或损坏的同名配置保持不动。

卸载不会静默 purge，并明确保留：

- 知识库及其中的 Markdown 和媒体；
- `~/.agent-wiki/` 下的配置、凭据引用、日志、缓存、任务、诊断和扩展副本；
- 任何无法验证归属的进程、文件或 launchd 服务。

需要删除保留数据时，应先备份并由用户人工确认目录内容。

## 缓存报告

内部 launcher 保留缓存报告和 dry-run 预览实现，但根级 `./agent-wiki` 当前没有公开缓存子命令。报告覆盖 `cache/`、`run-artifacts/` 和 `responses-memory/`，不跟随符号链接；清理实现只允许 dry-run 汇总普通文件数量和大小，当前代码没有真实删除入口。

视频入库的任务私有缓存 `cache/videos/<task_id>/` 不在上述报告管理范围内：ingest 在任务结束（成功、失败或 SIGTERM/取消）时自行删除该目录，删除不跟随符号链接、只影响本任务目录。

## 统一操作诊断

框架级结构化操作时间线位于：

```text
~/.agent-wiki/operations/
├── index.jsonl
└── by-id/<operationId>/
    ├── summary.json
    └── timeline.jsonl
```

任务状态、GitHub 批次/子项、知识库生命周期响应和控制面回复会公开 `operationId` 或 `diagnostics`。统一时间线只保存严格脱敏后的排错摘要和 artifact 路径。视频或派生的大 prompt、完整模型响应与详细中间产物继续位于 `run-artifacts/`；不要将这些运行文件提交到仓库。

## 安全恢复

状态损坏、PID 与元数据不一致、进程启动标识变化或入口脚本不匹配时，服务管理器会拒绝发送信号。先运行 `./agent-wiki status`、`./agent-wiki doctor` 和 `./agent-wiki autostart status` 检查；不要通过端口或进程名批量终止进程。
