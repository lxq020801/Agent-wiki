# 抖音视频拆解工具实现参考

> 实现日期: 2026-06-26  
> 对应 Task 5 (agent-wiki spec)
> 状态: v0.1 完成，端到端冒烟通过

## 架构概览

```
deps/douyin/
├── scripts/
│   ├── ingest.py          # 主入口（编排下载→拆解→写vault→归档）
│   ├── downloader.py      # 抖音下载（cookie注入 + vendor调用 + httpx下载）
│   ├── analyzer.py        # 火山拆解（ffprobe → fps → Files API → active轮询 → Responses stream）
│   ├── config_loader.py   # 配置读取（校验 + 模板生成 + bridge_root计算属性）
│   ├── status_writer.py   # 原子写status.json（tmp + rename）
│   ├── cost_estimator.py  # 成本估算（lite 0.003/mini 0.0015 元/k tokens）
│   └── prompts/
│       └── video_analysis.md  # 拆解prompt（八步结构）
├── vendor/                # 内嵌Evil0ctal爬虫（不改源码，内存patch cookie）
│   ├── crawlers/douyin/web/   # 7个核心文件
│   └── README.md
├── vendor-sync.sh         # 从上游同步反风控更新
├── setup.sh               # 环境初始化（venv + 依赖 + ffmpeg + 目录）
├── requirements.txt       # 依赖清单（httpx 0.27锁定）
└── SKILL.md               # 完整文档
```

## 关键设计决策

### 1. 独立venv（必须）

vendor锁定httpx 0.27（0.28删了proxies参数），hermes主体锁定httpx 0.28。**版本冲突必须用隔离venv**。

```bash
# setup.sh 自动创建
~/.hermes/skills/agent-wiki/deps/douyin/.venv/
```

### 2. Cookie注入方式（内存patch，不改vendor文件）

```python
# 错误：调用 crawler.update_cookie() —— 会写回vendor的config.yaml，污染只读目录
# 正确：直接patch内存config对象
from crawlers.douyin.web.web_crawler import config
config["TokenManager"]["douyin"]["headers"]["Cookie"] = cookie
```

### 3. 文件大小校验

v0.1 软限制50MB（抖音视频普遍<50MB），超出直接报错。v0.x才做自动压缩。

### 4. file_id不缓存

v0.1不缓存，每次重新上传。预留字段在vault frontmatter里，v0.x再加KV缓存。

## 8个技术坑（实现验证）

| # | 坑 | 代码位置 | 验证状态 |
|---|-----|---------|:---:|
| ① | 不走base64/url直传 | `analyzer.py:_upload_with_preprocess()` | ✅ |
| ② | 上传必须传`preprocess_configs.video.model` | `analyzer.py:files.create(extra_body=...)` | ✅ |
| ③ | fps必须上传时设 | `analyzer.py:files.create(fps=...)` | ✅ |
| ④ | fps×duration≤1280 | `analyzer.py:calc_fps()`动态clamp | ✅ |
| ⑤ | 文件≤50MB走Files API | `analyzer.py:_check_size()` | ✅ |
| ⑥ | 必须等file.status=="active" | `analyzer.py:_wait_for_active()` | ✅ |
| ⑦ | 换quality需重新上传 | v0.1不缓存，预留字段 | ✅ |
| ⑧ | 长视频>10min需切段 | v0.1仅警告 | ✅ |

## 动态fps计算验证

```python
# 91s 爆款情绪片
balanced:  fps=2.63, target=240,  actual=239   # 正常
quality:   fps=5.00, target=1250, actual=455   # 被clamp到fps_max

# 13min教程
quality:   fps=1.60, target=1250, actual=1248  # 自动压低，避免超1280

# 1小时长视频
quality:   fps=0.34, target=1250, actual=1224  # 接近fps_min
```

## 端到端冒烟测试结果

| 场景 | 预期 | 结果 |
|------|------|------|
| 全模块import | 通过 | ✅ |
| config_loader init + check | 通过 | ✅ |
| status_writer原子写 | 通过 | ✅ |
| cost_estimator 91s lite≈0.1275元 | 匹配历史实测 | ✅ |
| fps算法6个边界case | 全部正确 | ✅ |
| 空api_key端到端 | 走到Files API 401 | ✅ |
| 错误分类（config_error/analyzer_error） | 正确 | ✅ |
| 任务归档（failed/） | 正确 | ✅ |

## 已知限制（v0.1）

1. **cronjob触发**：Hermes不运行时任务堆积，扩展无反馈
2. **配置需手动填**：v0.1 config.toml需用户填api_key和vault.path
3. **>50MB视频**：直接报错，不自动压缩
4. **file_id不缓存**：同一视频换quality重新上传
5. **长视频不切段**：>10min仅警告
6. **无HTTP服务**：server/目录占位，未实现

## 与旧设计的差异

| 项目 | 旧设计（2026-06-18） | 新设计（2026-06-26） |
|------|---------------------|---------------------|
| 依赖方式 | pip install douyin-tiktok-scraper | vendor整目录复制 |
| Cookie配置 | 环境变量/命令行 | 文件桥 `cookie/douyin.txt` |
| API Key | 环境变量 `DOUBAO_API_KEY` | config.toml `[ark].api_key` |
| 视频拆解 | 本地ffmpeg抽帧+whisper转录+vision分析 | 火山Files API+Responses API |
| 模型 | MiniMax M3 / Gemini | 豆包Seed 2.0 Lite |
| 音频处理 | whisper本地转录 | 豆包内置音频转写（Seed 2.0） |
| 输出字段 | 含`transcript`（本地转录） | 删`transcript`，加`file_id/fps_used/cost` |
| 质量档位 | 无 | balanced(240帧)/quality(1250帧) |
| 进度反馈 | 无 | stream逐段写status.json |
| 成本估算 | 无 | 基于usage+单价估算 |

## 后续工作

1. **填真api_key + 真cookie** → 跑一条真实抖音视频
2. **vault路径** → 写Markdown到真实Obsidian仓库
3. **cronjob注册** → hermes轮询inbox/
4. **视频压缩** → v0.x处理>50MB视频
5. **file_id缓存** → v0.x复用72h内file_id
6. **HTTP服务** → v0.x启FastAPI给快捷指令
