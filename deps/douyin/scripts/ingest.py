"""
ingest.py - Douyin video ingest entrypoint

P0 main path:
  Agent calls:
     python scripts/ingest_url.py "<douyin-url>"

Supported lower-level modes:
  1. URL mode:
     python ingest.py --url "https://v.douyin.com/xxx/"
  2. Task-file compatibility mode:
     python ingest.py --task ~/.agent-wiki/inbox/{id}.json

Flow:
  1. 加载 config（失败 -> status 报错退出）
  2. 创建 StatusWriter
  3. download（vendor + cookie 注入）
  4. analyze（Ark Files + Responses）
  5. 写 SCHEMA Markdown + 更新 index.md
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
    analyze_images, analyze_video,
)
from config_loader import Config, ConfigError, load_config  # noqa: E402
from cost_estimator import estimate_cost_rmb  # noqa: E402
from derive_strategy import (  # noqa: E402
    derive_tasks_from_analysis, public_derived_tasks,
)
from downloader import (  # noqa: E402
    CookieInvalidError, DouyinError, DouyinRateLimitedError,
    NetworkError, VideoMeta, VideoNotFoundError, download_images,
    download_video, fetch_metadata,
)
from status_writer import StatusWriter, write_terminal  # noqa: E402

DEFAULT_INGEST_INTENT = "knowledge_ingest"
INGEST_PROFILE = {
    "asset_family": "knowledge_asset",
    "relative_root": "知识资产/知识入库",
    "section": "知识入库",
    "id_kind": "knowledge",
    "tags": ("knowledge-asset",),
}

CONTENT_TAG_RULES = (
    ("ai-agent", r"\b(?:ai[- ]?agent|agentic|agent)\b|智能体"),
    ("knowledge-management", r"知识库|知识管理|知识资产|obsidian|笔记"),
    ("prompt-engineering", r"\bprompt\b|提示词"),
    ("code-generation", r"代码生成|编程|coding|codex|claude code"),
    ("browser-automation", r"浏览器自动化|browser automation|playwright|selenium"),
    ("web-scraping", r"爬虫|抓取|web scraping|crawler"),
    ("api-design", r"\bapi\b|接口设计|endpoint"),
    ("rag", r"\brag\b|检索增强"),
    ("mcp", r"\bmcp\b|model context protocol"),
    ("llm", r"\bllm\b|大语言模型|大模型"),
    ("tool-use", r"工具调用|tool use|function calling"),
)


def normalize_ingest_intent(value: Any) -> str:
    """Return the only supported source-ingest intent."""
    intent = str(value or "").strip()
    if not intent:
        return DEFAULT_INGEST_INTENT
    if intent != DEFAULT_INGEST_INTENT:
        raise ValueError(f"不支持的 ingest_intent: {intent}；只支持知识入库")
    return intent


def _intent_profile(ingest_intent: str) -> dict[str, Any]:
    normalize_ingest_intent(ingest_intent)
    return INGEST_PROFILE


def _source_media(meta: VideoMeta) -> str:
    return "douyin_image_post" if getattr(meta, "media_type", "") == "image_post" else "douyin_video"


def _source_tag(source_media: str) -> str:
    return "image-analysis" if source_media == "douyin_image_post" else "video-analysis"


def _content_tags(text: Any) -> list[str]:
    source = str(text or "")
    return [tag for tag, pattern in CONTENT_TAG_RULES if re.search(pattern, source, re.I)]


def _tags_for_asset(
    ingest_intent: str,
    source_media: str,
    content: Any = "",
) -> tuple[str, ...]:
    tags = _content_tags(content)
    for tag in _intent_profile(ingest_intent)["tags"]:
        if tag not in tags:
            tags.append(tag)
    media_tag = _source_tag(source_media)
    if media_tag not in tags:
        tags.append(media_tag)
    if "douyin" not in tags:
        tags.append("douyin")
    return tuple(tags)


def _format_tags(tags: tuple[str, ...]) -> str:
    return "[" + ", ".join(tags) + "]"


def _visible_derived_items(decision: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not decision or not isinstance(decision.get("items"), list):
        return []
    return [
        item for item in decision["items"]
        if isinstance(item, dict) and item.get("decision") != "reject"
    ]


def _format_derived_tasks_section(
    decision: dict[str, Any] | None,
) -> str:
    if not decision or not decision.get("items"):
        return "- 当前没有待执行或已完成的派生。"
    items = _visible_derived_items(decision)
    if not items:
        return "- 暂无达到候选阈值的派生任务。"
    lines = [
        "> 这里只展示结构化策略的真实状态；正式父子关系只在子资产成功生成后建立。",
        "",
        "| 决策 | 类型 | 名称 | 分数 | 状态 | 原因 |",
        "|---|---|---|---:|---|---|",
    ]
    for item in items:
        target = item.get("target_url") or item.get("canonical_target") or ""
        name = str(item.get("name") or "未命名派生线索")
        if target and str(target).startswith("http"):
            display_name = f"[{name}]({target})"
        else:
            display_name = name
        reason = re.sub(r"\s+", " ", str(item.get("reason") or "")).strip()
        if len(reason) > 90:
            reason = reason[:87] + "..."
        lines.append(
            "| {decision} | {target_type} | {name} | {score} | {status} | {reason} |".format(
                decision=item.get("decision", "candidate"),
                target_type=item.get("target_type", ""),
                name=display_name.replace("|", "\\|"),
                score=int(item.get("score", 0) or 0),
                status=item.get("execution_status", "candidate"),
                reason=reason.replace("|", "\\|"),
            )
        )
    rejected = sum(1 for item in decision.get("items", []) if item.get("decision") == "reject")
    suppressed = int((decision.get("counts") or {}).get("suppressed", 0) or 0)
    if rejected or suppressed:
        lines.extend([
            "",
            f"- 已过滤低分、重复或非主要对象线索：{rejected + suppressed} 个。完整记录见运行审计。",
        ])
    return "\n".join(lines)


def _markdown_h2_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Parse level-two Markdown sections without rewriting arbitrary text."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in str(text or "").splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = []
            sections.append((match.group(1).strip(), current))
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    return "\n".join(preamble).strip(), [
        (heading, "\n".join(body).strip()) for heading, body in sections
    ]


def _plain_heading(heading: str) -> str:
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", str(heading or ""))
    text = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]\s*", "", text)
    return re.sub(r"\s+", "", text).lower()


def _first_content_line(text: str) -> str:
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("-*# >")
        if line and not line.startswith("|") and not line.startswith("```"):
            return line
    return ""


def _source_sections_from_analysis(text: str, fallback_title: str = "") -> dict[str, str]:
    """Map model Markdown into the three durable source-note sections."""
    preamble, parsed = _markdown_h2_sections(text)
    concise: list[str] = []
    complete: list[str] = []
    ai_analysis: list[str] = []
    for heading, body in parsed:
        plain = _plain_heading(heading)
        if "派生决策" in plain:
            continue
        if plain in {"简洁概括", "一句话资产摘要", "一句话总结", "摘要"}:
            if body:
                concise.append(body)
        elif plain == "完整内容整理":
            if body:
                complete.append(body)
        elif plain in {"ai分析", "人工智能分析"}:
            if body:
                ai_analysis.append(body)
        elif any(marker in plain for marker in ("风险与待验证", "反幻觉自检", "可沉淀资产建议")):
            if body:
                ai_analysis.append(f"### {heading}\n\n{body}")
        elif body:
            complete.append(f"### {heading}\n\n{body}")
    if preamble:
        complete.insert(0, preamble)
    concise_text = "\n\n".join(concise).strip()
    complete_text = "\n\n".join(complete).strip()
    ai_text = "\n\n".join(ai_analysis).strip()
    if not concise_text:
        concise_text = _first_content_line(complete_text) or fallback_title
    if not complete_text:
        complete_text = "来源未提供可进一步整理的正文。"
    if not ai_text:
        ai_text = "当前来源没有提供足够证据支持额外推断。"
    return {
        "concise": concise_text,
        "complete": complete_text,
        "ai_analysis": ai_text,
    }


def _replace_h2_section(text: str, headings: set[str], new_heading: str, body: str) -> str:
    lines = str(text or "").splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if not match:
            continue
        plain = _plain_heading(match.group(1))
        if start is None and plain in headings:
            start = index
        elif start is not None:
            end = index
            break
    replacement = [f"## {new_heading}", "", body.strip()]
    if start is None:
        return str(text or "").rstrip() + "\n\n" + "\n".join(replacement) + "\n"
    return "\n".join(lines[:start] + replacement + lines[end:]).rstrip() + "\n"


def mark_derived_candidate_executed(
    parent_path: Path | None,
    *,
    candidate_name: str,
    child_link: str,
) -> list[Path]:
    """Update the parent status only after the child and both links exist."""
    if parent_path is None or not parent_path.exists() or not child_link:
        return []
    parent_text = parent_path.read_text(encoding="utf-8")
    _preamble, sections = _markdown_h2_sections(parent_text)
    status_body = ""
    ai_body = ""
    status_span: tuple[int, int] | None = None
    for heading, body in sections:
        plain = _plain_heading(heading)
        if plain in {"派生状态", "派生任务候选", "派生候选"}:
            status_body = body
            break
        if plain == "ai分析":
            ai_body = body
            match = re.search(r"(?m)^###\s+派生状态(?:（系统）)?\s*$", body)
            if match:
                next_h3 = re.search(r"(?m)^###\s+", body[match.end():])
                end = match.end() + next_h3.start() if next_h3 else len(body)
                status_span = (match.start(), end)
                status_body = body[match.end():end].strip()
    lines = [
        line for line in status_body.splitlines()
        if "当前没有待执行或已完成的派生" not in line
    ]
    matched = False
    for index, line in enumerate(lines):
        if not line.strip().startswith("|") or candidate_name not in line:
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if len(columns) < 6:
            continue
        columns[0] = "completed"
        columns[2] = child_link.replace("|", "\\|")
        columns[4] = "completed"
        lines[index] = "| " + " | ".join(columns) + " |"
        matched = True
        break
    if not matched:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"- 已完成：{child_link}")
    status_body = "\n".join(lines).strip()
    if status_span is not None:
        start, end = status_span
        new_ai_body = (
            ai_body[:start].rstrip()
            + "\n\n### 派生状态（系统）\n\n"
            + status_body
            + ("\n\n" + ai_body[end:].lstrip() if ai_body[end:].strip() else "")
        )
        updated_parent = _replace_h2_section(
            parent_text,
            {"ai分析"},
            "AI 分析",
            new_ai_body,
        )
    elif ai_body:
        updated_parent = _replace_h2_section(
            parent_text,
            {"ai分析"},
            "AI 分析",
            ai_body.rstrip() + "\n\n### 派生状态（系统）\n\n" + status_body,
        )
    else:
        updated_parent = _replace_h2_section(
            parent_text,
            {"派生状态", "派生任务候选", "派生候选"},
            "派生状态",
            status_body,
        )
    if updated_parent != parent_text:
        parent_path.write_text(updated_parent, encoding="utf-8")
        return [parent_path]
    return []


def _derived_audit_artifacts(decision: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(decision, dict):
        return {}
    artifacts = decision.get("audit_artifacts")
    return artifacts if isinstance(artifacts, dict) else {}


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
source_id: "{source_id}"
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
---

# {title}

> 来源：douyin · {author} · {duration_sec_fmt} · [原始链接]({url})
>
{source_title_quote}

## 简洁概括

{concise}

## 完整内容整理

### 原始媒体

![[{video_path}]]

{complete}

## AI 分析

> 以下内容由 AI 仅依据当前来源生成，不代表外部事实核验。

{ai_analysis}

### 派生状态（系统）

{derived_tasks_section}
"""


