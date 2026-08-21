"""Configuration loading. One source of truth: config/*.yaml plus .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"


class Cfg(dict):
    """Dict with dotted access:  cfg.get_path("tts.voice", default)."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(item) from exc


@dataclass
class Secrets:
    """Credentials. Never logged, never sent anywhere except their own API."""

    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_allowed_user_ids: list[int] = field(default_factory=list)
    github_token: str = ""
    notion_token: str = ""
    missing: list[str] = field(default_factory=list)

    def require(self, name: str) -> str:
        value = getattr(self, name, "")
        if not value:
            raise RuntimeError(
                f"Missing credential: {name.upper()}. Add it to .env — see CREDENTIALS.md"
            )
        return value

    def has(self, name: str) -> bool:
        return bool(getattr(self, name, ""))


def _parse_user_ids(raw: str) -> list[int]:
    """Comma/space-separated numeric Telegram user IDs. Anything that
    doesn't parse as an int is dropped rather than crashing config load —
    a malformed .env line should degrade to 'nobody's allowed', the safe
    default, not take the whole app down."""
    ids: list[int] = []
    for part in raw.replace(",", " ").split():
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def load_secrets() -> Secrets:
    load_dotenv(ROOT / ".env")
    s = Secrets(
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_api_id=os.getenv("TELEGRAM_API_ID", ""),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
        telegram_allowed_user_ids=_parse_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        notion_token=os.getenv("NOTION_TOKEN", ""),
    )
    for key in ("groq_api_key", "telegram_bot_token"):
        if not getattr(s, key):
            s.missing.append(key.upper())
    return s


def _expand(node: Any, values: dict[str, str]) -> Any:
    """Replace {placeholders} inside string values, recursively."""
    if isinstance(node, str):
        try:
            return node.format(**values)
        except (KeyError, IndexError, ValueError):
            return node
    if isinstance(node, dict):
        return {k: _expand(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand(v, values) for v in node]
    return node


def load_config() -> Cfg:
    with open(CONFIG_DIR / "jarvis.yaml", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    with open(CONFIG_DIR / "safety.yaml", encoding="utf-8") as fh:
        raw["safety_tiers"] = yaml.safe_load(fh) or {}

    identity = raw.get("identity", {})
    raw = _expand(
        raw,
        {
            "user_name": identity.get("user_name", "there"),
            "address_user_as": identity.get("address_user_as", ""),
            "name": identity.get("name", "Jalen"),
        },
    )
    DATA_DIR.mkdir(exist_ok=True)
    MODELS_DIR.mkdir(exist_ok=True)
    return Cfg(raw)


CONFIG = load_config()
SECRETS = load_secrets()
