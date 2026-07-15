# GitHub 联动

GitHub 联动运行在现有本地控制服务中。Chrome 扩展只展示授权码、账号摘要、仓库列表和操作状态；OAuth token 不会进入扩展存储、普通配置、日志、审计产物或 Git 仓库。

## 开源部署配置

每个部署者需要创建自己的 GitHub App：

1. 在 GitHub Developer settings 中创建 GitHub App。
2. 开启 Device Flow。
3. Account permissions 只授予 `Starring: Read and write`。
4. Repository permissions 只保留 `Metadata: Read-only`，不申请 Contents 或私有仓库权限。
   公开仓库 README 与 Release 通过匿名 GitHub 官方 API 读取，因此不扩大 App 的 Contents 权限。
5. 取得 GitHub App client ID。client ID 不是 secret；不要创建或配置 client secret。
6. 启动本地服务前设置环境变量：

```bash
export AGENT_WIKI_GITHUB_CLIENT_ID="<your-github-app-client-id>"
python3.11 server/launcher.py restart
```

也可以在 `~/.agent-wiki/config.toml` 的 `[github].client_id` 写入同一个非敏感 client ID。环境变量优先。未配置时，扩展 GitHub 页面会明确显示缺少 client ID，登录按钮不会发起无效请求。

## 登录与凭证

扩展点击“登录 GitHub”后，本地服务向 GitHub Device Flow 申请一次性用户代码。用户在 GitHub 官方页面完成授权；扩展轮询本地服务获取等待、成功、拒绝、超时或错误状态。

授权成功后，token 只保存到 macOS Keychain：

- Keychain service：`com.agent-wiki.github`
- Keychain account：`github-oauth-token`
- 注销会删除该 Keychain 项。
- GitHub API 返回 `401` 时会删除失效 token，并要求重新登录。
- Device code 和完整授权响应只存在于本地服务内存，不写运行文件。

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
└── repositories.json   # repository ID、owner/repo、资产路径和刷新快照
```

GitHub 首次写入、Stars 导入和确认刷新只写本次资产与 `index.md`，不会执行 `git init`、`git add` 或 `git commit`。核心派生执行器在 GitHub 项目资产与索引成功写入后调用 `server.github_service.register_derived_repository(...)`；该钩子登记 repository ID/owner/repo，并在开关开启时执行非阻断自动 Star。
