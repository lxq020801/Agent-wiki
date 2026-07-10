# 项目归档与交接指南

## 何时归档

- 项目阶段性完成，准备交接给另一个 Agent（如 Codex）
- 需要保存聊天记录、决策记忆、产物供后续查阅
- 用户明确要求"打包"或"交接"

## 归档内容

1. **项目代码** — `project-code/`
   - 完整源代码（排除 .venv、logs、__pycache__）
   - 保留 .git 历史

2. **聊天记录** — `chat-history/session-summary.md`
   - 关键决策点
   - 技术栈和版本历史
   - 需求变化时间线

3. **记忆文件** — `memories/`
   - `user-profile.md` — 用户画像、行为准则、产品偏好
   - `project-evolution.md` — 项目演进时间线、技术债务
   - `technical-decisions.md` — 架构决策、踩坑记录

4. **交接文档** — `CODEX-HANDOFF.md`
   - 当前状态
   - 阻塞 bug
   - 待完成任务（P0-P3）
   - 验证命令

5. **归档索引** — `ARCHIVE-INDEX.md`
   - 文件速查表
   - 核心文件说明

## 脱敏要求

- ❌ 排除：cookie、API key、token、账号信息、headers、.env
- ✅ 保留：代码结构、架构图、协议规范、文档
- 🔄 泛化：用户路径 → `/Users/xxx/`，真实 key → `""`

## 生成命令

```bash
# 1. 创建归档目录
mkdir -p agent-wiki-archive/{project-code,chat-history,memories,docs,references}

# 2. 复制项目代码（排除敏感文件）
cp -r project/* project-code/
rm -rf project-code/.venv
rm -rf project-code/logs
rm -rf project-code/__pycache__

# 3. 生成记忆文件
cat > memories/user-profile.md << 'EOF'
# 用户画像
...（从 session 中提取）
EOF

# 4. 生成交接文档
cat > CODEX-HANDOFF.md << 'EOF'
# 交接文档
...（包含 bug 清单、待办、验证命令）
EOF

# 5. 打包
zip -r agent-wiki-archive.zip . -x "*.DS_Store" -x "*__pycache__*"
```

## 交接包验证

- [ ] 所有文件存在
- [ ] 不包含敏感信息
- [ ] ZIP 可解压
- [ ] 总大小合理（< 1M 为代码，< 10M 含 vendor）

## 参考

- 本次归档位置：`/Users/lixinqi/agent-wiki-archive/`
- 本次交接包：`/Users/lixinqi/.hermes/skills/agent-wiki/codex-handoff/`
