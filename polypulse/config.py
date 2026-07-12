"""Загрузка и валидация конфигурации из .env."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_ID",
    "ADMIN_CHAT_ID",
    "DEEPSEEK_API_KEY",
]


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_channel_id: str
    admin_chat_id: int
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    poll_interval_minutes: int
    price_delta_threshold: float
    price_delta_window_min: int
    volume_spike_multiplier: float
    min_market_volume_usd: float
    new_market_volume_usd: float
    max_alerts_per_hour: int
    dry_run: bool
    db_path: Path
    log_dir: Path


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        sys.exit(f"Ошибка конфигурации: {name}={raw!r} — не число")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"Ошибка конфигурации: {name}={raw!r} — не целое число")


def load_config(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or PROJECT_ROOT / ".env")

    missing = [v for v in REQUIRED_VARS if not os.getenv(v, "").strip()]
    if missing:
        sys.exit(
            "Ошибка конфигурации: в .env отсутствуют обязательные переменные: "
            + ", ".join(missing)
            + f"\nЗаполните их в {PROJECT_ROOT / '.env'} (шаблон — .env.example)."
        )

    admin_raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    try:
        admin_chat_id = int(admin_raw)
    except ValueError:
        sys.exit(f"Ошибка конфигурации: ADMIN_CHAT_ID={admin_raw!r} — должен быть числом (chat_id)")

    return Config(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
        telegram_channel_id=os.environ["TELEGRAM_CHANNEL_ID"].strip(),
        admin_chat_id=admin_chat_id,
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"].strip(),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
        poll_interval_minutes=_get_int("POLL_INTERVAL_MINUTES", 10),
        price_delta_threshold=_get_float("PRICE_DELTA_THRESHOLD", 0.07),
        price_delta_window_min=_get_int("PRICE_DELTA_WINDOW_MIN", 60),
        volume_spike_multiplier=_get_float("VOLUME_SPIKE_MULTIPLIER", 4),
        min_market_volume_usd=_get_float("MIN_MARKET_VOLUME_USD", 50000),
        new_market_volume_usd=_get_float("NEW_MARKET_VOLUME_USD", 100000),
        max_alerts_per_hour=_get_int("MAX_ALERTS_PER_HOUR", 6),
        dry_run=os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes"),
        db_path=PROJECT_ROOT / "data" / "polypulse.db",
        log_dir=PROJECT_ROOT / "logs",
    )
