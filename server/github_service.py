"""GitHub authentication, repository ingest, refresh, and deduplication.

OAuth tokens are kept exclusively in the macOS Keychain. Runtime JSON files
contain only non-secret settings, repository identities, and batch progress.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from server.vault_writer import vault_write_transaction


GITHUB_API = "https://api.github.com"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
KEYCHAIN_SERVICE = "com.agent-wiki.github"
KEYCHAIN_ACCOUNT = "github-oauth-token"
CLIENT_ID_ENV = "AGENT_WIKI_GITHUB_CLIENT_ID"
DEFAULT_CLIENT_ID = "Iv23liSzzn6LYCGleEQA"
USER_AGENT = "Agent-wiki/0.2.1"
MAX_README_CHARS = 500_000
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.details = details or {}

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.retry_after is not None:
            payload["retryAfter"] = self.retry_after
        payload.update(self.details)
        return payload


class MacOSKeychainTokenStore:
    """Minimal Keychain adapter. Command output is never logged or persisted."""

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        platform: str | None = None,
    ) -> None:
        self.service = service
        self.account = account
        self.runner = runner
        self.platform = platform or sys.platform

    def _ensure_available(self) -> None:
        if self.platform != "darwin":
            raise GitHubServiceError(
                "secure_store_unavailable",
                "GitHub 登录目前需要 macOS Keychain。",
            )

    def get(self) -> str:
        self._ensure_available()
        proc = self.runner(
            [
                "security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 44:
            return ""
        if proc.returncode != 0:
            raise GitHubServiceError("secure_store_failed", "无法读取 macOS Keychain 中的 GitHub 凭证。")
        return str(proc.stdout or "").strip()

    def set(self, token: str) -> None:
        self._ensure_available()
        if not token:
            raise GitHubServiceError("token_missing", "GitHub 未返回有效登录凭证。")
        proc = self.runner(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            # A trailing -w prompts for the password twice. Supplying both
            # lines over stdin keeps the token out of the process arguments.
            input=f"{token}\n{token}\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitHubServiceError("secure_store_failed", "GitHub 凭证无法写入 macOS Keychain。")

    def delete(self) -> None:
        self._ensure_available()
        self.runner(
            [
                "security",
                "delete-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
            ],
            capture_output=True,
            text=True,
            check=False,
        )


@dataclass
class HTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: float = 20,
    ) -> HTTPResponse:
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HTTPResponse(
                    int(response.status),
                    {str(key).lower(): str(value) for key, value in response.headers.items()},
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return HTTPResponse(
                int(exc.code),
                {str(key).lower(): str(value) for key, value in exc.headers.items()},
                exc.read(),
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitHubServiceError("network_error", "无法连接 GitHub，请检查网络后重试。") from exc


def _read_simple_config(path: Path, section: str, key: str, default: str = "") -> str:
    if not path.exists():
        return default
    current = ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current != section or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        clean = value.strip().split(" #", 1)[0].strip()
        if len(clean) >= 2 and clean[:1] == clean[-1:] and clean[0] in {'"', "'"}:
            clean = clean[1:-1]
        return clean
    return default


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_owner_repo(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urllib.parse.urlparse(text)
        if (parsed.hostname or "").lower() != "github.com":
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return ""
        text = f"{parts[0]}/{parts[1]}"
    text = re.sub(r"\.git$", "", text, flags=re.IGNORECASE).strip("/")
    return text.lower() if OWNER_REPO_PATTERN.fullmatch(text) else ""


def public_repository(repo: dict[str, Any]) -> dict[str, Any]:
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    license_info = repo.get("license") if isinstance(repo.get("license"), dict) else {}
    return {
        "id": _safe_int(repo.get("id")),
        "name": str(repo.get("name") or ""),
        "fullName": str(repo.get("full_name") or ""),
        "owner": str(owner.get("login") or ""),
        "url": str(repo.get("html_url") or ""),
        "description": str(repo.get("description") or ""),
        "language": str(repo.get("language") or ""),
        "stars": _safe_int(repo.get("stargazers_count")),
        "forks": _safe_int(repo.get("forks_count")),
        "openIssues": _safe_int(repo.get("open_issues_count")),
        "license": str(license_info.get("spdx_id") or ""),
        "archived": bool(repo.get("archived")),
        "private": bool(repo.get("private")),
        "defaultBranch": str(repo.get("default_branch") or ""),
        "pushedAt": str(repo.get("pushed_at") or ""),
        "updatedAt": str(repo.get("updated_at") or ""),
    }


class GitHubAPI:
    def __init__(self, transport: Any | None = None) -> None:
        self.transport = transport or UrllibTransport()

    def _headers(self, token: str = "", *, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def form_post(self, url: str, values: dict[str, Any]) -> dict[str, Any]:
        response = self.transport.request(
            "POST",
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            data=urllib.parse.urlencode(values).encode("ascii"),
        )
        return self._json_or_error(response)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        query: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        raw: bool = False,
        allow_not_found: bool = False,
    ) -> tuple[Any, dict[str, str]]:
        url = path if path.startswith("https://") else GITHUB_API + path
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        response = self.transport.request(method, url, headers=self._headers(token, accept=accept))
        if allow_not_found and response.status == 404:
            return None, response.headers
        if response.status >= 400:
            self._raise_api_error(response)
        if raw:
            return response.body.decode("utf-8", errors="replace"), response.headers
        return (response.json() if response.body else {}), response.headers

    def _json_or_error(self, response: HTTPResponse) -> dict[str, Any]:
        if response.status >= 400:
            self._raise_api_error(response)
        try:
            value = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubServiceError("invalid_response", "GitHub 返回了无法解析的响应。") from exc
        if not isinstance(value, dict):
            raise GitHubServiceError("invalid_response", "GitHub 返回了无法解析的响应。")
        return value

    def _raise_api_error(self, response: HTTPResponse) -> None:
        try:
            data = response.json()
        except (ValueError, UnicodeDecodeError):
            data = {}
        message = str(data.get("message") or "GitHub API 请求失败。") if isinstance(data, dict) else "GitHub API 请求失败。"
        if response.status == 401:
            raise GitHubServiceError("auth_expired", "GitHub 登录已失效，请重新登录。")
        remaining = response.headers.get("x-ratelimit-remaining")
        if response.status in {403, 429} and (remaining == "0" or response.status == 429):
            retry_after = _safe_int(response.headers.get("retry-after"))
            if not retry_after:
                reset = _safe_int(response.headers.get("x-ratelimit-reset"))
                retry_after = max(1, reset - int(time.time())) if reset else 60
            raise GitHubServiceError("rate_limited", "GitHub API 请求过于频繁，请稍后重试。", retry_after=retry_after)
        if response.status == 404:
            raise GitHubServiceError("not_found", "没有找到该 GitHub 仓库。")
        raise GitHubServiceError("github_api_error", message, details={"status": response.status})


class GitHubService:
    def __init__(
        self,
        *,
        runtime_root: Path | str | None = None,
        config_path: Path | str | None = None,
        client_id: str | None = None,
        token_store: Any | None = None,
        api: GitHubAPI | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.runtime_root = Path(runtime_root or os.environ.get("AGENT_WIKI_HOME") or Path.home() / ".agent-wiki").expanduser()
        self.config_path = Path(config_path or self.runtime_root / "config.toml").expanduser()
        self._explicit_client_id = client_id
        self.token_store = token_store or MacOSKeychainTokenStore()
        self.api = api or GitHubAPI()
        self.clock = clock
        self.github_root = self.runtime_root / "github"
        self.settings_path = self.github_root / "settings.json"
        self.registry_path = self.github_root / "repositories.json"
        self.authorization_path = self.github_root / "authorization.json"
        self.pending_flows: dict[str, dict[str, Any]] = {}
        self.pending_refreshes: dict[str, dict[str, Any]] = {}
        self.import_batches: dict[str, dict[str, Any]] = {}
        self._account_cache: dict[str, Any] = {}
        self._verified_token_hash = ""
        self._auth_generation = 0
        self._authenticated_flow_id = ""
        self._token_presence_cache: dict[str, Any] = {"known": False, "present": False, "expiresAt": 0.0}
        self._write_lock = threading.RLock()

    @staticmethod
    def _public_authorization(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "state": "waiting_for_user",
            "flowId": flow_id,
            "userCode": str(flow.get("userCode") or ""),
            "verificationUri": str(flow.get("verificationUri") or ""),
            "expiresAt": float(flow.get("expiresAt") or 0),
            "interval": int(flow.get("interval") or 5),
        }

    def active_authorization(self) -> dict[str, Any] | None:
        now = self.clock()
        with self._write_lock:
            for flow_id, flow in reversed(list(self.pending_flows.items())):
                if now < float(flow.get("expiresAt") or 0):
                    return self._public_authorization(flow_id, flow)
        return None

    def _client_id_and_source(self) -> tuple[str, str]:
        candidates = (
            (self._explicit_client_id, "explicit"),
            (os.environ.get(CLIENT_ID_ENV), "environment"),
            (_read_simple_config(self.config_path, "github", "client_id"), "runtime_config"),
            (DEFAULT_CLIENT_ID, "official_default"),
        )
        for raw_value, source in candidates:
            value = str(raw_value or "").strip()
            if value:
                return (value if CLIENT_ID_PATTERN.fullmatch(value) else ""), source
        return "", "missing"

    def client_id(self) -> str:
        return self._client_id_and_source()[0]

    def configuration_status(self) -> dict[str, Any]:
        client_id, source = self._client_id_and_source()
        configured = bool(client_id)
        return {
            "configured": configured,
            "source": source if configured else "missing",
            "message": "" if configured else f"缺少 GitHub App client ID。请设置 {CLIENT_ID_ENV} 后重启本地服务。",
        }

    def settings(self) -> dict[str, Any]:
        value = _read_json(self.settings_path, {"autoStar": False})
        return {"autoStar": bool(value.get("autoStar", False))}

    def update_settings(self, *, auto_star: Any) -> dict[str, Any]:
        settings = {"autoStar": bool(auto_star), "updatedAt": datetime.now().isoformat()}
        _atomic_json(self.settings_path, settings)
        return self.status(validate=False)

    def authorization_status(self) -> dict[str, Any]:
        value = _read_json(self.authorization_path, {"state": "logged_out"})
        state = str(value.get("state") or "logged_out")
        if state not in {"logged_out", "waiting_for_user", "ready", "failed", "cancelled"}:
            state = "logged_out"
        result: dict[str, Any] = {"state": state}
        error = value.get("lastAuthorizationError")
        if isinstance(error, dict):
            public_error = {
                "code": str(error.get("code") or "authorization_failed")[:128],
                "message": str(error.get("message") or "GitHub 授权失败。")[:1000],
                "stage": str(error.get("stage") or "authorization")[:128],
                "occurredAt": str(error.get("occurredAt") or "")[:128],
            }
            result["lastAuthorizationError"] = public_error
        return result

    def _write_authorization_state(
        self,
        state: str,
        *,
        error: GitHubServiceError | None = None,
        stage: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "state": state,
            "updatedAt": datetime.now().isoformat(),
        }
        if error is not None:
            payload["lastAuthorizationError"] = {
                "code": error.code,
                "message": error.message,
                "stage": stage or str(error.details.get("stage") or "authorization"),
                "occurredAt": datetime.now().isoformat(),
            }
        _atomic_json(self.authorization_path, payload)

    @staticmethod
    def _staged_error(error: Exception, stage: str) -> GitHubServiceError:
        if isinstance(error, GitHubServiceError):
            details = dict(error.details)
            details["stage"] = stage
            return GitHubServiceError(
                error.code,
                error.message,
                retry_after=error.retry_after,
                details=details,
            )
        return GitHubServiceError(
            "authorization_failed",
            "GitHub 授权失败。",
            details={"stage": stage},
        )

    def _finish_authorization_failure(
        self,
        flow_id: str,
        error: Exception,
        *,
        stage: str,
    ) -> GitHubServiceError:
        staged = self._staged_error(error, stage)
        self.pending_flows.pop(flow_id, None)
        self._write_authorization_state("failed", error=staged, stage=stage)
        return staged

    def _clear_authentication_cache(self) -> None:
        self._account_cache = {}
        self._verified_token_hash = ""

    def _mark_token_verified(self, token: str, account: dict[str, str]) -> None:
        self._verified_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self._account_cache = dict(account)
        self._token_presence_cache = {"known": True, "present": True, "expiresAt": self.clock() + 30}

    def _token_is_verified(self, token: str) -> bool:
        if not token or not self._verified_token_hash or not self._account_cache.get("login"):
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, self._verified_token_hash)

    def _validated_account(self, token: str) -> dict[str, str]:
        user, _ = self.api.request("GET", "/user", token=token)
        if not isinstance(user, dict) or not str(user.get("login") or "").strip():
            raise GitHubServiceError("invalid_response", "GitHub 账号验证响应不完整。")
        return {
            "login": str(user.get("login") or ""),
            "name": str(user.get("name") or ""),
        }

    def _delete_token_quietly(self) -> None:
        with contextlib.suppress(Exception):
            self.token_store.delete()

    def _token(self) -> str:
        token = self.token_store.get()
        if not token:
            self._clear_authentication_cache()
            self._token_presence_cache = {"known": True, "present": False, "expiresAt": self.clock() + 30}
            raise GitHubServiceError("auth_required", "请先登录 GitHub。")
        if self._verified_token_hash and not self._token_is_verified(token):
            self._clear_authentication_cache()
        self._token_presence_cache = {"known": True, "present": True, "expiresAt": self.clock() + 30}
        return token

    def _authenticated_request(self, *args: Any, **kwargs: Any) -> tuple[Any, dict[str, str]]:
        try:
            return self.api.request(*args, token=self._token(), **kwargs)
        except GitHubServiceError as exc:
            if exc.code == "auth_expired":
                self.token_store.delete()
                self._clear_authentication_cache()
                self._token_presence_cache = {"known": True, "present": False, "expiresAt": self.clock() + 30}
                staged = self._staged_error(exc, "token_validation")
                self._write_authorization_state("failed", error=staged, stage="token_validation")
            raise

    def status(self, *, validate: bool = False) -> dict[str, Any]:
        configured = self.configuration_status()
        authenticated = False
        account = None
        token = ""
        token_present = False
        validation_generation = None
        if validate:
            with self._write_lock:
                validation_generation = self._auth_generation
        try:
            if not validate and not self._token_presence_cache.get("known"):
                token_present = False
            elif not validate and self.clock() < float(self._token_presence_cache.get("expiresAt") or 0):
                token_present = bool(self._token_presence_cache.get("present"))
                authenticated = bool(token_present and self._verified_token_hash and self._account_cache.get("login"))
            else:
                token = self.token_store.get()
                token_present = bool(token)
                self._token_presence_cache = {"known": True, "present": bool(token), "expiresAt": self.clock() + 30}
                if not token:
                    self._clear_authentication_cache()
                elif not validate:
                    authenticated = self._token_is_verified(token)
        except GitHubServiceError as exc:
            authorization = self.authorization_status()
            return {
                "ok": False,
                "state": exc.code,
                "configured": configured,
                "authenticated": False,
                "account": None,
                "settings": self.settings(),
                "activeAuthorization": self.active_authorization(),
                "authorizationState": authorization["state"],
                "lastAuthorizationError": authorization.get("lastAuthorizationError"),
                "message": exc.message,
            }
        if token and validate:
            try:
                account = self._validated_account(token)
                with self._write_lock:
                    if validation_generation == self._auth_generation:
                        self._mark_token_verified(token, account)
                        self._write_authorization_state("ready")
                        authenticated = True
                    else:
                        token = ""
                        token_present = False
                        account = None
                        self._clear_authentication_cache()
                        self._token_presence_cache = {
                            "known": True,
                            "present": False,
                            "expiresAt": self.clock() + 30,
                        }
            except GitHubServiceError as exc:
                with self._write_lock:
                    validation_is_current = validation_generation == self._auth_generation
                    if validation_is_current and exc.code == "auth_expired":
                        self._delete_token_quietly()
                        self._clear_authentication_cache()
                        self._token_presence_cache = {"known": True, "present": False, "expiresAt": self.clock() + 30}
                        staged = self._staged_error(exc, "account_validation")
                        self._write_authorization_state("failed", error=staged, stage="account_validation")
                if not validation_is_current:
                    token = ""
                    token_present = False
                    account = None
                elif exc.code != "auth_expired":
                    raise
        elif authenticated:
            account = dict(self._account_cache)
        if not authenticated:
            account = None
        token_known = bool(self._token_presence_cache.get("known"))
        authorization = self.authorization_status()
        state = (
            "ready"
            if authenticated
            else (
                "authorization_failed"
                if configured["configured"] and authorization.get("lastAuthorizationError")
                else (
                    "unchecked"
                    if configured["configured"] and (not token_known or token_present)
                    else ("logged_out" if configured["configured"] else "not_configured")
                )
            )
        )
        active_import = None
        for batch in reversed(list(self.import_batches.values())):
            if batch.get("state") in {"queued", "running"}:
                active_import = self.public_batch(batch)
                break
        return {
            "ok": bool(configured["configured"] and authenticated),
            "state": state,
            "configured": configured,
            "authenticated": authenticated,
            "account": account,
            "settings": self.settings(),
            "activeImport": active_import,
            "activeAuthorization": None if authenticated else self.active_authorization(),
            "authorizationState": "ready" if authenticated else authorization["state"],
            "lastAuthorizationError": None if authenticated else authorization.get("lastAuthorizationError"),
            "message": configured.get("message") or (
                ""
                if authenticated
                else (
                    str((authorization.get("lastAuthorizationError") or {}).get("message") or "GitHub 登录状态尚未验证。")
                    if state in {"unchecked", "authorization_failed"}
                    else "尚未登录 GitHub。"
                )
            ),
        }

    def start_authorization(self) -> dict[str, Any]:
        client_id = self.client_id()
        if not client_id:
            raise GitHubServiceError("not_configured", self.configuration_status()["message"])
        with self._write_lock:
            active = self.active_authorization()
            if active:
                return active
            try:
                data = self.api.form_post(GITHUB_DEVICE_CODE_URL, {"client_id": client_id})
            except Exception as exc:
                staged = self._staged_error(exc, "device_code_request")
                self._write_authorization_state("failed", error=staged, stage="device_code_request")
                raise staged from exc
            device_code = str(data.get("device_code") or "")
            user_code = str(data.get("user_code") or "")
            verification_uri = str(data.get("verification_uri_complete") or data.get("verification_uri") or "")
            if not device_code or not user_code or not verification_uri:
                error = GitHubServiceError(
                    "invalid_response",
                    "GitHub Device Flow 响应不完整。",
                    details={"stage": "device_code_request"},
                )
                self._write_authorization_state("failed", error=error, stage="device_code_request")
                raise error
            flow_id = uuid.uuid4().hex
            expires_in = max(1, _safe_int(data.get("expires_in")) or 900)
            interval = max(5, _safe_int(data.get("interval")) or 5)
            now = self.clock()
            flow = {
                "deviceCode": device_code,
                "userCode": user_code,
                "verificationUri": verification_uri,
                "expiresAt": now + expires_in,
                "interval": interval,
                "nextPollAt": now,
                "polling": False,
            }
            self.pending_flows = {
                key: value
                for key, value in self.pending_flows.items()
                if now < float(value.get("expiresAt") or 0)
            }
            self.pending_flows[flow_id] = flow
            self._write_authorization_state("waiting_for_user")
            return self._public_authorization(flow_id, flow)

    def poll_authorization(self, flow_id: str) -> dict[str, Any]:
        flow_id = str(flow_id or "")
        with self._write_lock:
            flow = self.pending_flows.get(flow_id)
            if not flow:
                raise GitHubServiceError("authorization_missing", "GitHub 授权已取消或不存在，请重新开始。")
            now = self.clock()
            if now >= float(flow["expiresAt"]):
                error = GitHubServiceError("authorization_expired", "GitHub 授权已超时，请重新开始。")
                raise self._finish_authorization_failure(
                    flow_id,
                    error,
                    stage="device_code_expired",
                )
            if now < float(flow.get("nextPollAt") or 0):
                return {
                    "ok": True,
                    "state": "authorization_pending",
                    "flowId": flow_id,
                    "retryAfter": max(1, int(float(flow["nextPollAt"]) - now)),
                }
            if flow.get("polling"):
                return {
                    "ok": True,
                    "state": "authorization_pending",
                    "flowId": flow_id,
                    "retryAfter": max(1, int(flow.get("interval") or 5)),
                }
            flow["polling"] = True
            device_code = str(flow["deviceCode"])
            interval = int(flow["interval"])

        try:
            data = self.api.form_post(
                GITHUB_ACCESS_TOKEN_URL,
                {
                    "client_id": self.client_id(),
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except Exception as exc:
            with self._write_lock:
                current = self.pending_flows.get(flow_id)
                if current is flow:
                    flow["polling"] = False
                    flow["nextPollAt"] = self.clock() + interval
                else:
                    raise GitHubServiceError(
                        "authorization_missing",
                        "GitHub 授权已取消或不存在，请重新开始。",
                    ) from exc
            raise

        with self._write_lock:
            if self.pending_flows.get(flow_id) is not flow:
                raise GitHubServiceError("authorization_missing", "GitHub 授权已取消或不存在，请重新开始。")
            flow["polling"] = False
            now = self.clock()
            error = str(data.get("error") or "")
            if error == "authorization_pending":
                flow["nextPollAt"] = now + interval
                return {"ok": True, "state": error, "flowId": flow_id, "retryAfter": interval}
            if error == "slow_down":
                interval += max(5, _safe_int(data.get("interval")))
                flow["interval"] = interval
                flow["nextPollAt"] = now + interval
                return {"ok": True, "state": "authorization_pending", "flowId": flow_id, "retryAfter": interval}
            if error in {"access_denied", "expired_token", "incorrect_device_code"}:
                code = "authorization_denied" if error == "access_denied" else "authorization_expired"
                message = "你已拒绝 GitHub 授权。" if error == "access_denied" else "GitHub 授权已超时，请重新开始。"
                raise self._finish_authorization_failure(
                    flow_id,
                    GitHubServiceError(code, message),
                    stage="token_exchange",
                )
            if error:
                raise self._finish_authorization_failure(
                    flow_id,
                    GitHubServiceError(
                        "authorization_failed",
                        str(data.get("error_description") or "GitHub 授权失败。"),
                    ),
                    stage="token_exchange",
                )
            token = str(data.get("access_token") or "")
            if not token:
                flow["nextPollAt"] = now + interval
                raise GitHubServiceError("invalid_response", "GitHub 未返回有效登录凭证。")
            try:
                self.token_store.set(token)
            except Exception as exc:
                self._delete_token_quietly()
                fallback = exc if isinstance(exc, GitHubServiceError) else GitHubServiceError(
                    "secure_store_failed",
                    "GitHub 凭证无法写入 macOS Keychain。",
                )
                raise self._finish_authorization_failure(
                    flow_id,
                    fallback,
                    stage="keychain_write",
                ) from exc
            try:
                stored_token = self.token_store.get()
            except Exception as exc:
                self._delete_token_quietly()
                fallback = exc if isinstance(exc, GitHubServiceError) else GitHubServiceError(
                    "secure_store_failed",
                    "无法读取 macOS Keychain 中的 GitHub 凭证。",
                )
                raise self._finish_authorization_failure(
                    flow_id,
                    fallback,
                    stage="keychain_readback",
                ) from exc
            if not stored_token or not hmac.compare_digest(stored_token, token):
                self._delete_token_quietly()
                raise self._finish_authorization_failure(
                    flow_id,
                    GitHubServiceError(
                        "secure_store_failed",
                        "GitHub 凭证写入后无法从 macOS Keychain 读回。",
                    ),
                    stage="keychain_readback",
                )
            try:
                account = self._validated_account(stored_token)
            except Exception as exc:
                self._delete_token_quietly()
                self._clear_authentication_cache()
                raise self._finish_authorization_failure(
                    flow_id,
                    exc,
                    stage="account_validation",
                ) from exc
            self.pending_flows.pop(flow_id, None)
            self._authenticated_flow_id = flow_id
            self._mark_token_verified(stored_token, account)
            self._write_authorization_state("ready")
            return {"ok": True, "state": "ready", "authenticated": True, "account": dict(self._account_cache)}

    def cancel_authorization(self, flow_id: str) -> dict[str, Any]:
        flow_id = str(flow_id or "")
        with self._write_lock:
            self.pending_flows.pop(flow_id, None)
            if self._authenticated_flow_id == flow_id:
                self.token_store.delete()
                self._authenticated_flow_id = ""
                self._clear_authentication_cache()
                self._token_presence_cache = {"known": True, "present": False, "expiresAt": self.clock() + 30}
            self._write_authorization_state("cancelled")
        return {"ok": True, "state": "cancelled"}

    def logout(self) -> dict[str, Any]:
        with self._write_lock:
            self._auth_generation += 1
            self.pending_flows.clear()
            self.token_store.delete()
            self._authenticated_flow_id = ""
            self._clear_authentication_cache()
            self._token_presence_cache = {"known": True, "present": False, "expiresAt": self.clock() + 30}
            self._write_authorization_state("logged_out")
        return self.status(validate=False)

    def search_repositories(self, query: Any, *, page: Any = 1, per_page: Any = 20) -> dict[str, Any]:
        clean = re.sub(r"\s+", " ", str(query or "").strip())[:256]
        if len(clean) < 2:
            raise GitHubServiceError("query_invalid", "请输入至少 2 个字符搜索 GitHub 仓库。")
        page_num = max(1, min(100, _safe_int(page) or 1))
        page_size = max(1, min(50, _safe_int(per_page) or 20))
        data, _ = self._authenticated_request(
            "GET",
            "/search/repositories",
            query={"q": clean, "sort": "stars", "order": "desc", "page": page_num, "per_page": page_size},
        )
        items = data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else []
        repositories = [public_repository(repo) for repo in items if isinstance(repo, dict) and not repo.get("private")]
        total = _safe_int(data.get("total_count")) if isinstance(data, dict) else len(repositories)
        self._annotate_duplicates(repositories)
        return {
            "ok": True,
            "query": clean,
            "page": page_num,
            "perPage": page_size,
            "total": total,
            "hasNext": page_num * page_size < min(total, 1000),
            "repositories": repositories,
        }

    def starred_repositories(self, *, page: Any = 1, per_page: Any = 50) -> dict[str, Any]:
        page_num = max(1, _safe_int(page) or 1)
        page_size = max(1, min(100, _safe_int(per_page) or 50))
        data, headers = self._authenticated_request(
            "GET",
            "/user/starred",
            query={"page": page_num, "per_page": page_size, "sort": "created", "direction": "desc"},
        )
        items = data if isinstance(data, list) else []
        repositories = [public_repository(repo) for repo in items if isinstance(repo, dict) and not repo.get("private")]
        self._annotate_duplicates(repositories)
        return {
            "ok": True,
            "page": page_num,
            "perPage": page_size,
            "hasNext": 'rel="next"' in str(headers.get("link") or ""),
            "repositories": repositories,
        }

    def _registry(self) -> dict[str, Any]:
        data = _read_json(self.registry_path, {"version": 1, "repositories": []})
        if not isinstance(data.get("repositories"), list):
            data["repositories"] = []
        return data

    def _registry_match(self, repository_id: Any, full_name: Any) -> dict[str, Any] | None:
        wanted_id = _safe_int(repository_id)
        wanted_name = normalize_owner_repo(full_name)
        fallback = None
        for item in self._registry()["repositories"]:
            if not isinstance(item, dict):
                continue
            if wanted_id and _safe_int(item.get("repositoryId")) == wanted_id:
                return item
            if wanted_name and normalize_owner_repo(item.get("fullName")) == wanted_name:
                fallback = item
        return fallback

    def _annotate_duplicates(self, repositories: list[dict[str, Any]]) -> None:
        records = [item for item in self._registry()["repositories"] if isinstance(item, dict)]
        by_id = {_safe_int(item.get("repositoryId")): item for item in records if _safe_int(item.get("repositoryId"))}
        by_name = {normalize_owner_repo(item.get("fullName")): item for item in records if normalize_owner_repo(item.get("fullName"))}
        for repo in repositories:
            match = by_id.get(_safe_int(repo.get("id"))) or by_name.get(normalize_owner_repo(repo.get("fullName")))
            repo["ingested"] = bool(match)
            repo["assetPath"] = str(match.get("assetPath") or "") if match else ""

    @contextlib.contextmanager
    def _write_transaction(self):
        self.github_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.github_root / "repositories.lock"
        with self._write_lock:
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _fetch_repository(self, identity: Any) -> dict[str, Any]:
        repository_id = 0
        full_name = ""
        if isinstance(identity, dict):
            repository_id = _safe_int(identity.get("id") or identity.get("repositoryId"))
            full_name = normalize_owner_repo(identity.get("fullName") or identity.get("full_name") or identity.get("url"))
        else:
            full_name = normalize_owner_repo(identity)
        if repository_id:
            repo, _ = self._authenticated_request("GET", f"/repositories/{repository_id}")
        elif full_name:
            repo, _ = self._authenticated_request("GET", f"/repos/{full_name}")
        else:
            raise GitHubServiceError("repository_invalid", "GitHub 仓库标识无效。")
        if not isinstance(repo, dict):
            raise GitHubServiceError("invalid_response", "GitHub 仓库响应无效。")
        if repo.get("private"):
            raise GitHubServiceError("private_repository_unsupported", "当前版本不支持私有仓库入库。")
        if repository_id and _safe_int(repo.get("id")) != repository_id:
            raise GitHubServiceError("repository_mismatch", "GitHub 仓库 ID 校验失败。")
        return repo

    def _repository_material(self, identity: Any) -> dict[str, Any]:
        repo = self._fetch_repository(identity)
        full_name = normalize_owner_repo(repo.get("full_name"))
        if not full_name:
            raise GitHubServiceError("repository_invalid", "GitHub 仓库缺少 owner/repo。")
        # Public repository contents stay anonymous so the GitHub App does not
        # need Contents permission. Private repositories are rejected above.
        readme, _ = self.api.request(
            "GET",
            f"/repos/{full_name}/readme",
            token="",
            accept="application/vnd.github.raw+json",
            raw=True,
            allow_not_found=True,
        )
        release, _ = self.api.request(
            "GET",
            f"/repos/{full_name}/releases/latest",
            token="",
            allow_not_found=True,
        )
        readme_text = str(readme or "")[:MAX_README_CHARS]
        version = str(release.get("tag_name") or "") if isinstance(release, dict) else ""
        public = public_repository(repo)
        snapshot = {
            "readmeSha256": hashlib.sha256(readme_text.encode("utf-8")).hexdigest(),
            "version": version,
            "license": public["license"],
            "archived": public["archived"],
            "pushedAt": public["pushedAt"],
            "defaultBranch": public["defaultBranch"],
            "fullName": public["fullName"],
        }
        return {"repository": repo, "public": public, "readme": readme_text, "version": version, "snapshot": snapshot}

    def _vault_path(self) -> Path:
        raw = _read_simple_config(self.config_path, "vault", "path")
        if not raw:
            raise GitHubServiceError("vault_missing", "请先在扩展里识别或选择知识库。")
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_dir() or ".obsidian" in path.parts:
            raise GitHubServiceError("vault_invalid", "知识库路径无效，请重新识别。")
        return path

    def _asset_path(self, vault_path: Path, material: dict[str, Any], existing: dict[str, Any] | None) -> Path:
        if existing and existing.get("assetPath"):
            candidate = (vault_path / str(existing["assetPath"])).resolve()
            root = vault_path.resolve()
            if candidate == root or root not in candidate.parents or ".obsidian" in candidate.parts:
                raise GitHubServiceError("asset_path_invalid", "已登记的 GitHub 资产路径无效。")
            return candidate
        full_name = normalize_owner_repo(material["public"]["fullName"])
        slug = re.sub(r"[^a-z0-9]+", "-", full_name).strip("-")[:58] or f"github-{material['public']['id']}"
        date = datetime.now().strftime("%Y%m%d")
        repository_id = _safe_int(material["public"].get("id"))
        return vault_path / "知识资产" / "GitHub项目" / f"{date}-{slug}-{repository_id}.md"

    def _frontmatter_value(self, text: str, key: str) -> str:
        if not text.startswith("---\n"):
            return ""
        end = text.find("\n---", 4)
        if end < 0:
            return ""
        for raw in text[4:end].splitlines():
            if ":" not in raw:
                continue
            name, value = raw.split(":", 1)
            if name.strip() == key:
                return value.strip().strip("'\"")
        return ""

    def _find_vault_asset(self, vault_path: Path, repo: dict[str, Any]) -> Path | None:
        root = vault_path / "知识资产" / "GitHub项目"
        if not root.exists():
            return None
        repository_id = _safe_int(repo.get("id"))
        canonical = normalize_owner_repo(repo.get("full_name"))
        name_match = None
        for path in root.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            stored_id = _safe_int(self._frontmatter_value(text, "repository_id"))
            if repository_id and stored_id == repository_id:
                return path
            identities = {
                normalize_owner_repo(self._frontmatter_value(text, key))
                for key in ("repository_full_name", "repo", "source_url")
            }
            if canonical and canonical in identities:
                name_match = path
        return name_match

    def _snapshot_from_asset(self, path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {}
        marker = "\n## README 原文\n"
        readme = text.split(marker, 1)[1].strip() if marker in text else ""
        return {
            "readmeSha256": hashlib.sha256(readme.encode("utf-8")).hexdigest() if readme else "",
            "version": self._frontmatter_value(text, "latest_version"),
            "license": self._frontmatter_value(text, "license"),
            "archived": self._frontmatter_value(text, "status") == "archived",
            "pushedAt": self._frontmatter_value(text, "pushed_at"),
            "defaultBranch": self._frontmatter_value(text, "default_branch"),
            "fullName": self._frontmatter_value(text, "repository_full_name") or self._frontmatter_value(text, "title"),
        }

    def _asset_id(self, repository_id: int, existing: dict[str, Any] | None) -> str:
        if existing and existing.get("assetId"):
            return str(existing["assetId"])
        return f"{datetime.now().strftime('%Y%m%d')}-github-{repository_id}"

    def _render_asset(
        self,
        material: dict[str, Any],
        *,
        asset_id: str,
        ingest_intent: str,
        ingested_date: str,
        derived_from: list[str] | None = None,
    ) -> str:
        repo = material["public"]
        today = datetime.now().strftime("%Y-%m-%d")
        summary = re.sub(r"\s+", " ", repo["description"]).strip()[:80] or f"{repo['fullName']} GitHub 项目"
        status = "archived" if repo["archived"] else "active"
        weight = 0 if repo["archived"] else 100
        derived = [str(item) for item in (derived_from or []) if str(item).strip()]
        yaml_derived = json.dumps(derived, ensure_ascii=False)
        escaped = lambda value: str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        readme = material["readme"].strip() or "_仓库未提供 README。_"
        return f'''---
id: "{escaped(asset_id)}"
type: github_project
asset_family: github_project
source_media: github
ingest_intent: {ingest_intent}
title: "{escaped(repo['fullName'])}"
source_url: "{escaped(repo['url'])}"
repo: "{escaped(repo['url'])}"
repository_id: {repo['id']}
repository_full_name: "{escaped(repo['fullName'])}"
github_managed: true
default_branch: "{escaped(repo['defaultBranch'])}"
latest_version: "{escaped(material['version'])}"
pushed_at: "{escaped(repo['pushedAt'])}"
language: "{escaped(repo['language'])}"
stars: {repo['stars']}
forks: {repo['forks']}
open_issues: {repo['openIssues']}
license: "{escaped(repo['license'])}"
description: "{escaped(repo['description'])}"
ingested: {ingested_date}
updated: {today}
tags: [github, project, unverified]
summary: "{escaped(summary)}"
confidence: medium
weight: {weight}
status: {status}
derived_from: {yaml_derived}
related: []
---

# {repo['fullName']}

## 基本信息

- 仓库地址：[{repo['url']}]({repo['url']})
- 主要语言：{repo['language'] or '未标注'}
- Star / Fork / Issue：{repo['stars']} / {repo['forks']} / {repo['openIssues']}
- 许可证：{repo['license'] or '未标注'}
- 最新版本：{material['version'] or '未发布 Release'}
- 默认分支：{repo['defaultBranch'] or '未标注'}
- 归档状态：{'已归档' if repo['archived'] else '活跃'}
- 最近推送：{repo['pushedAt'] or '未知'}

## 项目概述

{repo['description'] or 'GitHub 仓库未提供 description。'}

## README 原文

{readme}
'''

    def _update_index(self, vault_path: Path, asset_path: Path, material: dict[str, Any]) -> Path:
        index = vault_path / "index.md"
        text = index.read_text(encoding="utf-8") if index.exists() else "# 知识库索引\n"
        repo = material["public"]
        summary = re.sub(r"\s+", " ", repo["description"]).strip()[:80] or f"{repo['fullName']} GitHub 项目"
        entry = f"- [[{asset_path.stem}|{repo['fullName']}]] — {summary} `#github` `#project`"
        lines = [line for line in text.splitlines() if f"[[{asset_path.stem}|" not in line]
        if not lines or lines[0] != "# 知识库索引":
            lines.insert(0, "# 知识库索引")
        asset_count = sum(1 for _ in (vault_path / "知识资产").glob("**/*.md"))
        meta = f"> 最后更新：{datetime.now().strftime('%Y-%m-%d')} | 资产总数：{asset_count}"
        if len(lines) > 1 and lines[1].startswith("> 最后更新："):
            lines[1] = meta
        else:
            lines.insert(1, meta)
        heading = "## GitHub项目 / 网页剪藏 / 代码模块"
        try:
            index_at = lines.index(heading) + 1
        except ValueError:
            lines.extend(["", heading])
            index_at = len(lines)
        while index_at < len(lines) and not lines[index_at].strip():
            index_at += 1
        lines.insert(index_at, entry)
        index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return index

    def _frontmatter_list(self, text: str, key: str) -> list[str]:
        raw = self._frontmatter_value(text, key)
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def _refresh_unmanaged_asset(self, old_text: str, material: dict[str, Any]) -> str:
        repo = material["public"]
        fields = {
            "title": f'"{repo["fullName"]}"',
            "source_url": f'"{repo["url"]}"',
            "repo": f'"{repo["url"]}"',
            "repository_id": str(repo["id"]),
            "repository_full_name": f'"{repo["fullName"]}"',
            "default_branch": f'"{repo["defaultBranch"]}"',
            "latest_version": f'"{material["version"]}"',
            "pushed_at": f'"{repo["pushedAt"]}"',
            "language": f'"{repo["language"]}"',
            "stars": str(repo["stars"]),
            "forks": str(repo["forks"]),
            "open_issues": str(repo["openIssues"]),
            "license": f'"{repo["license"]}"',
            "description": json.dumps(repo["description"], ensure_ascii=False),
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "weight": "0" if repo["archived"] else "100",
            "status": "archived" if repo["archived"] else "active",
        }
        end = old_text.find("\n---", 4) if old_text.startswith("---\n") else -1
        if end < 0:
            raise GitHubServiceError("asset_invalid", "GitHub 项目缺少有效 frontmatter，无法安全刷新。")
        frontmatter = old_text[4:end]
        for key, value in fields.items():
            pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*.*$")
            line = f"{key}: {value}"
            if pattern.search(frontmatter):
                frontmatter = pattern.sub(line, frontmatter, count=1)
            else:
                frontmatter = frontmatter.rstrip() + "\n" + line
        body = old_text[end + 4:].lstrip("\n")
        body = re.sub(r"(?m)^#\s+[^\n]+", f"# {repo['fullName']}", body, count=1)
        readme_section = "## README 原文\n\n" + (material["readme"].strip() or "_仓库未提供 README。_")
        marker = re.search(r"(?m)^## README 原文\s*$", body)
        if marker:
            next_heading = re.search(r"(?m)^##\s+", body[marker.end():])
            section_end = marker.end() + next_heading.start() if next_heading else len(body)
            body = body[:marker.start()] + readme_section + "\n\n" + body[section_end:].lstrip("\n")
        else:
            body = body.rstrip() + "\n\n" + readme_section + "\n"
        return "---\n" + frontmatter.rstrip() + "\n---\n\n" + body.rstrip() + "\n"

    def _save_registry(self, material: dict[str, Any], asset_path: Path, vault_path: Path, asset_id: str, *, ingest_intent: str) -> dict[str, Any]:
        data = self._registry()
        repo = material["public"]
        existing = None
        fallback = None
        for item in data["repositories"]:
            if not isinstance(item, dict):
                continue
            if _safe_int(item.get("repositoryId")) == repo["id"]:
                existing = item
                break
            if normalize_owner_repo(item.get("fullName")) == normalize_owner_repo(repo["fullName"]):
                fallback = item
        existing = existing or fallback
        record = existing if existing is not None else {}
        record.update({
            "repositoryId": repo["id"],
            "fullName": repo["fullName"],
            "canonicalName": normalize_owner_repo(repo["fullName"]),
            "repositoryUrl": repo["url"],
            "assetId": asset_id,
            "assetPath": str(asset_path.relative_to(vault_path)),
            "snapshot": material["snapshot"],
            "ingestIntent": ingest_intent,
            "updatedAt": datetime.now().isoformat(),
        })
        if existing is None:
            data["repositories"].append(record)
        data["updatedAt"] = datetime.now().isoformat()
        _atomic_json(self.registry_path, data)
        return record

    def ingest_repository(
        self,
        identity: Any,
        *,
        ingest_intent: str = "manual",
        derived_from: list[str] | None = None,
    ) -> dict[str, Any]:
        if ingest_intent not in {"manual", "derived_ingest"}:
            raise GitHubServiceError("ingest_intent_invalid", "GitHub 入库类型无效。")
        material = self._repository_material(identity)
        with self._write_transaction():
            return self._ingest_material(
                material,
                ingest_intent=ingest_intent,
                derived_from=derived_from,
            )

    def _ingest_material(
        self,
        material: dict[str, Any],
        *,
        ingest_intent: str,
        derived_from: list[str] | None,
    ) -> dict[str, Any]:
        vault_path = self._vault_path()
        with vault_write_transaction(vault_path):
            return self._ingest_material_locked(
                material,
                ingest_intent=ingest_intent,
                derived_from=derived_from,
                vault_path=vault_path,
            )

    def _ingest_material_locked(
        self,
        material: dict[str, Any],
        *,
        ingest_intent: str,
        derived_from: list[str] | None,
        vault_path: Path,
    ) -> dict[str, Any]:
        repo = material["public"]
        existing = self._registry_match(repo["id"], repo["fullName"])
        if existing is None:
            migrated_path = self._find_vault_asset(vault_path, material["repository"])
            if migrated_path:
                migration_material = dict(material)
                migration_material["snapshot"] = self._snapshot_from_asset(migrated_path)
                existing = self._save_registry(
                    migration_material,
                    migrated_path,
                    vault_path,
                    self._frontmatter_value(migrated_path.read_text(encoding="utf-8", errors="ignore"), "id")
                    or self._asset_id(repo["id"], None),
                    ingest_intent=ingest_intent,
                )
        asset_path = self._asset_path(vault_path, material, existing)
        asset_id = self._asset_id(repo["id"], existing)
        if existing and asset_path.exists():
            return {
                "ok": True,
                "state": "existing",
                "repository": repo,
                "assetPath": str(asset_path.relative_to(vault_path)),
                "deduplicated": True,
                "changed": False,
                "refreshAvailable": existing.get("snapshot") != material["snapshot"],
                "autoStar": {"attempted": False, "ok": True},
            }
        ingested_date = datetime.now().strftime("%Y-%m-%d")
        if asset_path.exists():
            match = re.search(r"(?m)^ingested:\s*([^\s]+)", asset_path.read_text(encoding="utf-8", errors="ignore"))
            if match:
                ingested_date = match.group(1)
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._render_asset(
            material,
            asset_id=asset_id,
            ingest_intent=ingest_intent,
            ingested_date=ingested_date,
            derived_from=derived_from,
        )
        previous = asset_path.read_text(encoding="utf-8") if asset_path.exists() else ""
        changed = previous != content
        if changed:
            tmp = asset_path.with_suffix(asset_path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(asset_path)
            self._update_index(vault_path, asset_path, material)
        record = self._save_registry(material, asset_path, vault_path, asset_id, ingest_intent=ingest_intent)
        star_result = {"attempted": False, "ok": True}
        if changed and ingest_intent == "derived_ingest" and self.settings()["autoStar"]:
            star_result = self._star_after_success(repo["fullName"])
        return {
            "ok": True,
            "state": "created",
            "repository": repo,
            "assetPath": record["assetPath"],
            "deduplicated": bool(existing),
            "changed": changed,
            "autoStar": star_result,
        }

    def _star_after_success(self, full_name: str) -> dict[str, Any]:
        try:
            self._authenticated_request("PUT", f"/user/starred/{normalize_owner_repo(full_name)}")
            return {"attempted": True, "ok": True}
        except GitHubServiceError as exc:
            return {"attempted": True, "ok": False, "code": exc.code, "message": exc.message}

    def check_refresh(self, identity: Any) -> dict[str, Any]:
        material = self._repository_material(identity)
        repo = material["public"]
        existing = self._registry_match(repo["id"], repo["fullName"])
        if not existing:
            raise GitHubServiceError("asset_missing", "该 GitHub 项目尚未入库。")
        before = existing.get("snapshot") if isinstance(existing.get("snapshot"), dict) else {}
        after = material["snapshot"]
        labels = {
            "readmeSha256": "README",
            "version": "版本",
            "license": "License",
            "archived": "归档状态",
            "pushedAt": "最近推送",
            "defaultBranch": "默认分支",
            "fullName": "仓库路径",
        }
        changes = [
            {"field": key, "label": label, "before": before.get(key), "after": after.get(key)}
            for key, label in labels.items()
            if before.get(key) != after.get(key)
        ]
        if not changes:
            return {"ok": True, "state": "no_changes", "repository": repo, "changes": [], "message": "项目资料没有变化。"}
        refresh_id = uuid.uuid4().hex
        self.pending_refreshes[refresh_id] = {"material": material, "createdAt": self.clock()}
        return {
            "ok": True,
            "state": "confirmation_required",
            "refreshId": refresh_id,
            "repository": repo,
            "changes": changes,
            "message": f"发现 {len(changes)} 项变化，确认后一起更新。",
        }

    def confirm_refresh(self, refresh_id: str) -> dict[str, Any]:
        pending = self.pending_refreshes.pop(str(refresh_id or ""), None)
        if not pending:
            raise GitHubServiceError("refresh_missing", "刷新确认已失效，请重新检查。")
        if self.clock() - float(pending.get("createdAt") or 0) > 900:
            raise GitHubServiceError("refresh_expired", "刷新确认已超时，请重新检查。")
        material = pending["material"]
        with self._write_transaction():
            return self._confirm_refresh_material(material)

    def _confirm_refresh_material(self, material: dict[str, Any]) -> dict[str, Any]:
        vault_path = self._vault_path()
        with vault_write_transaction(vault_path):
            return self._confirm_refresh_material_locked(material, vault_path=vault_path)

    def _confirm_refresh_material_locked(
        self,
        material: dict[str, Any],
        *,
        vault_path: Path,
    ) -> dict[str, Any]:
        repo = material["public"]
        existing = self._registry_match(repo["id"], repo["fullName"])
        if not existing:
            raise GitHubServiceError("asset_missing", "该 GitHub 项目尚未入库。")
        asset_path = self._asset_path(vault_path, material, existing)
        if not asset_path.exists():
            raise GitHubServiceError("asset_missing", "已登记的 GitHub 项目文件不存在。")
        old_text = asset_path.read_text(encoding="utf-8", errors="ignore")
        ingested_date = self._frontmatter_value(old_text, "ingested") or datetime.now().strftime("%Y-%m-%d")
        ingest_intent = self._frontmatter_value(old_text, "ingest_intent") or "manual"
        if self._frontmatter_value(old_text, "github_managed").lower() == "true":
            content = self._render_asset(
                material,
                asset_id=self._asset_id(repo["id"], existing),
                ingest_intent=ingest_intent if ingest_intent in {"manual", "derived_ingest"} else "manual",
                ingested_date=ingested_date,
                derived_from=self._frontmatter_list(old_text, "derived_from"),
            )
        else:
            content = self._refresh_unmanaged_asset(old_text, material)
        changed = content != old_text
        if changed:
            tmp = asset_path.with_suffix(asset_path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(asset_path)
            self._update_index(vault_path, asset_path, material)
        record = self._save_registry(
            material,
            asset_path,
            vault_path,
            self._asset_id(repo["id"], existing),
            ingest_intent=ingest_intent,
        )
        return {
            "ok": True,
            "state": "updated" if changed else "no_changes",
            "repository": repo,
            "assetPath": record["assetPath"],
            "changed": changed,
        }

    def cancel_refresh(self, refresh_id: str) -> dict[str, Any]:
        self.pending_refreshes.pop(str(refresh_id or ""), None)
        return {"ok": True, "state": "cancelled"}

    def create_import_batch(self, repositories: Any) -> dict[str, Any]:
        if not isinstance(repositories, list) or not repositories:
            raise GitHubServiceError("selection_empty", "请至少选择一个 GitHub Star。")
        clean: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for item in repositories:
            if not isinstance(item, dict):
                continue
            repository_id = _safe_int(item.get("id") or item.get("repositoryId"))
            full_name = normalize_owner_repo(item.get("fullName") or item.get("full_name") or item.get("url"))
            if repository_id and repository_id in seen_ids:
                continue
            if full_name and full_name in seen_names:
                continue
            if not repository_id and not full_name:
                continue
            clean.append({"id": repository_id, "fullName": full_name})
            if repository_id:
                seen_ids.add(repository_id)
            if full_name:
                seen_names.add(full_name)
        if not clean:
            raise GitHubServiceError("selection_invalid", "选择的 GitHub 仓库无效。")
        batch_id = uuid.uuid4().hex
        batch = {
            "id": batch_id,
            "state": "queued",
            "total": len(clean),
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": False,
            "items": clean,
            "results": [],
        }
        self.import_batches[batch_id] = batch
        return self.public_batch(batch)

    def public_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in batch.items() if key not in {"items", "cancelled"}}

    def cancel_import_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.import_batches.get(str(batch_id or ""))
        if not batch:
            raise GitHubServiceError("batch_missing", "导入任务不存在。")
        batch["cancelled"] = True
        if batch["state"] == "queued":
            batch["state"] = "cancelled"
        return self.public_batch(batch)


def register_derived_repository(
    repository: dict[str, Any],
    asset_path: Path | str,
    vault_path: Path | str,
    *,
    readme: str = "",
    version: str = "",
    runtime_root: Path | str | None = None,
    token_store: Any | None = None,
    api: GitHubAPI | None = None,
) -> dict[str, Any]:
    """Core derivation hook called only after the asset and index write succeed."""
    service = GitHubService(runtime_root=runtime_root, token_store=token_store, api=api)
    public = public_repository(repository)
    if not public["id"] or not normalize_owner_repo(public["fullName"]):
        raise GitHubServiceError("repository_invalid", "派生结果缺少 GitHub repository ID 或 owner/repo。")
    vault = Path(vault_path).resolve()
    asset = Path(asset_path).resolve()
    if vault not in asset.parents or ".obsidian" in asset.parts:
        raise GitHubServiceError("asset_path_invalid", "派生资产路径无效。")
    match = service._registry_match(public["id"], public["fullName"])
    snapshot = match.get("snapshot") if isinstance(match, dict) and isinstance(match.get("snapshot"), dict) else None
    material = {"public": public, "snapshot": snapshot or {
        "readmeSha256": hashlib.sha256(str(readme or "").encode("utf-8")).hexdigest() if readme else "",
        "version": str(version or ""),
        "license": public["license"],
        "archived": public["archived"],
        "pushedAt": public["pushedAt"],
        "defaultBranch": public["defaultBranch"],
        "fullName": public["fullName"],
    }}
    asset_id = ""
    try:
        text = asset.read_text(encoding="utf-8", errors="ignore")
        id_match = re.search(r'(?m)^id:\s*["\']?([^"\'\n]+)', text)
        asset_id = id_match.group(1).strip() if id_match else ""
    except OSError:
        pass
    with service._write_transaction():
        record = service._save_registry(
            material,
            asset,
            vault,
            asset_id or service._asset_id(public["id"], match),
            ingest_intent="derived_ingest",
        )
    star_result = {"attempted": False, "ok": True}
    if service.settings()["autoStar"]:
        star_result = service._star_after_success(public["fullName"])
    return {"ok": True, "record": record, "autoStar": star_result}
