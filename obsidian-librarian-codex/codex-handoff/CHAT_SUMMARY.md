# CHAT_SUMMARY.md — 会话摘要（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本文件只用于追溯 Hermes 迁移前后的对话背景，包含已废弃方向。
> 当前权威入口是项目根目录 `SKILL.md`、`SCHEMA.md` 和 `docs/` 下的当前文档。

## 会话时间线

### 2026-06-26 下午（13:25-13:56）
- **方案 C 定稿**: 文件桥 + Agent 自动初始化
- **核心决策**: 扩展只抓 cookie + 写配置，不直接触发拆解
- **电灯哲学**: 用户按开关就用，零操作

### 2026-06-27 全天
- **上午**: 实现文件桥通信（Downloads API）
  - 写 bridge_poller.py 轮询处理
  - 扩展安装、图标生成、权限修复
  - 前缀修复（去掉开头的点）
- **下午**: 用户要求"立刻响应式回传"
  - 评估 WebSocket vs HTTP vs Native Messaging
  - 选择 WebSocket（实时双向通信）
  - 重写扩展为 WebSocket 版
  - 实现 Agent 端 WebSocket 服务器
  - 写 launcher.py 自动启动器
- **晚上**: 
  - 修复 WebSocket 服务器 toml 导入 bug（未验证）
  - 完善 SKILL.md 文档
  - 提交 git commit

## 需求变化

1. **初始**: 文件桥（Downloads API）→ 用户要求"立刻响应式"
2. **变更**: 升级为 WebSocket 实时通信
3. **结果**: 扩展重写，服务器新增，协议重新设计

## 技术结论

- **通信方式**: WebSocket（ws://127.0.0.1:8765）
- **扩展职责**: 控制塔（配置 + 状态 + Cookie）
- **Agent 职责**: 执行拆解（下载 + 分析 + 入库）
- **模型**: doubao-seed-2-0-lite-260428（主）/ mini（备用）
- **成本**: 91秒视频 ≈ 0.1275 元（lite）

## 已做过的事情

- ✅ 视频拆解核心代码（ingest/downloader/analyzer）
- ✅ WebSocket 服务器框架
- ✅ 扩展控制塔（popup/background/manifest）
- ✅ 自动启动器（launcher.py）
- ✅ 通信协议文档
- ✅ 设计决策文档
- ✅ 踩坑记录文档
- ✅ 项目归档（ZIP 包）

## 未验证的事项

- ⚠️ WebSocket 服务器 toml bug 是否已修复
- ⚠️ 扩展-服务器端到端连接
- ⚠️ Cookie 同步流程
- ⚠️ 任务状态推送

## 用户偏好（必须遵守）

1. 不要让用户接触命令行/配置文件/路径
2. 先加日志再测试（console.log 优先）
3. 简洁直接，不要过度展开
4. 时间准确，禁止凭记忆猜测
5. todo 跟踪，允许跳过和重排

## 敏感信息排除

- cookie 内容（~/.agent-wiki/cookie/）
- API key（config.toml 中的 api_key）
- 火山方舟 endpoint
- 用户 home 路径（已泛化）
