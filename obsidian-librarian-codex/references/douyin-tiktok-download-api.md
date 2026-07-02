# Douyin_TikTok_Download_API 技术参考

> 上游依赖分析 | 2026-06-18  
> 仓库: https://github.com/Evil0ctal/Douyin_TikTok_Download_API  
> 状态: 18.4k Stars · Apache 2.0 · 活跃维护

## 与本库的关系

`douyin-ingest` 使用的 `douyin-tiktok-scraper` PyPI 包即本项目发布的官方解析库。了解上游架构有助于：
- 理解 Cookie 管理和风控机制
- 当 PyPI 包不足时，可直接调用 REST API 或 Docker 部署完整服务
- 评估是否需扩展支持 TikTok/Bilibili

## 项目架构

```
app/api/         → FastAPI REST (自动 OpenAPI 文档)
app/web/         → PyWebIO Web 前端
crawlers/        → HTTPX 异步爬虫核心
  douyin/web/    → 抖音 Web API + X-Bogus/A-Bogus 加密
  tiktok/web/    → TikTok Web API
  tiktok/app/    → TikTok App API
  bilibili/web/  → B站 Web API
  hybrid/        → 自动识别 URL 来源
chrome-cookie-sniffer/  → Chrome MV3 扩展（独立项目）
```

## 关键 API 端点

### 混合解析（最常用）
- `GET /api/hybrid/video_data?url=...&minimal=false` — 解析单一视频（自动识别抖音/TikTok/B站）
- `POST /api/hybrid/update_cookie` — 动态更新 Cookie（无需重启）

### 下载
- `GET /api/download?url=...&prefix=true&with_watermark=false` — 下载无水印视频/图集

### 抖音专用
- `GET /api/douyin/fetch_one_video?aweme_id=...` — 获取单个作品
- `GET /api/douyin/fetch_user_post_videos?sec_user_id=...` — 用户作品列表
- `GET /api/douyin/fetch_user_like_videos?sec_user_id=...` — 用户喜欢列表
- `GET /api/douyin/fetch_one_video_comments?aweme_id=...` — 视频评论
- `GET /api/douyin/fetch_user_live_videos?webcast_id=...` — 直播流
- `GET /api/douyin/gen_real_msToken` — 生成真 msToken（风控对抗）
- `GET /api/douyin/get_abogus?url=...` — 生成 A-Bogus 签名

### 完整 API 文档
- 在线: https://api.douyin.wtf/docs
- 本地部署: http://localhost/docs

## Chrome Cookie Sniffer 机制

### 工作原理
1. Chrome MV3 Service Worker 通过 `chrome.webRequest.onBeforeSendHeaders` 拦截所有发往 `*.douyin.com` 的请求
2. 从请求头中提取完整 Cookie 字符串
3. 防抖：5 分钟内不重复抓取同一服务；Cookie 无变化时跳过
4. 备用方案：`chrome.cookies.getAll({domain: '.douyin.com'})` 提取所有子域名 Cookie
5. Webhook 自动 POST 到配置的服务器地址
6. 服务端 `POST /api/hybrid/update_cookie` 动态写入配置，无需重启

### 与 douyin-ingest 的集成建议
- 用户安装 Chrome 扩展后，Cookie 自动回传
- 避免手动从浏览器 DevTools 复制 Cookie
- `ingest.py` 可直接读取部署在本地的 API 服务返回的最新 Cookie

## 水印处理

| 平台 | 无水印 | 有水印 | 实现方式 |
|------|--------|--------|----------|
| 抖音 | `nwm_video_url` (play) | `wm_video_url` (playwm) | URL 中 playwm→play 替换 |
| TikTok | `nwm_video_url_HQ` | `wm_video_url` | API 响应两套地址 |
| B站 | N/A | N/A | 无水印概念，音视频分离需 ffmpeg |

## 加密签名

- **A-Bogus**：2024年6月起替代 X-Bogus，用于 `POST_DETAIL`、`USER_POST`、`USER_FAVORITE_A` 等核心接口
- **msToken**：`TokenManager().gen_real_msToken()` 动态生成
- **verify_fp / s_v_web_id**：设备指纹，`VerifyFpManager` 生成

