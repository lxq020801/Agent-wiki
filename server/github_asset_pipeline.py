"""GitHub source adapter for the current model-backed asset pipeline."""
from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


CANONICAL_INDEX_SECTION = "GitHub项目 / 网页剪藏 / 代码模块"
INDEX_SECTION_ALIASES = {
    CANONICAL_INDEX_SECTION,
    "GitHub项目",
    "网页剪藏",
    "代码模块",
}
README_SOURCE_LIMIT = 60_000
README_SECTION_PRIORITY = (
    "overview",
    "introduction",
    "about",
    "features",
    "why",
    "quick start",
    "quickstart",
    "getting started",
    "installation",
    "install",
    "usage",
    "example",
    "api",
    "architecture",
    "configuration",
    "security",
    "license",
    "概述",
    "简介",
    "功能",
    "特性",
    "安装",
    "使用",
    "示例",
    "架构",
    "配置",
)
README_NOISE_HEADINGS = re.compile(
    r"(?i)^(?:table of contents|contents|toc|navigation|sponsors?|sponsorship|funding|"
    r"donate|donation|support us|community|chat|contact|discord|slack|wechat|qq|"
    r"acknowledg(?:e)?ments?|目录|导航|赞助|捐赠|交流群|群聊|社区|联系我们)\b"
)
BADGE_URL_PATTERN = re.compile(
    r"(?i)(?:shields\.io|badge\.fury\.io|badgen\.net|github\.com/.+?/actions/workflows|"
    r"codecov\.io|coveralls\.io|img\.shields\.io)"
)
NAV_LINK_PATTERN = re.compile(r"(?:\[[^]]+\]\(#[^)]+\)\s*(?:[|·•/]\s*)?){2,}")


class GitHubAssetPipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AssetWriteResult:
    asset_path: Path
    asset_id: str
    changed: bool
    summary: str
    tags: tuple[str, ...]
    usage: dict[str, Any]


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "deps" / "douyin" / "scripts"


def _ingest_tools() -> dict[str, Any]:
    scripts = _scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from ingest import (  # type: ignore
        _asset_title,
        _content_tags,
        _schema_asset_id,
        _slug_for_vault,
        _source_sections_from_analysis,
        _update_index,
    )

    return {
        "asset_title": _asset_title,
        "content_tags": _content_tags,
        "schema_asset_id": _schema_asset_id,
        "slug_for_vault": _slug_for_vault,
        "source_sections": _source_sections_from_analysis,
        "update_index": _update_index,
    }


def _strip_badge_markup(text: str) -> str:
    cleaned = re.sub(r"(?is)<!--.*?-->", "", text)
    cleaned = re.sub(
        r"(?is)<a\b[^>]*>\s*<img\b[^>]*(?:badge|shield|codecov|coveralls)[^>]*>\s*</a>",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?is)<img\b[^>]*(?:badge|shield|codecov|coveralls)[^>]*>",
        "",
        cleaned,
    )
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if BADGE_URL_PATTERN.search(line) and len(re.sub(r"!\[[^]]*\]\([^)]+\)", "", line).strip()) < 24:
            continue
        if NAV_LINK_PATTERN.fullmatch(line):
            continue
        lines.append(raw.rstrip())
    return "\n".join(lines)


def _markdown_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
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


