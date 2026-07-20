# Agent-wiki

Agent-wiki 是一个开源、以本地 Markdown 为核心的个人 AI 知识资产系统。它把值得长期保留的外部信息整理成用户自己拥有、可以直接阅读和编辑、也能继续交给 AI 使用的来源资产。

当前版本为 **v0.4.0**，重点解决“把内容可靠地收进知识库”以及“让陌生用户能独立安装和运行”这两件事：用户通过统一的 `./agent-wiki` 命令安装和管理本地服务，在 Chrome 扩展中按首次引导完成配置，再提交抖音内容或选择导入自己的 GitHub Stars，最终在 Obsidian 知识库中得到结构清楚、来源可追溯的 Markdown。

Agent-wiki 适合愿意在自己的电脑上运行 Python 服务、管理模型凭据，并希望长期掌控知识文件的个人用户和 Agent 使用者。它不是云端收藏服务，也不是 Obsidian 插件；Obsidian 是当前默认的阅读与管理载体，知识资产本身仍是普通文件，可以被其他编辑器、Agent 和项目直接使用。

## 当前能力

### 已支持的来源与入口

| 来源 | 当前入口 | 生成结果 | 使用前需要 |
|---|---|---|---|
| 抖音视频 | Chrome 扩展提交当前页面或分享链接；Agent 可调用本地入库执行层 | 视频来源资产；长视频会先做全局概览，再分片精拆并汇总 | 用户自行配置 Ark API Key、同步已登录的抖音 Cookie，并安装 `ffmpeg` / `ffprobe` |
| 抖音图文 | Chrome 扩展提交当前页面或分享链接；Agent 可调用本地入库执行层 | 下载多图并生成图文来源资产，保留原始图像引用 | 同上；来源需要能被当前抖音解析链路正常访问 |
| GitHub 公开仓库 | 登录后读取并选择导入自己的 Stars；抖音来源也可产生由服务内部搜索、解析的 GitHub 项目派生候选 | GitHub 项目来源资产，依据官方 API 字段与仓库 README 整理 | 用户自行配置 Ark API Key；Stars 与账号能力需要在 macOS 上完成 GitHub Device Flow 登录 |

这些链路已经在产品代码中实现，并有静态、协议和 mock（模拟依赖）测试覆盖；抖音访问、GitHub OAuth、Stars 导入和 Ark 模型调用仍依赖用户自己的账号、网络、Cookie、配额与第三方服务状态，首次安装后需要在真实环境中自行验收。

当前没有为通用网页、本地图片、本地音频或备忘录提供完整的用户入口。仓库中存在兼容字段、模板或派生执行代码，不代表这些来源已经是可直接使用的正式能力。

### 每个来源都形成三段式资产

每次成功入库都会生成一份独立的来源 Markdown，正文固定区分三类信息：

1. **简洁概括**：快速说明来源主要讲了什么。
2. **完整内容整理**：尽量完整地保留来源表达、论证关系和必要上下文，但不追求逐字转录，也不按固定清单填充无关内容。
3. **AI 分析**：明确标识为 AI 生成，只依据当前来源解释其意义、用途、适用条件与风险；不读取知识库中的其他资产，也不替用户完成外部事实核验。

资产 frontmatter（文档头部元数据）会记录来源 URL、来源类型、平台 ID 等可获得的信息。抖音来源写入 `知识资产/知识入库/`，GitHub 项目写入 `知识资产/GitHub项目/`，并同步更新知识库根目录的 `index.md`。只有派生资产真实生成后，父子资产之间才会建立链接。

### Chrome 扩展、本地服务与知识库

```text
Chrome 扩展
  配置 / Cookie / 知识库选择 / 任务与 GitHub 操作
        |
        | ws://127.0.0.1:8765
        v
本地 Agent-wiki 服务
  持久任务队列 / 来源工具 / 模型调用 / 统一诊断 / 文件写入
        |
        v
Obsidian 知识库文件夹
  Markdown 来源资产 / 来源图片 / index.md
```

- **Chrome 扩展**是本地控制台，不直接下载、分析或写知识库。它提供抖音 Cookie 同步、Ark 配置、系统文件夹选择、任务状态、GitHub 登录与导入入口，并自动跟随系统深色或浅色外观。
- **本地服务**负责执行与持久化。普通入库任务、GitHub 导入批次和诊断时间线保存在 `~/.agent-wiki/`；关闭扩展弹窗不会丢失任务，服务重启后会重新接续仍在队列中的任务与未完成批次。
- **Obsidian 知识库**是最终资产所在位置。Agent-wiki 只处理自己的 Markdown、索引和相关媒体文件，不依赖 Obsidian 私有格式，也不会读取或修改 `.obsidian/`。

任务面板可以查看进度和结果，并对符合条件的任务执行取消或重试。每次操作通过 `operationId`、`taskId` 和父子关系进入统一的脱敏诊断时间线，便于定位控制面、下载、模型、写入或 GitHub 批次中的失败。

