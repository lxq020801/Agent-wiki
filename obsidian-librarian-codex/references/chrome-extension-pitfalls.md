# Chrome 扩展开发坑与修复记录

> 本项目的 Chrome 扩展开发中踩过的坑，按时间顺序记录。供未来维护参考。

---

## 1. manifest.json 权限错误

**现象**：扩展加载失败，提示权限不足。

**原因**：`host_permissions` 错误地放在 `permissions` 数组内。

**修复**：`host_permissions` 必须作为顶层独立字段，与 `permissions` 同级。

```json
// 错误
"permissions": ["storage", "downloads", "cookies", "https://*.douyin.com/*"]

// 正确
"permissions": ["storage", "downloads", "cookies"],
"host_permissions": ["https://*.douyin.com/*"]
```

---

## 2. 图标缺失

**现象**：`Could not load icon 'icons/icon-16.png'`

**修复**：必须提供 16/32/48/128 四种尺寸图标。可用 Python Pillow 生成纯色圆点图标。

---

## 3. Cookie 抓取失败：`Cannot read properties of undefined`

**现象**：`chrome.cookies.getAll` 报错 undefined。

**原因**：manifest.json 缺少 `"cookies"` 权限。

**修复**：添加 `"cookies"` 到 `permissions` 数组。

---

## 4. 文件名非法：`Invalid filename`

**现象**：`chrome.downloads.download` 拒绝以 `.` 开头的文件名。

**原因**：`BRIDGE_PREFIX = ".obsidian-librarian."` 以点开头，被 Chrome 拒绝。

**修复**：改为 `obsidian-librarian.`（去掉开头点）。

---

## 5. 扩展写 Downloads 文件用户可见

**现象**：用户反馈"为什么下载文件夹会多出文件"。

**原因**：早期设计用 Downloads API 做文件桥，文件留在 Downloads 目录。

**修复方向**：
- v0.1：Agent 自动扫描 Downloads 并清理（已删除 bridge_poller.py，改由 Agent 处理）
- v0.x：改用 WebSocket 直接通信，不走文件桥

---

## 6. WebSocket 连接状态未显示

**现象**：扩展始终显示"Agent 未连接"，但没有状态灯。

**原因**：popup.html 缺少 `connection-status` 元素，popup.css 缺少 `.status-row` 和 `.offline` 样式。

**修复**：
- popup.html：添加 `<div class="status-dot" id="connection-status">`
- popup.css：添加 `.status-row { display: flex; gap: 8px; }` 和 `.status-dot.offline { background: var(--error); }`
- popup.js：`updateConnectionStatus()` 操作 `connection-status` 元素

---

## 7. 日志优先调试模式

**用户要求**："先加日志再测试"——不要盲试，先加详细 console.log 再跑。

**实践**：
- 扩展代码中每个函数入口加 `console.log('[Librarian] 开始 xxx')`
- 每个分支加 `console.log('[Librarian] 尝试 xxx')`
- 错误加 `console.error('[Librarian] xxx 失败:', err)`
- 背景页（background.js）和弹出页（popup.js）都加日志

---

## 8. 版本对应关系

| 扩展版本 | 通信方式 | 文件 |
|---------|---------|------|
| v0.1 (文件桥) | Downloads API + bridge_poller.py | 已废弃 |
| v0.1 (WebSocket) | WebSocket ws://127.0.0.1:8765 | 当前 |
| v0.x | WebSocket + 扩展直接触发 | 预留 |

---

## 关键用户约束

- **电灯哲学**：用户只装扩展，其他全自动。不能要求用户手动运行命令。
- **零命令行**：用户不接触终端、配置文件、路径。
- **先日志后测试**：加日志 → 测试 → 看日志 → 修复，不是盲试。
