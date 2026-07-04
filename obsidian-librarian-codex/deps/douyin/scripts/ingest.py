"""
ingest.py - Douyin video ingest entrypoint

P0 main path:
  Agent calls:
     python scripts/ingest_url.py "<douyin-url>"

Supported lower-level modes:
  1. URL mode:
     python ingest.py --url "https://v.douyin.com/xxx/"
  2. Task-file compatibility mode:
     python ingest.py --task ~/.obsidian-librarian/inbox/{id}.json

Flow:
  1. 加载 config（失败 -> status 报错退出）
  2. 创建 StatusWriter
  3. download（vendor + cookie 注入）
  4. analyze（Ark Files + Responses）
  5. 写 SCHEMA Markdown + 更新 index.md + git commit
  6. task-file 模式归档到 archive/ 或 failed/
  7. 终态 status.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# 把 scripts/ 加入 path 才能 import 同目录的模块
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from analyzer import (  # noqa: E402
    AnalyzerError, FileNotActiveError, FileTooLargeError, FFprobeError,
    ResponseTimeoutError,
    analyze_images, analyze_images_many, analyze_video, analyze_video_many,
)
from config_loader import Config, ConfigError, load_config  # noqa: E402
from cost_estimator import estimate_cost_rmb  # noqa: E402
from downloader import (  # noqa: E402
    CookieInvalidError, DouyinError, DouyinRateLimitedError,
    NetworkError, VideoMeta, VideoNotFoundError, download_images,
    download_video, fetch_metadata,
)
from status_writer import StatusWriter, write_terminal  # noqa: E402

_PROJECT_ROOT = _SCRIPTS_DIR.parents[2]

DEFAULT_INGEST_INTENT = "knowledge_ingest"
ALL_INGEST_INTENTS = ("knowledge_ingest", "viral_breakdown")
INGEST_INTENT_PROFILES = {
    "knowledge_ingest": {
        "asset_family": "knowledge_asset",
        "relative_root": "知识资产/知识入库",
        "section": "知识入库",
        "id_kind": "knowledge",
        "tags": ("douyin", "knowledge-asset", "case-study"),
    },
    "viral_breakdown": {
        "asset_family": "creative_pattern",
        "relative_root": "知识资产/创作模式",
        "section": "创作模式",
        "id_kind": "creative",
        "tags": ("douyin", "creative-pattern", "case-study"),
    },
}


def normalize_ingest_intent(value: Any) -> str:
    """Return the supported asset-purpose intent, defaulting only for empty input."""
    intent = str(value or "").strip()
    if not intent:
        return DEFAULT_INGEST_INTENT
    if intent not in INGEST_INTENT_PROFILES:
        raise ValueError(
            f"未知 ingest_intent: {intent}；只支持 "
            f"{', '.join(INGEST_INTENT_PROFILES)}"
        )
    return intent


def normalize_ingest_intents(value: Any) -> tuple[str, ...]:
    """Normalize one or many asset-purpose intents.

    Accepts a list/tuple, a comma-separated string, or aliases like "both".
    Empty input keeps the default single knowledge ingest path.
    """
    if value is None or value == "":
        raw_items: list[Any] = [DEFAULT_INGEST_INTENT]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value).strip()
        if text in {"both", "all", "knowledge_and_viral"}:
            raw_items = list(ALL_INGEST_INTENTS)
        elif "," in text:
            raw_items = [item.strip() for item in text.split(",")]
        else:
            raw_items = [text]

    normalized: list[str] = []
    for item in raw_items:
        intent = normalize_ingest_intent(item)
        if intent not in normalized:
            normalized.append(intent)
    return tuple(normalized or [DEFAULT_INGEST_INTENT])


def _intent_profile(ingest_intent: str) -> dict[str, Any]:
    return INGEST_INTENT_PROFILES[normalize_ingest_intent(ingest_intent)]


def _source_media(meta: VideoMeta) -> str:
    return "douyin_image_post" if getattr(meta, "media_type", "") == "image_post" else "douyin_video"


def _source_tag(source_media: str) -> str:
    return "image-analysis" if source_media == "douyin_image_post" else "video-analysis"


def _tags_for_asset(ingest_intent: str, source_media: str) -> tuple[str, ...]:
    tags = list(_intent_profile(ingest_intent)["tags"])
    tag = _source_tag(source_media)
    if tag not in tags:
        tags.append(tag)
    return tuple(tags)


def _format_tags(tags: tuple[str, ...]) -> str:
    return "[" + ", ".join(tags) + "]"


def _prompt_for(source_media: str, ingest_intent: str) -> str:
    suffix = "image_post" if source_media == "douyin_image_post" else "video"
    return f"{suffix}_{normalize_ingest_intent(ingest_intent)}.md"


# ─────────────────────────────────────────────────────────────────
# 任务文件
# ─────────────────────────────────────────────────────────────────


def _make_task_id() -> str:
    """生成 yyyymmdd-HHMMSS-{rand4} 形式的任务 id。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _load_task(task_file: Path) -> dict[str, Any]:
    """加载 inbox JSON。"""
    if not task_file.exists():
        raise FileNotFoundError(f"任务文件不存在: {task_file}")
    return json.loads(task_file.read_text(encoding="utf-8"))