## 部署

### Docker（推荐）
```bash
docker pull evil0ctal/douyin_tiktok_download_api:latest
docker run -d --name douyin_tiktok_api -p 80:80 evil0ctal/douyin_tiktok_download_api
```

### 一键脚本
```bash
wget -O install.sh https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/bash/install.sh && sudo bash install.sh
```

### 系统要求
- Python 3.11+（Docker 内 `python:3.11-slim`）
- ffmpeg（仅 B 站音视频合并需要）
- 推荐部署在美国服务器（TikTok）或中国大陆（抖音）

## 限制

| 限制项 | 说明 |
|--------|------|
| Cookie 必需 | 强烈建议配置，否则大部分接口触发风控 |
| Cookie 有效期 | 约 24 小时，需定期更新 |
| 私密视频 | 无法解析 |
| 删除的视频 | 返回错误 |
| 下载端点 | 演示站已关闭，自部署需开启 `config.yaml` 中的 `Download_Switch` |
| TikTok 直链 | 直接访问返回 403，必须用 `/api/download` 接口 |
| 清晰度 | 默认返回最高可用版本，不支持参数指定 |

## 与 douyin-ingest 的对比

| 维度 | douyin-ingest | Douyin_TikTok_Download_API |
|------|--------------|----------------------------|
| 部署 | 临时 venv + CLI | Docker 服务 |
| Cookie | 手动配置 | Chrome 扩展自动嗅探 |
| 多平台 | 仅抖音 | 抖音 + TikTok + B站 |
| 批量 | 单视频 CLI | Web 批量 + REST API |
| 增值功能 | 豆包转录+分析+派生 | 无（纯数据爬取） |

## obsidian-librarian 的内嵌复用方案（决议 2026-06-26）

`deps/douyin/scripts/ingest.py` 的下载部分**内嵌 vendor 整目录复用**，不依赖 PyPI、不依赖 Docker。

### 三种内嵌方式对比与选型

| 方式 | 做法 | 优点 | 缺点 | 决议 |
|------|------|------|------|------|
| A. pip 包 | `pip install douyin-tiktok-scraper` | 一行装好 | Stable 分支落后 main，反风控可能不及时 | ❌ |
| **B. 整目录复制** | 把 `crawlers/` 复制到 `deps/douyin/vendor/` | main 分支最新、完全可控、douyin-manager 验证过 | 仓库 +6MB，需手动同步上游 | ✅ |
| C. Git submodule | submodule 指向上游 | 同步方便 | 用户 clone 多一步 | ❌ |

**选择 B**——抖音风控变化快，main 分支最新比 PyPI 版本更可靠；douyin-manager-main 在 `backend/douyin_api/` 已用同样手法且跑得通。

### 目标目录结构

```
~/.hermes/skills/obsidian-librarian/deps/douyin/
├── SKILL.md
├── vendor/                        ← 整目录复制自上游 crawlers/
│   ├── README.md                  ← 记录上游版本、commit SHA、同步方法
│   └── crawlers/douyin/web/
│       ├── web_crawler.py
│       ├── endpoints.py
│       ├── xbogus.py
│       ├── abogus.py
│       ├── utils.py
│       └── config.yaml
├── scripts/                       ← 我们自己的入口（不污染 vendor）
│   ├── ingest.py                  主入口
│   ├── downloader.py              调 vendor/ 下载
│   └── analyzer.py                调豆包视频拆解
├── requirements.txt
└── vendor-sync.sh                 可选：从上游 GitHub 拉最新 crawlers/
```

**vendor 目录是只读的**——不要改里面任何代码，所有 cookie 注入、参数定制都在 `scripts/` 里完成。

### Cookie 注入手法（不改 vendor 代码）

参考项目 `douyin-fetcher/main.py` 用 `X-Douyin-Cookie` HTTP header 注入；我们没 HTTP 服务，**改在内存里 patch 全局 config**：

