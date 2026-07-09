"""
config_loader.py — 读取并校验 ~/.agent-wiki/config.toml

设计原则：
- 缺配置时报错明确，告诉用户具体去填哪一行
- 不读 cookie（cookie 是动态的，由 downloader 自己读）
- API key 字段不入日志、不入异常消息

公共契约：
    load_config(path=None) -> Config
    Config.provider
    Config.ark_api_key / .ark_endpoint  # 字节跳动火山方舟 Ark 凭据/端点
    Config.analyzer_model / .analyzer_fallback / .strategy_model
    Config.quality_params(quality) -> dict
    Config.vault_path / .vault_relative_root
    Config.cookie_path
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore


def default_bridge_root() -> Path:
    """Runtime root for local state.

    Tests can override this with AGENT_WIKI_HOME without touching the
    user's real config/cookie files.
    """
    raw = os.environ.get("AGENT_WIKI_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".agent-wiki"


def default_config_path() -> Path:
    return default_bridge_root() / "config.toml"


DEFAULT_CONFIG_PATH = default_config_path()
DEFAULT_PROVIDER = "doubao"
DEFAULT_DOUBAO_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3"
TRUSTED_ARK_HOSTS = {"ark.cn-beijing.volces.com"}


def _normalize_provider(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "ark": "doubao",
        "ark_api": "doubao",
        "doubao_api": "doubao",
        "normal_ark": "doubao",
        # Agent Plan 曾验证过 inline base64 小视频路径，但不再作为产品运行通道。
        "agent_plan": "doubao",
        "agentplan": "doubao",
        "volcengine-agent-plan": "doubao",
        "volcengine_agent": "doubao",
        "ark_agent_plan": "doubao",
        "volcengine_agent_plan": "doubao",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized == "doubao" else DEFAULT_PROVIDER


class ConfigError(Exception):
    """配置错误。message 不应包含敏感字段值。"""


def _validate_ark_endpoint(value: object) -> str:
    endpoint = str(value or DEFAULT_DOUBAO_ENDPOINT).strip().rstrip("/")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError("Ark endpoint 必须是有效的 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ConfigError("Ark endpoint 不能包含账号密码")
    if parsed.hostname.lower() not in TRUSTED_ARK_HOSTS:
        raise ConfigError("Ark endpoint 必须使用可信 Ark 官方域名")
    if endpoint.endswith("/api/plan/v3"):
        raise ConfigError("Agent Plan endpoint 不能作为普通 Ark API 使用")
    return endpoint


@dataclass
class Config:
    # ark
    ark_api_key: str
    ark_endpoint: str
    # models
    analyzer_model: str
    analyzer_fallback: str
    strategy_model: str
    # analysis
    default_quality: str
    balanced_target_frames: int
    quality_target_frames: int
    fps_min: float
    fps_max: float
    file_active_timeout_sec: int
    # douyin
    cookie_path: Path
    # vault
    vault_path: Path
    vault_relative_root: str
    # server (预留)
    server_enabled: bool
    server_host: str
    server_port: int
    # 元数据
    config_file: Path
    provider: str = DEFAULT_PROVIDER
    doubao_api_key: str = ""
    doubao_endpoint: str = DEFAULT_DOUBAO_ENDPOINT
    agent_plan_api_key: str = ""
    agent_plan_endpoint: str = ""
    files_api_key: str = ""
    files_endpoint: str = DEFAULT_DOUBAO_ENDPOINT
    response_timeout_sec: int = 900
    chunk_concurrency: int = 2

    # 计算属性（不暴露给 toml）
    @property
    def bridge_root(self) -> Path:
        return self.config_file.parent

    def quality_params(self, quality: str) -> dict:
        """返回指定质量档位的参数。

        quality: 'balanced' | 'quality'
        """
        if quality not in ("balanced", "quality"):
            raise ConfigError(
                f"未知 quality 档位: '{quality}'，只支持 'balanced' 或 'quality'"
            )
        target = (
            self.quality_target_frames
            if quality == "quality"
            else self.balanced_target_frames
        )
        return {
            "target_frames": target,
            "fps_min": self.fps_min,
            "fps_max": self.fps_max,
        }


def _get(d: dict, *keys, default=None, required=False, config_file=None):
    """安全地从嵌套 dict 取值。"""
    cur = d
    path = []
    for k in keys:
        path.append(k)
        if not isinstance(cur, dict) or k not in cur:
            if required:
                raise ConfigError(
                    f"配置缺失：[{'.'.join(path[:-1])}].{path[-1]}"
                    + (f"（{config_file}）" if config_file else "")
                )
            return default
        cur = cur[k]
    return cur


def load_config(path: Optional[Path] = None) -> Config:
    """读取并校验 config.toml。

    Raises:
        ConfigError: 文件不存在 / 必填项为空 / 路径无效
    """
    config_file = Path(path) if path else DEFAULT_CONFIG_PATH
    config_file = config_file.expanduser().resolve()

    if not config_file.exists():
        raise ConfigError(
            f"配置文件不存在：{config_file}\n"
            f"请先运行：python3 deps/douyin/scripts/config_loader.py init 生成模板"
        )

    with config_file.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"配置文件格式错误：{config_file}\n{e}")

    provider = _normalize_provider(
        _get(data, "provider", "active", default=DEFAULT_PROVIDER, config_file=config_file)
    )

    # provider / credentials
    doubao_api_key = _get(data, "ark", "api_key", default="", config_file=config_file)
    doubao_endpoint = _get(
        data,
        "ark",
        "endpoint",
        default=DEFAULT_DOUBAO_ENDPOINT,
    )
    doubao_endpoint = _validate_ark_endpoint(doubao_endpoint)
    # 保留字段只为读旧配置/测试兼容，不参与运行。不要把旧 Agent Plan key
    # 自动迁移到普通 Ark key，避免用户误用不同权限体系的密钥。
    agent_plan_api_key = _get(data, "agent_plan", "api_key", default="", config_file=config_file)
    agent_plan_endpoint = _get(data, "agent_plan", "endpoint", default="")
    ark_api_key = doubao_api_key
    ark_endpoint = doubao_endpoint
    files_api_key = doubao_api_key
    files_endpoint = doubao_endpoint
    key_section = "ark"
    key_name = "火山方舟 API Key"
    if not ark_api_key or ark_api_key.strip() == "":
        raise ConfigError(
            f"{key_name} 未配置。\n"
            f"请编辑 {config_file}，在 [{key_section}] 段填入 api_key。\n"
            f"Agent Plan Key 不会自动当作普通 Ark Key 使用。"
        )

    # models
    analyzer_model = _get(
        data,
        "models",
        "analyzer",
        default="doubao-seed-2-0-lite-260428",
    )
    analyzer_fallback = _get(
        data,
        "models",
        "analyzer_fallback",
        default="doubao-seed-2-0-mini-260428",
    )
    strategy_model = _get(
        data,
        "models",
        "strategy",
        default=analyzer_fallback or "doubao-seed-2-0-mini-260428",
    )

    # analysis
    configured_quality = _get(data, "analysis", "default_quality", default="quality")
    if configured_quality not in ("balanced", "quality"):
        raise ConfigError(
            f"[analysis].default_quality 必须是 'balanced' 或 'quality'，"
            f"实际：'{configured_quality}'"
        )
    # P0 产品路径固定走 quality。balanced 只保留为内部调试参数，
    # 旧配置里的 balanced 不应影响 Agent 入库质量。
    default_quality = "quality"
    balanced_target_frames = int(
        _get(data, "analysis", "balanced_target_frames", default=240)
    )
    quality_target_frames = int(
        _get(data, "analysis", "quality_target_frames", default=1250)
    )
    fps_min = float(_get(data, "analysis", "fps_min", default=0.2))
    fps_max = float(_get(data, "analysis", "fps_max", default=5.0))
    file_active_timeout_sec = int(
        _get(data, "analysis", "file_active_timeout_sec", default=120)
    )
    response_timeout_sec = int(
        _get(data, "analysis", "response_timeout_sec", default=900)
    )
    try:
        chunk_concurrency = int(_get(data, "analysis", "chunk_concurrency", default=2))
    except (TypeError, ValueError):
        chunk_concurrency = 2
    chunk_concurrency = max(1, min(4, chunk_concurrency))

    # douyin
    cookie_path_raw = _get(
        data,
        "douyin",
        "cookie_path",
        default=str(default_bridge_root() / "cookie" / "douyin.txt"),
    )
    cookie_path = Path(cookie_path_raw).expanduser()

    # vault
    vault_path_raw = _get(data, "vault", "path", default="")
    if not vault_path_raw:
        raise ConfigError(
            f"Obsidian vault 路径未配置。\n"
            f"请编辑 {config_file}，在 [vault] 段填入 path。"
        )
    vault_path = Path(vault_path_raw).expanduser().resolve()
    if not vault_path.exists():
        raise ConfigError(
            f"Obsidian vault 路径不存在：{vault_path}\n"
            f"请检查 [vault].path 是否正确。"
        )
    if not vault_path.is_dir():
        raise ConfigError(f"[vault].path 不是目录：{vault_path}")

    vault_relative_root = _get(
        data,
        "vault",
        "relative_root",
        default="知识资产/知识入库",
    )

    # server（预留）
    server_enabled = bool(_get(data, "server", "enabled", default=False))
    server_host = _get(data, "server", "host", default="127.0.0.1")
    server_port = int(_get(data, "server", "port", default=8765))

    return Config(
        ark_api_key=ark_api_key,
        ark_endpoint=ark_endpoint,
        analyzer_model=analyzer_model,
        analyzer_fallback=analyzer_fallback,
        strategy_model=strategy_model,
        default_quality=default_quality,
        balanced_target_frames=balanced_target_frames,
        quality_target_frames=quality_target_frames,
        fps_min=fps_min,
        fps_max=fps_max,
        file_active_timeout_sec=file_active_timeout_sec,
        response_timeout_sec=response_timeout_sec,
        cookie_path=cookie_path,
        vault_path=vault_path,
        vault_relative_root=vault_relative_root,
        server_enabled=server_enabled,
        server_host=server_host,
        server_port=server_port,
        config_file=config_file,
        provider=provider,
        doubao_api_key=doubao_api_key,
        doubao_endpoint=doubao_endpoint,
        agent_plan_api_key=agent_plan_api_key,
        agent_plan_endpoint=agent_plan_endpoint,
        files_api_key=files_api_key,
        files_endpoint=files_endpoint,
        chunk_concurrency=chunk_concurrency,
    )


CONFIG_TEMPLATE = """\
# Agent-wiki 配置文件
# 由 Agent 自动初始化。⚠️ 标记的字段需要用户填写。
# 后期 Chrome 扩展会提供 GUI 编辑，目前手动填。

