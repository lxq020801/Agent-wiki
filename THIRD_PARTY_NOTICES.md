# 第三方依赖与归属

本文记录当前源码发布中直接声明或内嵌的第三方代码。它是发布审计清单，不替代各项目许可证原文，也不是法律意见。

## 内嵌源码

`deps/douyin/vendor/` 包含 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 的部分源码：

- 上游 commit：`42784ffc83a72a516bfe952153ad7e2a3998d16c`
- 快照日期：2026-06-26
- 上游许可证：Apache License 2.0
- 上游未提供独立 `NOTICE` 文件
- 本仓库的 Apache-2.0 全文见 [LICENSE](LICENSE)
- 复制范围和本地修改见 [deps/douyin/vendor/README.md](deps/douyin/vendor/README.md)

本地修改仅用于清除上游示例凭据和避免 Cookie 日志泄露；修改文件已保留上游版权头并增加修改说明。

## 直接 Python 依赖

下表来自 `requirements.txt` 与 `deps/douyin/requirements.txt`。许可证列按 2026-07-14 的上游仓库或 PyPI 元数据整理；版本范围未完全锁定，因此发布二进制包或容器前必须对实际解析出的完整依赖树重新审计。

| 包 | 当前约束 | 声明许可证 | 备注 |
|---|---|---|---|
| `websockets` | `>=12.0` | BSD-3-Clause | 控制面 WebSocket |
| `tomli` | `>=2.0` | MIT | Python 3.11 以下兼容声明；当前运行要求为 Python 3.11+ |
| `httpx` | `==0.27.*` | BSD-3-Clause | vendor HTTP 客户端兼容约束 |
| `PyYAML` | `>=6.0` | MIT | vendor 配置读取 |
| `pycryptodomex` | `>=3.19` | BSD / Public Domain | PyPI 元数据包含两类声明 |
| `pydantic` | `>=2.0` | MIT | vendor 数据模型 |
| `gmssl` | `>=3.2` | 需人工确认 | PyPI 元数据写 BSD，链接仓库当前许可证写 MIT |
| `browser-cookie3` | `>=0.19` | LGPL-3.0 | vendor import 链路 |
| `qrcode` | `>=7.4` | BSD；含 Other/Proprietary classifier | 发布捆绑产物前复核实际包内容 |
| `rich` | `>=13.0` | MIT | vendor 日志 |
| `importlib_resources` | `>=6.0` | Apache-2.0 | vendor 资源读取 |
| `openai` | `>=1.40.0` | Apache-2.0 | Ark Files / Responses API 客户端 |

这些包通过包管理器安装，不属于仓库内嵌源码。传递依赖会随解析时间和 Python 平台变化，不在本表中静态猜测。
