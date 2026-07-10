# vendor/ — 抖音爬虫库（内嵌副本）

本目录是 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 项目的**部分源码内嵌副本**，专供 `agent-wiki` 的视频拆解工具调用。

---

## 为什么内嵌而不是 pip install

| 选择 | 问题 |
|------|------|
| `pip install douyin-tiktok-scraper` | PyPI 包是 Stable 分支，**反风控更新慢**，main 分支新代码进不去 |
| `git submodule` | 需要用户装 git + submodule init，破坏「无感」哲学 |
| **整目录复制（当前方案）** | 反风控可手动 sync main 分支；可改源码；离线可用 |

抖音风控频繁变化，**vendor 必须跟得上 main 分支**，所以选内嵌。

---

## 复制范围（最小化）

只复制视频解析需要的部分，**不复制** FastAPI 服务、TikTok、Bilibili 等无关代码：

```
vendor/crawlers/
├── __init__.py
├── base_crawler.py              基础 HTTP 客户端
├── douyin/
│   ├── __init__.py
│   └── web/
│       ├── __init__.py
│       ├── web_crawler.py       核心：DouyinWebCrawler
│       ├── endpoints.py         API 端点定义
│       ├── models.py            请求/响应模型
│       ├── utils.py             AwemeIdFetcher / BogusManager / TokenManager
│       ├── xbogus.py            X-Bogus 签名算法（黑科技）
│       ├── abogus.py            A-Bogus 签名算法（黑科技）
│       └── config.yaml          ⚠️ 含默认 Cookie，运行时被 monkey patch 覆盖
└── utils/
    ├── __init__.py
    ├── api_exceptions.py
    ├── deprecated.py
    ├── logger.py
    └── utils.py
```

**没复制的**：`app/`, `tiktok/`, `bilibili/`, `hybrid/`, `daemon/`, `start.py`。

---

## Cookie 注入机制

`vendor/crawlers/douyin/web/config.yaml` 自带一份默认 Cookie，但**会过期**。

我们的 `scripts/downloader.py` 在 import 之后用 **monkey patch** 把 cookie 替换为用户当前的（从 `~/.agent-wiki/cookie/douyin.txt` 读取）：

```python
from crawlers.douyin.web import web_crawler
web_crawler.config["TokenManager"]["douyin"]["headers"]["Cookie"] = fresh_cookie
```

**不改 vendor 源文件**，保持 sync 干净。

---

## 上游版本和同步

- **快照来源**：Evil0ctal/Douyin_TikTok_Download_API main 分支源码快照
- **快照日期**：2026-06-26
- **上游 Git**：https://github.com/Evil0ctal/Douyin_TikTok_Download_API
- **上游分支**：main

更新方式：

```bash
# 1. 从 GitHub 下载最新 zip 解压到 ~/Downloads/
# 2. 跑同步脚本
bash <repo-root>/deps/douyin/vendor-sync.sh
```

同步脚本会**只复制相关文件**，并保留我们自己加的 `__init__.py` 和 README。

---

## License

上游使用 [Apache License 2.0](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/LICENSE)。本目录代码遵循原 license。

---

## ⚠️ 注意事项

1. **不要修改 vendor 内的 .py 文件**——会让 sync 出冲突
2. `config.yaml` 里的 Cookie 是占位用，**实际不依赖**，由 monkey patch 覆盖
3. `models.py` 用了 pydantic v2，与新版兼容
4. 反风控失效时（视频解析返回 `{}`），先 sync vendor，再换 cookie