[provider]
# 固定使用字节跳动火山方舟 API。旧 Agent Plan 配置会回落为 doubao。
active = "doubao"

[ark]
# ⚠️ 火山方舟 API Key（provider.active = "doubao" 时必填）
api_key = ""
endpoint = "https://ark.cn-beijing.volces.com/api/v3"

[models]
# 拆解模型（Seed 2.0 Lite，复刻信息量最强）
analyzer = "doubao-seed-2-0-lite-260428"
# 长视频概览与分段策略模型（Mini，成本低；只做粗看、决策和 JSON 修复）
strategy = "doubao-seed-2-0-mini-260428"
# 备用模型（Mini，保留兼容字段）
analyzer_fallback = "doubao-seed-2-0-mini-260428"

[analysis]
# 默认质量档固定为 quality：优先 5fps，超过 1250 帧安全目标才下调 fps；
# 1280 是火山硬上限，项目侧留 30 帧冗余。
# balanced 仅保留为调试兼容档。
default_quality = "quality"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120
# Responses API 分析超时；防止网络/代理/云端异常时无限占用 worker。
response_timeout_sec = 900
chunk_concurrency = 2

[douyin]
# Cookie 文件路径（由 Chrome 扩展自动写入）
cookie_path = "~/.agent-wiki/cookie/douyin.txt"