## 从安装到第一份资产

### 1. 准备 macOS 环境

当前完整桌面流程以 **macOS** 为准：系统文件夹选择器使用 macOS 原生窗口，GitHub 凭证只保存在 macOS Keychain。Linux 可以运行部分 Python 工具和本地服务，但当前不具备等价的扩展选库与 GitHub 登录体验。

需要准备：

- Python 3.11 或更高版本
- Git（用于 clone 源码）
- Chrome 或 Chromium 系浏览器
- Obsidian（当前推荐的知识库阅读与管理工具）
- FFmpeg（安装后需要同时能运行 `ffmpeg` 与 `ffprobe`）
- 火山方舟 Ark API Key
- 处理抖音内容时可用的抖音网页登录状态

Python 和 FFmpeg 需要由用户事先通过 Homebrew、python.org 或其他可信方式安装；`./agent-wiki install` 不会修改系统 Python，也不会代装 Homebrew。Node.js 不是产品运行依赖，只在开发扩展和执行 JavaScript 检查时使用。

### 2. 初始化运行环境

```bash
git clone https://github.com/lxq020801/Agent-wiki.git
cd Agent-wiki
./agent-wiki install
```

安装命令会准备 `~/.agent-wiki/`、安装隔离的运行依赖，并把当前扩展复制到 `~/.agent-wiki/extension/`。它可以重复执行，已有配置和知识库选择会保留。源码、运行数据与个人知识库彼此独立：

```text
Agent-wiki/        # 项目源码
~/.agent-wiki/     # 本地配置、凭据引用、缓存、任务、日志与扩展副本
<你的知识库>/      # Markdown 知识资产
```

### 3. 启动本地服务

```bash
./agent-wiki start
./agent-wiki status
```

常用的服务管理和只读诊断命令是：

```bash
./agent-wiki start
./agent-wiki status
./agent-wiki doctor
./agent-wiki stop
./agent-wiki restart
```

服务只允许回环地址，不应暴露到局域网或公网。`status` 在服务未运行时返回状态码 `3`，这表示“已停止”，不表示命令自身崩溃。

### 4. 加载 Chrome 扩展