_IMAGE_FM_TPL = """---
id: "{asset_id}"
type: image_post_analysis
asset_family: {asset_family}
source_media: {source_media}
ingest_intent: {ingest_intent}
title: "{title_escaped}"
source_url: "{url}"
source_id: "{source_id}"
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
---

# {title}

> 来源：douyin · {author} · {image_count} 张图片 · [原始链接]({url})
>
{source_title_quote}

## 简洁概括

{concise}

## 完整内容整理

### 原始媒体

{image_embeds}

{complete}

## AI 分析

> 以下内容由 AI 仅依据当前来源生成，不代表外部事实核验。

{ai_analysis}

### 派生状态（系统）

{derived_tasks_section}
"""


def _yaml_escape(text: str) -> str:
    return text.replace('"', '\\"').replace("\n", " ").strip()


def _asset_title(title: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _source_asset_title(title: str, max_len: int = 42) -> str:
    raw = str(title or "")
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    cleaned = re.sub(r"https?://\S+", "", first_line)
    cleaned = re.sub(r"(?:\s*#[^#\s]+)+\s*$", "", cleaned).strip(" -—:：|，,")
    if len(cleaned) > max_len:
        sentence = re.split(r"(?<=[。！？!?])", cleaned, maxsplit=1)[0].strip()
        if 8 <= len(sentence) <= max_len:
            cleaned = sentence
    return _asset_title(cleaned or first_line or "未命名来源", max_len=max_len)


def _source_title_quote(title: str) -> str:
    lines = [line.strip() for line in str(title or "").splitlines() if line.strip()]
    if not lines:
        return "> 原始标题/文案：未提供"
    return "\n".join(
        [f"> 原始标题/文案：{lines[0]}"] + [f"> {line}" for line in lines[1:]]
    )


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


def _frontmatter_scalar_values(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _existing_source_asset(vault_path: Path, source_id: str, source_url: str) -> Path | None:
    root = vault_path / "知识资产" / "知识入库"
    if not root.exists():
        return None
    for path in root.glob("*.md"):
        try:
            values = _frontmatter_scalar_values(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        stored_id = values.get("source_id") or values.get("aweme_id")
        if source_id and stored_id == source_id:
            return path
        if source_url and values.get("source_url") == source_url:
            return path
    return None


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


def _ensure_vault_structure(vault_path: Path) -> None:
    """Create write targets without copying project rules into the user vault."""
    (vault_path / "知识资产" / "知识入库").mkdir(parents=True, exist_ok=True)

    index = vault_path / "index.md"
    if not index.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        index.write_text(
            f"# 知识库索引\n> 最后更新：{today} | 资产总数：0\n\n## 知识入库\n",
            encoding="utf-8",
        )


def _indexed_asset_count(vault_path: Path, lines: list[str]) -> int:
    asset_root = vault_path / "知识资产"
    existing: dict[str, Path] = {}
    if asset_root.exists():
        for path in asset_root.glob("**/*.md"):
            try:
                values = _frontmatter_scalar_values(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if values.get("id") and values.get("status", "active") != "archived":
                existing[path.stem] = path
    indexed: set[str] = set()
    for line in lines:
        for target in re.findall(r"\[\[([^|\]#]+)", line):
            stem = Path(target.strip()).stem
            if stem in existing:
                indexed.add(stem)
    return len(indexed)


def _update_index(
    vault_path: Path,
    md_path: Path,
    title: str,
    summary: str,
    *,
    section: str = "知识入库",
    tags: tuple[str, ...] = ("knowledge-asset",),
) -> None:
    index = vault_path / "index.md"
    today = datetime.now().strftime("%Y-%m-%d")
    if index.exists():
        text = index.read_text(encoding="utf-8")
    else:
        text = "# 知识库索引\n\n## 知识入库\n"

    rel_stem = md_path.stem
    tag_text = " ".join(f"`#{tag}`" for tag in tags)
    entry = (
        f"- [[{rel_stem}|{title}]] — {summary} "
        f"{tag_text}"
    )
    lines = [line for line in text.splitlines() if f"[[{rel_stem}|" not in line]
    if not lines or not lines[0].startswith("# 知识库索引"):
        lines.insert(0, "# 知识库索引")

    try:
        section_idx = lines.index(f"## {section}")
    except ValueError:
        lines.extend(["", f"## {section}"])
        section_idx = len(lines) - 1

    insert_at = section_idx + 1
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    lines.insert(insert_at, entry)

    asset_count = _indexed_asset_count(vault_path, lines)
    meta = f"> 最后更新：{today} | 资产总数：{asset_count}"
    if len(lines) > 1 and lines[1].startswith("> 最后更新："):
        lines[1] = meta
    else:
        lines.insert(1, meta)
    index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_to_vault(
    config: Config,
    meta: VideoMeta,
    video_path: Path,
    result,
    cost: dict[str, Any],
    ingest_intent: str = DEFAULT_INGEST_INTENT,
    derived_decision: dict[str, Any] | None = None,
    task_id: str = "",
) -> tuple[Path, str]:
    """把拆解结果写到 vault；Git 由用户或外部备份工具管理。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)
    source_media = "douyin_video"
    existing = _existing_source_asset(config.vault_path, meta.aweme_id, meta.source_url)
    if existing is not None:
        return existing, "existing_source"
    sections = _source_sections_from_analysis(result.text, meta.title)
    tags = _tags_for_asset(ingest_intent, source_media, f"{meta.title}\n{result.text}")
    _ensure_vault_structure(config.vault_path)

    # 视频文件搬进 vault（如果不在 vault 内）
    raw_dir = config.vault_path / "raw" / "videos"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if config.vault_path not in video_path.parents:
        target_video = raw_dir / video_path.name
        if not target_video.exists():
            shutil.copy2(video_path, target_video)
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
    asset_title = _source_asset_title(meta.title)
    slug = _slug_for_vault(asset_title, meta.aweme_id)
    md_path = md_dir / f"{date}-{slug}.md"
    asset_id = _schema_asset_id(config.vault_path, date, profile["id_kind"])
    summary = _summary_from_text(sections["concise"], asset_title)
    if derived_decision and isinstance(derived_decision.get("items"), list):
        rel_parent = str(md_path.relative_to(config.vault_path))
        for item in derived_decision["items"]:
            if not isinstance(item, dict):
                continue
            item["parent_task_id"] = task_id
            item["parent_asset_id"] = asset_id
            item["parent_asset_path"] = rel_parent
            item["parent_source_url"] = meta.source_url
            item["parent_aweme_id"] = meta.aweme_id
    content = _FM_TPL.format(
        asset_id=asset_id,
        asset_family=profile["asset_family"],
        source_media=source_media,
        ingest_intent=ingest_intent,
        source_id=meta.aweme_id,
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
        duration_sec_fmt=_format_duration(meta.duration_sec),
        video_path=str(rel_video),
        source_title_quote=_source_title_quote(meta.title),
        concise=sections["concise"],
        complete=sections["complete"],
        ai_analysis=sections["ai_analysis"],
        derived_tasks_section=_format_derived_tasks_section(derived_decision),
    )

    md_path.write_text(content, encoding="utf-8")
    _update_index(
        config.vault_path,
        md_path,
        asset_title,
        summary,
        section=profile["section"],
        tags=tags,
    )
    return md_path, "not_managed"


def write_image_post_to_vault(
    config: Config,
    meta: VideoMeta,
    image_paths: list[Path],
    result,
    cost: dict[str, Any],
    ingest_intent: str = DEFAULT_INGEST_INTENT,
    derived_decision: dict[str, Any] | None = None,
    task_id: str = "",
) -> tuple[Path, str]:
    """把抖音图文拆解结果写到 vault。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)
    source_media = "douyin_image_post"
    existing = _existing_source_asset(config.vault_path, meta.aweme_id, meta.source_url)
    if existing is not None:
        return existing, "existing_source"
    sections = _source_sections_from_analysis(result.text, meta.title)
    tags = _tags_for_asset(ingest_intent, source_media, f"{meta.title}\n{result.text}")
    _ensure_vault_structure(config.vault_path)

    date = time.strftime("%Y%m%d")
    date_iso = datetime.now().strftime("%Y-%m-%d")
    asset_title = _source_asset_title(meta.title)
    slug = _slug_for_vault(asset_title, meta.aweme_id)
    raw_dir = config.vault_path / "raw" / "images" / f"{date}-{slug}"
    raw_dir.mkdir(parents=True, exist_ok=True)

    vault_images: list[Path] = []
    for index, image_path in enumerate(image_paths, start=1):
        image_path = Path(image_path)
        suffix = image_path.suffix or ".jpg"
        target = raw_dir / f"{index:02d}{suffix}"
        if not target.exists():
            shutil.copy2(image_path, target)
        vault_images.append(target)

    rel_images = [path.relative_to(config.vault_path) for path in vault_images]
    image_embeds = "\n".join(f"- ![[{rel}]]" for rel in rel_images) or "- [无图片]"

    md_dir = config.vault_path / _purpose_relative_dir(ingest_intent)
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"{date}-{slug}.md"
    asset_id = _schema_asset_id(config.vault_path, date, profile["id_kind"])
    summary = _summary_from_text(sections["concise"], asset_title)
    if derived_decision and isinstance(derived_decision.get("items"), list):
        rel_parent = str(md_path.relative_to(config.vault_path))
        for item in derived_decision["items"]:
            if not isinstance(item, dict):
                continue
            item["parent_task_id"] = task_id
            item["parent_asset_id"] = asset_id
            item["parent_asset_path"] = rel_parent
            item["parent_source_url"] = meta.source_url
            item["parent_aweme_id"] = meta.aweme_id
    content = _IMAGE_FM_TPL.format(
        asset_id=asset_id,
        asset_family=profile["asset_family"],
        source_media=source_media,
        ingest_intent=ingest_intent,
        source_id=meta.aweme_id,
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
        image_count=len(vault_images),
        image_embeds=image_embeds,
        source_title_quote=_source_title_quote(meta.title),
        concise=sections["concise"],
        complete=sections["complete"],
        ai_analysis=sections["ai_analysis"],
        derived_tasks_section=_format_derived_tasks_section(derived_decision),
    )

    md_path.write_text(content, encoding="utf-8")
    _update_index(
        config.vault_path,
        md_path,
        asset_title,
        summary,
        section=profile["section"],
        tags=tags,
    )
    return md_path, "not_managed"


# ─────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────


async def run_task(
    *,
    task_id: str,
    url: str,
    quality: str,
    ingest_intent: str | None = None,
    config: Config,
    sw: StatusWriter,
    cache_dir: Path,
) -> dict[str, Any]:
    """执行一个任务，返回最终 state 摘要。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)

    # ── 阶段 1：取 metadata 并按内容形态下载 ──
    sw.update(
        stage="downloading",
        url=url,
        ingest_intent=ingest_intent,
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
                ingest_intent=ingest_intent,
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
    prompt = (
        _SCRIPTS_DIR / "prompts" / _prompt_for("douyin_video", ingest_intent)
    ).read_text(encoding="utf-8")

    async def an_progress(stage: str, info: dict) -> None:
        sw.progress(stage, info)

    try:
        result = await analyze_video(
            video_path,
            prompt,
            api_key=config.ark_api_key,
            endpoint=config.ark_endpoint,
            model=config.analyzer_model,
            strategy_model=config.strategy_model,
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
            audit_id=task_id,
            analysis_key=ingest_intent,
            file_active_timeout_sec=config.file_active_timeout_sec,
            response_timeout_sec=config.response_timeout_sec,
            chunk_concurrency=config.chunk_concurrency,
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

    chunked = bool(getattr(result, "chunked", False))
    chunk_count = int(getattr(result, "chunk_count", 1) or 1)
    sw.update(
        stage="analyzed",
        file_id=result.file_id,
        fps_used=result.fps_used,
        chunked=chunked,
        chunk_count=chunk_count,
        audit_artifacts=getattr(result, "audit_artifacts", {}),
    )

    # ── 阶段 3：成本估算 ──
    cost = estimate_cost_rmb(result.model, result.usage)
    sw.update(cost_estimate=cost)

    # ── 阶段 4：派生候选决策（高置信候选后续由服务端自动入队） ──
    derived_decision = derive_tasks_from_analysis(
        result.text,
        source_id=meta.aweme_id,
        source_url=meta.source_url,
        source_media="douyin_video",
        ingest_intent=ingest_intent,
        vault_path=config.vault_path,
        task_id=task_id,
    )
    sw.update(
        stage="derived_candidates_ready",
        derived_tasks=public_derived_tasks(derived_decision),
        derived_summary=derived_decision.get("counts", {}),
        derived_audit_artifacts=_derived_audit_artifacts(derived_decision),
    )

    # ── 阶段 5：写 vault ──
    sw.update(stage="writing_vault")
    try:
        md_path, git_status = write_to_vault(
            config,
            meta,
            video_path,
            result,
            cost,
            ingest_intent,
            derived_decision,
            task_id,
        )
    except Exception as e:
        raise IngestError("vault_write_error", str(e)) from e
    asset = {
        "ingest_intent": ingest_intent,
        "asset_family": profile["asset_family"],
        "title": meta.title,
        "vault_path": str(md_path),
        "git_status": git_status,
        "derived_tasks": public_derived_tasks(derived_decision),
        "derived_summary": derived_decision.get("counts", {}),
        "derived_audit_artifacts": _derived_audit_artifacts(derived_decision),
        "audit_artifacts": getattr(result, "audit_artifacts", {}),
    }

    return {
        "vault_path": asset["vault_path"],
        "git_status": asset["git_status"],
        "assets": [asset],
        "video_path": str(video_path),
        "ingest_intent": ingest_intent,
        "asset_family": profile["asset_family"],
        "source_media": "douyin_video",
        "meta": {
            "aweme_id": meta.aweme_id,
            "title": meta.title,
            "author": meta.author,
            "duration_sec": meta.duration_sec,
        },
        "analysis": {
            "file_id": result.file_id,
            "fps_used": result.fps_used,
            "quality": result.quality,
            "model": result.model,
            "target_frames": result.target_frames,
            "actual_frames_estimate": result.actual_frames_estimate,
            "truncated": result.truncated,
            "chunked": chunked,
            "chunk_count": chunk_count,
            "chunks": getattr(result, "chunks", []),
            "audit_artifacts": getattr(result, "audit_artifacts", {}),
        },
        "cost": cost,
        "derived_tasks": public_derived_tasks(derived_decision),
        "derived_summary": derived_decision.get("counts", {}),
        "derived_audit_artifacts": _derived_audit_artifacts(derived_decision),
    }


async def run_image_post_task(
    *,
    task_id: str,
    config: Config,
    sw: StatusWriter,
    meta: VideoMeta,
    image_paths: list[Path],
    quality: str,
    ingest_intent: str = DEFAULT_INGEST_INTENT,
) -> dict[str, Any]:
    """执行抖音图文拆解分支。"""
    ingest_intent = normalize_ingest_intent(ingest_intent)
    profile = _intent_profile(ingest_intent)
    prompt = (
        _SCRIPTS_DIR / "prompts" / _prompt_for("douyin_image_post", ingest_intent)
    ).read_text(encoding="utf-8")

    async def an_progress(stage: str, info: dict) -> None:
        sw.progress(stage, info)

    try:
        result = await analyze_images(
            image_paths,
            prompt,
            api_key=config.ark_api_key,
            endpoint=config.ark_endpoint,
            model=config.analyzer_model,
            quality=quality,
            analysis_key=ingest_intent,
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

    sw.update(
        stage="analyzed",
        file_id=result.file_id,
        image_count=result.image_count,
        media_type="image_post",
    )

    cost = estimate_cost_rmb(result.model, result.usage)
    sw.update(cost_estimate=cost)

    derived_decision = derive_tasks_from_analysis(
        result.text,
        source_id=meta.aweme_id,
        source_url=meta.source_url,
        source_media="douyin_image_post",
        ingest_intent=ingest_intent,
        vault_path=config.vault_path,
        task_id=task_id,
    )
    sw.update(
        stage="derived_candidates_ready",
        derived_tasks=public_derived_tasks(derived_decision),
        derived_summary=derived_decision.get("counts", {}),
        derived_audit_artifacts=_derived_audit_artifacts(derived_decision),
    )

    sw.update(stage="writing_vault")
    try:
        md_path, git_status = write_image_post_to_vault(
            config,
            meta,
            image_paths,
            result,
            cost,
            ingest_intent,
            derived_decision,
            task_id,
        )
    except Exception as e:
        raise IngestError("vault_write_error", str(e)) from e
    asset = {
        "ingest_intent": ingest_intent,
        "asset_family": profile["asset_family"],
        "title": meta.title,
        "vault_path": str(md_path),
        "git_status": git_status,
        "derived_tasks": public_derived_tasks(derived_decision),
        "derived_summary": derived_decision.get("counts", {}),
        "derived_audit_artifacts": _derived_audit_artifacts(derived_decision),
    }

    return {
        "vault_path": asset["vault_path"],
        "git_status": asset["git_status"],
        "assets": [asset],
        "image_paths": [str(path) for path in image_paths],
        "ingest_intent": ingest_intent,
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
            "file_id": result.file_id,
            "quality": result.quality,
            "model": result.model,
            "image_count": result.image_count,
            "truncated": result.truncated,
        },
        "cost": cost,
        "derived_tasks": public_derived_tasks(derived_decision),
        "derived_summary": derived_decision.get("counts", {}),
        "derived_audit_artifacts": _derived_audit_artifacts(derived_decision),
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
        description="Douyin video ingest for Agent-wiki"
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--task", type=Path,
                   help="inbox task JSON path (compatibility mode)")
    g.add_argument("--url", help="Douyin URL or share text (Agent/P0 mode)")
    p.add_argument("--quality", default=None,
                   choices=["balanced", "quality"],
                   help=argparse.SUPPRESS)
    p.add_argument("--config", default=None, type=Path,
                   help="自定义 config.toml 路径")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 默认 bridge 根（供 ConfigError 兜底写 status 用）
    default_bridge = Path.home() / ".agent-wiki"
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
            "hint": "请检查 ~/.agent-wiki/config.toml",
        })
        # config 错时不归档任务（用户改完 config 还能重试）
        print(f"✗ ConfigError: {e}", file=sys.stderr)
        return 2

    base_dir = config.bridge_root
    status_dir = base_dir / "status"
    cache_dir = base_dir / "cache" / "videos"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. 确定 url / quality / ingest_intent ──
    if task_data is not None:
        url = task_data.get("url")
        quality = "quality"
        intent_raw = (
            task_data.get("ingest_intent")
            or task_data.get("ingestIntent")
            or DEFAULT_INGEST_INTENT
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
        intent_raw = DEFAULT_INGEST_INTENT

    try:
        ingest_intent = normalize_ingest_intent(intent_raw)
    except ValueError as e:
        write_terminal(task_id, status_dir, {
            "ok": False, "stage": "task_invalid",
            "error": str(e),
        })
        if task_file:
            _archive_task(task_file, base_dir, ok=False)
        print(f"✗ {e}", file=sys.stderr)
        return 2

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
        asset_family=_intent_profile(ingest_intent)["asset_family"],
        **task_meta,
    )

    try:
        summary = asyncio.run(run_task(
            task_id=task_id, url=url, quality=quality,
            ingest_intent=ingest_intent,
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
