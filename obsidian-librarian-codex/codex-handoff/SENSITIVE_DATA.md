# SENSITIVE_DATA.md — 敏感信息排除说明（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本文件记录旧交接包的脱敏说明。当前敏感信息处理以当前代码、`docs/websocket-protocol.md`
> 和运行时 `~/.obsidian-librarian/` 的实际行为为准。

## 已排除的敏感信息类型

### 1. Cookie 数据
- **类型**: 浏览器 Cookie（抖音登录凭证）
- **位置**: `~/.obsidian-librarian/cookie/douyin.txt`
- **状态**: 已排除，交接包中不包含
- **说明**: 包含 sessionid、ttwid、msToken 等登录凭证

### 2. API Key
- **类型**: 火山方舟 API Key
- **位置**: `~/.obsidian-librarian/config.toml` 中的 `api_key`
- **状态**: 已排除，交接包中显示为空字符串
- **说明**: 用户私有密钥，用于调用火山方舟 API

### 3. Endpoint 地址
- **类型**: 火山方舟 API 端点
- **位置**: `~/.obsidian-librarian/config.toml` 中的 `endpoint`
- **状态**: 已泛化，交接包中显示为示例地址
- **说明**: 实际地址为 `https://ark.cn-beijing.volces.com/api/v3`

### 4. 用户路径
- **类型**: 用户 home 目录路径
- **位置**: 配置文件、脚本中的 `/Users/lixinqi/`
- **状态**: 已泛化，显示为 `/Users/xxx/` 或 `~`
- **说明**: 实际路径为 `/Users/lixinqi/`

### 5. 浏览器凭据
- **类型**: Chrome 扩展相关的任何凭据
- **位置**: 扩展代码、背景页存储
- **状态**: 已排除
- **说明**: 扩展不存储密码，只传递 cookie

### 6. 环境变量
- **类型**: `.env` 文件内容
- **位置**: 项目根目录（如果存在）
- **状态**: 已排除，项目未使用 `.env`
- **说明**: 配置走 TOML 文件，不走环境变量

### 7. 日志中的敏感信息
- **类型**: 运行时日志
- **位置**: `deps/douyin/logs/`, `~/.obsidian-librarian/logs/`
- **状态**: 已排除，交接包中不包含日志文件
- **说明**: 日志可能包含 URL、错误堆栈等

### 8. 虚拟环境
- **类型**: Python 虚拟环境（`.venv/`）
- **位置**: `deps/douyin/.venv/`
- **状态**: 已排除（133M，需重新创建）
- **说明**: 包含 pip 包、Python 解释器等，可能包含平台相关路径

## 交接包中的敏感信息处理

| 信息类型 | 处理方式 | 交接包状态 |
|----------|----------|------------|
| Cookie | 完全排除 | 不包含 |
| API Key | 替换为空字符串 | 显示 `""` |
| Endpoint | 泛化示例 | 显示示例地址 |
| 用户路径 | 泛化为 `~` 或 `/Users/xxx/` | 已处理 |
| 日志文件 | 完全排除 | 不包含 |
| 虚拟环境 | 完全排除 | 不包含 |
| `.env` | 完全排除 | 项目未使用 |

## 需要 Codex 自行配置的信息

1. **API Key**: 用户需提供火山方舟 API Key
2. **Vault 路径**: 用户需提供 Obsidian Vault 路径
3. **Cookie**: 扩展自动抓取，无需手动配置

## 安全提醒

- 不要将 `config.toml` 提交到 git（已在 .gitignore）
- 不要将 `cookie/` 目录提交到 git（已在 .gitignore）
- 不要将日志文件提交到 git（已在 .gitignore）
- 不要将 `.venv/` 提交到 git（已在 .gitignore）

---

> 交接包已脱敏，不包含任何真实凭据。