[vault]
# ⚠️ Obsidian 仓库根目录（必填）
path = ""
# 默认知识资产相对路径。当前 Douyin 工具会按 ingest_intent 写入
# 知识资产/知识入库 或 知识资产/创作模式。
relative_root = "知识资产/知识入库"

[server]
# Agent 控制服务；task_concurrency 控制同时处理多少个入库任务。
enabled = false
host = "127.0.0.1"
port = 8765
task_concurrency = 2
"""


def write_template(target: Optional[Path] = None, force: bool = False) -> Path:
    """生成空配置模板到指定路径。已存在则不覆盖（除非 force=True）。"""
    target = Path(target) if target else default_config_path()
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return target
    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return target


if __name__ == "__main__":
    # 命令行：python3 -m scripts.config_loader init  → 写模板
    #         python3 -m scripts.config_loader check → 验证当前配置
    import sys
    if len(sys.argv) < 2 or sys.argv[1] == "check":
        try:
            cfg = load_config()
            print(f"✓ 配置 OK：{cfg.config_file}")
            print(f"  provider: {cfg.provider}")
            print(f"  vault: {cfg.vault_path}")
            print(f"  model: {cfg.analyzer_model}")
            print(f"  quality: {cfg.default_quality}")
        except ConfigError as e:
            print(f"✗ 配置错误：{e}", file=sys.stderr)
            sys.exit(1)
    elif sys.argv[1] == "init":
        p = write_template()
        print(f"✓ 模板已写入：{p}")
        print(f"  请编辑该文件，填入 [ark].api_key 和 [vault].path")
