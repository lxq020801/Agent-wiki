# 安全说明

Agent-wiki 会处理本地配置、Cookie、API Key、视频缓存和 Obsidian 笔记，因此安全边界很重要。

## 不应该提交的内容

- `~/.agent-wiki/`
- 真实 Obsidian 私人 vault
- 抖音 Cookie
- Ark API Key
- 任何 access token、private token、Bearer token
- 运行日志、缓存视频、任务状态文件

## 报告安全问题

如果你发现可能泄露本地隐私或密钥的问题，请不要在公开 issue 里贴真实密钥、Cookie 或私人路径。

优先使用仓库的 GitHub Private vulnerability reporting（私密漏洞报告）。如果该入口不可用，再提交一个完全脱敏的公开 issue，并只说明：

- 影响范围
- 复现步骤
- 相关文件或函数
- 是否会暴露密钥、Cookie、私人笔记或本地路径

项目处于 `0.x` 阶段，安全修复只面向最新发布版本；旧快照不承诺单独维护。报告被确认后，维护者会在修复公开前协调披露时间。

## 设计约束

- 运行时敏感信息默认只放在 `~/.agent-wiki/`。
- 状态响应和审计文件应尽量脱敏。
- WebSocket 服务默认只监听 `127.0.0.1`。
- 文档示例只能使用占位符，不得出现真实密钥或 Cookie。

## 本地信任边界

- `127.0.0.1` 控制面不是网络服务，也不提供独立用户认证；它假定同一用户账户下的本地进程和已安装浏览器扩展可信。
- 服务会拒绝带有非 Chrome 扩展 Origin 的浏览器连接，但无 Origin 的本地客户端仍可连接。不要把监听地址改成 `0.0.0.0` 或暴露给局域网/公网。
- `~/.agent-wiki/config.toml` 与 Cookie 文件应保持仅当前用户可读；备份、日志收集和问题报告都不得包含其正文。
- 发布审计只扫描仓库跟踪文件，不会读取 `~/.agent-wiki/` 或 Obsidian vault。公开发布前应运行 `python3 scripts/release_audit.py --history`。
