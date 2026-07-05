"""
derive_strategy.py — turn model-discovered follow-up leads into bounded candidates.

The derivation layer is intentionally conservative: it scores and records
candidates, but does not enqueue child tasks by default. That keeps one video
from exploding into a noisy task tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any


ALLOWED_TARGET_TYPES = {"github_project", "official_doc", "web_research"}
MAX_RAW_CANDIDATES = 12
MAX_RETAINED_CANDIDATES = 8
CANDIDATE_THRESHOLD = 50

SCORE_WEIGHTS = {
    "knowledge_value": 1.4,
    "parent_dependency": 1.2,
    "evidence_strength": 1.2,
    "actionability": 1.0,
    "freshness_risk": 0.9,
    "novelty": 1.1,
    "asset_fit": 1.0,
    "cost_risk_inverse": 0.8,
    "ambiguity_inverse": 0.9,
}
MAX_WEIGHTED_SCORE = sum(weight * 5 for weight in SCORE_WEIGHTS.values())

SECRET_PATTERNS = [
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(api[_-]?key|ark[_-]?api[_-]?key|cookie|set-cookie)\s*[:=]\s*[^\s,}]+"),
    re.compile(r"\bresp[_-][A-Za-z0-9._-]+\b"),
]


def _runtime_root() -> Path:
    raw = os.environ.get("OBSIDIAN_LIBRARIAN_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".obsidian-librarian"


def _redact_text(text: Any) -> str:
    cleaned = str(text or "")
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned.strip()


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            canonical = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if canonical.endswith("apikey") or canonical in {"cookie", "setcookie", "authorization"}:
                continue
            clean[key] = _redact_value(child)
        return clean
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _write_derive_log(event: str, payload: dict[str, Any]) -> None:
    try:
        log_dir = _runtime_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = _redact_value({
            "event": event,
            "at": time.time(),
            **payload,
        })
        path = log_dir / "derive-strategy-events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _string_list(value: Any, *, limit: int = 6) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [str(value)]
    result: list[str] = []
    for item in items:
        text = _redact_text(item)
        if text and text not in result:
            result.append(text[:300])
        if len(result) >= limit:
            break
    return result


def _score_value(value: Any, default: int = 3) -> int:
    try:
        number = int(round(float(value)))
    except Exception:
        return default
    return max(0, min(5, number))


def _confidence_value(value: Any) -> float:
    if isinstance(value, str):
        mapping = {"high": 0.85, "medium": 0.65, "low": 0.35}
        return mapping.get(value.strip().lower(), 0.55)
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.55


def _normalize_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", _redact_text(value)).strip(" -:：")
    return text[:120]


def canonicalize_url(value: Any) -> str:
    url = _redact_text(value)
    if not url or not re.match(r"https?://", url, re.I):
        return ""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    if host == "github.com":
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) >= 2:
            return f"https://github.com/{parts[0]}/{parts[1]}"
    drop_prefixes = ("utm_",)
    drop_keys = {"spm", "from", "share_token", "share_id"}
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    clean_query = [
        (key, val)
        for key, val in query
        if not key.lower().startswith(drop_prefixes) and key.lower() not in drop_keys
    ]
    return urllib.parse.urlunparse((
        parsed.scheme.lower(),
        host,
        path.rstrip("/") or "/",
        "",
        urllib.parse.urlencode(clean_query, doseq=True),
        "",
    ))


def _target_type_from_action(action: str, name: str = "", item_type: str = "") -> str:
    text = f"{action} {name} {item_type}".lower()
    if "github" in text or "repo" in text or "仓库" in text:
        return "github_project"
    if "api" in text or "文档" in text or "官方" in text or "docs" in text:
        return "official_doc"
    return "web_research"


def _relation_type(target_type: str, subtype: str = "") -> str:
    if target_type == "github_project":
        return "implements"
    if subtype == "api_doc":
        return "documents"
    if target_type == "official_doc":
        return "verifies"
    return "expands"


def _canonical_target(target_type: str, name: str, url: str) -> str:
    canonical = canonicalize_url(url)
    if canonical:
        return canonical
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", name.lower()).strip("-")
    return f"name:{target_type}:{normalized}" if normalized else ""


def _candidate_id(dedupe_key: str) -> str:
    return "dt-" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:12]


def _dedupe_key(target_type: str, canonical_target: str) -> str:
    base = f"{target_type}|{canonical_target}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    blocks: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I):
        blocks.append(match.group(1))
    blocks.append(text)

    results: list[dict[str, Any]] = []
    for block in blocks:
        start = block.find("{")
        while start >= 0:
            depth = 0
            in_str = False
            escape = False
            for idx in range(start, len(block)):
                ch = block[idx]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw = block[start: idx + 1]
                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            break
                        if isinstance(parsed, dict):
                            results.append(parsed)
                        break
            start = block.find("{", start + 1)
    return results


def _derived_json_section(text: str) -> str:
    match = re.search(r"(?:^|\n)##\s*[九9][、.．]\s*派生决策 JSON\b", text, re.I)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"\n##\s+", text[start:])
    if next_heading:
        return text[start: start + next_heading.start()]
    return text[start:]


def _candidates_from_json(text: str) -> list[dict[str, Any]]:
    section = _derived_json_section(text)
    if not section:
        return []
    for obj in _extract_json_objects(section):
        raw = obj.get("candidates")
        if isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict) and not _is_placeholder_candidate(item)]
            if items:
                return items
            continue
        raw = obj.get("derived_candidates") or obj.get("derived_tasks")
        if isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict) and not _is_placeholder_candidate(item)]
            if items:
                return items
    return []


def _is_placeholder_candidate(raw: dict[str, Any]) -> bool:
    text = " ".join(str(raw.get(key, "")) for key in ("name", "title", "candidate_name", "reason"))
    return any(marker in text for marker in ("候选名称", "为什么这个派生", "它在视频中如何被使用", "它在图文中如何被使用"))


def _section_text(text: str, heading_pattern: str) -> str:
    match = re.search(heading_pattern, text, re.I)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"\n##\s+", text[start:])
    if next_heading:
        return text[start: start + next_heading.start()]
    return text[start:]


def _candidates_from_markdown(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    table = _section_text(text, r"\n##\s+[四五][、.．]\s*工具、项目、API、关键词")
    for raw in table.splitlines():
        line = raw.strip()
        if not line.startswith("|") or "派生" not in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 4 or parts[0] in {"名称", "---"}:
            continue
        name, item_type, context, action = parts[:4]
        target_type = _target_type_from_action(action, name, item_type)
        candidates.append({
            "name": name,
            "target_type": target_type,
            "subtype": "api_doc" if "api" in action.lower() else "",
            "mentioned_context": context,
            "reason": action,
            "evidence": [context],
            "scores": _heuristic_scores(name, target_type, action, item_type, context),
            "confidence": 0.55,
            "requires_confirmation": True,
            "source_action": action,
        })

    recommended = _section_text(text, r"\n##\s+七[、.．]\s*可沉淀资产建议")
    for raw in recommended.splitlines():
        if "派生" not in raw and not re.match(r"\s*\d+[.)、]\s*", raw):
            continue
        item = re.sub(r"^\s*[-*\d.)、]+\s*", "", raw).strip()
        if not item or item.startswith("推荐派生任务"):
            continue
        target_type = _target_type_from_action(item, item)
        candidates.append({
            "name": item[:80],
            "target_type": target_type,
            "mentioned_context": item,
            "reason": "推荐派生任务",
            "evidence": [item],
            "scores": _heuristic_scores(item, target_type, "推荐派生任务", "", item),
            "confidence": 0.45,
            "requires_confirmation": True,
            "source_action": "推荐派生任务",
        })
    return candidates


def _heuristic_scores(name: str, target_type: str, action: str, item_type: str, context: str) -> dict[str, int]:
    text = f"{name} {target_type} {action} {item_type} {context}".lower()
    has_url = bool(re.search(r"https?://", text))
    is_company_only = any(word in item_type for word in ("企业", "公司", "人名"))
    is_generic = any(word in name.lower() for word in ("ai", "agent", "大模型", "人工智能")) and len(name) < 12
    scores = {
        "knowledge_value": 4,
        "parent_dependency": 3,
        "evidence_strength": 4 if has_url else 3,
        "actionability": 4 if target_type in {"github_project", "official_doc"} else 3,
        "freshness_risk": 4 if any(word in text for word in ("api", "模型", "github", "官方", "版本")) else 3,
        "novelty": 4,
        "asset_fit": 4 if target_type in ALLOWED_TARGET_TYPES else 2,
        "cost_risk_inverse": 4,
        "ambiguity_inverse": 4 if has_url else 3,
    }
    if is_company_only:
        scores["knowledge_value"] = 2
        scores["asset_fit"] = 2
        scores["parent_dependency"] = 2
    if is_generic:
        scores["knowledge_value"] = 1
        scores["asset_fit"] = 1
        scores["ambiguity_inverse"] = 1
    return scores


def _normalize_scores(raw_scores: Any, fallback: dict[str, int]) -> dict[str, int]:
    raw = raw_scores if isinstance(raw_scores, dict) else {}
    return {
        key: _score_value(raw.get(key), fallback.get(key, 3))
        for key in SCORE_WEIGHTS
    }


def _normalized_score(scores: dict[str, int]) -> int:
    total = sum(scores[key] * weight for key, weight in SCORE_WEIGHTS.items())
    return int(round(total / MAX_WEIGHTED_SCORE * 100))


def _vault_contains_target(vault_path: Path, canonical_target: str) -> tuple[str, str]:
    if not canonical_target or canonical_target.startswith("name:") or not vault_path.exists():
        return "new", ""
    asset_root = vault_path / "知识资产"
    if not asset_root.exists():
        return "new", ""
    needle = canonical_target.lower().rstrip("/")
    for md in asset_root.glob("**/*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _frontmatter_block(text)
        if not fm:
            continue
        for value in _frontmatter_target_values(fm):
            if value.lower().rstrip("/") == needle:
                return "existing_related", str(md)
    return "new", ""


def _frontmatter_block(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    return text[4:end]


def _frontmatter_target_values(frontmatter: str) -> list[str]:
    keys = {
        "source_url",
        "repo",
        "repository",
        "official_url",
        "homepage",
        "docs_url",
    }
    values: list[str] = []
    for raw in frontmatter.splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        if key.strip() not in keys:
            continue
        cleaned = value.strip().strip("'\"")
        if key.strip() in {"repo", "repository"} and re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", cleaned):
            cleaned = f"https://github.com/{cleaned}"
        canonical = canonicalize_url(cleaned)
        if canonical and canonical not in values:
            values.append(canonical)
    return values


def _decision_from_score(
    score: int,
    scores: dict[str, int],
    downgrade_flags: list[str],
    existing_status: str,
) -> tuple[str, list[str]]:
    reject_reasons: list[str] = []
    if existing_status != "new":
        return "candidate", ["existing_asset"]
    if score < CANDIDATE_THRESHOLD:
        return "reject", ["score_below_candidate_threshold"]
    required = {
        "evidence_strength": 4,
        "asset_fit": 3,
        "ambiguity_inverse": 3,
        "cost_risk_inverse": 3,
        "novelty": 3,
    }
    for key, minimum in required.items():
        if scores.get(key, 0) < minimum:
            downgrade_flags.append(f"{key}_below_{minimum}")
    return "candidate", reject_reasons


def _task_kind(target_type: str, subtype: str = "") -> str:
    if target_type == "github_project":
        return "github_project_ingest"
    if target_type == "official_doc" or subtype == "api_doc":
        return "official_doc_ingest"
    return "web_research"


def _build_search_query(name: str, target_type: str, subtype: str, context: str) -> str:
    base = " ".join(part for part in [name, context[:120]] if part).strip()
    if target_type == "github_project":
        return f"{base} GitHub repository".strip()
    if target_type == "official_doc" or subtype == "api_doc":
        return f"{base} official documentation API".strip()
    return base[:180]


def _default_acceptance_criteria(target_type: str, subtype: str = "") -> list[str]:
    if target_type == "github_project":
        return [
            "确认 canonical GitHub 仓库 URL 与视频/图文线索一致",
            "提取 README、安装/运行方式、核心能力、维护状态和许可证",
            "写入 GitHub 项目资产，并反链到父资产证据",
        ]
    if target_type == "official_doc" or subtype == "api_doc":
        return [
            "确认来源为官方域名或官方发布渠道",
            "提取与父资产结论相关的接口、参数、限制、版本和风险",
            "写入网页剪藏/官方文档资产，并标明待验证点",
        ]
    return [
        "用至少两个可信来源核验父资产中的关键说法",
        "区分事实、观点和仍需确认的信息",
        "写入网页研究资产，并给出可复用结论",
    ]


def _normalize_candidate(
    raw: dict[str, Any],
    *,
    source_id: str,
    vault_path: Path,
) -> dict[str, Any]:
    name = _normalize_name(raw.get("name") or raw.get("title") or raw.get("candidate_name"))
    target_url = canonicalize_url(raw.get("target_url") or raw.get("url") or raw.get("candidate_url"))
    raw_type = str(raw.get("target_type") or raw.get("derived_kind") or raw.get("candidate_type") or "").strip()
    subtype = str(raw.get("subtype") or raw.get("derived_subtype") or "").strip()
    target_type = raw_type if raw_type in ALLOWED_TARGET_TYPES else _target_type_from_action(
        str(raw.get("suggested_action") or raw.get("source_action") or raw.get("reason") or ""),
        name,
        str(raw.get("type") or ""),
    )
    if target_type not in ALLOWED_TARGET_TYPES:
        target_type = "web_research"
    if "api" in subtype.lower() or "api" in str(raw.get("suggested_action") or "").lower():
        target_type = "official_doc"
        subtype = "api_doc"
    parent_context = _redact_text(raw.get("mentioned_context") or raw.get("context") or raw.get("reason"))[:500]
    fallback_scores = _heuristic_scores(
        name,
        target_type,
        str(raw.get("suggested_action") or raw.get("source_action") or raw.get("reason") or ""),
        str(raw.get("type") or raw.get("item_type") or ""),
        parent_context,
    )
    scores = _normalize_scores(raw.get("scores"), fallback_scores)
    score = _normalized_score(scores)
    canonical_target = _canonical_target(target_type, name, target_url)
    dedupe_key = _dedupe_key(target_type, canonical_target)
    dedupe_status, matched_asset = _vault_contains_target(vault_path, canonical_target)

    downgrade_flags = _string_list(raw.get("downgrade_flags"), limit=8)
    if not name and not target_url:
        downgrade_flags.append("missing_name_and_url")
    if not target_url and target_type in {"github_project", "official_doc"}:
        downgrade_flags.append("missing_explicit_url")
    if "[不确定]" in str(raw) or "[看不清]" in str(raw) or "[看不见]" in str(raw):
        downgrade_flags.append("uncertain_evidence")
    if bool(raw.get("requires_confirmation")):
        downgrade_flags.append("requires_confirmation")
    decision, reject_reasons = _decision_from_score(score, scores, downgrade_flags, dedupe_status)
    execution_status = "candidate"
    if decision == "reject":
        execution_status = "rejected"
    elif dedupe_status != "new":
        execution_status = "existing_related"
    elif not target_url and target_type in {"github_project", "official_doc"}:
        execution_status = "needs_target"
    task_kind = _task_kind(target_type, subtype)
    search_query = _redact_text(raw.get("search_query") or _build_search_query(
        name,
        target_type,
        subtype,
        parent_context,
    ))[:220]
    acceptance_criteria = _string_list(
        raw.get("acceptance_criteria") or _default_acceptance_criteria(target_type, subtype),
        limit=6,
    )

    return {
        "id": _candidate_id(dedupe_key),
        "name": name or target_url or "未命名派生线索",
        "target_type": target_type,
        "derived_kind": "api_doc" if subtype == "api_doc" else target_type,
        "task_kind": task_kind,
        "target_url": target_url,
        "search_query": search_query,
        "canonical_target": canonical_target,
        "dedupe_key": dedupe_key,
        "decision": decision,
        "execution_status": execution_status,
        "score": score,
        "confidence": round(_confidence_value(raw.get("confidence")), 2),
        "scores": scores,
        "reason": _redact_text(raw.get("reason") or parent_context or raw.get("suggested_action"))[:500],
        "parent_context": parent_context,
        "evidence": _string_list(raw.get("evidence") or raw.get("source_evidence"), limit=6),
        "acceptance_criteria": acceptance_criteria,
        "relation_type": _relation_type(target_type, subtype),
        "intended_asset_family": "github_project" if target_type == "github_project" else "knowledge_asset",
        "lineage_depth": 1,
        "allow_child_derivation": False,
        "dedupe": {
            "status": dedupe_status,
            "matched_asset": matched_asset,
        },
        "downgrade_flags": sorted(set(downgrade_flags)),
        "reject_reasons": reject_reasons,
        "next_action": "manual_review",
    }


def derive_tasks_from_analysis(
    analysis_text: str,
    *,
    source_id: str,
    source_url: str,
    source_media: str,
    ingest_intent: str,
    vault_path: Path,
    task_id: str = "",
) -> dict[str, Any]:
    """Return bounded derivation decisions from an analysis Markdown body."""
    if ingest_intent != "knowledge_ingest":
        return {
            "enabled": False,
            "reason": "derivation_only_runs_for_knowledge_ingest",
            "items": [],
            "counts": {"candidate": 0, "rejected": 0, "suppressed": 0},
        }

    raw_candidates = _candidates_from_json(analysis_text)
    source = "json"
    if not raw_candidates:
        raw_candidates = _candidates_from_markdown(analysis_text)
        source = "markdown_fallback"
    raw_candidates = raw_candidates[:MAX_RAW_CANDIDATES]

    by_key: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for raw in raw_candidates:
        item = _normalize_candidate(raw, source_id=source_id, vault_path=vault_path)
        current = by_key.get(item["dedupe_key"])
        if current is None or item["score"] > current["score"]:
            by_key[item["dedupe_key"]] = item
            if current is not None:
                duplicate_count += 1
        else:
            duplicate_count += 1

    ranked = sorted(
        by_key.values(),
        key=lambda item: (
            item["decision"] != "reject",
            item["score"],
            item["scores"].get("parent_dependency", 0),
            item["scores"].get("novelty", 0),
        ),
        reverse=True,
    )
    retained: list[dict[str, Any]] = []
    suppressed_count = 0
    for item in ranked:
        if len(retained) >= MAX_RETAINED_CANDIDATES:
            suppressed_count += 1
            item["execution_status"] = "suppressed"
            item["reject_reasons"] = sorted(set(item.get("reject_reasons", []) + ["retained_limit_exceeded"]))
            _write_derive_log("derive_candidate_suppressed", {
                "task_id": task_id,
                "source_id": source_id,
                "candidate": item,
            })
            continue
        retained.append(item)
        _write_derive_log("derive_decision", {
            "task_id": task_id,
            "source_id": source_id,
            "source_url": source_url,
            "source_media": source_media,
            "ingest_intent": ingest_intent,
            "candidate": item,
        })

    counts = {
        "candidate": sum(1 for item in retained if item.get("decision") == "candidate"),
        "rejected": sum(1 for item in retained if item.get("decision") == "reject"),
        "existing_related": sum(1 for item in retained if item.get("execution_status") == "existing_related"),
        "needs_target": sum(1 for item in retained if item.get("execution_status") == "needs_target"),
        "suppressed": suppressed_count,
        "raw": len(raw_candidates),
        "unique": len(by_key),
        "duplicate": duplicate_count,
        "retained": len(retained),
    }
    return {
        "enabled": True,
        "source": source,
        "limits": {
            "raw": MAX_RAW_CANDIDATES,
            "retained": MAX_RETAINED_CANDIDATES,
        },
        "counts": counts,
        "items": retained,
    }


def public_derived_tasks(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """Small status/UI-safe projection."""
    items = decision.get("items") if isinstance(decision, dict) else []
    if not isinstance(items, list):
        return []
    public: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("decision") == "reject":
            continue
        public.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "targetType": item.get("target_type"),
            "taskKind": item.get("task_kind"),
            "targetUrl": item.get("target_url"),
            "searchQuery": item.get("search_query"),
            "decision": item.get("decision"),
            "status": item.get("execution_status"),
            "candidateStatus": item.get("execution_status"),
            "score": item.get("score"),
            "reason": item.get("reason"),
        })
    return public
