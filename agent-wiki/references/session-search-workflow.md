# 聊天记录检索工作流

## 触发条件

当用户说以下任何一种表达时，**必须**执行本工作流，**禁止**凭记忆猜测：

- "你先去全部看一下记忆文档和聊天记录"
- "你先去看一遍全部的内容"
- "对齐一下"
- "找到昨天下午的聊天记录"
- "搜索...聊天记录"
- "看看有没有什么遗漏的"
- 任何要求回顾历史对话、对齐上下文、检查遗漏的表达

## 为什么必须查 DB

用户多次纠正助手：
- "你现在的东西和我之前说的牛头不对马嘴"
- "时间戳不对"
- "内容猜错"
- "没看全"

**用户不接受「我猜你大概意思是...」**

## 标准操作流程

### 1. 确认 DB 路径

```bash
# 主数据库
~/.hermes/state.db

# 状态快照（可能有旧数据）
~/.hermes/state-snapshots/<timestamp>/state.db

# 其他 profile
~/.hermes/profiles/<name>/state.db
```

### 2. 列出相关 sessions

```bash
# 按日期筛选
sqlite3 ~/.hermes/state.db "SELECT id, title, datetime(started_at, 'unixepoch', 'localtime') as started, model, message_count FROM sessions WHERE datetime(started_at, 'unixepoch', 'localtime') LIKE '2026-06-26%' ORDER BY started_at ASC;"
```

### 3. 读取用户消息

```bash
# 读取特定 session 的 user 消息
sqlite3 ~/.hermes/state.db "SELECT id, role, datetime(timestamp, 'unixepoch', 'localtime') as local_time, substr(content, 1, 500) as preview FROM messages WHERE session_id = '<session_id>' AND role = 'user' AND content NOT LIKE '%[System:%' AND content NOT LIKE '%[CONTEXT COMPACTION%' AND content NOT LIKE '%[ASYNC DELEGATION%' ORDER BY timestamp ASC LIMIT 100;"
```

### 4. 按时间排序汇总

- 合并多个 session 的消息
- 按时间戳排序
- 提取关键决策点

### 5. 验证时间准确性

```bash
# 验证特定时间戳的本地时间
python3 -c "import time; print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(1782451674)))"
```

## 常见错误

| 错误 | 后果 | 正确做法 |
|------|------|----------|
| 凭记忆猜测 | "牛头不对马嘴" | 必须查 DB |
| 只看当前 session | "没看全" | 检查所有相关 session |
| 忽略时间戳时区 | 时间对不上 | UTC 转本地时间验证 |
| 忽略 model 切换 | 遗漏其他模型的对话 | 检查所有 model 的 session |
| 只读 compacted summary | 丢失细节 | 读原始 messages 表 |

## 6.26 下午案例

**用户要求**："找到昨天下午的聊天记录"

**正确执行**：
1. 列出 6.26 所有 session → 发现 18 个 session
2. 读取 13:00-16:00 的 user 消息
3. 发现下午从 13:25 开始（不是 14:00）
4. 发现方案 C 在 13:56 定稿（不是 14:29）
5. 记录用户原话到文档

**错误执行**：
- 凭记忆说 "下午从 14:00 开始" → 用户纠正 "一点多"
- 没检查其他 model → 遗漏 GLM-5.2（虽然最终没找到）

## 工具

- `sqlite3` - 查询 DB
- `session_search` - Hermes 内置搜索（但可能不全）
- `delegate_task` - 派生 agent 并行搜索多个 session

## 输出格式

检索完成后，输出：
1. 完整时间线（按时间排序）
2. 关键决策点（带用户原话）
3. 确认是否有遗漏
