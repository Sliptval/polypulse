"""Публикация алертов в канал и личные алерты подписчикам."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from . import db, texts
from .config import Config
from .detector import Alert
from .gamma import Market
from .llm import DeepSeekClient

log = logging.getLogger(__name__)

PERSONAL_COOLDOWN_HOURS = 2


class Publisher:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bot: Bot,
                 llm: DeepSeekClient) -> None:
        self._cfg = cfg
        self._conn = conn
        self._bot = bot
        self._llm = llm

    async def publish_alerts(self, alerts: list[Alert]) -> int:
        """Отправляет алерты в канал с учётом лимита в час. Возвращает число публикаций."""
        if not alerts:
            return 0
        budget = self._cfg.max_alerts_per_hour - db.published_last_hour(self._conn)
        ranked = sorted(alerts, key=lambda a: a.score, reverse=True)
        sent = 0
        for alert in ranked:
            if sent >= max(budget, 0):
                log.info("Лимит канала (%d/ч) исчерпан, скип: %s %s (score=%.0f)",
                         self._cfg.max_alerts_per_hour, alert.alert_type,
                         alert.market.slug, alert.score)
                continue
            if await self._publish_one(alert):
                sent += 1
        return sent

    async def _publish_one(self, alert: Alert) -> bool:
        m = alert.market
        comment = await self._llm.generate_comment(
            alert.alert_type, m.question, alert.payload, m.volume_total, m.end_date
        )
        message = texts.format_channel_message(
            alert.alert_type, m.question, m.slug, alert.payload,
            m.volume_total, m.end_date, comment,
        )
        if self._cfg.dry_run:
            log.info("[DRY_RUN] Алерт %s / %s:\n%s", alert.alert_type, m.slug, message)
            db.insert_alert(self._conn, m.condition_id, alert.alert_type,
                            alert.payload, message, published=False)
            db.touch_cooldown(self._conn, m.condition_id, alert.alert_type)
            return True
        try:
            await self._bot.send_message(
                self._cfg.telegram_channel_id, message,
                parse_mode="HTML", disable_web_page_preview=True,
            )
        except TelegramAPIError as exc:
            log.error("Не удалось отправить алерт в канал (%s): %s", m.slug, exc)
            db.insert_alert(self._conn, m.condition_id, alert.alert_type,
                            alert.payload, message, published=False)
            return False
        db.insert_alert(self._conn, m.condition_id, alert.alert_type,
                        alert.payload, message, published=True)
        db.touch_cooldown(self._conn, m.condition_id, alert.alert_type)
        log.info("Опубликован алерт %s / %s", alert.alert_type, m.slug)
        return True

    async def send_personal_alerts(self, markets: list[Market]) -> int:
        """Личные алерты по подпискам: сдвиг >= threshold от точки последнего
        личного алерта (или от момента подписки), не чаще 1 раза в 2 часа."""
        by_cid = {m.condition_id: m for m in markets}
        sent = 0
        for sub in db.all_subscriptions(self._conn):
            m = by_cid.get(sub["condition_id"])
            if m is None or m.yes_price is None:
                continue
            state = db.personal_state(self._conn, sub["chat_id"], sub["condition_id"])
            if state is None:
                # точка отсчёта — текущая цена на момент первой проверки
                db.touch_personal_state(self._conn, sub["chat_id"], sub["condition_id"], m.yes_price)
                continue
            last_ts = db.parse_ts(state["last_ts"])
            if last_ts and (datetime.now(timezone.utc) - last_ts).total_seconds() < PERSONAL_COOLDOWN_HOURS * 3600:
                continue
            old_p = state["last_yes_price"]
            if old_p is None or abs(m.yes_price - old_p) < sub["threshold"]:
                continue
            message = texts.format_personal_message(m.question, m.slug, old_p, m.yes_price)
            if self._cfg.dry_run:
                log.info("[DRY_RUN] Личный алерт chat_id=%s:\n%s", sub["chat_id"], message)
            else:
                try:
                    await self._bot.send_message(sub["chat_id"], message,
                                                 parse_mode="HTML",
                                                 disable_web_page_preview=True)
                except TelegramForbiddenError:
                    log.info("chat_id=%s заблокировал бота, подписка остаётся", sub["chat_id"])
                    continue
                except TelegramAPIError as exc:
                    log.error("Личный алерт chat_id=%s не отправлен: %s", sub["chat_id"], exc)
                    continue
            db.touch_personal_state(self._conn, sub["chat_id"], sub["condition_id"], m.yes_price)
            sent += 1
        return sent