def _archive_task(task_file: Path, base_dir: Path, ok: bool) -> Path:
    """成功 → archive/，失败 → failed/"""
    sub = "archive" if ok else "failed"
    dest_dir = base_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / task_file.name
    # 同名时加后缀
    n = 0
    while dest.exists():
        n += 1
        dest = dest_dir / f"{task_file.stem}.{n}{task_file.suffix}"
    shutil.move(str(task_file), str(dest))
    return dest


# ─────────────────────────────────────────────────────────────────
# Vault 写入
# ─────────────────────────────────────────────────────────────────


_FM_TPL = """---
id: "{asset_id}"
type: video_analysis
asset_family: {asset_family}
source_media: {source_media}
ingest_intent: {ingest_intent}
title: "{title_escaped}"
source_url: "{url}"
ingested: {date_iso}
updated: {date_iso}
tags: {tags}
summary: "{summary_escaped}"
confidence: medium
weight: 100
status: active
related: []
platform: douyin
author: "{author_escaped}"
duration: "{duration_sec_fmt}"
aweme_id: "{aweme_id}"
video_path: "{video_path}"
analyzed_at: "{analyzed_at}"
file_id: "{file_id}"
fps_used: {fps_used}
quality: "{quality}"
model: "{model}"
target_frames: {target_frames}
actual_frames_estimate: {actual_frames_estimate}
truncated: {truncated}
input_tokens: {input_tokens}
output_tokens: {output_tokens}
total_tokens: {total_tokens}
cost_rmb_estimate: {cost_rmb_estimate}
---

# {title}

## 基本信息
- 平台：douyin
- 作者：{author}
- 时长：{duration_sec_fmt}
- 原始链接：{url}
- 收录时间：{analyzed_at}
- 本地视频：![[{video_path}]]

## 一句话总结
{summary}

## 资产化拆解
- 资产用途：{asset_family}
- 来源形态：{source_media}
- 入库意图：{ingest_intent}

## 拆解正文
{body}

## 分析元数据
- 模型：{model}
- 质量档：{quality}
- fps：{fps_used}
- 估算帧数：{actual_frames_estimate}
- 成本估算：{cost_rmb_estimate} RMB

## 不确定/待验证
- 模型输出未人工复核，标记为 medium confidence。
"""


_IMAGE_FM_TPL = """---
id: "{asset_id}"
type: image_post_analysis
asset_family: {asset_family}
source_media: {source_media}
ingest_intent: {ingest_intent}
title: "{title_escaped}"
source_url: "{url}"
ingested: {date_iso}
updated: {date_iso}
tags: {tags}
summary: "{summary_escaped}"
confidence: medium
weight: 100
status: active
related: []
platform: douyin
author: "{author_escaped}"
image_count: {image_count}
aweme_id: "{aweme_id}"
analyzed_at: "{analyzed_at}"
file_id: "{file_id}"
quality: "{quality}"
model: "{model}"
truncated: {truncated}
input_tokens: {input_tokens}
output_tokens: {output_tokens}
total_tokens: {total_tokens}
cost_rmb_estimate: {cost_rmb_estimate}
---

# {title}

## 基本信息
- 平台：douyin
- 作者：{author}
- 图片数量：{image_count}
- 原始链接：{url}
- 收录时间：{analyzed_at}

## 原始图片
{image_embeds}

## 一句话总结
{summary}

## 资产化拆解
- 资产用途：{asset_family}
- 来源形态：{source_media}
- 入库意图：{ingest_intent}

## 拆解正文
{body}

## 分析元数据
- 模型：{model}
- 质量档：{quality}
- 图片输入：{image_count}
- 成本估算：{cost_rmb_estimate} RMB

## 不确定/待验证
- 模型输出未人工复核，标记为 medium confidence。
"""


def _yaml_escape(text: str) -> str:
    return text.replace('"', '\\"').replace("\n", " ").strip()


