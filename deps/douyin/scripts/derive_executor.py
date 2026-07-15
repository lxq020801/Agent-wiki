"""
derive_executor.py — execute approved derivation candidates into Obsidian assets.

This script consumes derived_ingest task JSON. It resolves the candidate target,
asks the configured Lite model to produce a durable asset body, writes the child
asset, and only then links parent and child with real Obsidian wikilinks.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from config_loader import Config, ConfigError, load_config
from cost_estimator import estimate_cost_rmb
from ingest import (
    _asset_title,
    _content_tags,
    _ensure_vault_structure,
    mark_derived_candidate_executed,
    _schema_asset_id,
    _slug_for_vault,
    _summary_from_text,
    _update_index,
)
from status_writer import StatusWriter, write_terminal

try:
    from server.github_service import register_derived_repository
    from server.vault_writer import VAULT_GIT_STATUS, vault_write_transaction
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from server.github_service import register_derived_repository
    from server.vault_writer import VAULT_GIT_STATUS, vault_write_transaction


DEFAULT_BRIDGE_ROOT = Path.home() / ".agent-wiki"
_AUDIT_ROOT_NAME = "run-artifacts"
AUTO_MATCH_SCORE = 6
AUTO_MATCH_MARGIN = 2
MAX_GITHUB_SEARCH_QUERIES = 8
MAX_GITHUB_REPOS_TO_SCORE = 10
MAX_GITHUB_REPOS_README = 4
SECRET_PATTERNS = [
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"), "ghp_[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_[REDACTED]"),
    (re.compile(r"(?i)(access_token|private_token|github_token)=([^&\s]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)([?&][^=&#]*(token|key|secret|signature|sig)[^=&#]*=)[^&#\s]+"), r"\1[REDACTED]"),
    (re.compile(r"\bresp[_-][A-Za-z0-9._-]+\b"), "resp-[REDACTED]"),
    (
        re.compile(
            r"(?i)[\"']?(api[_-]?key|ark[_-]?api[_-]?key|authorization|cookie|set-cookie|"
            r"response[_-]?id|previous[_-]?response[_-]?id)[\"']?\s*[:=]\s*"
            r"(\"[^\"]*\"|'[^']*'|[^,\s}\]\n\r]+)"
        ),
        "sensitive=[REDACTED]",
    ),
]
MACHINE_CONTEXT_LABELS = ("父资产与派生上下文", "目标来源材料")
MACHINE_CONTEXT_KEYS = ("candidate_name", "parent_source_url", "acceptance_criteria", "source_block")
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "private_token",
    "github_token",
    "token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "client_secret",
    "signature",
    "sig",
}


class DeriveError(Exception):
    def __init__(self, kind: str, message: str, *, hint: str = "", recoverable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.hint = hint
        self.recoverable = recoverable


def _redact_text(text: Any) -> str:
    cleaned = str(text or "")
    for pattern, repl in SECRET_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)
    return cleaned


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            canonical = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if canonical.endswith("apikey") or canonical in {
                "authorization",
                "cookie",
                "setcookie",
                "responseid",
                "previousresponseid",
                "githubtoken",
                "accesstoken",
                "privatetoken",
            }:
                continue
            clean[key] = _redact_value(child)
        return clean
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _runtime_root() -> Path:
    raw = os.environ.get("AGENT_WIKI_HOME")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_BRIDGE_ROOT


def _safe_artifact_name(value: Any, *, default: str = "run") -> str:
    text = str(value or "").strip() or default
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text[:120] or default


def _audit_dir(task_id: str) -> Path | None:
    if not task_id:
        return None
    path = _runtime_root() / _AUDIT_ROOT_NAME / _safe_artifact_name(task_id) / "05-derive-executor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit_rel(path: Path) -> str:
    try:
        return str(path.relative_to(_runtime_root()))
    except ValueError:
        return str(path)


def _write_audit_text(audit_dir: Path | None, rel_path: str, text: Any) -> str:
    if audit_dir is None:
        return ""
    target = audit_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_redact_text(text), encoding="utf-8")
    return _audit_rel(target)


def _write_audit_json(audit_dir: Path | None, rel_path: str, payload: Any) -> str:
    if audit_dir is None:
        return ""
    target = audit_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_redact_value(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _audit_rel(target)


def _add_artifact(artifacts: dict[str, Any], key: str, path: str) -> None:
    if path:
        artifacts[key] = path


def _artifact_index(audit_dir: Path | None, artifacts: dict[str, Any]) -> dict[str, Any]:
    if not artifacts:
        return {}
    return {
        "dir": _audit_rel(audit_dir.parent) if audit_dir else "",
        "files": artifacts,
    }


def _find_balanced_json_end(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] not in "{[":
        return None
    pairs = {"{": "}", "[": "]"}
    stack = [text[start]]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or pairs[stack[-1]] != char:
                return None
            stack.pop()
            if not stack:
                return index + 1
    return None


def _remove_labeled_machine_block(text: str, label: str) -> str:
    pattern = re.compile(rf"(?m)^.*{re.escape(label)}[：:].*$")
    while True:
        match = pattern.search(text)
        if not match:
            return text
        start = match.start()
        cursor = match.end()
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        end = None
        if cursor < len(text) and text[cursor] in "{[":
            json_end = _find_balanced_json_end(text, cursor)
            if json_end is not None:
                end = json_end
                while end < len(text) and text[end] in " \t\r\n":
                    end += 1
        if end is None:
            next_heading = re.search(r"(?m)^#{1,6}\s+", text[match.end():])
            end = match.end() + next_heading.start() if next_heading else len(text)
        text = text[:start].rstrip() + "\n\n" + text[end:].lstrip()


def _is_machine_material_json(value: Any) -> bool:
    if isinstance(value, list):
        return any(_is_machine_material_json(item) for item in value)
    if not isinstance(value, dict):
        return False
    keys = {str(key) for key in value.keys()}
    if keys.intersection(MACHINE_CONTEXT_KEYS):
        return True
    if "repo" in keys and ("readme" in keys or isinstance(value.get("repo"), dict)):
        return True
    if {"url", "title", "domain", "text"}.issubset(keys):
        return True
    return False


def _json_loads_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _remove_machine_fenced_blocks(text: str) -> str:
    key_pattern = re.compile(rf'"(?:{"|".join(re.escape(key) for key in MACHINE_CONTEXT_KEYS)})"\s*:')

    def replace(match: re.Match[str]) -> str:
        block = match.group(0)
        inner = match.group(1)
        if any(label in inner for label in MACHINE_CONTEXT_LABELS) or key_pattern.search(inner):
            return ""
        parsed = _json_loads_or_none(inner.strip())
        if _is_machine_material_json(parsed):
            return ""
        return block

    return re.sub(r"```[^\n]*\n([\s\S]*?)\n```", replace, text)


def _remove_standalone_machine_json_blocks(text: str) -> str:
    cursor = 0
    output = []
    for match in re.finditer(r"(?m)^[ \t]*[\{\[]", text):
        start = match.start()
        if start < cursor:
            continue
        json_start = match.end() - 1
        json_end = _find_balanced_json_end(text, json_start)
        if json_end is None:
            continue
        parsed = _json_loads_or_none(text[json_start:json_end])
        if not _is_machine_material_json(parsed):
            continue
        output.append(text[cursor:start])
        cursor = json_end
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
    if not output:
        return text
    output.append(text[cursor:])
    return "".join(output)


def _looks_like_machine_echo(text: str) -> bool:
    if any(label in text for label in MACHINE_CONTEXT_LABELS):
        return True
    key_pattern = re.compile(rf'"(?:{"|".join(re.escape(key) for key in MACHINE_CONTEXT_KEYS)})"\s*:')
    if key_pattern.search(text):
        return True
    github_material = re.search(r'"(?:repo|readme)"\s*:', text) and re.search(
        r'"(?:full_name|html_url|stargazers_count)"\s*:',
        text,
    )
    web_material = re.search(r'"(?:url|domain|text)"\s*:', text) and re.search(r'"title"\s*:', text)
    return bool(github_material or web_material)


def _sanitize_generated_body(text: Any) -> str:
    cleaned = _remove_machine_fenced_blocks(_redact_text(text))
    cleaned = _remove_standalone_machine_json_blocks(cleaned)
    for label in MACHINE_CONTEXT_LABELS:
        cleaned = _remove_labeled_machine_block(cleaned, label)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = re.sub(r"(?s)^#(?!#)\s+[^\n]+(?:\n+|$)", "", cleaned, count=1).strip()
    if _looks_like_machine_echo(cleaned):
        raise DeriveError(
            "unsafe_model_output",
            "模型输出疑似回显内部上下文，已拒绝写入正文",
            recoverable=True,
        )
    if not cleaned:
        raise DeriveError(
            "empty_model_output",
            "模型输出为空或只有内部上下文，已拒绝写入正文",
            recoverable=True,
        )
    return cleaned


@dataclass
class ResolvedTarget:
    url: str
    title: str
    kind: str
    confidence: float
    evidence: list[str]
    raw: dict[str, Any]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
            return
        if not self.skip_depth:
            self.parts.append(text)

    def text(self) -> str:
        body = "\n".join(self.parts)
        body = re.sub(r"\n{3,}", "\n\n", body)
        body = re.sub(r"[ \t]{2,}", " ", body)
        return body.strip()


def _load_task(task_file: Path) -> dict[str, Any]:
    if not task_file.exists():
        raise FileNotFoundError(f"任务文件不存在: {task_file}")
    return json.loads(task_file.read_text(encoding="utf-8"))


def _archive_task(task_file: Path, base_dir: Path, ok: bool) -> Path:
    sub = "archive" if ok else "failed"
    dest_dir = base_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / task_file.name
    n = 0
    while dest.exists():
        n += 1
        dest = dest_dir / f"{task_file.stem}.{n}{task_file.suffix}"
    task_file.replace(dest)
    return dest


def _json_request(url: str, *, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "agent-wiki-derive",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise DeriveError("http_error", f"HTTP {exc.code}: {_display_url(url)}", recoverable=True) from exc
    except Exception as exc:
        raise DeriveError("network_error", f"{type(exc).__name__}: {exc}", recoverable=True) from exc


def _text_request(url: str, *, timeout: int = 25) -> tuple[str, str]:
    _ensure_safe_external_url(url)
    req = urllib.request.Request(url, headers={
        "Accept": "text/html, text/plain;q=0.9, */*;q=0.8",
        "User-Agent": "agent-wiki-derive",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = getattr(resp, "url", "") or resp.geturl()
            _ensure_safe_external_url(final_url)
            raw = resp.read(1_500_000)
            content_type = resp.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        raise DeriveError("http_error", f"HTTP {exc.code}: {_display_url(url)}", recoverable=True) from exc
    except Exception as exc:
        raise DeriveError("network_error", f"{type(exc).__name__}: {exc}", recoverable=True) from exc
    text = raw.decode("utf-8", errors="replace")
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = TextExtractor()
        parser.feed(text)
        return parser.title, parser.text()
    return "", text.strip()


def _ensure_safe_external_url(value: str) -> None:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise DeriveError("invalid_target_url", "派生目标 URL 必须是 HTTPS 外部链接", recoverable=True)
    if parsed.username or parsed.password:
        raise DeriveError("invalid_target_url", "派生目标 URL 不能包含账号密码", recoverable=True)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise DeriveError("invalid_target_url", "派生目标 URL 缺少域名", recoverable=True)
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        raise DeriveError("invalid_target_url", "派生目标 URL 不能指向本机或内网域名", recoverable=True)
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise DeriveError("invalid_target_url", "派生目标 URL 不能指向本机或内网地址", recoverable=True)


def _clean_external_url(value: str) -> str:
    _ensure_safe_external_url(value)
    parsed = urllib.parse.urlparse(str(value or "").strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    clean_query = [
        (key, val)
        for key, val in query
        if key.lower() not in SENSITIVE_QUERY_KEYS
        and not any(marker in key.lower() for marker in ("token", "secret", "signature"))
    ]
    return urllib.parse.urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        "",
        urllib.parse.urlencode(clean_query, doseq=True),
        "",
    ))


def _display_url(value: str) -> str:
    try:
        return _clean_external_url(value)
    except DeriveError:
        return _redact_text(value)


def _repo_audit_summary(repo: dict[str, Any], *, score: int | None = None, query: str = "") -> dict[str, Any]:
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    summary: dict[str, Any] = {
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "archived": repo.get("archived"),
        "owner": owner.get("login") if isinstance(owner, dict) else "",
    }
    if score is not None:
        summary["match_score"] = score
    if query:
        summary["search_query"] = query
    return summary


def _target_audit_summary(target: ResolvedTarget) -> dict[str, Any]:
    raw_summary: dict[str, Any] = {}
    if target.kind == "github_project":
        repo = target.raw.get("repo") if isinstance(target.raw, dict) else {}
        readme = str(target.raw.get("readme") or "") if isinstance(target.raw, dict) else ""
        raw_summary = {
            "repo": _repo_audit_summary(repo) if isinstance(repo, dict) else {},
            "readme": readme[:120_000],
            "readme_chars": len(readme),
        }
    else:
        text = str(target.raw.get("text") or "") if isinstance(target.raw, dict) else ""
        raw_summary = {
            "title": target.raw.get("title") if isinstance(target.raw, dict) else "",
            "domain": target.raw.get("domain") if isinstance(target.raw, dict) else "",
            "text": text[:120_000],
            "text_chars": len(text),
        }
    return {
        "url": target.url,
        "title": target.title,
        "kind": target.kind,
        "confidence": target.confidence,
        "evidence": target.evidence,
        "source_material": raw_summary,
    }


def _github_owner_repo(url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        _ensure_safe_external_url(str(url or ""))
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2:
            return parts[0], re.sub(r"\.git$", "", parts[1], flags=re.I)
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", str(url or "")):
        owner, repo = str(url).split("/", 1)
        return owner, re.sub(r"\.git$", "", repo, flags=re.I)
    return None


def _candidate_text_values(candidate: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "name",
        "searchQuery",
        "search_query",
        "reason",
        "parentContext",
        "parent_context",
        "targetUrl",
        "target_url",
    ):
        value = candidate.get(key)
        if value:
            values.append(str(value))
    for key in ("evidence", "acceptanceCriteria", "acceptance_criteria"):
        value = candidate.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item)
        elif value:
            values.append(str(value))
    return values


def _github_ref_from_candidate_text(candidate: dict[str, Any]) -> tuple[str, str] | None:
    for text in _candidate_text_values(candidate):
        for pattern in (
            r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
            r"git@github\.com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        ):
            match = re.search(pattern, text)
            if match:
                owner, repo = match.group(1).split("/", 1)
                return owner, re.sub(r"\.git$", "", repo, flags=re.I)
    return None


def _split_camel_words(text: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)


def _compact_identity(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _clean_alias_tokens(tokens: list[str]) -> list[str]:
    generic = {
        "github",
        "git",
        "repository",
        "repo",
        "project",
        "official",
        "documentation",
        "docs",
        "api",
    }
    return [token for token in tokens if token.lower() not in generic]


def _github_candidate_aliases(candidate: dict[str, Any]) -> list[str]:
    aliases: list[str] = []

    def add(value: str) -> None:
        clean = re.sub(r"\s+", " ", str(value or "").strip(" -_/.,:;()[]{}\"'"))
        if not clean:
            return
        compact = _compact_identity(clean)
        if len(compact) < 3 or compact in {"github", "repository", "project", "official", "api"}:
            return
        if all(_compact_identity(item) != compact for item in aliases):
            aliases.append(clean)

    for text in _candidate_text_values(candidate):
        for owner, repo in re.findall(r"(?:github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text):
            add(f"{owner}/{re.sub(r'.git$', '', repo, flags=re.I)}")
            add(re.sub(r"\.git$", "", repo, flags=re.I))

        latin_runs = re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[\s._-]+[A-Za-z][A-Za-z0-9]*)*", text)
        for run in latin_runs:
            raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", run)
            tokens = _clean_alias_tokens(raw_tokens)
            if not tokens:
                continue
            phrase = " ".join(tokens)
            add(phrase)
            add("-".join(tokens))
            add("".join(tokens))
            camel = _split_camel_words(phrase)
            if camel != phrase:
                camel_tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", camel)
                add(" ".join(camel_tokens))
                add("-".join(camel_tokens))
                add("".join(camel_tokens))

    return aliases[:16]


def _github_alias_variants(aliases: list[str]) -> list[str]:
    variants: list[str] = []
    for alias in aliases:
        for variant in (alias, _split_camel_words(alias), _split_camel_words(alias).replace(" ", "-")):
            clean = re.sub(r"\s+", " ", variant.strip())
            if clean and clean not in variants:
                variants.append(clean)
    return variants


def _github_context_terms(candidate: dict[str, Any]) -> list[str]:
    generic = {
        "github",
        "git",
        "repository",
        "repo",
        "project",
        "official",
        "documentation",
        "docs",
        "api",
        "open",
        "source",
    }
    terms: list[str] = []
    for text in _candidate_text_values(candidate):
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", _split_camel_words(text).lower()):
            token = token.strip("_-")
            if token and token not in generic and token not in terms:
                terms.append(token)
    return terms[:5]


def _github_search_queries(candidate: dict[str, Any]) -> list[str]:
    aliases = _github_alias_variants(_github_candidate_aliases(candidate))
    context_terms = _github_context_terms(candidate)
    queries: list[str] = []

    def add(query: str) -> None:
        clean = re.sub(r"\s+", " ", query.strip())
        if clean and clean not in queries:
            queries.append(clean)

    for alias in aliases:
        if "/" in alias and re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", alias):
            add(alias)
        if " " in alias:
            add(f'"{alias}" in:name,description,readme')
        add(f"{alias} in:name,description,readme")
        if " " in alias:
            add(f"{alias.replace(' ', '-')} in:name,description,readme")
        if len(queries) >= MAX_GITHUB_SEARCH_QUERIES:
            return queries[:MAX_GITHUB_SEARCH_QUERIES]
    for alias in aliases:
        if context_terms:
            add(f"{alias} {' '.join(context_terms[:3])} in:name,description,readme")
        if len(queries) >= MAX_GITHUB_SEARCH_QUERIES:
            break
    return queries[:MAX_GITHUB_SEARCH_QUERIES]


def _github_repo_payload(owner: str, repo: str) -> tuple[dict[str, Any], str]:
    api = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
    meta = _json_request(api)
    readme = ""
    try:
        readme_obj = _json_request(f"{api}/readme")
        content = str(readme_obj.get("content") or "")
        if content:
            readme = _redact_text(base64.b64decode(content).decode("utf-8", errors="replace"))
    except DeriveError:
        readme = ""
    return meta, readme[:80_000]


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {"github", "project", "official", "documentation", "api", "视频", "项目", "工具", "官方", "文档"}
    return {word for word in words if word not in stop}


def _score_repo_match(candidate: dict[str, Any], repo: dict[str, Any], readme: str) -> int:
    name = str(candidate.get("name") or "").lower()
    aliases = _github_alias_variants(_github_candidate_aliases(candidate))
    context = " ".join([
        str(candidate.get("reason") or ""),
        str(candidate.get("parent_context") or ""),
        str(candidate.get("parentContext") or ""),
        " ".join(str(x) for x in candidate.get("evidence") or []),
        str(candidate.get("searchQuery") or candidate.get("search_query") or ""),
    ]).lower()
    haystack = " ".join([
        str(repo.get("full_name") or ""),
        str(repo.get("name") or ""),
        str(repo.get("description") or ""),
        readme[:12000],
    ]).lower()
    score = 0
    repo_name = str(repo.get("name") or "").lower()
    full_name = str(repo.get("full_name") or "").lower()
    repo_compact = _compact_identity(repo_name)
    full_compact = _compact_identity(full_name)
    alias_score = 0
    for alias in aliases:
        alias_lower = alias.lower()
        alias_separator_normalized = re.sub(r"[\s_]+", "-", alias_lower)
        alias_compact = _compact_identity(alias)
        if not alias_compact:
            continue
        if alias_lower in {repo_name, full_name} or alias_separator_normalized == repo_name:
            alias_score = max(alias_score, 9 if "-" in alias_separator_normalized else 8)
        elif alias_compact == repo_compact:
            alias_score = max(alias_score, 8)
        elif alias_compact == full_compact:
            alias_score = max(alias_score, 7)
        elif len(alias_compact) >= 5 and (repo_compact.endswith(alias_compact) or alias_compact in repo_compact):
            alias_score = max(alias_score, 3)
    score += alias_score
    if name and name == repo_name:
        score += 5
    elif name and (name in repo_name or name in full_name):
        score += 3
    overlap = _keywords(context) & _keywords(haystack)
    score += min(4, len(overlap))
    if repo.get("stargazers_count", 0) >= 100:
        score += 1
    if repo.get("archived") is True:
        score -= 2
    return score


def resolve_github_target(
    candidate: dict[str, Any],
    *,
    audit_dir: Path | None = None,
    audit_files: dict[str, Any] | None = None,
) -> ResolvedTarget:
    audit_files = audit_files if audit_files is not None else {}
    target_url = str(candidate.get("targetUrl") or candidate.get("target_url") or "").strip()
    repo_ref = _github_owner_repo(target_url)
    if not repo_ref:
        repo_ref = _github_ref_from_candidate_text(candidate)
    if repo_ref:
        owner, repo = repo_ref
        meta, readme = _github_repo_payload(owner, repo)
        target = ResolvedTarget(
            url=str(meta.get("html_url") or f"https://github.com/{owner}/{repo}"),
            title=str(meta.get("full_name") or f"{owner}/{repo}"),
            kind="github_project",
            confidence=0.95,
            evidence=["候选已提供明确 GitHub URL"],
            raw={"repo": meta, "readme": readme},
        )
        _add_artifact(audit_files, "derive_target_resolution", _write_audit_json(
            audit_dir,
            "01-target-resolution.json",
            {
                "method": "explicit_github_reference",
                "candidate": candidate,
                "repo_ref": f"{owner}/{repo}",
                "resolved_target": _target_audit_summary(target),
            },
        ))
        return target

    name = str(candidate.get("name") or "").strip()
    query = str(candidate.get("searchQuery") or candidate.get("search_query") or name).strip()
    if not name and not query:
        raise DeriveError("needs_target", "GitHub 派生缺少项目名或 URL", recoverable=True)
    search_queries = _github_search_queries(candidate) or [f"{name or query} in:name,description,readme"]
    seen: set[str] = set()
    repo_candidates: list[tuple[dict[str, Any], str]] = []
    search_audit: list[dict[str, Any]] = []
    for search_query in search_queries:
        search_q = urllib.parse.quote(search_query)
        search = _json_request(f"https://api.github.com/search/repositories?q={search_q}&sort=stars&order=desc&per_page=5")
        repos = search.get("items") if isinstance(search.get("items"), list) else []
        query_hits: list[dict[str, Any]] = []
        for repo in repos[:5]:
            if not isinstance(repo, dict):
                continue
            query_hits.append(_repo_audit_summary(repo, query=search_query))
            full_name = str(repo.get("full_name") or "")
            if full_name and full_name.lower() in seen:
                continue
            if full_name:
                seen.add(full_name.lower())
            repo_candidates.append((repo, search_query))
            if len(repo_candidates) >= MAX_GITHUB_REPOS_TO_SCORE:
                break
        search_audit.append({
            "query": search_query,
            "total_count": search.get("total_count"),
            "hits": query_hits,
        })
        if len(repo_candidates) >= MAX_GITHUB_REPOS_TO_SCORE:
            break
    prelim = sorted(
        ((_score_repo_match(candidate, repo, ""), repo, search_query) for repo, search_query in repo_candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    scored: list[tuple[int, dict[str, Any], str, str]] = []
    for _prelim_score, repo, search_query in prelim[:MAX_GITHUB_REPOS_README]:
        try:
            owner = repo["owner"]["login"]
            repo_name = repo["name"]
            meta, readme = _github_repo_payload(owner, repo_name)
        except Exception:
            meta, readme = repo, ""
        scored.append((_score_repo_match(candidate, meta, readme), meta, readme, search_query))
    scored.sort(key=lambda item: item[0], reverse=True)
    scored_audit = [
        _repo_audit_summary(repo, score=score, query=search_query)
        for score, repo, _readme, search_query in scored
    ]
    if not scored:
        _add_artifact(audit_files, "derive_target_resolution", _write_audit_json(
            audit_dir,
            "01-target-resolution.json",
            {
                "method": "github_search",
                "candidate": candidate,
                "queries": search_queries,
                "search_results": search_audit,
                "scored_results": scored_audit,
                "outcome": "needs_target",
            },
        ))
        raise DeriveError("needs_target", f"GitHub API 未找到项目：{name or query}", recoverable=True)
    best_score, best, best_readme, best_query = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0
    if best_score < AUTO_MATCH_SCORE or best_score - second < AUTO_MATCH_MARGIN:
        _add_artifact(audit_files, "derive_target_resolution", _write_audit_json(
            audit_dir,
            "01-target-resolution.json",
            {
                "method": "github_search",
                "candidate": candidate,
                "queries": search_queries,
                "search_results": search_audit,
                "scored_results": scored_audit,
                "best_score": best_score,
                "second_score": second,
                "outcome": "ambiguous_target",
            },
        ))
        raise DeriveError(
            "ambiguous_target",
            f"GitHub 项目无法唯一匹配：{name or query}",
            hint="请在扩展里补充明确 GitHub URL 后再确认派生。",
            recoverable=True,
        )
    target = ResolvedTarget(
        url=str(best.get("html_url") or ""),
        title=str(best.get("full_name") or name or query),
        kind="github_project",
        confidence=min(0.95, 0.55 + best_score / 20),
        evidence=[
            f"GitHub API 搜索命中 {best.get('full_name')}",
            f"匹配查询：{best_query}",
            f"README/描述与视频上下文匹配分 {best_score}",
        ],
        raw={"repo": best, "readme": best_readme},
    )
    _add_artifact(audit_files, "derive_target_resolution", _write_audit_json(
        audit_dir,
        "01-target-resolution.json",
        {
            "method": "github_search",
            "candidate": candidate,
            "queries": search_queries,
            "search_results": search_audit,
            "scored_results": scored_audit,
            "best_score": best_score,
            "second_score": second,
            "resolved_target": _target_audit_summary(target),
            "outcome": "resolved",
        },
    ))
    return target


def resolve_web_target(
    candidate: dict[str, Any],
    target_type: str,
    *,
    audit_dir: Path | None = None,
    audit_files: dict[str, Any] | None = None,
) -> ResolvedTarget:
    audit_files = audit_files if audit_files is not None else {}
    target_url = str(candidate.get("targetUrl") or candidate.get("target_url") or "").strip()
    if not target_url:
        raise DeriveError("needs_target", f"{target_type} 派生需要明确 URL", recoverable=True)
    target_url = _clean_external_url(target_url)
    title, text = _text_request(target_url)
    parsed = urllib.parse.urlparse(target_url)
    target = ResolvedTarget(
        url=target_url,
        title=title or str(candidate.get("name") or parsed.netloc or target_url),
        kind=target_type,
        confidence=0.85,
        evidence=["候选已提供明确网页 URL"],
        raw={"title": title, "text": text[:120_000], "domain": parsed.hostname or ""},
    )
    _add_artifact(audit_files, "derive_target_resolution", _write_audit_json(
        audit_dir,
        "01-target-resolution.json",
        {
            "method": "explicit_web_url",
            "candidate": candidate,
            "resolved_target": _target_audit_summary(target),
            "outcome": "resolved",
        },
    ))
    return target


def resolve_target(
    candidate: dict[str, Any],
    *,
    audit_dir: Path | None = None,
    audit_files: dict[str, Any] | None = None,
) -> ResolvedTarget:
    target_type = str(candidate.get("targetType") or candidate.get("target_type") or "")
    if target_type == "github_project":
        return resolve_github_target(candidate, audit_dir=audit_dir, audit_files=audit_files)
    if target_type in {"official_doc", "web_research"}:
        return resolve_web_target(candidate, target_type, audit_dir=audit_dir, audit_files=audit_files)
    raise DeriveError("unsupported_target_type", f"不支持的派生类型：{target_type}")


def _call_lite_model(config: Config, prompt: str) -> tuple[str, dict[str, Any]]:
    from openai import OpenAI  # type: ignore
    from analyzer import _extract_response_text, _usage_to_dict

    client = OpenAI(api_key=config.ark_api_key, base_url=config.ark_endpoint)
    response = client.responses.create(
        model=config.analyzer_model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        stream=False,
        store=True,
    )
    text = _extract_response_text(response)
    usage = _usage_to_dict(getattr(response, "usage", None))
    if not text.strip():
        raise DeriveError("empty_model_output", "派生模型输出为空", recoverable=True)
    return text.strip(), usage


def _safe_link_alias(value: Any, fallback: str) -> str:
    alias = str(value or fallback or "").strip()
    alias = alias.replace("[", "").replace("]", "").replace("|", "-")
    alias = re.sub(r"\s+", " ", alias).strip()
    if len(alias) > 88:
        alias = alias[:87].rstrip() + "…"
    return alias or str(fallback or "").strip() or "Untitled"


def _parent_link(task: dict[str, Any], vault_path: Path) -> tuple[str, Path | None]:
    parent_path = task.get("parent_asset_path") or task.get("parentAssetPath")
    if not parent_path:
        return "", None
    path = Path(str(parent_path)).expanduser()
    if not path.exists():
        return "", None
    try:
        path.resolve().relative_to(vault_path.resolve())
    except ValueError:
        return "", None
    title = ""
    try:
        title = _frontmatter_value(_frontmatter_block(path.read_text(encoding="utf-8", errors="ignore")), "title")
    except OSError:
        title = ""
    title = title or str(task.get("parent_title") or task.get("parentTitle") or path.stem)
    return _asset_link(path, title), path


def _asset_link(path: Path, title: str) -> str:
    return f"[[{path.stem}|{_safe_link_alias(title, path.stem)}]]"


def _canonical_asset_url(value: str) -> str:
    try:
        cleaned = _clean_external_url(value)
    except DeriveError:
        return ""
    parsed = urllib.parse.urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    if host == "github.com":
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) >= 2:
            return f"https://github.com/{parts[0]}/{parts[1]}".lower()
    return urllib.parse.urlunparse((
        parsed.scheme.lower(),
        host,
        path.rstrip("/") or "/",
        "",
        parsed.query,
        "",
    )).lower().rstrip("/")


def _frontmatter_block(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    return text[4:end]


def _frontmatter_value(frontmatter: str, key: str) -> str:
    for raw in frontmatter.splitlines():
        if ":" not in raw:
            continue
        name, value = raw.split(":", 1)
        if name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def _frontmatter_target_values(frontmatter: str) -> list[str]:
    keys = {"source_url", "repo", "repository", "official_url", "homepage", "docs_url"}
    values: list[str] = []
    for key in keys:
        value = _frontmatter_value(frontmatter, key)
        if not value:
            continue
        if key in {"repo", "repository"} and re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value):
            value = f"https://github.com/{value}"
        canonical = _canonical_asset_url(value)
        if canonical and canonical not in values:
            values.append(canonical)
    return values


def _existing_asset_for_target(vault_path: Path, target: ResolvedTarget) -> tuple[Path | None, str]:
    canonical = _canonical_asset_url(target.url)
    if not canonical:
        return None, ""
    asset_root = vault_path / "知识资产"
    if not asset_root.exists():
        return None, ""
    target_repo = target.raw.get("repo") if target.kind == "github_project" and isinstance(target.raw, dict) else {}
    target_repository_id = int(target_repo.get("id") or 0) if isinstance(target_repo, dict) else 0
    for md in asset_root.glob("**/*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = _frontmatter_block(text)
        if not fm:
            continue
        stored_repository_id = _frontmatter_value(fm, "repository_id")
        same_repository_id = bool(
            target_repository_id
            and stored_repository_id.isdigit()
            and int(stored_repository_id) == target_repository_id
        )
        if not same_repository_id and canonical not in _frontmatter_target_values(fm):
            continue
        title = _frontmatter_value(fm, "title") or md.stem
        return md, title
    return None, ""


def _yaml_escape(value: Any) -> str:
    return str(value or "").replace('"', '\\"').replace("\n", " ").strip()


def _format_yaml_list(values: list[str]) -> str:
    clean = [str(v).strip() for v in values if str(v).strip()]
    if not clean:
        return "[]"
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in clean) + "]"


def _tag_list(target_type: str, content: str = "") -> tuple[str, ...]:
    tags = _content_tags(content)
    auxiliary = {
        "github_project": ("project", "derived-asset", "github"),
        "official_doc": ("official-doc", "derived-asset", "webpage"),
        "web_research": ("web-research", "derived-asset", "webpage"),
    }.get(target_type, ("derived-asset", "webpage"))
    for tag in auxiliary:
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _build_prompt(task: dict[str, Any], candidate: dict[str, Any], target: ResolvedTarget, parent_link: str) -> str:
    target_type = str(candidate.get("targetType") or candidate.get("target_type") or target.kind)
    raw = target.raw
    if target_type == "github_project":
        repo = raw.get("repo", {})
        source_block = json.dumps({
            "repo": {
                "full_name": repo.get("full_name"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "stars": repo.get("stargazers_count"),
                "forks": repo.get("forks_count"),
                "open_issues": repo.get("open_issues_count"),
                "license": (repo.get("license") or {}).get("spdx_id") if isinstance(repo.get("license"), dict) else "",
                "pushed_at": repo.get("pushed_at"),
                "html_url": repo.get("html_url"),
            },
            "readme": raw.get("readme", "")[:50000],
        }, ensure_ascii=False)
        required = "项目结论、能解决什么问题、最小可运行路径、核心 API/架构、与父视频说法关系、采用判断、风险、可复用片段"
    else:
        source_block = json.dumps({
            "url": target.url,
            "title": target.title,
            "domain": raw.get("domain"),
            "text": raw.get("text", "")[:50000],
        }, ensure_ascii=False)
        required = "来源链路、核心结论、关键事实、与父视频说法对照、可执行建议、风险和不确定"
    context = {
        "candidate_name": candidate.get("name"),
        "target_type": target_type,
        "reason": candidate.get("reason"),
        "evidence": candidate.get("evidence") or [],
        "acceptance_criteria": candidate.get("acceptanceCriteria") or candidate.get("acceptance_criteria") or [],
        "parent_link": parent_link,
        "parent_source_url": task.get("parent_source_url") or task.get("parentSourceUrl") or "",
    }
    return (
        "你是 Obsidian 知识资产派生工具。请基于给定来源生成一篇中文 Markdown 子资产正文。\n"
        "要求：不要输出 frontmatter；不要编造；不确定处明确标注；内容要可复用、可执行、可验证。"
        "来源事实必须归因到仓库、README 或网页；AI 判断单列并使用限定语，不得写成来源无法支持的绝对结论。\n"
        f"必须包含这些信息：{required}。\n\n"
        f"父资产与派生上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"目标来源材料：\n{source_block}\n"
    )


def _child_frontmatter(
    *,
    config: Config,
    task: dict[str, Any],
    candidate: dict[str, Any],
    target: ResolvedTarget,
    title: str,
    summary: str,
    parent_link: str,
    body_text: str,
) -> tuple[str, Path, tuple[str, ...]]:
    date = datetime.now().strftime("%Y%m%d")
    target_type = str(candidate.get("targetType") or candidate.get("target_type") or target.kind)
    target_material = json.dumps(target.raw, ensure_ascii=False)[:50000]
    tags = _tag_list(target_type, f"{title}\n{target_material}\n{body_text}")
    if target_type == "github_project":
        asset_id = _schema_asset_id(config.vault_path, date, "github")
        rel_dir = Path("知识资产/GitHub项目")
        repo = target.raw.get("repo", {})
        md_slug = _slug_for_vault(str(repo.get("full_name") or title), str(candidate.get("id") or "derived"), 58)
        md_path = config.vault_path / rel_dir / f"{date}-{md_slug}.md"
        license_id = ""
        if isinstance(repo.get("license"), dict):
            license_id = str(repo["license"].get("spdx_id") or "")
        frontmatter = f"""---
