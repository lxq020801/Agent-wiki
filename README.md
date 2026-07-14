# Agent-wiki

Agent-wiki 是一个本地优先的个人知识资产系统。它把抖音视频、图文和后续派生资料整理进 Obsidian，让 AI 后续工作可以直接复用你的本地知识库。

这个项目目前偏向“给会使用本地 AI / Agent 工具的人自部署使用”，不是一个开箱即用的云服务。

当前版本专注知识入库与后续知识派生。对标视频、爆款拆解、选题和创作分析不属于 Agent-wiki 的产品范围。

## 能做什么

- 从抖音视频或图文链接创建知识笔记。
- 通过 `knowledge_ingest` 将抖音来源整理为可长期复用的知识资产。
- 调用火山方舟 Ark 视频理解能力，把内容拆成结构化 Obsidian Markdown。
- 通过 Chrome 扩展同步 Cookie、模型配置、任务状态和 Obsidian vault 路径。
- 对高置信的 GitHub 项目线索生成派生候选，方便继续沉淀项目资料。

## 项目结构

下面的 `.` 代表 Git 仓库根目录，也就是克隆后得到的 `Agent-wiki/` 文件夹，不需要再套一层项目目录。

```text
.
├── AGENTS.md             # 开发 AI 的入口和文档权威边界
├── chrome-extension/     # Chrome 扩展，负责配置、Cookie 和任务入口
├── deps/douyin/          # 抖音内容解析与 Ark 分析工具链
├── docs/                 # 当前技术说明和协议文档
├── install/              # 本地初始化脚本
├── PROJECT_INTENT.md     # 唯一长期产品基准
├── rules/                # Obsidian 笔记规则
├── scripts/              # 命令行入口
├── server/               # 本地 WebSocket 控制服务
├── templates/            # 知识资产模板
├── tests/                # 静态和回归测试
├── SCHEMA.md             # Obsidian 知识库结构约束
└── SKILL.md              # 给 Agent 读取的项目说明
```

源码、运行数据和个人知识库是三个不同位置：

```text
Agent-wiki/       # Git 仓库源码
~/.agent-wiki/    # 本地配置、服务、扩展副本、缓存和任务状态
<Obsidian vault>/ # 你的个人知识资产
```

`~/.agent-wiki/` 和 Obsidian vault 都不属于本仓库，不应该提交到 GitHub。

## 准备条件

- macOS 或 Linux
- Python 3.11+
- Git
- Chrome 或 Chromium 系浏览器
- Obsidian vault
- 火山方舟 Ark API Key
- `ffmpeg` / `ffprobe`

Node.js 不是运行依赖；贡献者只在执行扩展脚本语法检查时需要它。

## 快速开始

克隆仓库并进入仓库根目录：

```bash
git clone https://github.com/lxq020801/Agent-wiki.git
cd Agent-wiki
```

初始化本地运行环境：

```bash
python3.11 install/bootstrap.py
```

启动托管的本地控制服务：

```bash
python3.11 server/launcher.py start
python3.11 server/launcher.py status
```

`start` 会先检查 Python、控制面依赖、配置中的回环地址、端口占用、旧部署和已有服务的源码位置。它不会按进程名杀进程；`stop` 只停止由 Agent-wiki 私有状态完整确认的进程。需要前台运行时使用 `python3.11 server/launcher.py foreground`，无参数调用仍兼容此前的前台行为。

环境诊断和缓存占用预览：

```bash
python3.11 server/launcher.py doctor
python3.11 server/launcher.py cache report
python3.11 server/launcher.py cache clean --dry-run
```

缓存命令不实现真实删除。完整说明见 [本地运行与诊断](docs/runtime-operations.md)。

安装 Chrome 扩展：

1. 打开 `chrome://extensions/`
2. 打开“开发者模式”
3. 选择“加载已解压的扩展程序”
4. 选择 `~/.agent-wiki/extension/`

然后在扩展里完成：

- 填入 Ark API Key
- 同步抖音 Cookie
- 选择或识别 Obsidian vault
- 在抖音页面提交“知识入库”任务

也可以用命令行提交链接：

```bash
python3 scripts/ingest_url.py "https://v.douyin.com/..."
```

## 隐私和安全

- API Key、Cookie、任务状态和缓存只应该保存在 `~/.agent-wiki/`。
- 不要把 `~/.agent-wiki/`、Obsidian 私人 vault、真实 Cookie 或真实 API Key 提交到仓库。
- 项目里有脱敏逻辑，但开源前仍建议运行 secret scan（密钥扫描）。
- 解析公开视频内容时，请遵守平台规则、版权要求和你所在地区的法律。

## 验证

常用检查：

```bash
python3.11 scripts/release_audit.py
python3.11 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py server/runtime_manager.py server/service_entry.py server/launcher.py install/bootstrap.py scripts/release_audit.py
python3.11 tests/test_runtime_manager.py
python3.11 tests/test_p0_static.py
python3.11 tests/test_douyin_image_post_static.py
python3.11 tests/test_runtime_version_protocol.py
python3.11 tests/test_ci_integration.py
python3.11 tests/test_release_audit.py
node tests/test_extension_runtime_version.js
node tests/test_extension_contract.js
node --check chrome-extension/background.js
node --check chrome-extension/runtime-version.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

准备公开发布时，再运行包含 Git 历史的只读扫描：

```bash
python3.11 scripts/release_audit.py --history
```

## 更多文档

- [产品基准线](PROJECT_INTENT.md)
- [开发 AI 入口](AGENTS.md)
- [技术总览](docs/technical-overview.md)
- [本地运行与诊断](docs/runtime-operations.md)
- [WebSocket 协议](docs/websocket-protocol.md)
- [Ark 视频理解链路](docs/ark-video-understanding.md)
- [抖音工具说明](deps/douyin/SKILL.md)
- [知识库结构约束](SCHEMA.md)
- [第三方依赖与归属](THIRD_PARTY_NOTICES.md)
- [发布检查清单](RELEASE_CHECKLIST.md)

## 许可证

本项目使用 Apache License 2.0，见 [LICENSE](LICENSE)。

`deps/douyin/vendor/` 内嵌了 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 的部分源码快照；该部分遵循其上游 Apache-2.0 许可证。完整来源、版本和本地修改见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