```python
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).parent.parent / "vendor"
sys.path.insert(0, str(VENDOR_DIR))

# 1. 读 cookie 文件桥
cookie_path = Path("~/.obsidian-librarian/cookie/douyin.txt").expanduser()
cookie = cookie_path.read_text().strip()

# 2. import vendor 并在内存 patch config（不动文件）
from crawlers.douyin.web.web_crawler import DouyinWebCrawler, config
config["TokenManager"]["douyin"]["headers"]["Cookie"] = cookie

# 3. 正常调用
crawler = DouyinWebCrawler()
data = await crawler.fetch_one_video(aweme_id)
```

**关键**：`vendor/` 目录任何文件都不动，cookie patch 只活在进程内存里。这样上游 sync 时不会有冲突。

### 视频文件实际下载（vendor 不直接给）

`fetch_one_video()` 返回的是 metadata（含 `play_addr.url_list`），**视频文件还得自己用 httpx 下载**，且必须带 `Referer`：

```python
video_url = data["aweme_detail"]["video"]["play_addr"]["url_list"][0]
async with httpx.AsyncClient(follow_redirects=True) as client:
    r = await client.get(
        video_url,
        headers={
            "Cookie": cookie,
            "Referer": "https://www.douyin.com/",  # ⚠️ 必须，否则 403
            "User-Agent": "Mozilla/5.0 ..."
        }
    )
    output_path.write_bytes(r.content)
```

少了 `Referer` 一律 403；少了 `Cookie` 一律 401。

### 视频拆解模型选型（豆包 Seed）

| 模型 | 能力 | 价格 | 是否处理音频 |
|------|------|------|:---:|
| `doubao-seed-1.6` | 视频抽帧 + OCR + ASR | 较高 | ✅ |
| `doubao-seed-1.6-lite` | 视频抽帧 + OCR | 便宜 | ❌（实测验证） |

**关键事实**：MiniMax M3 / 豆包 Seed lite 系列**都不处理音频**——文档明说「不支持音频输入」，静音视频仍会幻觉说「听到声音」。如需口播转录：
- 用全量 `seed-1.6`（带 ASR）
- 或用 Gemini 经 OpenAI-Hub 走 `/v1/chat/completions` 兜底音频

### vendor 同步策略

抖音风控算法不定期升级（xbogus/abogus 失效会导致下载 401）。`vendor-sync.sh` 的职责：

```bash
# 1. 拉上游 main 分支最新
git clone --depth 1 https://github.com/Evil0ctal/Douyin_TikTok_Download_API tmp
# 2. 复制 crawlers/ 覆盖 vendor/crawlers/
rsync -a --delete tmp/crawlers/ vendor/crawlers/
# 3. 记录 commit SHA 到 vendor/README.md
git -C tmp rev-parse HEAD > vendor/UPSTREAM_SHA
# 4. 清理
rm -rf tmp
```

跑完后跑一遍 `scripts/ingest.py` 自测，若 401 持续则上游也没修复，需手动 issue 跟进。

### 实施 phase（Task 5 细化）

| Phase | 内容 | 预计 |
|:---:|------|:---:|
| 1 | vendor 整目录复制 + requirements.txt + vendor/README.md | 30 分 |
| 2 | downloader.py（cookie patch + metadata 抓取 + 视频下载） | 1-2 小时 |
| 3 | analyzer.py（豆包视频拆解 + ANALYSIS_PROMPT 对齐 templates/video_analysis.md） | 2-3 小时 |
| 4 | ingest.py 主入口（串联 + 错误处理 + 统一 JSON 输出） | 1 小时 |

总计 4-6 小时。Phase 1 是地基不依赖任何决策，可以最先开干。

### 决策对齐清单（实施前需用户确认）

1. **vendor 内嵌方式** → 方式 B（整目录复制）
2. **拆解模型** → seed-1.6（带音频，贵）vs seed-1.6-lite（无音频，便宜）—— 取决于用户对口播转录的需求
3. **API key 注入** → 环境变量 `ARK_API_KEY` / 配置文件 / 复用 Hermes config
4. **第一条端到端测试视频** → 由用户提供（公开、<3min、信息密度高）
