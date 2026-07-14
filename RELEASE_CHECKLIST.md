# 开源发布检查清单

本清单只覆盖当前仓库的技术发布步骤，不记录产品路线或未来功能。

## 版本与范围

- [ ] 工作区干净，发布 commit 已经完成代码审查。
- [ ] 发布版本与 `chrome-extension/manifest.json` 的 `version` 一致。
- [ ] `git ls-files` 只包含准备公开的源码、文档和静态资源。
- [ ] README 的平台、Python 版本、安装路径和运行目录仍与代码一致。

## 安全与许可

- [ ] 运行 `python3 scripts/release_audit.py --history`；报告中不复制疑似秘密正文。
- [ ] 确认 `~/.agent-wiki/`、真实 vault、Cookie、API Key、日志、缓存和本地配置不在 Git 跟踪列表中。
- [ ] 对实际解析出的 Python 直接/传递依赖重新生成清单并复核许可证。
- [ ] 人工处理 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中标记为“需人工确认”的许可元数据。
- [ ] vendor commit、复制范围、本地修改标记和 Apache-2.0 归属说明保持一致。
- [ ] GitHub Private vulnerability reporting 可用；若不可用，确认 `SECURITY.md` 的脱敏报告兜底仍适用。

## 验证

- [ ] 运行 `python3 -m py_compile deps/douyin/scripts/analyzer.py deps/douyin/scripts/config_loader.py deps/douyin/scripts/ingest.py server/websocket_server.py install/bootstrap.py scripts/release_audit.py`。
- [ ] 运行 `python3 tests/test_p0_static.py`。
- [ ] 运行 `python3 tests/test_douyin_image_post_static.py`。
- [ ] 运行 `python3 tests/test_release_audit.py`。
- [ ] 运行三个 `node --check chrome-extension/...` 命令，确认扩展脚本语法通过。

## 发布

- [ ] 确认目标 tag 尚不存在；若同名 tag 已发布且不指向当前 commit，不移动旧 tag，改用新版本并同步扩展 manifest。
- [ ] 从已验证 commit 创建与版本一致的 `vX.Y.Z` tag。
- [ ] 核对 GitHub 自动生成的源码归档不含忽略文件或本地运行数据。
- [ ] 发布说明只描述本版本已经实现并验证的变更、安全注意事项和已知限制。
