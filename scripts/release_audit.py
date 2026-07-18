#!/usr/bin/env python3
"""Read-only checks for publishing the Agent-wiki source repository."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "PROJECT_INTENT.md",
    "THIRD_PARTY_NOTICES.md",
    "RELEASE_CHECKLIST.md",
    "requirements.txt",
    "deps/douyin/requirements.txt",
    "deps/douyin/vendor/README.md",
)
REQUIREMENT_FILES = ("requirements.txt", "deps/douyin/requirements.txt")
EXPECTED_IGNORED_PATHS = (
    ".env",
    ".env.local",
    "config.toml",
    "cookies.txt",
    "douyin.txt",
    "local.pem",
    "local.key",
    "local.p12",
    "local.pfx",
    ".agent-wiki/config.toml",
    "cookie/douyin.txt",
    "logs/server.log",
)
SENSITIVE_PATH_PATTERNS = (
    re.compile(r"(^|/)\.env($|\.)", re.IGNORECASE),
    re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$", re.IGNORECASE),
    re.compile(r"(^|/)config\.toml$", re.IGNORECASE),
    re.compile(r"(^|/)(cookies?|douyin)\.txt$", re.IGNORECASE),
    re.compile(r"(^|/)(credentials?|secrets?)(\.|$)", re.IGNORECASE),
    re.compile(r"\.(log|sqlite|sqlite3|db)$", re.IGNORECASE),
)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {
    ".gitignore",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
}
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "bearer-token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}\b", re.IGNORECASE),
    ),
)
QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|private[_-]?token|client[_-]?secret|"
    r"password|authorization|cookie)\b\s*[:=]\s*([\"'])([^\"'\s]{16,})\1",
    re.IGNORECASE,
)
ENV_SECRET_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|PRIVATE_TOKEN|"
    r"CLIENT_SECRET|PASSWORD|COOKIE)\s*=\s*(?![\"'])([^\s#]{16,})",
    re.IGNORECASE,
)
QUERY_SECRET_ASSIGNMENT = re.compile(
    r"(?:access_token|private_token|github_token|api_key|client_secret|msToken)="
    r"([A-Za-z0-9._~-]{16,})",
    re.IGNORECASE,
)
PLACEHOLDER_MARKERS = (
    "changeme",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "redacted",
    "sample",
    "your_",
    "your-",
)
HISTORY_GIT_PATTERN = (
    r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|"
    r"(AKIA|ASIA)[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"Bearer[[:space:]]+[A-Za-z0-9._~+/-]{20,}"
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
RELEASE_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
VENDOR_COMMIT = "42784ffc83a72a516bfe952153ad7e2a3998d16c"


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    line: int | None
    detail: str


@dataclass(frozen=True)
class Dependency:
    name: str
    canonical_name: str
    declaration: str
    manifest: str


@dataclass
class AuditResult:
    findings: list[Finding]
    dependencies: list[Dependency]
    tracked_files: int
    history_scanned: bool
    nearest_release_tag: str
    exact_head_release_tags: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def run_git(root: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def tracked_paths(root: Path) -> list[Path]:
    proc = run_git(root, ["ls-files", "-z"])
    return [root / raw.decode("utf-8", "surrogateescape") for raw in proc.stdout.split(b"\0") if raw]


def is_text_path(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def read_text(path: Path) -> str | None:
    if not path.is_file() or not is_text_path(path):
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", "replace")


def is_placeholder(value: str) -> bool:
    normalized = value.strip().rstrip(",;").strip("\"'").lower()
    if not normalized:
        return True
    if normalized in {"ms_token", "sec_user_id"}:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def is_code_expression(value: str) -> bool:
    candidate = value.strip().rstrip(",;").strip("\"'")
    if any(char in candidate for char in "(){}[]"):
        return True
    return bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", candidate)
        and ("_" in candidate or "." in candidate)
    )


def scan_text(path: str, text: str, *, high_confidence_only: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(line) and (kind, line_number) not in seen:
                findings.append(Finding("secret", path, line_number, f"possible {kind}"))
                seen.add((kind, line_number))
        if high_confidence_only:
            continue
        for kind, pattern in (
            ("quoted-secret-assignment", QUOTED_SECRET_ASSIGNMENT),
            ("environment-secret-assignment", ENV_SECRET_ASSIGNMENT),
            ("query-secret", QUERY_SECRET_ASSIGNMENT),
        ):
            match = pattern.search(line)
            if not match:
                continue
            candidate = match.group(match.lastindex or 1)
            if is_placeholder(candidate) or is_code_expression(candidate):
                continue
            if (kind, line_number) not in seen:
                findings.append(Finding("secret", path, line_number, f"possible {kind}"))
                seen.add((kind, line_number))
    return findings


def canonicalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(root: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for relative in REQUIREMENT_FILES:
        path = root / relative
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            declaration = raw.split("#", 1)[0].strip()
            if not declaration or declaration.startswith("-"):
                continue
            match = REQUIREMENT_NAME.match(declaration)
            if not match:
                continue
            name = match.group(1)
            dependencies.append(
                Dependency(name, canonicalize_package(name), declaration, relative)
            )
    return dependencies


def check_required_files(root: Path) -> list[Finding]:
    findings = []
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file() or path.stat().st_size == 0:
            findings.append(Finding("required-file", relative, None, "missing or empty"))
    return findings


def check_sensitive_tracked_paths(root: Path, paths: Iterable[Path]) -> list[Finding]:
    findings = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative.lower().endswith(".env.example"):
            continue
        if any(pattern.search(relative) for pattern in SENSITIVE_PATH_PATTERNS):
            findings.append(Finding("tracked-path", relative, None, "sensitive filename is tracked"))
    return findings


def check_ignore_policy(root: Path) -> list[Finding]:
    findings = []
    for relative in EXPECTED_IGNORED_PATHS:
        proc = run_git(root, ["check-ignore", "--quiet", "--no-index", relative], check=False)
        if proc.returncode != 0:
            findings.append(Finding("ignore-policy", relative, None, "sensitive path is not ignored"))
    return findings


def check_dependency_notices(root: Path, dependencies: Sequence[Dependency]) -> list[Finding]:
    notice_path = root / "THIRD_PARTY_NOTICES.md"
    if not notice_path.is_file():
        return []
    notice = notice_path.read_text(encoding="utf-8")
    mentioned = {
        canonicalize_package(token)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", notice)
    }
    findings = []
    for name in sorted({item.canonical_name for item in dependencies}):
        if name not in mentioned:
            findings.append(Finding("dependency-notice", "THIRD_PARTY_NOTICES.md", None, f"missing direct dependency {name}"))
    return findings


def check_markdown_links(root: Path, paths: Iterable[Path]) -> list[Finding]:
    findings = []
    for path in paths:
        if path.suffix.lower() != ".md":
            continue
        text = read_text(path)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for raw_target in MARKDOWN_LINK.findall(line):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                target = target.split(" ", 1)[0]
                candidate = path.parent / urllib.parse.unquote(target)
                if not candidate.exists():
                    findings.append(
                        Finding(
                            "markdown-link",
                            path.relative_to(root).as_posix(),
                            line_number,
                            f"missing local target {target}",
                        )
                    )
    return findings


def release_tag_state(root: Path) -> tuple[str, tuple[str, ...]]:
    proc = run_git(root, ["tag", "--list"], check=False)
    if proc.returncode != 0:
        return "", ()
    release_tags = sorted(
        tag
        for tag in proc.stdout.decode("utf-8", "replace").splitlines()
        if RELEASE_TAG.fullmatch(tag)
    )
    if not release_tags:
        return "", ()

    head_proc = run_git(root, ["rev-parse", "HEAD"], check=False)
    head = head_proc.stdout.decode("ascii", "replace").strip() if head_proc.returncode == 0 else ""
    exact_tags = []
    if head:
        for tag in release_tags:
            tag_proc = run_git(root, ["rev-parse", f"refs/tags/{tag}^{{}}"], check=False)
            if tag_proc.returncode == 0 and tag_proc.stdout.decode("ascii", "replace").strip() == head:
                exact_tags.append(tag)

    describe_args = ["describe", "--tags", "--abbrev=0"]
    for tag in release_tags:
        describe_args.extend(["--match", tag])
    describe_args.append("HEAD")
    nearest_proc = run_git(root, describe_args, check=False)
    nearest = (
        nearest_proc.stdout.decode("utf-8", "replace").strip()
        if nearest_proc.returncode == 0
        else ""
    )
    return nearest, tuple(exact_tags)


def check_license_and_version(root: Path) -> tuple[list[Finding], str, tuple[str, ...]]:
    findings = []
    license_path = root / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8") if license_path.is_file() else ""
    if license_text and ("Apache License" not in license_text or "Version 2.0" not in license_text):
        findings.append(Finding("license", "LICENSE", None, "expected Apache License 2.0 text"))

    manifest_path = root / "chrome-extension" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        findings.append(Finding("version", "chrome-extension/manifest.json", None, "invalid JSON"))
        manifest = {}
    manifest_version = str(manifest.get("version", ""))
    if not VERSION.fullmatch(manifest_version):
        findings.append(Finding("version", "chrome-extension/manifest.json", None, "missing semantic version"))
    else:
        version_surfaces = (
            ("README.md", f"当前版本为 **v{manifest_version}**"),
            ("server/github_service.py", f'USER_AGENT = "Agent-wiki/{manifest_version}"'),
            ("docs/websocket-protocol.md", f"当前产品版本：`{manifest_version}`"),
        )
        for relative, expected in version_surfaces:
            path = root / relative
            if path.is_file() and expected not in path.read_text(encoding="utf-8"):
                findings.append(
                    Finding(
                        "version",
                        relative,
                        None,
                        f"does not match manifest version {manifest_version}",
                    )
                )

    nearest_release_tag, exact_head_release_tags = release_tag_state(root)
    expected_tag = f"v{manifest_version}"
    if exact_head_release_tags and exact_head_release_tags != (expected_tag,):
        exact_display = ", ".join(exact_head_release_tags)
        findings.append(
            Finding(
                "version",
                "chrome-extension/manifest.json",
                None,
                f"version {manifest_version} does not match exact HEAD release tag(s) {exact_display}",
            )
        )
    return findings, nearest_release_tag, exact_head_release_tags


def check_vendor(root: Path) -> list[Finding]:
    findings = []
    for relative in ("deps/douyin/vendor/README.md", "THIRD_PARTY_NOTICES.md"):
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if VENDOR_COMMIT not in text or "Apache" not in text:
            findings.append(Finding("vendor-provenance", relative, None, "missing commit or Apache attribution"))

    modified_files = (
        "deps/douyin/vendor/crawlers/douyin/web/config.yaml",
        "deps/douyin/vendor/crawlers/douyin/web/web_crawler.py",
        "deps/douyin/vendor/crawlers/douyin/web/xbogus.py",
    )
    for relative in modified_files:
        path = root / relative
        if not path.is_file():
            findings.append(Finding("vendor-modification", relative, None, "modified vendor file is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        if "Modified by Agent-wiki" not in text:
            findings.append(Finding("vendor-modification", relative, None, "missing modification notice"))

    config_path = root / modified_files[0]
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        cookie_line = re.search(r"^\s*Cookie:\s*(.*?)\s*$", config_text, re.MULTILINE)
        if not cookie_line or cookie_line.group(1).strip().strip("\"'"):
            findings.append(Finding("vendor-hardening", modified_files[0], None, "Cookie value must stay empty"))

    crawler_path = root / modified_files[1]
    if crawler_path.is_file():
        crawler_text = crawler_path.read_text(encoding="utf-8")
        if re.search(r"print\([^\n]*(?:Cookie|cookie)", crawler_text):
            findings.append(Finding("vendor-hardening", modified_files[1], None, "Cookie logging is present"))

    xbogus_path = root / modified_files[2]
    if xbogus_path.is_file():
        xbogus_text = xbogus_path.read_text(encoding="utf-8")
        for match in re.finditer(r"msToken=([A-Za-z0-9._~-]+)", xbogus_text, re.IGNORECASE):
            if match.group(1) != "MS_TOKEN":
                line = xbogus_text.count("\n", 0, match.start()) + 1
                findings.append(Finding("vendor-hardening", modified_files[2], line, "example token is not a placeholder"))
    return findings


def scan_reachable_history(root: Path) -> list[Finding]:
    findings = []
    commits = run_git(root, ["rev-list", "--all"]).stdout.decode("ascii").splitlines()
    seen: set[str] = set()
    for commit in commits:
        proc = run_git(
            root,
            ["grep", "-I", "-l", "-E", "-e", HISTORY_GIT_PATTERN, commit, "--"],
            check=False,
        )
        if proc.returncode not in {0, 1}:
            findings.append(Finding("history-scan", "<git>", None, f"git grep failed at {commit[:12]}"))
            continue
        for raw in proc.stdout.decode("utf-8", "replace").splitlines():
            path = raw.split(":", 1)[-1]
            if path in seen:
                continue
            seen.add(path)
            findings.append(
                Finding(
                    "history-secret",
                    path,
                    None,
                    f"possible high-confidence secret in reachable commit {commit[:12]}",
                )
            )
    return findings


def audit(root: Path = PROJECT_ROOT, *, history: bool = False) -> AuditResult:
    root = root.resolve()
    paths = tracked_paths(root)
    dependencies = parse_requirements(root)
    findings: list[Finding] = []
    findings.extend(check_required_files(root))
    findings.extend(check_sensitive_tracked_paths(root, paths))
    findings.extend(check_ignore_policy(root))
    findings.extend(check_dependency_notices(root, dependencies))
    findings.extend(check_markdown_links(root, paths))
    version_findings, nearest_release_tag, exact_head_release_tags = check_license_and_version(root)
    findings.extend(version_findings)
    findings.extend(check_vendor(root))

    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        relative = path.relative_to(root).as_posix()
        findings.extend(scan_text(relative, text))

    if history:
        findings.extend(scan_reachable_history(root))

    findings.sort(key=lambda item: (item.check, item.path, item.line or 0, item.detail))
    return AuditResult(
        findings,
        dependencies,
        len(paths),
        history,
        nearest_release_tag,
        exact_head_release_tags,
    )


def print_human(result: AuditResult, *, verbose: bool = False) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(f"release audit: {status}")
    print(f"tracked files: {result.tracked_files}")
    print(f"direct dependency declarations: {len(result.dependencies)}")
    print(f"nearest release tag: {result.nearest_release_tag or 'none'}")
    exact_tags = ", ".join(result.exact_head_release_tags) or "none"
    print(f"exact HEAD release tag: {exact_tags}")
    history_status = "complete (high-confidence patterns)" if result.history_scanned else "skipped (use --history)"
    print(f"history scan: {history_status}")
    for finding in result.findings:
        location = finding.path if finding.line is None else f"{finding.path}:{finding.line}"
        print(f"ERROR [{finding.check}] {location}: {finding.detail}")
    if verbose:
        for dependency in result.dependencies:
            print(f"DEPENDENCY {dependency.manifest}: {dependency.declaration}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="scan reachable Git history for high-confidence secrets")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--verbose", action="store_true", help="list direct dependency declarations")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = audit(args.root, history=args.history)
    if args.json:
        payload = {
            "ok": result.ok,
            "tracked_files": result.tracked_files,
            "history_scanned": result.history_scanned,
            "nearest_release_tag": result.nearest_release_tag,
            "exact_head_release_tags": result.exact_head_release_tags,
            "dependencies": [asdict(item) for item in result.dependencies],
            "findings": [asdict(item) for item in result.findings],
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print_human(result, verbose=args.verbose)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
