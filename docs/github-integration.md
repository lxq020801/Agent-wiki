# GitHub 联动

GitHub 联动运行在现有本地控制服务中。Chrome 扩展只展示授权码、账号摘要、仓库列表和操作状态；OAuth token 不会进入扩展存储、普通配置、日志、审计产物或 Git 仓库。

## 开源部署配置

Agent-wiki 内置官方 GitHub App 的公开 client ID。普通用户安装后直接登录即可，不需要创建 GitHub App，也不需要配置 token、client secret 或私钥。client ID 只是公开的应用标识，不是登录凭证。

官方 App 当前配置为：

- Device Flow：开启。
- Account permissions：`Starring: Read and write`。
- Repository permissions：只保留 `Metadata: Read-only`，不申请 Contents 或私有仓库权限。
- 公开仓库 README 与 Release 通过匿名 GitHub 官方 API 读取，因此不扩大 App 的 Contents 权限。

自行维护的分支如需改用自己的 GitHub App，可以：

1. 创建启用 Device Flow 的 GitHub App，并采用同样的最小权限。
2. 取得 GitHub App client ID。Device Flow 的设备码申请和 token 轮询只发送 client ID，不需要 client secret；本项目不读取、保存或配置 client secret。
3. 启动本地服务前设置环境变量：

```bash
export AGENT_WIKI_GITHUB_CLIENT_ID="<your-github-app-client-id>"
./agent-wiki restart
```

也可以在 `~/.agent-wiki/config.toml` 的 `[github].client_id` 写入同一个非敏感 client ID。优先级依次为显式构造参数、环境变量、本地配置和官方默认值。

## 登录与凭证

扩展点击“登录 GitHub”后，本地服务向 GitHub Device Flow 申请一次性用户代码。扩展提供复制按钮并打开 GitHub 官方授权页；本地服务在后台持续轮询 GitHub，因此关闭扩展弹窗不会中断登录。再次打开扩展时，会从本地服务恢复同一验证码、有效期或已登录状态。短暂网络错误会退避重试；验证码过期或本地服务重启后需要重新生成。点击取消会终止对应流程，即使换取 token 的网络请求已经发出也不会保留该流程取得的凭证。

授权成功后，token 只保存到 macOS Keychain：

- Keychain service：`com.agent-wiki.github`
- Keychain account：`github-oauth-token`
- `security add-generic-password` 的末尾 `-w` 会提示输入并确认密码；服务通过 stdin 提供两次相同内容，避免 token 出现在进程参数中。
- 服务必须在写入后使用 `security find-generic-password -w` 读回同一个 token，再用读回值请求 `GET /user`。只有写入成功、读回完全一致且账号验证成功后，状态才会返回 `authenticated=true` 和账号信息。
- 注销会删除该 Keychain 项。
- GitHub API 返回 `401` 时会删除失效 token，并要求重新登录。
- Device code 和完整授权响应只存在于本地服务内存，不写运行文件。

授权流程会把非敏感状态写入 `github/authorization.json`。后台轮询即使发生在扩展弹窗关闭后，最终失败也会记录 `state=failed` 和 `lastAuthorizationError`；错误只包含 `code`、面向用户的 `message`、失败 `stage` 和时间，不保存 token、device code、Cookie 或 GitHub 完整响应。新的授权、取消、成功或注销会更新该状态。

登录请求还会生成统一 `operationId`。后台 Device Flow 轮询、可重试错误、最终成功/失败或取消继续写入同一条 `operations/by-id/<operationId>/timeline.jsonl`；`userCode`、`deviceCode` 和完整授权响应会在进入统一时间线前删除。Stars 批次和每个仓库子项分别持有 operation，并通过 `parentId` 关联，因此弹窗关闭或服务重启后仍能定位单项失败。

### Keychain 持久化故障根因

旧实现向末尾裸 `-w` 的 `security add-generic-password` stdin 只写入一次 token。macOS `security` 实际要求连续输入密码和确认密码；单行输入会先发生确认不匹配，随后可能在 EOF 下写入空密码却返回退出码 `0`。服务当时没有读回验证，仍用内存中的 token 请求 `/user`，所以弹窗会短暂显示正确账号；后续真实状态检查从 Keychain 读到空值后才变成未连接。当前实现以写后读回和 `/user` 验证共同阻断这条假成功路径。

## 仓库、Stars 与刷新

- 仓库搜索使用 `GET /search/repositories`，支持分页；限流响应会返回建议重试时间。
- “导入我的 Stars”使用 `GET /user/starred`。界面可全选当前已加载列表或逐项选择，批量任务逐项报告成功、已存在或失败，并可取消尚未执行的项目。
- 正式 GitHub 项目资产写入 `知识资产/GitHub项目/`，并更新 `index.md`。
- 自动 Star 默认关闭。开启后，仅在正式派生资产与索引成功写入并调用登记钩子后，才请求 `PUT /user/starred/{owner}/{repo}`。Stars 导入和普通首次写入不会重复 Star；Star 失败只作为附加结果返回，不回滚知识资产。
- 刷新只由用户点击“检查更新”触发。服务比较 README、最新 Release、License、归档状态、最近推送、默认分支和仓库路径；发现变化后先返回摘要，只有用户确认才改写资产。无变化会明确返回 `no_changes`。

## 去重与运行文件

去重同时比较 GitHub repository ID 和规范化的小写 `owner/repo`。repository ID 优先，因此仓库改名后仍识别为同一资产；路径信息只在用户确认刷新后更新。

非敏感运行文件位于：

```text
~/.agent-wiki/github/
├── settings.json       # autoStar 等非敏感设置
├── repositories.json   # repository ID、owner/repo、资产路径和刷新快照
└── authorization.json  # 非敏感授权状态与最后失败阶段
```

GitHub 首次写入、Stars 导入和确认刷新只写本次资产与 `index.md`，不会执行 `git init`、`git add` 或 `git commit`。核心派生执行器在 GitHub 项目资产与索引成功写入后调用 `server.github_service.register_derived_repository(...)`；该钩子登记 repository ID/owner/repo，并在开关开启时执行非阻断自动 Star。
