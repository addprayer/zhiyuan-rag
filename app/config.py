"""Application configuration and local settings storage."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-chat",
    "llm_temperature": 0.2,
    "llm_max_tokens": 1200,
    "embedding_provider": "local",
    "embedding_base_url": "https://api.openai.com/v1",
    "embedding_api_key": "",
    "embedding_model": "text-embedding-3-small",
    "top_k": 5,
    "chunk_size": 520,
    "chunk_overlap": 80,
    "hybrid_alpha": 0.65,
    "seed_demo": True,
}

ENV_MAP = {
    "ZHIYUAN_LLM_BASE_URL": "llm_base_url",
    "ZHIYUAN_LLM_API_KEY": "llm_api_key",
    "ZHIYUAN_LLM_MODEL": "llm_model",
    "ZHIYUAN_LLM_TEMPERATURE": "llm_temperature",
    "ZHIYUAN_LLM_MAX_TOKENS": "llm_max_tokens",
    "ZHIYUAN_EMBEDDING_PROVIDER": "embedding_provider",
    "ZHIYUAN_EMBEDDING_BASE_URL": "embedding_base_url",
    "ZHIYUAN_EMBEDDING_API_KEY": "embedding_api_key",
    "ZHIYUAN_EMBEDDING_MODEL": "embedding_model",
    "ZHIYUAN_TOP_K": "top_k",
    "ZHIYUAN_CHUNK_SIZE": "chunk_size",
    "ZHIYUAN_CHUNK_OVERLAP": "chunk_overlap",
    "ZHIYUAN_HYBRID_ALPHA": "hybrid_alpha",
    "ZHIYUAN_SEED_DEMO": "seed_demo",
}

SECRET_KEYS = {"llm_api_key", "embedding_api_key"}


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return str(value)


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "已保存"
    return "••••" + secret[-4:]


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    data_dir: Path
    upload_dir: Path
    db_path: Path
    settings_path: Path
    samples_dir: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        raw = os.getenv("ZHIYUAN_DATA_DIR", "")
        data_dir = Path(raw).expanduser() if raw else BASE_DIR / "data"
        if not data_dir.is_absolute():
            data_dir = BASE_DIR / data_dir
        data_dir = data_dir.resolve()
        return cls(
            base_dir=BASE_DIR,
            data_dir=data_dir,
            upload_dir=data_dir / "uploads",
            db_path=data_dir / "zhiyuan.db",
            settings_path=data_dir / "settings.json",
            samples_dir=BASE_DIR / "samples",
        )

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


class SettingsStore:
    """Defaults < environment < local settings.json.

    The local file wins so the in-app settings panel behaves predictably.
    Environment variables are still useful for containers and first-run defaults.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.Lock()

    def _file_values(self) -> dict[str, Any]:
        if not self.config.settings_path.exists():
            return {}
        try:
            data = json.loads(self.config.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self) -> dict[str, Any]:
        values = dict(DEFAULTS)
        for env_name, key in ENV_MAP.items():
            raw = os.getenv(env_name)
            if raw is not None and raw != "":
                try:
                    values[key] = _coerce(raw, DEFAULTS[key])
                except (TypeError, ValueError):
                    pass
        for key, value in self._file_values().items():
            if key in DEFAULTS:
                try:
                    values[key] = _coerce(value, DEFAULTS[key])
                except (TypeError, ValueError):
                    pass
        return values

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            raw = self._file_values()
            for key, value in patch.items():
                if key in DEFAULTS and value is not None:
                    raw[key] = value
            self.config.settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config.settings_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.config.settings_path)
        return self.get()

    def public(self) -> dict[str, Any]:
        s = self.get()
        provider = s["embedding_provider"]
        return {
            "llm_base_url": s["llm_base_url"],
            "llm_model": s["llm_model"],
            "llm_temperature": s["llm_temperature"],
            "llm_max_tokens": s["llm_max_tokens"],
            "llm_configured": bool(s["llm_api_key"]),
            "llm_api_key_masked": _mask(s["llm_api_key"]),
            "embedding_provider": provider,
            "embedding_base_url": s["embedding_base_url"],
            "embedding_model": s["embedding_model"],
            "embedding_configured": provider == "local" or bool(s["embedding_api_key"]),
            "embedding_api_key_masked": _mask(s["embedding_api_key"]),
            "top_k": s["top_k"],
            "chunk_size": s["chunk_size"],
            "chunk_overlap": s["chunk_overlap"],
            "hybrid_alpha": s["hybrid_alpha"],
            "seed_demo": s["seed_demo"],
            "mode": "llm" if s["llm_api_key"] else "demo",
        }