id: "{asset_id}"
type: github_project
asset_family: github_project
source_media: github
ingest_intent: derived_ingest
title: "{_yaml_escape(title)}"
source_url: "{_yaml_escape(target.url)}"
repo: "{_yaml_escape(target.url)}"
repository_id: {int(repo.get("id") or 0)}
repository_full_name: "{_yaml_escape(repo.get("full_name") or "")}"
language: "{_yaml_escape(repo.get("language") or "")}"
stars: {int(repo.get("stargazers_count") or 0)}
forks: {int(repo.get("forks_count") or 0)}
open_issues: {int(repo.get("open_issues_count") or 0)}
license: "{_yaml_escape(license_id)}"
description: "{_yaml_escape(repo.get("description") or "")}"
ingested: {datetime.now().strftime("%Y-%m-%d")}
updated: {datetime.now().strftime("%Y-%m-%d")}
tags: {_format_yaml_list(list(tags))}
summary: "{_yaml_escape(summary)}"
confidence: medium
weight: 100
status: active
derived_kind: github_project
derived_from: {_format_yaml_list([parent_link] if parent_link else [])}
parent_source_url: "{_yaml_escape(task.get("parent_source_url") or task.get("parentSourceUrl") or "")}"
verification_status: partially_verified
evidence_level: primary
related: {_format_yaml_list([parent_link] if parent_link else [])}
---
"""
        return frontmatter, md_path, tags

    asset_id = _schema_asset_id(config.vault_path, date, "web")
    rel_dir = Path("知识资产/网页剪藏")
    md_slug = _slug_for_vault(title, str(candidate.get("id") or "derived"), 58)
    md_path = config.vault_path / rel_dir / f"{date}-{md_slug}.md"
    domain = urllib.parse.urlparse(target.url).hostname or ""
    derived_kind = "official_doc" if target_type == "official_doc" else "web_research"
    frontmatter = f"""---