def _asset_title(title: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _slug_for_vault(title: str, aweme_id: str, max_len: int = 50) -> str:
    t = re.sub(r"[\x00-\x1f\x7f]", "", title).lower()
    t = re.sub(r"[\\/\|:*?\"<>#]", "-", t)
    t = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", t, flags=re.UNICODE)
    t = re.sub(r"[_\s]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-. ")
    if len(t) > max_len:
        t = t[:max_len].strip("-")
    return f"{t or 'untitled'}-{aweme_id[-6:]}"


def _format_duration(sec: float) -> str:
    sec = int(sec)
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


def _schema_asset_id(vault_path: Path, date: str, kind: str = "video") -> str:
    """Return the next SCHEMA id in {YYYYMMDD}-{kind}-{NNN} format."""
    pattern = re.compile(rf"^id:\s*[\"']?{re.escape(date)}-{re.escape(kind)}-(\d{{3}})[\"']?\s*$", re.MULTILINE)
    max_seq = 0
    asset_root = vault_path / "知识资产"
    if asset_root.exists():
        for md in asset_root.glob("**/*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = pattern.search(text)
            if match:
                max_seq = max(max_seq, int(match.group(1)))
    return f"{date}-{kind}-{max_seq + 1:03d}"


def _vault_relative_dir(config: Config) -> Path:
    rel = str(config.vault_relative_root or "知识资产/知识入库").strip().strip("/")
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("[vault].relative_root 必须是 vault 内的相对路径")
    return rel_path


def _purpose_relative_dir(ingest_intent: str) -> Path:
    rel = str(_intent_profile(ingest_intent)["relative_root"]).strip().strip("/")
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("ingest_intent 对应的写入目录必须是 vault 内相对路径")
    return rel_path


def _summary_from_text(text: str, title: str) -> str:
    """Generate a short SCHEMA-compatible summary without calling another model."""
    heading_re = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*")
    for raw in text.splitlines():
        line = raw.strip().strip("-*# >")
        if not line:
            continue
        if heading_re.match(line) or line.startswith("|") or set(line) <= {"-", "|", " "}:
            continue
        if re.search(r"[（(]≤?\s*\d+\s*字[）)]", line):
            continue
        line = re.sub(r"\s+", " ", line)
        if len(line) > 80:
            return line[:77] + "..."
        return line
    return (title[:77] + "...") if len(title) > 80 else title


def _combine_costs(costs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not costs:
        return {}
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_rmb_estimate": 0.0,
        "by_intent": costs,
    }
    model = ""
    note = ""
    for cost in costs.values():
        total["input_tokens"] += int(cost.get("input_tokens", 0) or 0)
        total["output_tokens"] += int(cost.get("output_tokens", 0) or 0)
        total["total_tokens"] += int(cost.get("total_tokens", 0) or 0)
        total["cost_rmb_estimate"] += float(cost.get("cost_rmb_estimate", 0) or 0)
        model = model or str(cost.get("model", "") or "")
        note = note or str(cost.get("note", "") or "")
    total["cost_rmb_estimate"] = round(total["cost_rmb_estimate"], 4)
    if model:
        total["model"] = model
    if note:
        total["note"] = note
    return total


def _ensure_vault_structure(vault_path: Path) -> list[Path]:
    """Create the minimal SCHEMA.md directory structure required for writes."""
    touched: list[Path] = []
    for rel in [
        "templates",
        "raw/videos",
        "raw/images",
        "raw/web",
        "raw/github",
        "知识资产/知识入库",
        "知识资产/创作模式",
        "知识资产/GitHub项目",
        "知识资产/网页剪藏",
        "知识资产/代码模块",
        "系统记录/维护报告",
        "系统记录/变更日志",
        "系统记录/回收站",
    ]:
        (vault_path / rel).mkdir(parents=True, exist_ok=True)

    index = vault_path / "index.md"
    if not index.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        index.write_text(
            f"# 知识库索引\n> 最后更新：{today} | 资产总数：0\n\n## 知识入库\n\n## 创作模式\n",
            encoding="utf-8",
        )
        touched.append(index)

    gitignore = vault_path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            ".obsidian/workspace.json\n"
            ".obsidian/workspace-mobile.json\n"
            ".trash/\n"
            ".DS_Store\n",
            encoding="utf-8",
        )
        touched.append(gitignore)

    for name in ["SCHEMA.md"]:
        src = _PROJECT_ROOT / name
        dest = vault_path / name
        if src.exists() and not dest.exists():
            shutil.copy2(src, dest)
            touched.append(dest)

    for folder in ["templates", "rules"]:
        src_dir = _PROJECT_ROOT / folder
        dest_dir = vault_path / folder
        if src_dir.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            for src in src_dir.glob("*.md"):
                dest = dest_dir / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    touched.append(dest)
    return touched


def _update_index(
    vault_path: Path,
    md_path: Path,
    title: str,
    summary: str,
    *,
    section: str = "知识入库",
    tags: tuple[str, ...] = ("douyin", "knowledge-asset", "case-study"),
) -> None:
    index = vault_path / "index.md"
    today = datetime.now().strftime("%Y-%m-%d")
    if index.exists():
        text = index.read_text(encoding="utf-8")
    else:
        text = "# 知识库索引\n\n## 知识入库\n\n## 创作模式\n"

    rel_stem = md_path.stem
    tag_text = " ".join(f"`#{tag}`" for tag in tags)
    entry = (
        f"- [[{rel_stem}|{title}]] — {summary} "
        f"{tag_text}"
    )
    lines = [line for line in text.splitlines() if f"[[{rel_stem}|" not in line]
    if not lines or not lines[0].startswith("# 知识库索引"):
        lines.insert(0, "# 知识库索引")

    # Refresh or insert metadata line.
    asset_count = sum(1 for _ in (vault_path / "知识资产").glob("**/*.md"))
    meta = f"> 最后更新：{today} | 资产总数：{asset_count}"
    if len(lines) > 1 and lines[1].startswith("> 最后更新："):
        lines[1] = meta
    else:
        lines.insert(1, meta)

    try:
        section_idx = lines.index(f"## {section}")
    except ValueError:
        lines.extend(["", f"## {section}"])
        section_idx = len(lines) - 1

    insert_at = section_idx + 1
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    lines.insert(insert_at, entry)
    index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _git_commit(
    vault_path: Path,
    title: str,
    paths: list[Path],
    *,
    asset_type: str = "video_analysis",
) -> str:
    """Commit only the files touched by this ingest. Raises on failure."""
    if not (vault_path / ".git").exists():
        init = subprocess.run(
            ["git", "init"],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if init.returncode != 0:
            raise RuntimeError("git init failed")

    for key, value in [
        ("user.name", "Obsidian Librarian"),
        ("user.email", "obsidian-librarian@local"),
    ]:
        current = subprocess.run(
            ["git", "config", "--local", "--get", key],
            cwd=vault_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if current.returncode != 0 or not current.stdout.strip():
            set_config = subprocess.run(
                ["git", "config", "--local", key, value],
                cwd=vault_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if set_config.returncode != 0:
                raise RuntimeError(f"git config {key} failed")

    rel_paths: list[str] = []
    for path in paths:
        try:
            rel = path.resolve().relative_to(vault_path.resolve())
        except ValueError:
            continue
        rel_paths.append(str(rel))
    if not rel_paths:
        raise RuntimeError("no vault files to commit")

    add = subprocess.run(
        ["git", "add", "--", *rel_paths],
        cwd=vault_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if add.returncode != 0:
        raise RuntimeError("git add failed")

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=vault_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if diff.returncode == 0:
        return "no changes to commit"

    safe_title = re.sub(r"\s+", " ", title).strip()[:60] or "douyin video"
    commit = subprocess.run(
        ["git", "commit", "-m", f"ingest({asset_type}): {safe_title}"],
        cwd=vault_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if commit.returncode != 0:
        raise RuntimeError("git commit failed")
    return "committed"


def write_to_vault(
    config: Config,
    meta: VideoMeta,
    video_path: Path,
    result,
    cost: dict[str, Any],
    ingest_intent: str = DEFAULT_INGEST_INTENT,
) -> tuple[Path, str]:
    """把拆解结果写到 vault。返回 Markdown 路径和 git 状态。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)
    source_media = "douyin_video"
    tags = _tags_for_asset(ingest_intent, source_media)
    touched = _ensure_vault_structure(config.vault_path)

    # 视频文件搬进 vault（如果不在 vault 内）
    raw_dir = config.vault_path / "raw" / "videos"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if config.vault_path not in video_path.parents:
        target_video = raw_dir / video_path.name
        if not target_video.exists():
            shutil.copy2(video_path, target_video)
            touched.append(target_video)
        vault_video_path = target_video
    else:
        vault_video_path = video_path

    # 计算 video_path 相对 vault 的引用
    rel_video = vault_video_path.relative_to(config.vault_path)

    # Markdown 输出位置
    md_dir = config.vault_path / _purpose_relative_dir(ingest_intent)
    md_dir.mkdir(parents=True, exist_ok=True)
    date = time.strftime("%Y%m%d")
    date_iso = datetime.now().strftime("%Y-%m-%d")
    slug = _slug_for_vault(meta.title, meta.aweme_id)
    md_path = md_dir / f"{date}-{slug}.md"
    asset_id = _schema_asset_id(config.vault_path, date, profile["id_kind"])
    asset_title = _asset_title(meta.title)
    summary = _summary_from_text(result.text, asset_title)

    content = _FM_TPL.format(
        asset_id=asset_id,
        asset_family=profile["asset_family"],
        source_media=source_media,
        ingest_intent=ingest_intent,
        aweme_id=meta.aweme_id,
        url=meta.source_url,
        title=asset_title,
        title_escaped=_yaml_escape(asset_title),
        tags=_format_tags(tags),
        summary=summary,
        summary_escaped=_yaml_escape(summary),
        author=meta.author or "[未知]",
        author_escaped=_yaml_escape(meta.author or ""),
        author_sec_uid=meta.author_sec_uid,
        date_iso=date_iso,
        duration_sec=round(meta.duration_sec, 2),
        duration_sec_fmt=_format_duration(meta.duration_sec),
        cover_url=meta.cover_url,
        video_path=str(rel_video),
        analyzed_at=datetime.now().isoformat(timespec="seconds"),
        file_id=result.file_id,
        fps_used=result.fps_used,
        quality=result.quality,
        model=result.model,
        target_frames=result.target_frames,
        actual_frames_estimate=result.actual_frames_estimate,
        truncated="true" if result.truncated else "false",
        input_tokens=cost.get("input_tokens", 0),
        output_tokens=cost.get("output_tokens", 0),
        total_tokens=cost.get("total_tokens", 0),
        cost_rmb_estimate=cost.get("cost_rmb_estimate", 0),
        body=result.text,
    )

    md_path.write_text(content, encoding="utf-8")
    touched.append(md_path)
    _update_index(
        config.vault_path,
        md_path,
        asset_title,
        summary,
        section=profile["section"],
        tags=tags,
    )
    touched.append(config.vault_path / "index.md")
    git_status = _git_commit(
        config.vault_path,
        asset_title,
        touched,
        asset_type=profile["asset_family"],
    )
    return md_path, git_status


def write_image_post_to_vault(
    config: Config,
    meta: VideoMeta,
    image_paths: list[Path],
    result,
    cost: dict[str, Any],
    ingest_intent: str = DEFAULT_INGEST_INTENT,
) -> tuple[Path, str]:
    """把抖音图文拆解结果写到 vault。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)
    source_media = "douyin_image_post"
    tags = _tags_for_asset(ingest_intent, source_media)
    touched = _ensure_vault_structure(config.vault_path)

    date = time.strftime("%Y%m%d")
    date_iso = datetime.now().strftime("%Y-%m-%d")
    slug = _slug_for_vault(meta.title, meta.aweme_id)
    raw_dir = config.vault_path / "raw" / "images" / f"{date}-{slug}"
    raw_dir.mkdir(parents=True, exist_ok=True)

    vault_images: list[Path] = []
    for index, image_path in enumerate(image_paths, start=1):
        image_path = Path(image_path)
        suffix = image_path.suffix or ".jpg"
        target = raw_dir / f"{index:02d}{suffix}"
        if not target.exists():
            shutil.copy2(image_path, target)
            touched.append(target)
        vault_images.append(target)

    rel_images = [path.relative_to(config.vault_path) for path in vault_images]
    image_embeds = "\n".join(f"- ![[{rel}]]" for rel in rel_images) or "- [无图片]"

    md_dir = config.vault_path / _purpose_relative_dir(ingest_intent)
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"{date}-{slug}.md"
    asset_id = _schema_asset_id(config.vault_path, date, profile["id_kind"])
    asset_title = _asset_title(meta.title)
    summary = _summary_from_text(result.text, asset_title)
    analyzed_at = datetime.now().isoformat(timespec="seconds")

    content = _IMAGE_FM_TPL.format(
        asset_id=asset_id,
        asset_family=profile["asset_family"],
        source_media=source_media,
        ingest_intent=ingest_intent,
        aweme_id=meta.aweme_id,
        url=meta.source_url,
        title=asset_title,
        title_escaped=_yaml_escape(asset_title),
        tags=_format_tags(tags),
        summary=summary,
        summary_escaped=_yaml_escape(summary),
        author=meta.author or "[未知]",
        author_escaped=_yaml_escape(meta.author or ""),
        date_iso=date_iso,
        analyzed_at=analyzed_at,
        image_count=len(vault_images),
        image_embeds=image_embeds,
        file_id=result.file_id,
        quality=result.quality,
        model=result.model,
        truncated="true" if result.truncated else "false",
        input_tokens=cost.get("input_tokens", 0),
        output_tokens=cost.get("output_tokens", 0),
        total_tokens=cost.get("total_tokens", 0),
        cost_rmb_estimate=cost.get("cost_rmb_estimate", 0),
        body=result.text,
    )

    md_path.write_text(content, encoding="utf-8")
    touched.append(md_path)
    _update_index(
        config.vault_path,
        md_path,
        asset_title,
        summary,
        section=profile["section"],
        tags=tags,
    )
    touched.append(config.vault_path / "index.md")
    git_status = _git_commit(
        config.vault_path,
        asset_title,
        touched,
        asset_type=profile["asset_family"],
    )
    return md_path, git_status


# ─────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────


async def run_task(
    *,
    task_id: str,
    url: str,
    quality: str,
    ingest_intent: str | None = None,
    ingest_intents: tuple[str, ...] | list[str] | None = None,
    config: Config,
    sw: StatusWriter,
    cache_dir: Path,
) -> dict[str, Any]:
    """执行一个任务，返回最终 state 摘要。"""
    intents = normalize_ingest_intents(ingest_intents or ingest_intent)
    primary_intent = intents[0]
    profile = _intent_profile(primary_intent)

    # ── 阶段 1：取 metadata 并按内容形态下载 ──
    sw.update(
        stage="downloading",
        url=url,
        ingest_intent=primary_intent,
        ingest_intents=list(intents),
        asset_family=profile["asset_family"],
    )
    try:
        meta = await fetch_metadata(url, config.cookie_path)

        if getattr(meta, "media_type", "") == "image_post":
            sw.update(
                stage="downloading_images",
                meta={
                    "aweme_id": meta.aweme_id,
                    "title": meta.title,
                    "author": meta.author,
                    "image_count": len(meta.image_urls),
                    "media_type": meta.media_type,
                },
            )

            async def img_progress(got: int, total: int, index: int = 1, count: int = 1) -> None:
                if total:
                    sw.progress("download_images", {
                        "image_index": index,
                        "image_count": count,
                        "got_mb": round(got / 1024 / 1024, 2),
                        "total_mb": round(total / 1024 / 1024, 2),
                        "pct": round(got / total * 100, 1),
                    })

            image_paths = await download_images(
                meta,
                cache_dir.parent / "images",
                progress_cb=img_progress,
            )
            sw.update(
                stage="downloaded_images",
                meta={
                    "aweme_id": meta.aweme_id,
                    "title": meta.title,
                    "author": meta.author,
                    "image_count": len(image_paths),
                    "media_type": meta.media_type,
                },
                image_paths=[str(path) for path in image_paths],
                image_count=len(image_paths),
            )
            return await run_image_post_task(
                task_id=task_id,
                config=config,
                sw=sw,
                meta=meta,
                image_paths=image_paths,
                quality=quality,
                ingest_intents=intents,
            )

        async def dl_progress(got: int, total: int) -> None:
            if total:
                sw.progress("download", {
                    "got_mb": round(got / 1024 / 1024, 2),
                    "total_mb": round(total / 1024 / 1024, 2),
                    "pct": round(got / total * 100, 1),
                })
        video_path = await download_video(
            meta,
            cache_dir,
            progress_cb=dl_progress,
        )
    except VideoNotFoundError as e:
        raise IngestError("video_not_found", str(e), recoverable=False) from e
    except CookieInvalidError as e:
        raise IngestError("cookie_invalid", str(e),
                          hint="抖音 cookie 失效，请用 Chrome 扩展重新抓取") from e
    except DouyinRateLimitedError as e:
        raise IngestError("rate_limited", str(e),
                          hint="抖音风控限流，稍后重试") from e
    except NetworkError as e:
        raise IngestError("network_error", str(e)) from e
    except DouyinError as e:
        raise IngestError("douyin_error", str(e)) from e

    sw.update(
        stage="downloaded",
        meta={
            "aweme_id": meta.aweme_id,
            "title": meta.title,
            "author": meta.author,
            "duration_sec": meta.duration_sec,
        },
        video_path=str(video_path),
        video_size_mb=round(video_path.stat().st_size / 1024 / 1024, 2),
    )

    # ── 阶段 2：拆解 ──
    prompts = {
        intent: (_SCRIPTS_DIR / "prompts" / _prompt_for("douyin_video", intent)).read_text(encoding="utf-8")
        for intent in intents
    }

    async def an_progress(stage: str, info: dict) -> None:
        sw.progress(stage, info)

    try:
        results = await analyze_video_many(
            video_path,
            prompts,
            api_key=config.ark_api_key,
            endpoint=config.ark_endpoint,
            model=config.analyzer_model,
            file_api_key=config.files_api_key,
            file_endpoint=config.files_endpoint,
            quality=quality,
            quality_params={
                "fps_min": config.fps_min,
                "fps_max": config.fps_max,
                "target_frames": (
                    config.quality_target_frames if quality == "quality"
                    else config.balanced_target_frames
                ),
            },
            source_id=meta.aweme_id,
            file_active_timeout_sec=config.file_active_timeout_sec,
            response_timeout_sec=config.response_timeout_sec,
            on_progress=an_progress,
        )
    except FileTooLargeError as e:
        raise IngestError("file_too_large", str(e),
                          hint="v0.1 不支持超大视频压缩") from e
    except FileNotActiveError as e:
        raise IngestError("file_active_timeout", str(e),
                          hint="火山预处理超时，可重试") from e
    except FFprobeError as e:
        raise IngestError("ffprobe_error", str(e),
                          hint="请安装 ffmpeg: brew install ffmpeg") from e
    except ResponseTimeoutError as e:
        raise IngestError("response_timeout", str(e),
                          hint="模型分析超时，可稍后重试或拆更短的视频") from e
    except AnalyzerError as e:
        raise IngestError("analyzer_error", str(e)) from e

    primary_result = results[primary_intent]
    primary_chunked = bool(getattr(primary_result, "chunked", False))
    primary_chunk_count = int(getattr(primary_result, "chunk_count", 1) or 1)
    sw.update(
        stage="analyzed",
        file_id=primary_result.file_id,
        fps_used=primary_result.fps_used,
        chunked=primary_chunked,
        chunk_count=primary_chunk_count,
        ingest_intents=list(intents),
    )

    # ── 阶段 3：成本估算 ──
    costs = {
        intent: estimate_cost_rmb(result.model, result.usage)
        for intent, result in results.items()
    }
    total_cost = _combine_costs(costs)
    sw.update(cost_estimate=total_cost)

    # ── 阶段 4：写 vault ──
    sw.update(stage="writing_vault")
    assets: list[dict[str, Any]] = []
    try:
        for intent in intents:
            md_path, git_status = write_to_vault(
                config, meta, video_path, results[intent], costs[intent], intent
            )
            assets.append({
                "ingest_intent": intent,
                "asset_family": _intent_profile(intent)["asset_family"],
                "vault_path": str(md_path),
                "git_status": git_status,
            })
    except Exception as e:
        raise IngestError("vault_write_error", str(e)) from e
    primary_asset = assets[0]

    return {
        "vault_path": primary_asset["vault_path"],
        "git_status": primary_asset["git_status"],
        "assets": assets,
        "video_path": str(video_path),
        "ingest_intent": primary_intent,
        "ingest_intents": list(intents),
        "asset_family": profile["asset_family"],
        "source_media": "douyin_video",
        "meta": {
            "aweme_id": meta.aweme_id,
            "title": meta.title,
            "author": meta.author,
            "duration_sec": meta.duration_sec,
        },
        "analysis": {
            "file_id": primary_result.file_id,
            "fps_used": primary_result.fps_used,
            "quality": primary_result.quality,
            "model": primary_result.model,
            "target_frames": primary_result.target_frames,
            "actual_frames_estimate": primary_result.actual_frames_estimate,
            "truncated": primary_result.truncated,
            "chunked": primary_chunked,
            "chunk_count": primary_chunk_count,
        },
        "cost": total_cost,
    }


async def run_image_post_task(
    *,
    task_id: str,
    config: Config,
    sw: StatusWriter,
    meta: VideoMeta,
    image_paths: list[Path],
    quality: str,
    ingest_intents: tuple[str, ...] | list[str] | str,
) -> dict[str, Any]:
    """执行抖音图文拆解分支。"""
    intents = normalize_ingest_intents(ingest_intents)
    primary_intent = intents[0]
    profile = _intent_profile(primary_intent)
    prompts = {
        intent: (_SCRIPTS_DIR / "prompts" / _prompt_for("douyin_image_post", intent)).read_text(encoding="utf-8")
        for intent in intents
    }

    async def an_progress(stage: str, info: dict) -> None:
        sw.progress(stage, info)

    try:
        results = await analyze_images_many(
            image_paths,
            prompts,
            api_key=config.ark_api_key,
            endpoint=config.ark_endpoint,
            model=config.analyzer_model,
            quality=quality,
            response_timeout_sec=config.response_timeout_sec,
            on_progress=an_progress,
        )
    except FileTooLargeError as e:
        raise IngestError("file_too_large", str(e),
                          hint="图文图片体积过大，后续需要接入 TOS 或压缩") from e
    except ResponseTimeoutError as e:
        raise IngestError("response_timeout", str(e),
                          hint="模型分析超时，可稍后重试") from e
    except AnalyzerError as e:
        raise IngestError("analyzer_error", str(e)) from e

    primary_result = results[primary_intent]
    sw.update(
        stage="analyzed",
        file_id=primary_result.file_id,
        image_count=primary_result.image_count,
        media_type="image_post",
        ingest_intents=list(intents),
    )

    costs = {
        intent: estimate_cost_rmb(result.model, result.usage)
        for intent, result in results.items()
    }
    total_cost = _combine_costs(costs)
    sw.update(cost_estimate=total_cost)

    sw.update(stage="writing_vault")
    assets: list[dict[str, Any]] = []
    try:
        for intent in intents:
            md_path, git_status = write_image_post_to_vault(
                config, meta, image_paths, results[intent], costs[intent], intent
            )
            assets.append({
                "ingest_intent": intent,
                "asset_family": _intent_profile(intent)["asset_family"],
                "vault_path": str(md_path),
                "git_status": git_status,
            })
    except Exception as e:
        raise IngestError("vault_write_error", str(e)) from e
    primary_asset = assets[0]

    return {
        "vault_path": primary_asset["vault_path"],
        "git_status": primary_asset["git_status"],
        "assets": assets,
        "image_paths": [str(path) for path in image_paths],
        "ingest_intent": primary_intent,
        "ingest_intents": list(intents),
        "asset_family": profile["asset_family"],
        "source_media": "douyin_image_post",
        "meta": {
            "aweme_id": meta.aweme_id,
            "title": meta.title,
            "author": meta.author,
            "image_count": len(image_paths),
            "media_type": "image_post",
        },
        "analysis": {
            "file_id": primary_result.file_id,
            "quality": primary_result.quality,
            "model": primary_result.model,
            "image_count": primary_result.image_count,
            "truncated": primary_result.truncated,
        },
        "cost": total_cost,
    }


class IngestError(Exception):
    """ingest 阶段的统一异常，带分类标签和可选 hint。"""
    def __init__(self, kind: str, msg: str, *, hint: str | None = None,
                 recoverable: bool = False):
        super().__init__(msg)
        self.kind = kind
        self.hint = hint
        self.recoverable = recoverable


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Douyin video ingest for obsidian-librarian"
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--task", type=Path,
                   help="inbox task JSON path (compatibility mode)")
    g.add_argument("--url", help="Douyin URL or share text (Agent/P0 mode)")
    p.add_argument("--quality", default=None,
                   choices=["balanced", "quality"],
                   help=argparse.SUPPRESS)
    p.add_argument("--intent", default=DEFAULT_INGEST_INTENT,
                   choices=sorted([*INGEST_INTENT_PROFILES.keys(), "both"]),
                   help="入库意图：knowledge_ingest、viral_breakdown 或 both")
    p.add_argument("--intents", default=None,
                   help="多个入库意图，逗号分隔；可用 both 同时产出知识入库和爆款拆解")
    p.add_argument("--config", default=None, type=Path,
                   help="自定义 config.toml 路径")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 默认 bridge 根（供 ConfigError 兜底写 status 用）
    default_bridge = Path.home() / ".obsidian-librarian"
    default_status = default_bridge / "status"

    # ── 0. 先读 task 文件（如果是 --task 模式），拿到真 task_id ──
    task_file: Path | None = None
    task_data: dict[str, Any] | None = None
    if args.task:
        task_file = Path(args.task).expanduser().resolve()
        try:
            task_data = _load_task(task_file)
        except Exception as e:
            print(f"✗ 任务文件加载失败: {e}", file=sys.stderr)
            return 2
        task_id = task_data.get("id") or task_file.stem
    else:
        task_id = _make_task_id()

    # ── 1. 加载 config（用真 task_id 写 status，便于 Agent 诊断）──
    try:
        config = load_config(args.config)
    except ConfigError as e:
        write_terminal(task_id, default_status, {
            "ok": False,
            "stage": "config_error",
            "error": str(e),
            "hint": "请检查 ~/.obsidian-librarian/config.toml",
        })
        # config 错时不归档任务（用户改完 config 还能重试）
        print(f"✗ ConfigError: {e}", file=sys.stderr)
        return 2

    base_dir = config.bridge_root
    status_dir = base_dir / "status"
    cache_dir = base_dir / "cache" / "videos"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. 确定 url / quality / ingest_intents ──
    if task_data is not None:
        url = task_data.get("url")
        quality = "quality"
        intent_raw = (
            task_data.get("ingest_intents")
            or task_data.get("ingestIntents")
            or task_data.get("intents")
            or task_data.get("intent")
            or task_data.get("ingestIntent")
            or task_data.get("ingest_intent")
            or args.intent
        )
        if not url:
            write_terminal(task_id, status_dir, {
                "ok": False, "stage": "task_invalid",
                "error": "任务 JSON 缺 url 字段",
            })
            assert task_file is not None
            _archive_task(task_file, base_dir, ok=False)
            print("✗ 任务 JSON 缺 url 字段", file=sys.stderr)
            return 2
    else:
        url = args.url
        quality = "quality"
        intent_raw = args.intents or args.intent

    try:
        ingest_intents = normalize_ingest_intents(intent_raw)
    except ValueError as e:
        write_terminal(task_id, status_dir, {
            "ok": False, "stage": "task_invalid",
            "error": str(e),
        })
        if task_file:
            _archive_task(task_file, base_dir, ok=False)
        print(f"✗ {e}", file=sys.stderr)
        return 2
    ingest_intent = ingest_intents[0]

    # ── 2. 跑 ──
    sw = StatusWriter(task_id, status_dir)
    task_meta = {}
    if task_data is not None:
        task_meta = {
            "source": task_data.get("source") or "agent",
            "page_title": task_data.get("page_title") or "",
            "page_url": task_data.get("page_url") or "",
            "aweme_id": task_data.get("aweme_id") or "",
            "detected_by": task_data.get("detected_by") or "",
            "created_at": task_data.get("created_at") or "",
        }
    sw.update(
        stage="started",
        quality=quality,
        source_url=url,
        ingest_intent=ingest_intent,
        ingest_intents=list(ingest_intents),
        asset_family=_intent_profile(ingest_intent)["asset_family"],
        **task_meta,
    )

    try:
        summary = asyncio.run(run_task(
            task_id=task_id, url=url, quality=quality,
            ingest_intents=ingest_intents,
            config=config, sw=sw, cache_dir=cache_dir,
        ))
    except IngestError as e:
        sw.update(
            stage="failed", ok=False,
            error=str(e), error_kind=e.kind,
            hint=e.hint, recoverable=e.recoverable,
        )
        if task_file:
            _archive_task(task_file, base_dir, ok=False)
        print(f"✗ [{e.kind}] {e}" + (f"\n  hint: {e.hint}" if e.hint else ""),
              file=sys.stderr)
        return 1
    except Exception as e:
        sw.update(
            stage="failed", ok=False,
            error=f"{type(e).__name__}: {e}",
            error_kind="unexpected",
            traceback=traceback.format_exc(),
        )
        if task_file:
            _archive_task(task_file, base_dir, ok=False)
        print(f"✗ Unexpected: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    sw.update(stage="done", ok=True, **summary)
    if task_file:
        _archive_task(task_file, base_dir, ok=True)

    print(f"✓ done: {summary['vault_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
