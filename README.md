# Agent-wiki

Agent-wiki 是一个本地优先的个人知识资产系统。它把抖音视频、图文和后续派生资料整理进 Obsidian，让 AI 后续工作可以直接复用你的本地知识库。

这个项目目前偏向“给会使用本地 AI / Agent 工具的人自部署使用”，不是一个开箱即用的云服务。

## 能做什么

- 从抖音视频或图文链接创建知识笔记。
- 支持两种入库意图：`knowledge_ingest`（知识入库）和 `viral_breakdown`（爆款拆解）。
- 调用火山方舟 Ark 视频理解能力，把内容拆成结构化 Obsidian Markdown。
- 通过 Chrome 扩展同步 Cookie、模型配置、任务状态和 Obsidian vault 路径。
- 对高置信的 GitHub 项目线索生成派生候选，方便继续沉淀项目资料。

## 项目结构

```text
.
├── chrome-extension/     # Chrome 扩展，负责配置、Cookie 和任务入口
├── deps/douyin/          # 抖音内容解析与 Ark 分析工具链
├── docs/                 # 当前技术说明和协议文档
├── install/              # 本地初始化脚本
├── references/           # 设计参考和历史材料
├── rules/                # Obsidian 笔记规则
├── scripts/              # 命令行入口
├── server/               # 本地 WebSocket 控制服务
├── templates/            # 知识资产模板
├── tests/                # 静态和回归测试
├── SCHEMA.md             # Obsidian 知识库结构约束
└── SKILL.md              # 给 Agent 读取的项目说明
```

运行时数据默认写在本机用户目录：

```text
~/.agent-wiki/
```

这里会保存配置、任务状态、缓存、Cookie、扩展副本和运行审计文件。它们不应该提交到 Git。

## 准备条件

- macOS 或 Linux
- Python 3.11+
- Node.js（只用于检查扩展脚本语法）
- Chrome 或 Chromium 系浏览器
- Obsidian vault
- 火山方舟 Ark API Key
- `ffmpeg` / `ffprobe`

## 快速开始

初始化本地环境：

```bash
python3 install/bootstrap.py
```

启动本地控制服务：

```bash
python3 server/launcher.py
```

安装 Chrome 扩展：

1. 打开 `chrome://extensions/`
2. 打开“开发者模式”
3. 选择“加载已解压的扩展程序”
4. 选择 `~/.agent-wiki/extension/`

然后在扩展里完成：

- 填入 Ark API Key
- 同步抖音 Cookie
- 选择或识别 Obsidian vault
- 在抖音页面提交“知识入库”或“爆款拆解”任务

也可以用命令行提交链接：

```bash
python3 scripts/ingest_url.py "https://v.douyin.com/..." --intent knowledge_ingest
```

## 隐私和安全

- API Key、Cookie、任务状态和缓存只应该保存在 `~/.agent-wiki/`。
- 不要把 `~/.agent-wiki/`、Obsidian 私人 vault、真实 Cookie 或真实 API Key 提交到仓库。
- 项目里有脱敏逻辑，但开源前仍建议运行 secret scan（密钥扫描）。
- 解析公开视频内容时，请遵守平台规则、版权要求和你所在地区的法律。

## 验证

常用检查：

```bash
python3 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py install/bootstrap.py
python3 tests/test_p0_static.py
python3 tests/test_douyin_image_post_static.py
node --check chrome-extension/background.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

## 更多文档

- [技术总览](docs/technical-overview.md)
- [WebSocket 协议](docs/websocket-protocol.md)
- [Ark 视频理解链路](docs/ark-video-understanding.md)
- [抖音工具说明](deps/douyin/SKILL.md)
- [知识库结构约束](SCHEMA.md)

## 许可证

本项目使用 Apache License 2.0，见 [LICENSE](LICENSE)。

`deps/douyin/vendor/` 内嵌了 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 的部分源码快照；该部分遵循其上游 Apache-2.0 许可证。
