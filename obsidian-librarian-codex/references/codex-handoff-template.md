# Codex 交接模板

## 用途

当需要将项目从 Hermes 交接给 Codex 继续开发时，生成此脱敏交接包。

## 生成步骤

1. **创建 codex-handoff/ 目录**
   ```bash
   mkdir -p codex-handoff
   ```

2. **生成 HANDOFF.md**
   - 项目目标、背景、当前架构
   - 当前进度、最后卡住的问题
   - 下一步建议、git 状态

3. **生成 CHAT_SUMMARY.md**
   - 会话时间线、需求变化
   - 技术结论、已做过的事情

4. **生成 DECISIONS.md**
   - 关键技术决策、替代方案
   - 后续风险

5. **生成 OPEN_TASKS.md**
   - 未完成任务（P0-P3）
   - 每项写清楚目标、状态、阻塞点

6. **生成 FILE_MAP.md**
   - 核心文件清单、用途、状态

7. **生成 RUNBOOK.md**
   - 本地运行、调试、验证命令

8. **生成 TEST_RESULTS.md**
   - 最近一次验证结果
   - 通过项、失败项、错误信息

9. **生成 SENSITIVE_DATA.md**
   - 说明哪些信息已被排除
   - 只写类型和位置，不写具体值

10. **生成 sanitized-chat.jsonl**
    - 脱敏会话记录
    - 只保留 role、timestamp、content_summary

11. **生成 project-snapshot.zip**
    - 项目源码和文档
    - 排除：.git、node_modules、venv、__pycache__、logs、cookie、.env

## 脱敏检查清单

- [ ] 不包含 cookie 数据
- [ ] 不包含 API key
- [ ] 不包含 token/secret
- [ ] 不包含用户真实路径
- [ ] 不包含日志文件
- [ ] 不包含虚拟环境
- [ ] 不包含 .env 文件

## 参考

- 本次交接包位置：`/Users/lixinqi/.hermes/skills/obsidian-librarian/codex-handoff/`
- 文件数：11 个
- 总大小：207K
