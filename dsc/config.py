"""Configuration loading for dsc: env vars + TOML file + defaults.

Precedence: CLI flags > environment variables > config file > defaults.
The config file lives at ~/.config/dsc/config.toml. TOML parsing uses
tomllib on 3.11+, falls back to tomli if installed, then to a tiny
hand-rolled parser for flat `key = value` tables (strings, ints, bools).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _toml_bool(s: str) -> bool:
    v = s.strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(f"invalid TOML bool: {s!r}")


def _mini_toml(text: str) -> dict:
    """Parse a flat TOML document (no nested tables, no arrays)."""
    out: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue  # section headers ignored; we keep a flat namespace
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().strip('"')
        val = val.strip()
        # strip trailing inline comments (not inside quotes)
        if val and val[0] not in "\"'":
            val = val.split("#", 1)[0].strip()
        if val and val[0] == '"' and val[-1] == '"':
            out[key] = val[1:-1]
        elif val and val[0] == "'" and val[-1] == "'":
            out[key] = val[1:-1]
        elif val.lower() in ("true", "false"):
            out[key] = _toml_bool(val)
        else:
            try:
                out[key] = int(val)
            except ValueError:
                out[key] = val
    return out


def _load_toml_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            with open(path, "rb") as f:
                return tomllib.load(f)
        try:
            import tomli  # type: ignore

            with open(path, "rb") as f:
                return tomli.load(f)
        except ImportError:
            return _mini_toml(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


MODES = ("plan", "default", "yolo")
SANDBOXES = ("auto", "docker", "bwrap", "none")


@dataclass
class Config:
    """Resolved dsc configuration. All fields have safe defaults."""

    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    context_tokens: int = 96_000
    mode: str = "default"
    workspace_root: Path = field(default_factory=Path.cwd)
    search_backend: str = "duckduckgo"
    sandbox: str = "auto"
    allow_network_for_bash: bool = False
    max_turns: int = 60
    auto_yes: bool = False  # from --yes flag
    no_color: bool = False  # from --no-color / NO_COLOR

    @property
    def workspace(self) -> Path:
        return self.workspace_root.resolve()

    def validate(self) -> list[str]:
        """Return a list of validation problems (empty list = ok)."""
        problems: list[str] = []
        if self.mode not in MODES:
            problems.append(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.sandbox not in SANDBOXES:
            problems.append(f"sandbox must be one of {SANDBOXES}, got {self.sandbox!r}")
        if self.context_tokens < 4_000:
            problems.append("context_tokens too small (min 4000)")
        if self.max_turns < 1:
            problems.append("max_turns must be >= 1")
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            problems.append("base_url must start with http(s)://")
        return problems


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "dsc"
    return Path.home() / ".config" / "dsc"


def data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "dsc"
    return Path.home() / ".local" / "share" / "dsc"


def _from_env(c: Config) -> None:
    if c.api_key is None:
        c.api_key = os.environ.get("DSC_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    v = os.environ.get("DSC_BASE_URL")
    if v:
        c.base_url = v
    v = os.environ.get("DSC_MODEL")
    if v:
        c.model = v
    v = os.environ.get("DSC_CONTEXT_TOKENS")
    if v:
        try:
            c.context_tokens = int(v)
        except ValueError:
            pass
    v = os.environ.get("DSC_MODE")
    if v:
        c.mode = v
    v = os.environ.get("DSC_WORKSPACE")
    if v:
        c.workspace_root = Path(v).expanduser().resolve()
    v = os.environ.get("DSC_SEARCH_BACKEND")
    if v:
        c.search_backend = v
    v = os.environ.get("DSC_SANDBOX")
    if v:
        c.sandbox = v
    v = os.environ.get("DSC_ALLOW_NETWORK_FOR_BASH")
    if v is not None:
        c.allow_network_for_bash = v.lower() in ("1", "true", "yes")
    v = os.environ.get("DSC_USE_DOCKER")
    if v is not None:
        c.allow_docker = v.lower() in ("1", "true", "yes")


def load_config(argv_overrides: Optional[dict] = None) -> Config:
    """Build the effective config: file < env < CLI overrides dict."""
    c = Config()
    c.allow_docker = False
    file_cfg = _load_toml_file(config_dir() / "config.toml")

    def pick(key: str, fallback):
        return file_cfg.get(key, fallback)

    c.api_key = c.api_key or pick("api_key", None) or os.environ.get(
        "DEEPSEEK_API_KEY"
    )
    c.base_url = pick("base_url", c.base_url)
    c.model = pick("model", c.model)
    c.context_tokens = int(pick("context_tokens", c.context_tokens))
    c.mode = pick("mode", c.mode)
    c.search_backend = pick("search_backend", c.search_backend)
    c.sandbox = pick("sandbox", c.sandbox)
    c.allow_network_for_bash = bool(pick("allow_network_for_bash", c.allow_network_for_bash))
    wr = pick("workspace_root", None)
    if wr:
        c.workspace_root = Path(str(wr)).expanduser().resolve()

    _from_env(c)
    if argv_overrides:
        for k, v in argv_overrides.items():
            if v is not None:
                setattr(c, k, v)
    return c