1. 打开 `chrome://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择 `~/.agent-wiki/extension/`。

扩展与本地服务会校验产品版本、协议版本和部署来源；不匹配时仍可显示诊断状态，但会暂停配置同步与入库写操作。

### 5. 按首次引导完成配置

打开扩展后，按页面给出的顺序完成：

1. **Agent**：确认本地服务已经连接；未连接时回到项目目录运行 `./agent-wiki start`，再重试连接。
2. **Ark API**：填写自己的 Ark API Key 和 Endpoint，选择或填写当前账号可用的主分析模型 ID，保存后执行连接测试。Agent-wiki 不提供模型账号、Key 或额度。
3. **知识库**：点击“选择知识库”，通过 macOS 原生文件夹选择器选择目标文件夹。有效 Agent-wiki 知识库会直接连接；空目录会补齐最小结构；非空未标记目录会先要求确认，并且不会覆盖、复制、迁移或删除已有内容。
4. **抖音 Cookie**：先在当前 Chrome 登录抖音网页版，再由扩展同步 Cookie。Cookie 需要用户自行取得并维护有效状态。
5. **GitHub（可选）**：需要导入自己的 Stars 时，再完成 GitHub Device Flow（设备授权流程）登录；不登录不影响抖音入库。

敏感信息不要粘贴到 issue、聊天或知识库笔记中。扩展显示“已配置”只说明本地状态满足检查；真实模型调用、OAuth、Stars 读取和入库结果仍需要用户在自己的账号与网络环境中完成验收。

### 6. 提交并查看结果

- 在抖音页面打开扩展，提交当前内容；也可以粘贴抖音分享链接或分享文案。
- 在 GitHub 资产页完成登录后，可读取 Stars、选择本批仓库并开始导入。每个仓库会分别返回成功、已存在或失败状态，尚未执行的批次项可以取消。
- 在任务面板查看进度、结果、派生状态与诊断信息；完成后到知识库的 `index.md` 或对应资产目录阅读 Markdown。

根级 CLI 当前负责安装、服务管理和诊断，没有公开的 `ingest` 子命令。终端用户应从扩展提交内容；Agent 集成可以调用仓库内的入库执行层。

### 7. 按需启用开机启动

Agent-wiki 安装后默认不会注册常驻服务。只有用户明确需要时才启用 macOS 开机启动：

```bash
./agent-wiki autostart enable
./agent-wiki autostart status
./agent-wiki autostart disable
```

`disable` 只移除身份校验通过的 Agent-wiki LaunchAgent，不会停止当前已经运行的服务；需要时再运行 `./agent-wiki stop`。未知、损坏或不属于当前源码的同名启动项不会被覆盖或删除。

### 8. 安全卸载

```bash
./agent-wiki uninstall
```

卸载只停止身份校验通过的服务，并移除确定由当前 Agent-wiki 管理的开机启动接线。它不会清理知识库，也会保留 `~/.agent-wiki/` 中的配置、凭据引用、日志、缓存、任务、诊断和扩展副本；如需删除这些数据，应先备份并由用户人工确认目录内容。

## GitHub 资产能力

Agent-wiki 内置官方 GitHub App 的公开 client ID。普通安装可直接通过 Device Flow（设备授权流程）登录，不需要自行创建 App，也不需要提供 client secret 或手工填写 token。自维护部署可以用 `AGENT_WIKI_GITHUB_CLIENT_ID` 覆盖默认 client ID。

当前 GitHub 能力保持在个人资产导入所需的范围内：

- 用户可见入口是分页读取自己的 Stars，再选择性批量导入；扩展当前不提供手动仓库搜索。
- 抖音来源产生 GitHub 项目派生候选后，服务会通过 GitHub 官方 API 在内部搜索和解析目标；唯一可信匹配可以继续执行，匹配歧义时等待用户确认。
- 使用 repository ID 和规范化的 `owner/repo` 双重去重；仓库改名后仍优先按稳定 ID 识别。
- 依据 GitHub 官方 API 元数据与公开 README 生成同样的“简洁概括 / 完整内容整理 / AI 分析”资产。
- 由用户点击“检查更新”，先比较 README、Release、License、归档状态、默认分支等来源信息；只有用户确认后才改写资产。
- “资产创建后自动 Star”默认关闭；即使开启，Star 失败也不会回滚已写入的知识资产。

当前不支持私有仓库入库。官方 App 只申请 `Starring: Read and write` 与 `Metadata: Read-only`；公开 README 和 Release 通过匿名 GitHub API 获取。详细权限、凭证与刷新边界见 [GitHub 联动](docs/github-integration.md)。

## 隐私与安全边界

Agent-wiki 是本地优先产品，但不是完全离线产品。使用前应理解这些数据流：

- Ark API Key、抖音 Cookie、本地任务和缓存保存在 `~/.agent-wiki/`；GitHub OAuth token 只保存在 macOS Keychain，不进入扩展存储、普通配置、诊断文件或知识库。
- 抖音视频、图文及分析提示会发送到用户配置的火山方舟 Ark Files / Responses API；GitHub 公开来源材料会发送给已配置的 Ark 模型生成资产。第三方平台如何处理数据由其服务条款与用户账号配置决定。
- 本地 WebSocket 默认只监听 `127.0.0.1`，没有独立多用户认证，信任边界是当前系统用户下的本地进程与已安装扩展。不要改成 `0.0.0.0`。
- `doctor` 只检查 Cookie 文件是否存在，不读取 Cookie 正文；只检查知识库顶层标记，不读取 `.obsidian/` 内容。正常入库同样不会读写 `.obsidian/`。
- Agent-wiki 不会对知识库执行 `git init`、`git add` 或 `git commit`，也不会修改已有 Git 历史。版本控制与备份由用户自行管理。
- 不要提交或分享 `~/.agent-wiki/`、私人知识库、真实 Cookie、API Key、token、日志或缓存。报告安全问题前请阅读 [安全说明](SECURITY.md)。

## 运行诊断

```bash
./agent-wiki status
./agent-wiki doctor
./agent-wiki autostart status
```

`doctor` 是只读检查，不会读取 Cookie 正文或 `.obsidian/` 内容。服务管理、进程身份校验、运行目录、缓存报告的内部边界和诊断格式见 [本地运行与诊断](docs/runtime-operations.md)。

## 开发与验证

开始修改项目前，请先阅读 [产品基准线](PROJECT_INTENT.md) 和 [开发 AI 入口](AGENTS.md)。贡献约定与提交前检查见 [贡献指南](CONTRIBUTING.md)。

常用的完整静态与回归检查入口：

```bash
python3.11 scripts/release_audit.py
python3.11 -m unittest discover -s tests -p 'test_*.py'
node tests/test_extension_runtime_version.js
node tests/test_extension_contract.js
node tests/test_github_extension_contract.js
node tests/test_douyin_current_video_title.js
node tests/test_popup_ui_contract.js
node --check chrome-extension/background.js
node --check chrome-extension/runtime-version.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

关键技术文档：

- [当前工具运行说明](SKILL.md)
- [技术总览](docs/technical-overview.md)
- [知识资产结构契约](SCHEMA.md)
- [WebSocket 协议](docs/websocket-protocol.md)
- [Ark 视频理解链路](docs/ark-video-understanding.md)
- [抖音工具说明](deps/douyin/SKILL.md)
- [发布检查清单](RELEASE_CHECKLIST.md)

## 许可证与第三方归属

Agent-wiki 使用 [Apache License 2.0](LICENSE)。

`deps/douyin/vendor/` 内嵌了 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 的部分源码快照，该部分遵循上游 Apache-2.0 许可证。具体上游版本、复制范围、本地修改以及直接依赖的许可信息见 [第三方依赖与归属](THIRD_PARTY_NOTICES.md)。
