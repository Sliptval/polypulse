"""Правила детектирования аномалий -> список Alert."""
from __future__ import annotations

import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import db
from .config import Config
from .gamma import Market, parse_iso

log = logging.getLogger(__name__)

COOLDOWN_HOURS = 6
VOLUME_SPIKE_MIN_ABS_USD = 10_000
NEW_MARKET_MAX_AGE_H = 48
CLOSING_SOON_WINDOW_H = 48
CLOSING_PROB_RANGE = (0.35, 0.65)


@dataclass
class Alert:
    alert_type: str          # price_move | volume_spike | new_market | closing_soon
    market: Market
    payload: dict = field(default_factory=dict)
    score: float = 0.0       # для приоритизации при лимите канала


def _iso_minus(ts: str, minutes: int) -> str:
    dt = parse_iso(ts) or datetime.now(timezone.utc)
    return (dt - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect(
    conn: sqlite3.Connection, cfg: Config, now_ts: str, markets: list[Market]
) -> list[Alert]:
    """Прогоняет все правила по свежему списку рынков. Кулдауны здесь только
    проверяются; фиксирует их publisher после успешной отправки."""
    alerts: list[Alert] = []
    now = parse_iso(now_ts) or datetime.now(timezone.utc)

    for m in markets:
        vol_total = m.volume_total or 0.0
        vol_24h = m.volume_24h or 0.0

        # --- new_market: единственное правило, работающее и для небинарных ---
        created = parse_iso(m.created_at)
        if (
            created
            and (now - created) < timedelta(hours=NEW_MARKET_MAX_AGE_H)
            and vol_total >= cfg.new_market_volume_usd
            and not db.market_seen_before(conn, m.condition_id, now_ts)
            and not db.cooldown_active(conn, m.condition_id, "new_market", COOLDOWN_HOURS)
        ):
            hours_ago = max(1, int((now - created).total_seconds() // 3600))
            alerts.append(Alert(
                "new_market", m,
                payload={"hours_ago": hours_ago, "volume_total": vol_total,
                         "new_prob": m.yes_price},
                score=vol_total,
            ))

        if not m.is_binary or m.yes_price is None:
            continue
        if vol_total < cfg.min_market_volume_usd:
            continue

        # --- price_move ---
        then = db.snapshot_near(
            conn, m.condition_id,
            _iso_minus(now_ts, cfg.price_delta_window_min),
            tolerance_min=max(cfg.poll_interval_minutes * 2, 20),
        )
        if then is not None and then["yes_price"] is not None:
            delta = m.yes_price - then["yes_price"]
            if (
                abs(delta) >= cfg.price_delta_threshold
                and not db.cooldown_active(conn, m.condition_id, "price_move", COOLDOWN_HOURS)
            ):
                alerts.append(Alert(
                    "price_move", m,
                    payload={
                        "old_prob": then["yes_price"], "new_prob": m.yes_price,
                        "delta": delta,
                        "window_min": cfg.price_delta_window_min,
                        "direction": "up" if delta > 0 else "down",
                        "volume_24h": vol_24h,
                    },
                    score=abs(delta) * vol_24h,
                ))

        # --- volume_spike: прирост за окно vs медианный прирост за 24 ч ---
        if then is not None and then["volume_total"] is not None:
            window_delta = vol_total - then["volume_total"]
            deltas = [d for d in db.volume_deltas_24h(conn, m.condition_id, now_ts) if d > 0]
            if len(deltas) >= 6:  # мало истории — не судим
                median = statistics.median(deltas)
                if (
                    median > 0
                    and window_delta >= VOLUME_SPIKE_MIN_ABS_USD
                    and window_delta / median >= cfg.volume_spike_multiplier
                    and not db.cooldown_active(conn, m.condition_id, "volume_spike", COOLDOWN_HOURS)
                ):
                    alerts.append(Alert(
                        "volume_spike", m,
                        payload={
                            "window_delta": window_delta, "median_delta": median,
                            "multiplier": window_delta / median,
                            "window_min": cfg.price_delta_window_min,
                            "new_prob": m.yes_price, "volume_24h": vol_24h,
                        },
                        score=window_delta,
                    ))

        # --- closing_soon: не чаще 1 раза на рынок (за всю жизнь) ---
        end = parse_iso(m.end_date)
        if (
            end
            and timedelta(0) < (end - now) <= timedelta(hours=CLOSING_SOON_WINDOW_H)
            and CLOSING_PROB_RANGE[0] <= m.yes_price <= CLOSING_PROB_RANGE[1]
            and not db.ever_alerted(conn, m.condition_id, "closing_soon")
        ):
            hours_left = max(1, int((end - now).total_seconds() // 3600))
            alerts.append(Alert(
                "closing_soon", m,
                payload={"hours_left": hours_left, "new_prob": m.yes_price,
                         "volume_24h": vol_24h},
                score=vol_24h * (0.5 - abs(m.yes_price - 0.5)),
            ))

    if alerts:
        log.info("Детектор: %d кандидатов (%s)",
                 len(alerts), ", ".join(f"{a.alert_type}:{a.market.slug}" for a in alerts))
    return alerts
