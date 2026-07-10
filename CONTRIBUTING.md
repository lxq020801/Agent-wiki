# 贡献指南

欢迎提交 issue 和 pull request。这个项目还在早期阶段，最需要的是清晰的复现信息、谨慎的小改动，以及不破坏本地隐私边界。

## 开发原则

- 默认保护用户本地数据，不读取或提交私人 vault、Cookie、API Key、运行缓存。
- 代码改动尽量小而清楚，避免顺手重构无关模块。
- 文档以中文为主，专业名词可以保留英文，但需要解释清楚。
- 影响入库流程、Chrome 扩展、配置写入或文件路径的改动，需要补充测试或手动验证说明。

## 本地检查

提交前建议运行：

```bash
python3 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py install/bootstrap.py
python3 tests/test_p0_static.py
python3 tests/test_douyin_image_post_static.py
node --check chrome-extension/background.js
node --check chrome-extension/popup/popup.js
node --check chrome-extension/content/douyin-current-video.js
```

## 提交前自查

- 没有提交 `~/.agent-wiki/`。
- 没有提交 Obsidian 私人 vault 内容。
- 没有提交真实 Cookie、API Key、access token、日志或运行缓存。
- README 或相关文档已经同步更新。
- 失败的测试已经说明原因，最好附上复现命令。
