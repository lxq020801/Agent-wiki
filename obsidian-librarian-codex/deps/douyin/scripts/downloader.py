"""
downloader.py — 抖音视频下载

职责：
  1. 解析抖音 URL（短链/完整链/分享口令）→ aweme_id
  2. 注入用户 cookie（不写 vendor 文件，monkey patch 内存）
  3. 调 vendor crawler 拿 metadata
  4. 用 httpx 下载无水印视频到本地路径

不做：
  - 火山 API 调用（交给 analyzer）
  - 写 vault Markdown（交给 ingest）

关键点：
  - vendor 的 update_cookie() 会写回 config.yaml 污染源文件 → 不能用
  - 改用内存 patch：vendor.crawlers.douyin.web.web_crawler.config[...]
  - 抖音风控会让 fetch_one_video 返回 None 或 status=2 → 抛 DouyinError
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 把 vendor 加进 sys.path
_VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


class DouyinError(Exception):
    """抖音下载相关错误。子类区分原因，方便 ingest 做 status 分类。"""


class CookieInvalidError(DouyinError):
    """Cookie 失效或风控（fetch 返回空/异常）"""


class VideoNotFoundError(DouyinError):
    """视频不存在或已删除"""


class DouyinRateLimitedError(DouyinError):
    """抖音侧限流"""


class NetworkError(DouyinError):
    """网络层错误（下载/解析）"""


@dataclass
class VideoMeta:
    aweme_id: str
    title: str
    author: str
    author_sec_uid: str
    duration_sec: float
    cover_url: str
    play_url: str          # 无水印 URL
    source_url: str        # 用户输入的原始 URL
    raw: dict              # vendor 返回的完整 metadata，留给上层挖掘


# ─────────────────────────────────────────────────────────────────
# Cookie 注入
# ─────────────────────────────────────────────────────────────────


def _read_cookie(cookie_path: Path) -> str:
    """从文件读 cookie。文件不存在或为空时返回空字符串（用 vendor 默认）。"""
    if not cookie_path.exists():
        return ""
    text = cookie_path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    # 容错：扩展可能存成 JSON 或 Set-Cookie 行
    if text.startswith("{") or text.startswith("["):
        # JSON 形式（如 [{"name":"...","value":"..."},...]）
        import json
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                return "; ".join(f"{c['name']}={c['value']}" for c in arr if "name" in c)
        except Exception:
            pass
    # Netscape cookie file format from the Chrome extension:
    # domain \t include_subdomains \t path \t secure \t expires \t name \t value
    if "\t" in text:
        pairs: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name, value = parts[-2], parts[-1]
                if name:
                    pairs.append(f"{name}={value}")
        if pairs:
            return "; ".join(pairs)
    return text


def _patch_cookie(cookie: str) -> None:
    """把 cookie 注入到 vendor 的内存配置里。

    ⚠️ 不调用 vendor.update_cookie()，避免污染 config.yaml 源文件。
    """
    if not cookie:
        return
    from crawlers.douyin.web import web_crawler as wc  # type: ignore
    wc.config["TokenManager"]["douyin"]["headers"]["Cookie"] = cookie


# ─────────────────────────────────────────────────────────────────
# URL 解析
# ─────────────────────────────────────────────────────────────────


_DY_URL_PATTERN = re.compile(
    r"https?://(?:v\.douyin\.com/[A-Za-z0-9_-]+|"
    r"(?:www\.)?(?:douyin|iesdouyin)\.com/(?:video|share/video|note)/\d+)",
    re.IGNORECASE,
)


def extract_url(text: str) -> str:
    """从分享口令文本中提取真正的链接。

    输入示例：
        "7.43 pda:/ 让你 ... https://v.douyin.com/L5pbfdP/  复制"
    输出：
        "https://v.douyin.com/L5pbfdP/"
    """
    text = text.strip()
    m = _DY_URL_PATTERN.search(text)
    if m:
        return m.group(0)
    raise DouyinError(f"无法从输入中提取抖音链接：{text[:80]!r}")


# ─────────────────────────────────────────────────────────────────
# Metadata 拿取
# ─────────────────────────────────────────────────────────────────


def _extract_video_meta(aweme_id: str, raw: dict, source_url: str) -> VideoMeta:
    """从 vendor 返回的 aweme dict 抽出我们关心的字段。

    抖音返回结构（常见）：
        raw["aweme_detail"] = {
            "desc": "标题",
            "author": {"nickname": "...", "sec_uid": "..."},
            "video": {
                "play_addr": {"url_list": [...]},
                "cover": {"url_list": [...]},
                "duration": 12345  # 毫秒
            },
            "status": {"is_delete": False},
            ...
        }
    """
    detail = raw.get("aweme_detail") or raw
    if not isinstance(detail, dict):
        raise CookieInvalidError(
            "metadata 不是 dict，可能是 cookie 失效或风控（vendor 返回空响应）"
        )

    # 视频被删
    status = detail.get("status") or {}
    if status.get("is_delete") or status.get("allow_share") is False:
        raise VideoNotFoundError(f"视频已被删除或限制访问：{aweme_id}")

    video = detail.get("video") or {}
    play_addr = video.get("play_addr") or {}
    url_list = play_addr.get("url_list") or []
    if not url_list:
        # 没拿到播放地址，多半是风控
        raise CookieInvalidError(
            f"未取到视频播放 URL（aweme_id={aweme_id}），cookie 可能失效"
        )

    play_url = url_list[0].replace("playwm", "play")  # 去水印兜底
    cover_list = (video.get("cover") or {}).get("url_list") or []
    cover_url = cover_list[0] if cover_list else ""

    duration_ms = video.get("duration") or detail.get("duration") or 0
    duration_sec = float(duration_ms) / 1000.0 if duration_ms > 1000 else float(duration_ms)

    author = detail.get("author") or {}
    return VideoMeta(
        aweme_id=str(aweme_id),
        title=(detail.get("desc") or "").strip() or f"untitled-{aweme_id}",
        author=(author.get("nickname") or "").strip(),
        author_sec_uid=(author.get("sec_uid") or "").strip(),
        duration_sec=duration_sec,
        cover_url=cover_url,
        play_url=play_url,
        source_url=source_url,
        raw=detail,
    )


async def fetch_metadata(url: str, cookie_path: Path) -> VideoMeta:
    """从抖音 URL 拿视频 metadata。"""
    real_url = extract_url(url)
    cookie = _read_cookie(cookie_path)
    _patch_cookie(cookie)

    # 延迟 import：让 cookie patch 先生效
    from crawlers.douyin.web.web_crawler import DouyinWebCrawler  # type: ignore

    crawler = DouyinWebCrawler()

    try:
        aweme_id = await crawler.get_aweme_id(real_url)
    except Exception as e:
        raise NetworkError(f"解析 aweme_id 失败：{e}") from e

    if not aweme_id:
        raise DouyinError(f"无法从链接提取 aweme_id：{real_url}")

    try:
        raw = await crawler.fetch_one_video(aweme_id)
    except Exception as e:
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "限流" in msg:
            raise DouyinRateLimitedError(str(e)) from e
        raise NetworkError(f"获取视频信息失败：{e}") from e

    if not raw:
        raise CookieInvalidError(
            f"vendor 返回空响应（aweme_id={aweme_id}），cookie 失效或风控"
        )

    return _extract_video_meta(aweme_id, raw, source_url=real_url)


# ─────────────────────────────────────────────────────────────────
# 视频文件下载
# ─────────────────────────────────────────────────────────────────


def _slugify(text: str, max_len: int = 60) -> str:
    """生成文件名安全的 slug。中文保留。"""
    # 去掉换行/控制字符
    t = re.sub(r"[\x00-\x1f\x7f]", "", text)
    # 替换文件系统不允许的字符
    t = re.sub(r"[\\/\|:*?\"<>]", "_", t)
    # 折叠空格
    t = re.sub(r"\s+", "-", t).strip("-_.")
    if len(t) > max_len:
        t = t[:max_len]
    return t or "untitled"


async def download_video(
    meta: VideoMeta,
    out_dir: Path,
    *,
    timeout: float = 120.0,
    progress_cb=None,
) -> Path:
    """把 meta.play_url 指向的视频下载到 out_dir。

    返回保存的 mp4 路径。

    progress_cb(downloaded_bytes, total_bytes) — 可选回调
    """
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    date = time.strftime("%Y%m%d")
    fname = f"{date}-{_slugify(meta.title)}-{meta.aweme_id[-6:]}.mp4"
    out_path = out_dir / fname

    if out_path.exists() and out_path.stat().st_size > 0:
        # 已下载过，跳过
        return out_path

    # 抖音 CDN 要求 Referer
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
    }

    import httpx

    tmp_path = out_path.with_suffix(".mp4.part")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, read=timeout),
        follow_redirects=True,
    ) as client:
        try:
            async with client.stream("GET", meta.play_url, headers=headers) as resp:
                if resp.status_code == 404:
                    raise VideoNotFoundError(f"CDN 返回 404：{meta.play_url}")
                if resp.status_code >= 400:
                    raise NetworkError(
                        f"CDN HTTP {resp.status_code}：{meta.play_url}"
                    )

                total = int(resp.headers.get("content-length", "0"))
                got = 0
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(got, total)
                            except Exception:
                                pass
        except httpx.HTTPError as e:
            tmp_path.unlink(missing_ok=True)
            raise NetworkError(f"下载失败：{e}") from e

    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise NetworkError("下载完成但文件为空")

    tmp_path.rename(out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────
# 高层入口（供 ingest 调用）
# ─────────────────────────────────────────────────────────────────


async def download(url: str, *, cookie_path: Path, out_dir: Path,
                   progress_cb=None) -> tuple[VideoMeta, Path]:
    """端到端：URL → metadata → 下载 → (meta, 本地路径)"""
    meta = await fetch_metadata(url, cookie_path)
    path = await download_video(meta, out_dir, progress_cb=progress_cb)
    return meta, path


# ─────────────────────────────────────────────────────────────────
# CLI for debug
# ─────────────────────────────────────────────────────────────────


def _cli_main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="抖音下载（debug 用）")
    parser.add_argument("url", help="抖音链接或分享口令文本")
    parser.add_argument(
        "--cookie", default="~/.obsidian-librarian/cookie/douyin.txt"
    )
    parser.add_argument("--out-dir", default="/tmp/douyin-test")
    parser.add_argument("--meta-only", action="store_true", help="只取 metadata")
    args = parser.parse_args()

    cookie_path = Path(args.cookie).expanduser()
    out_dir = Path(args.out_dir).expanduser()

    async def run():
        if args.meta_only:
            meta = await fetch_metadata(args.url, cookie_path)
            print(f"✓ meta:")
            print(f"  aweme_id: {meta.aweme_id}")
            print(f"  title:    {meta.title}")
            print(f"  author:   {meta.author}")
            print(f"  duration: {meta.duration_sec:.1f}s")
            print(f"  play_url: {meta.play_url[:100]}...")
            return
        meta, path = await download(
            args.url, cookie_path=cookie_path, out_dir=out_dir,
            progress_cb=lambda got, total: print(
                f"\r  下载中 {got/1024/1024:.1f}/{total/1024/1024:.1f} MB",
                end="", flush=True
            ),
        )
        print()
        print(f"✓ 已下载: {path} ({path.stat().st_size/1024/1024:.1f} MB)")

    try:
        asyncio.run(run())
        return 0
    except DouyinError as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli_main())