id: "{asset_id}"
type: web_clip
asset_family: knowledge_asset
source_media: webpage
ingest_intent: derived_ingest
title: "{_yaml_escape(title)}"
source_url: "{_yaml_escape(target.url)}"
author: "{_yaml_escape(domain)}"
published: ""
ingested: {datetime.now().strftime("%Y-%m-%d")}
updated: {datetime.now().strftime("%Y-%m-%d")}
tags: {_format_yaml_list(list(tags))}
domain: "{_yaml_escape(domain)}"
summary: "{_yaml_escape(summary)}"
confidence: medium
weight: 100
status: active
derived_kind: {derived_kind}
derived_from: {_format_yaml_list([parent_link] if parent_link else [])}
parent_source_url: "{_yaml_escape(task.get("parent_source_url") or task.get("parentSourceUrl") or "")}"
verification_status: partially_verified
evidence_level: {"official" if target_type == "official_doc" else "secondary"}
related: {_format_yaml_list([parent_link] if parent_link else [])}
---
"""
    return frontmatter, md_path, tags


def _frontmatter_span(text: str) -> tuple[int, int] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    return 0, end + 4


def _append_related_to_frontmatter(text: str, link: str) -> str:
    if not link:
        return text
    span = _frontmatter_span(text)
    if not span:
        return text
    start, end = span
    fm = text[start:end]
    if link in fm:
        return text
    rest = text[end:]
    if re.search(r"^related:\s*\[\]\s*$", fm, re.M):
        fm = re.sub(r"^related:\s*\[\]\s*$", f"related: [{json.dumps(link, ensure_ascii=False)}]", fm, flags=re.M)
    elif re.search(r"^related:\s*\[", fm, re.M):
        fm = re.sub(r"^(related:\s*\[)(.*?)(\]\s*)$", lambda m: m.group(1) + (m.group(2).rstrip() + ", " if m.group(2).strip() else "") + json.dumps(link, ensure_ascii=False) + m.group(3), fm, flags=re.M)
    elif re.search(r"^related:\s*$", fm, re.M):
        fm = re.sub(r"^related:\s*$", f"related:\n  - {json.dumps(link, ensure_ascii=False)}", fm, flags=re.M)
    else:
        fm = fm.rstrip() + f"\nrelated: [{json.dumps(link, ensure_ascii=False)}]\n"
    return fm + rest


def _append_related_section(text: str, child_link: str, relation: str) -> str:
    line = f"- {child_link}：{relation}"
    span = _frontmatter_span(text)
    body_text = text[span[1]:] if span else text
    if child_link in body_text:
        return text
    marker = "\n## 相关资产\n"
    if marker in text:
        before, after = text.split(marker, 1)
        next_heading = re.search(r"\n##\s+", after)
        if next_heading:
            section = after[:next_heading.start()].rstrip()
            rest = after[next_heading.start():]
            return before + marker + section + "\n" + line + "\n" + rest
        return before + marker + after.rstrip() + "\n" + line + "\n"
    return text.rstrip() + "\n\n## 相关资产\n" + line + "\n"


def _append_backlink_section(text: str, parent_link: str, relation: str) -> str:
    if not parent_link:
        return text
    line = f"- {parent_link}：{relation}"
    if parent_link in text:
        return text
    marker = "\n## 被引用\n"
    if marker in text:
        before, after = text.split(marker, 1)
        next_heading = re.search(r"\n##\s+", after)
        if next_heading:
            section = after[:next_heading.start()].rstrip()
            rest = after[next_heading.start():]
            return before + marker + section + "\n" + line + "\n" + rest
        return before + marker + after.rstrip() + "\n" + line + "\n"
    return text.rstrip() + "\n\n## 被引用\n" + line + "\n"


def _normalize_wikilink_aliases(text: str, target_path: Path, clean_link: str) -> str:
    if not clean_link:
        return text
    pattern = re.compile(r"\[\[" + re.escape(target_path.stem) + r"(?:\|[^\]]*)?\]\]", re.S)
    return pattern.sub(clean_link, text)


def _normalize_duplicate_leading_h1(text: str) -> str:
    span = _frontmatter_span(text)
    prefix = text[:span[1]] if span else ""
    body = text[span[1]:] if span else text
    updated = re.sub(
        r"(?s)^(\s*#(?!#)\s+[^\n]+\n+)\s*#(?!#)\s+[^\n]+(?:\n+|$)",
        r"\1",
        body,
        count=1,
    )
    return prefix + updated


def _link_child_back_to_parent(parent_path: Path | None, child_path: Path, parent_link: str, relation: str) -> list[Path]:
    if not parent_path or not parent_path.exists() or not child_path.exists() or not parent_link:
        return []
    text = child_path.read_text(encoding="utf-8")
    updated = _normalize_wikilink_aliases(text, parent_path, parent_link)
    updated = _normalize_duplicate_leading_h1(updated)
    updated = _append_backlink_section(updated, parent_link, relation)
    if updated != text:
        child_path.write_text(updated, encoding="utf-8")
        return [child_path]
    return []


def _link_parent_child(parent_path: Path | None, child_path: Path, child_title: str, relation: str) -> list[Path]:
    if not parent_path or not parent_path.exists():
        return []
    child_link = _asset_link(child_path, child_title)
    text = parent_path.read_text(encoding="utf-8")
    updated = _append_related_to_frontmatter(text, child_link)
    updated = _append_related_section(updated, child_link, relation)
    if updated != text:
        parent_path.write_text(updated, encoding="utf-8")
        return [parent_path]
    return []


def _register_github_target(target: ResolvedTarget, asset_path: Path, vault_path: Path) -> dict[str, Any]:
    if target.kind != "github_project" or not isinstance(target.raw.get("repo"), dict):
        return {}
    try:
        return register_derived_repository(
            target.raw["repo"],
            asset_path,
            vault_path,
            readme=str(target.raw.get("readme") or ""),
        )
    except Exception as exc:
        return {
            "ok": False,
            "code": "github_post_write_hook_failed",
            "message": type(exc).__name__,
        }


def _link_existing_target_locked(
    *,
    config: Config,
    task: dict[str, Any],
    candidate: dict[str, Any],
    target: ResolvedTarget,
    parent_link: str,
    parent_path: Path | None,
) -> dict[str, Any] | None:
    existing_path, existing_title = _existing_asset_for_target(config.vault_path, target)
    if not existing_path:
        return None
    title = existing_title or existing_path.stem
    relation = str(candidate.get("relationType") or "派生资产")
    text = existing_path.read_text(encoding="utf-8", errors="ignore")
    summary = _summary_from_text(text, title)
    section = "GitHub项目" if target.kind == "github_project" else "网页剪藏"
    tags = _tag_list(target.kind, text)
    _ensure_vault_structure(config.vault_path)
    _update_index(config.vault_path, existing_path, title, summary, section=section, tags=tags)
    touched = [config.vault_path / "index.md"]
    touched.extend(_link_parent_child(parent_path, existing_path, title, relation))
    touched.extend(_link_child_back_to_parent(parent_path, existing_path, parent_link, relation))
    child_link = _asset_link(existing_path, title)
    touched.extend(mark_derived_candidate_executed(
        parent_path,
        candidate_name=str(candidate.get("name") or target.title),
        child_link=child_link,
        candidate_type=str(candidate.get("targetType") or candidate.get("target_type") or target.kind),
        candidate_url=str(candidate.get("targetUrl") or candidate.get("target_url") or target.url),
    ))
    return {
        "path": existing_path,
        "title": title,
        "relation": relation,
        "child_link": child_link,
        "touched": list(dict.fromkeys(touched)),
    }


def _finish_existing_target(
    *,
    task: dict[str, Any],
    candidate: dict[str, Any],
    target: ResolvedTarget,
    config: Config,
    parent_link: str,
    parent_path: Path | None,
    existing: dict[str, Any],
    audit_root: Path | None,
    audit_files: dict[str, Any],
) -> dict[str, Any]:
    existing_path = existing["path"]
    github_integration = _register_github_target(target, existing_path, config.vault_path)
    resolved_summary = {
        "url": target.url,
        "title": target.title,
        "kind": target.kind,
        "confidence": target.confidence,
        "evidence": target.evidence + ["vault 中已存在同一目标资产，已避免重复写入"],
    }
    git_status = VAULT_GIT_STATUS
    touched = existing["touched"]
    _add_artifact(audit_files, "derive_write_result", _write_audit_json(
        audit_root,
        "06-write-result.json",
        {
            "mode": "existing_asset",
            "vault_path": str(existing_path),
            "git_status": git_status,
            "touched": [str(path) for path in touched],
            "github_integration": github_integration,
        },
    ))
    _add_artifact(audit_files, "derive_linkback", _write_audit_json(
        audit_root,
        "07-linkback.json",
        {
            "parent_path": str(parent_path) if parent_path else "",
            "parent_link": parent_link,
            "child_path": str(existing_path),
            "child_link": existing["child_link"],
            "relation": existing["relation"],
            "updated_files": [str(path) for path in touched],
        },
    ))
    return {
        "vault_path": str(existing_path),
        "git_status": git_status,
        "candidate_id": candidate.get("id"),
        "target_type": candidate.get("targetType") or candidate.get("target_type"),
        "resolved_target": resolved_summary,
        "parent_asset_path": str(parent_path) if parent_path else "",
        "asset_link": existing["child_link"],
        "cost": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_rmb_estimate": 0.0},
        "dedupe_status": "existing_asset_linked",
        "github_integration": github_integration,
        "audit_artifacts": _artifact_index(audit_root, audit_files),
    }


def execute_derived_task(task: dict[str, Any], config: Config, sw: StatusWriter) -> dict[str, Any]:
    candidate = task.get("candidate") if isinstance(task.get("candidate"), dict) else {}
    if not candidate:
        raise DeriveError("invalid_task", "派生任务缺少 candidate")
    task_id = str(task.get("id") or task.get("task_id") or "")
    audit_root = _audit_dir(task_id)
    audit_files: dict[str, Any] = {}
    _add_artifact(audit_files, "derive_executor_task", _write_audit_json(
        audit_root,
        "00-task.json",
        {
            "task": task,
            "candidate": candidate,
        },
    ))
    sw.update(
        stage="resolving_target",
        candidate_id=candidate.get("id"),
        derived_task=candidate,
        audit_artifacts=_artifact_index(audit_root, audit_files),
    )
    try:
        target = resolve_target(candidate, audit_dir=audit_root, audit_files=audit_files)
    except DeriveError as exc:
        _add_artifact(audit_files, "derive_executor_error", _write_audit_json(
            audit_root,
            "99-error.json",
            {
                "stage": "resolving_target",
                "error_kind": exc.kind,
                "error": str(exc),
                "hint": exc.hint,
                "recoverable": exc.recoverable,
            },
        ))
        sw.update(audit_artifacts=_artifact_index(audit_root, audit_files))
        raise
    sw.update(stage="target_resolved", resolved_target={
        "url": target.url,
        "title": target.title,
        "kind": target.kind,
        "confidence": target.confidence,
        "evidence": target.evidence,
    }, audit_artifacts=_artifact_index(audit_root, audit_files))
    _add_artifact(audit_files, "derive_source_material", _write_audit_json(
        audit_root,
        "02-source-material.json",
        _target_audit_summary(target),
    ))

    parent_link, parent_path = _parent_link(task, config.vault_path)
    with vault_write_transaction(config.vault_path):
        existing = _link_existing_target_locked(
            config=config,
            task=task,
            candidate=candidate,
            target=target,
            parent_link=parent_link,
            parent_path=parent_path,
        )
    if existing:
        return _finish_existing_target(
            task=task,
            candidate=candidate,
            target=target,
            config=config,
            parent_link=parent_link,
            parent_path=parent_path,
            existing=existing,
            audit_root=audit_root,
            audit_files=audit_files,
        )
    prompt = _build_prompt(task, candidate, target, parent_link)
    _add_artifact(audit_files, "derive_model_prompt", _write_audit_text(
        audit_root,
        "03-model-prompt.md",
        prompt,
    ))
    sw.update(
        stage="analyzing_derived_target",
        model=config.analyzer_model,
        audit_artifacts=_artifact_index(audit_root, audit_files),
    )
    try:
        body_raw, usage = _call_lite_model(config, prompt)
    except DeriveError as exc:
        _add_artifact(audit_files, "derive_executor_error", _write_audit_json(
            audit_root,
            "99-error.json",
            {
                "stage": "analyzing_derived_target",
                "error_kind": exc.kind,
                "error": str(exc),
                "hint": exc.hint,
                "recoverable": exc.recoverable,
            },
        ))
        sw.update(audit_artifacts=_artifact_index(audit_root, audit_files))
        raise
    _add_artifact(audit_files, "derive_model_output_raw", _write_audit_text(
        audit_root,
        "04-model-output-raw.md",
        body_raw,
    ))
    try:
        body = _sanitize_generated_body(body_raw)
    except DeriveError as exc:
        _add_artifact(audit_files, "derive_executor_error", _write_audit_json(
            audit_root,
            "99-error.json",
            {
                "stage": "sanitize_model_output",
                "error_kind": exc.kind,
                "error": str(exc),
                "hint": exc.hint,
                "recoverable": exc.recoverable,
            },
        ))
        sw.update(audit_artifacts=_artifact_index(audit_root, audit_files))
        raise
    _add_artifact(audit_files, "derive_model_output_sanitized", _write_audit_text(
        audit_root,
        "05-model-output-sanitized.md",
        body,
    ))
    title = _asset_title(target.title or str(candidate.get("name") or "派生资产"))
    summary = _summary_from_text(body, title)
    cost = estimate_cost_rmb(config.analyzer_model, usage)

    sw.update(stage="writing_vault", audit_artifacts=_artifact_index(audit_root, audit_files))
    with vault_write_transaction(config.vault_path):
        raced_existing = _link_existing_target_locked(
            config=config,
            task=task,
            candidate=candidate,
            target=target,
            parent_link=parent_link,
            parent_path=parent_path,
        )
        if raced_existing is None:
            _ensure_vault_structure(config.vault_path)
            frontmatter, md_path, tags = _child_frontmatter(
                config=config,
                task=task,
                candidate=candidate,
                target=target,
                title=title,
                summary=summary,
                parent_link=parent_link,
                body_text=body,
            )
            md_path.parent.mkdir(parents=True, exist_ok=True)
            if md_path.exists():
                raise DeriveError("asset_exists", f"派生资产已存在：{md_path}", recoverable=True)
            child_body = "# " + title + "\n\n" + body.strip() + "\n"
            child_body = _append_backlink_section(
                child_body,
                parent_link,
                str(candidate.get("relationType") or "派生资产"),
            )
            md_path.write_text(frontmatter + "\n" + child_body, encoding="utf-8")
            section = "GitHub项目" if target.kind == "github_project" else "网页剪藏"
            _update_index(config.vault_path, md_path, title, summary, section=section, tags=tags)
            touched = [md_path, config.vault_path / "index.md"]
            relation = str(candidate.get("relationType") or "派生资产")
            parent_touched = _link_parent_child(parent_path, md_path, title, relation)
            touched.extend(parent_touched)
            child_link = _asset_link(md_path, title)
            touched.extend(mark_derived_candidate_executed(
                parent_path,
                candidate_name=str(candidate.get("name") or target.title),
                child_link=child_link,
                candidate_type=str(candidate.get("targetType") or candidate.get("target_type") or target.kind),
                candidate_url=str(candidate.get("targetUrl") or candidate.get("target_url") or target.url),
            ))
    if raced_existing:
        return _finish_existing_target(
            task=task,
            candidate=candidate,
            target=target,
            config=config,
            parent_link=parent_link,
            parent_path=parent_path,
            existing=raced_existing,
            audit_root=audit_root,
            audit_files=audit_files,
        )
    resolved_summary = {
        "url": target.url,
        "title": target.title,
        "kind": target.kind,
        "confidence": target.confidence,
        "evidence": target.evidence,
    }
    git_status = VAULT_GIT_STATUS
    github_integration = _register_github_target(target, md_path, config.vault_path)
    _add_artifact(audit_files, "derive_write_result", _write_audit_json(
        audit_root,
        "06-write-result.json",
        {
            "mode": "new_asset",
            "vault_path": str(md_path),
            "title": title,
            "summary": summary,
            "section": section,
            "git_status": git_status,
            "touched": [str(path) for path in touched],
            "cost": cost,
            "github_integration": github_integration,
        },
    ))
    _add_artifact(audit_files, "derive_linkback", _write_audit_json(
        audit_root,
        "07-linkback.json",
        {
            "parent_path": str(parent_path) if parent_path else "",
            "parent_link": parent_link,
            "child_path": str(md_path),
            "child_link": child_link,
            "relation": relation,
            "updated_files": [str(path) for path in parent_touched],
        },
    ))

    return {
        "vault_path": str(md_path),
        "git_status": git_status,
        "candidate_id": candidate.get("id"),
        "target_type": candidate.get("targetType") or candidate.get("target_type"),
        "resolved_target": resolved_summary,
        "parent_asset_path": str(parent_path) if parent_path else "",
        "asset_link": child_link,
        "cost": cost,
        "github_integration": github_integration,
        "audit_artifacts": _artifact_index(audit_root, audit_files),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute an Agent-wiki derived task")
    parser.add_argument("--task", required=True, help="derived_ingest task JSON path")
    args = parser.parse_args(argv)
    task_file = Path(args.task).expanduser()
    base_dir = task_file.parent.parent if task_file.parent.name == "inbox" else DEFAULT_BRIDGE_ROOT
    status_dir = base_dir / "status"
    task_id = task_file.stem

    try:
        task = _load_task(task_file)
        task_id = str(task.get("id") or task_id)
    except Exception as exc:
        write_terminal(task_id, status_dir, {
            "stage": "failed",
            "ok": False,
            "error": f"task_load_error: {exc}",
            "error_kind": "task_load_error",
        })
        return 1

    sw = StatusWriter(task_id, status_dir)
    try:
        config = load_config()
        sw.update(
            stage="started",
            type="derived_ingest",
            ingest_intent="derived_ingest",
            source="derived_tool",
            parent_task_id=task.get("parent_task_id") or task.get("parentTaskId") or "",
            source_url=task.get("parent_source_url") or task.get("parentSourceUrl") or "",
        )
        summary = execute_derived_task(task, config, sw)
    except ConfigError as exc:
        sw.update(stage="failed", ok=False, error=str(exc), error_kind="config_error", recoverable=True)
        _archive_task(task_file, base_dir, ok=False)
        return 1
    except DeriveError as exc:
        sw.update(
            stage="failed",
            ok=False,
            error=str(exc),
            error_kind=exc.kind,
            hint=exc.hint,
            recoverable=exc.recoverable,
        )
        _archive_task(task_file, base_dir, ok=False)
        return 1
    except Exception as exc:
        sw.update(
            stage="failed",
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            error_kind="unexpected",
            traceback=traceback.format_exc(),
        )
        _archive_task(task_file, base_dir, ok=False)
        return 1

    sw.update(stage="done", ok=True, **summary)
    _archive_task(task_file, base_dir, ok=True)
    print(f"✓ derived done: {summary['vault_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
