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

可以在 GitHub 上提交一个不含敏感内容的 issue，说明：

- 影响范围
- 复现步骤
- 相关文件或函数
- 是否会暴露密钥、Cookie、私人笔记或本地路径

## 设计约束

- 运行时敏感信息默认只放在 `~/.agent-wiki/`。
- 状态响应和审计文件应尽量脱敏。
- WebSocket 服务默认只监听 `127.0.0.1`。
- 文档示例只能使用占位符，不得出现真实密钥或 Cookie。