def clean_readme(value: Any, *, limit: int = README_SOURCE_LIMIT) -> str:
    """Remove presentation noise and select semantic sections within a char budget."""
    text = html.unescape(str(value or "")).replace("\x00", "")
    text = _strip_badge_markup(text)
    preamble, sections = _markdown_sections(text)
    useful = [
        (heading, body)
        for heading, body in sections
        if not README_NOISE_HEADINGS.search(re.sub(r"[*_`#]", "", heading).strip())
    ]
    blocks: list[str] = []
    if preamble:
        blocks.append(preamble)
    blocks.extend(f"## {heading}\n\n{body}".rstrip() for heading, body in useful if body)
    cleaned = "\n\n".join(blocks)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned

    chosen: list[tuple[int, int, str]] = []
    for index, (heading, body) in enumerate(useful):
        heading_lower = heading.lower()
        priority = next(
            (len(README_SECTION_PRIORITY) - rank for rank, key in enumerate(README_SECTION_PRIORITY) if key in heading_lower),
            0,
        )
        chosen.append((priority, -index, f"## {heading}\n\n{body}".rstrip()))
    selected: list[tuple[int, str]] = []
    remaining = limit
    if preamble:
        clipped = preamble[: min(len(preamble), max(2_000, limit // 5))].rstrip()
        selected.append((-1, clipped))
        remaining -= len(clipped)
    for _priority, negative_index, block in sorted(chosen, reverse=True):
        if remaining <= 1_000:
            break
        if len(block) <= remaining:
            selected.append((-negative_index, block))
            remaining -= len(block)
    if not any(index >= 0 for index, _block in selected) and useful:
        ranked = max(chosen, key=lambda item: (item[0], item[1]))
        index = -ranked[1]
        selected.append((index, ranked[2][: max(0, remaining)].rstrip()))
    return "\n\n".join(block for _index, block in sorted(selected, key=lambda item: item[0])).strip()[:limit]


def truncate_summary(value: Any, *, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    room = max(1, limit - 3)
    clipped = text[:room]
    if room < len(text) and clipped and clipped[-1].isascii() and clipped[-1].isalnum() and text[room].isascii() and text[room].isalnum():
        boundary = max(clipped.rfind(" "), clipped.rfind("/"), clipped.rfind("-"))
        if boundary > 0:
            clipped = clipped[:boundary]
    return clipped.rstrip(" ,.;:，。；：-/") + "..."


def _yaml_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def _frontmatter_list(text: str, key: str) -> list[str]:
    raw = _frontmatter_value(text, key)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _consolidate_index_sections(index_path: Path) -> None:
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("## "):
            current = []
            sections.append((line[3:].strip(), current))
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    target_indexes = [index for index, (heading, _body) in enumerate(sections) if heading in INDEX_SECTION_ALIASES]
    if not target_indexes:
        return
    merged: list[str] = []
    seen_entries: set[str] = set()
    for index in target_indexes:
        for line in sections[index][1]:
            match = re.search(r"\[\[([^|\]#]+)", line)
            key = f"link:{match.group(1).strip()}" if match else f"line:{line.strip()}"
            if line.strip() and key in seen_entries:
                continue
            if line.strip():
                seen_entries.add(key)
            merged.append(line)
    while merged and not merged[0].strip():
        merged.pop(0)
    while merged and not merged[-1].strip():
        merged.pop()
    first = target_indexes[0]
    rebuilt_sections: list[tuple[str, list[str]]] = []
    for index, section in enumerate(sections):
        if index == first:
            rebuilt_sections.append((CANONICAL_INDEX_SECTION, [""] + merged))
        elif index not in target_indexes:
            rebuilt_sections.append(section)
    output = list(preamble)
    for heading, body in rebuilt_sections:
        if output and output[-1].strip():
            output.append("")
        output.append(f"## {heading}")
        output.extend(body)
    index_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


class GitHubAssetPipeline:
    """Adapt GitHub material to the same model and writer primitives as other sources."""

    def __init__(
        self,
        *,
        config_path: Path | str,
        analyzer: Callable[[dict[str, Any], str, str], str | tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.analyzer = analyzer

    def _current_model_analysis(
        self,
        material: dict[str, Any],
        cleaned_readme: str,
        ingest_intent: str,
    ) -> tuple[str, dict[str, Any]]:
        scripts = _scripts_dir()
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from config_loader import load_config  # type: ignore
        from derive_executor import _call_lite_model, _sanitize_generated_body  # type: ignore

        repo = material["public"]
        source = {
            "repository_id": repo.get("id"),
            "full_name": repo.get("fullName"),
            "url": repo.get("url"),
            "description": repo.get("description"),
            "language": repo.get("language"),
            "license": repo.get("license"),
            "default_branch": repo.get("defaultBranch"),
            "archived": repo.get("archived"),
            "stars": repo.get("stars"),
            "forks": repo.get("forks"),
            "open_issues": repo.get("openIssues"),
            "latest_release": material.get("version"),
            "pushed_at": repo.get("pushedAt"),
            "readme": cleaned_readme,
        }
        prompt = (
            "你是 Agent-wiki 的统一来源整理器。请只依据下面的 GitHub 官方 API 字段与清理后的 README，"
            "生成中文 Markdown 正文，不输出 frontmatter 或一级标题。必须严格包含且只使用三个二级章节："
            "## 简洁概括、## 完整内容整理、## AI 分析。\n"
            "简洁概括要准确说明项目是什么；完整内容整理应覆盖来源实际表达的功能、适用问题、安装/使用、"
            "关键接口或架构、限制与状态，但不要逐行复制 README，也不要为了固定栏目填充来源没有的信息。"
            "AI 分析必须明确是基于当前来源的推断，使用限定语说明价值、适用条件和风险。来源事实与 AI 推断"
            "不得混写。去掉徽章、重复导航、赞助和群聊噪声；不杜撰测试结果、兼容性、安全性或采用建议。\n\n"
            f"入库入口：{ingest_intent}\n"
            "GitHub 来源材料：\n"
            + json.dumps(source, ensure_ascii=False, indent=2)
        )
        config = load_config(self.config_path)
        text, usage = _call_lite_model(config, prompt)
        return _sanitize_generated_body(text), usage

    def _analyze(
        self,
        material: dict[str, Any],
        cleaned_readme: str,
        ingest_intent: str,
    ) -> tuple[str, dict[str, Any]]:
        if self.analyzer is None:
            return self._current_model_analysis(material, cleaned_readme, ingest_intent)
        result = self.analyzer(material, cleaned_readme, ingest_intent)
        if isinstance(result, tuple):
            text, usage = result
            return str(text or ""), dict(usage or {})
        return str(result or ""), {}

    def write(
        self,
        material: dict[str, Any],
        *,
        vault_path: Path,
        ingest_intent: str,
        derived_from: list[str] | None = None,
        asset_path: Path | None = None,
        existing_text: str = "",
    ) -> AssetWriteResult:
        repo = material.get("public") if isinstance(material.get("public"), dict) else {}
        if not int(repo.get("id") or 0) or not str(repo.get("fullName") or ""):
            raise GitHubAssetPipelineError("repository_invalid", "GitHub 来源缺少官方仓库身份。")
        cleaned_readme = clean_readme(material.get("readme"))
        try:
            model_text, usage = self._analyze(material, cleaned_readme, ingest_intent)
        except GitHubAssetPipelineError:
            raise
        except Exception as exc:
            raise GitHubAssetPipelineError(
                "asset_analysis_failed",
                f"GitHub 来源分析失败：{type(exc).__name__}",
            ) from exc
        headings = {
            re.sub(r"\s+", "", match.group(1)).lower()
            for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", model_text)
        }
        required = {"简洁概括", "完整内容整理", "ai分析"}
        if not required.issubset(headings):
            raise GitHubAssetPipelineError(
                "asset_sections_invalid",
                "模型输出缺少简洁概括、完整内容整理或 AI 分析。",
            )
        tools = _ingest_tools()
        sections = tools["source_sections"](model_text, str(repo.get("fullName") or ""))
        title = tools["asset_title"](str(repo.get("fullName") or "GitHub 项目"))
        summary = truncate_summary(sections["concise"] or title)
        content_for_tags = "\n".join([
            title,
            str(repo.get("description") or ""),
            sections["concise"],
            sections["complete"],
            sections["ai_analysis"],
        ])
        tags = list(tools["content_tags"](content_for_tags))
        for tag in ("github", "project"):
            if tag not in tags:
                tags.append(tag)
        if ingest_intent == "derived_ingest" and "derived-asset" not in tags:
            tags.append("derived-asset")

        date = datetime.now().strftime("%Y%m%d")
        date_iso = datetime.now().strftime("%Y-%m-%d")
        asset_id = _frontmatter_value(existing_text, "id") or tools["schema_asset_id"](vault_path, date, "github")
        ingested = _frontmatter_value(existing_text, "ingested") or date_iso
        related = _frontmatter_list(existing_text, "related")
        lineage = list(derived_from or _frontmatter_list(existing_text, "derived_from"))
        if asset_path is None:
            slug = tools["slug_for_vault"](
                str(repo.get("fullName") or title),
                str(repo.get("id") or "github"),
                52,
            )
            asset_path = vault_path / "知识资产" / "GitHub项目" / f"{date}-{slug}-{int(repo['id'])}.md"
        try:
            asset_path.resolve().relative_to(vault_path.resolve())
        except ValueError as exc:
            raise GitHubAssetPipelineError("asset_path_invalid", "GitHub 资产路径超出知识库。") from exc
        if any(part.casefold() == ".obsidian" for part in asset_path.parts):
            raise GitHubAssetPipelineError("asset_path_invalid", "GitHub 资产路径不能位于 .obsidian。")

        ai_analysis = sections["ai_analysis"].strip()
        if not re.match(r"^>\s*以下内容由 AI 生成", ai_analysis):
            ai_analysis = "> 以下内容由 AI 生成，仅依据当前 GitHub 来源。\n\n" + ai_analysis
        status = "archived" if repo.get("archived") else "active"
        weight = 0 if repo.get("archived") else 100
        content = f'''---
id: "{_yaml_escape(asset_id)}"
type: github_project
asset_family: github_project
source_media: github
ingest_intent: {ingest_intent}
title: "{_yaml_escape(title)}"
source_url: "{_yaml_escape(repo.get('url'))}"
source_id: "{int(repo.get('id') or 0)}"
repo: "{_yaml_escape(repo.get('url'))}"
repository_id: {int(repo.get('id') or 0)}
repository_full_name: "{_yaml_escape(repo.get('fullName'))}"
github_managed: true
default_branch: "{_yaml_escape(repo.get('defaultBranch'))}"
latest_version: "{_yaml_escape(material.get('version'))}"
pushed_at: "{_yaml_escape(repo.get('pushedAt'))}"
language: "{_yaml_escape(repo.get('language'))}"
stars: {int(repo.get('stars') or 0)}
forks: {int(repo.get('forks') or 0)}
open_issues: {int(repo.get('openIssues') or 0)}
license: "{_yaml_escape(repo.get('license'))}"
description: "{_yaml_escape(repo.get('description'))}"
ingested: {ingested}
updated: {date_iso}
tags: {json.dumps(tags, ensure_ascii=False)}
summary: "{_yaml_escape(summary)}"
confidence: medium
weight: {weight}
status: {status}
derived_from: {json.dumps(lineage, ensure_ascii=False)}
related: {json.dumps(related, ensure_ascii=False)}
---

# {title}

## 简洁概括

{sections['concise'].strip()}

## 完整内容整理

{sections['complete'].strip()}

## AI 分析

{ai_analysis}
'''
        previous = existing_text or (asset_path.read_text(encoding="utf-8") if asset_path.exists() else "")
        changed = previous != content
        if changed:
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = asset_path.with_suffix(asset_path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(asset_path)
        tools["update_index"](
            vault_path,
            asset_path,
            title,
            summary,
            section=CANONICAL_INDEX_SECTION,
            tags=tuple(tags),
        )
        _consolidate_index_sections(vault_path / "index.md")
        return AssetWriteResult(
            asset_path=asset_path,
            asset_id=asset_id,
            changed=changed,
            summary=summary,
            tags=tuple(tags),
            usage=usage,
        )
