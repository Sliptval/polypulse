"""PolyPulse: scheduler (сбор + детект + публикация) и Telegram-бот в одном процессе."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import time

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from polypulse import __version__, db
from polypulse.bot import AppState, build_dispatcher, setup_bot_commands
from polypulse.collector import collect_once
from polypulse.config import Config, load_config
from polypulse.detector import detect
from polypulse.gamma import GammaClient
from polypulse.llm import DeepSeekClient
from polypulse.publisher import Publisher

log = logging.getLogger("polypulse")

ADMIN_NOTIFY_COOLDOWN_SEC = 15 * 60


def setup_logging(cfg: Config) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(fmt)
    root.addHandler(stdout)

    file_handler = logging.handlers.RotatingFileHandler(
        cfg.log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.conn = db.connect(cfg.db_path)
        self.gamma = GammaClient()
        self.llm = DeepSeekClient(cfg)
        self.bot = Bot(token=cfg.telegram_bot_token)
        self.publisher = Publisher(cfg, self.conn, self.bot, self.llm)
        self.state = AppState()
        self._gamma_failures = 0
        self._last_admin_notify = 0.0

    async def notify_admin(self, text: str) -> None:
        """Сервисное сообщение владельцу, не чаще 1 раза в 15 минут."""
        now = time.monotonic()
        if now - self._last_admin_notify < ADMIN_NOTIFY_COOLDOWN_SEC:
            log.info("Сервисное уведомление подавлено (кулдаун): %s", text)
            return
        self._last_admin_notify = now
        try:
            await self.bot.send_message(self.cfg.admin_chat_id, text)
        except Exception as exc:  # noqa: BLE001 — канал уведомлений не должен ронять процесс
            log.error("Не удалось отправить сервисное сообщение админу: %s", exc)

    async def cycle(self) -> None:
        """Один цикл: снапшоты -> детектор -> публикация -> личные алерты."""
        try:
            ts, markets = await collect_once(self.conn, self.gamma)
            self._gamma_failures = 0
        except RuntimeError as exc:
            self._gamma_failures += 1
            log.error("Цикл сбора не удался (%d подряд): %s", self._gamma_failures, exc)
            if self._gamma_failures >= 2:
                await self.notify_admin(
                    f"⚠️ PolyPulse: Gamma API недоступен {self._gamma_failures} циклов подряд."
                )
            return

        self.state.last_cycle_ts = ts
        self.state.markets_monitored = len(markets)
        self.state.cycles_completed += 1

        try:
            alerts = detect(self.conn, self.cfg, ts, markets)
            published = await self.publisher.publish_alerts(alerts)
            personal = await self.publisher.send_personal_alerts(markets)
            if published or personal:
                log.info("Цикл %s: %d алертов в канал, %d личных", ts, published, personal)
        except Exception:
            log.exception("Ошибка в детекторе/публикации")
            await self.notify_admin("⚠️ PolyPulse: ошибка в детекторе/публикации, см. логи.")

    async def retention(self) -> None:
        deleted = db.delete_old_snapshots(self.conn, days=30)
        log.info("Ретенция: удалено %d старых снапшотов", deleted)

    async def weekly_vacuum(self) -> None:
        db.vacuum(self.conn)
        log.info("VACUUM выполнен")

    async def run(self) -> None:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(self.cycle, "interval",
                          minutes=self.cfg.poll_interval_minutes,
                          max_instances=1, coalesce=True)
        scheduler.add_job(self.retention, "cron", hour=3, minute=15)
        scheduler.add_job(self.weekly_vacuum, "cron", day_of_week="sun", hour=4, minute=0)
        scheduler.start()

        dp = build_dispatcher(self.cfg, self.conn, self.gamma, self.state)
        try:
            await setup_bot_commands(self.bot)
        except Exception as exc:  # noqa: BLE001 — меню команд не критично для запуска
            log.warning("Не удалось установить меню команд: %s", exc)

        await self.notify_admin(
            f"✅ PolyPulse запущен, версия {__version__}, DRY_RUN={self.cfg.dry_run}"
        )
        log.info("PolyPulse %s запущен: опрос каждые %d мин, DRY_RUN=%s",
                 __version__, self.cfg.poll_interval_minutes, self.cfg.dry_run)

        # первый цикл сразу, не дожидаясь интервала
        await self.cycle()

        try:
            await dp.start_polling(self.bot)
        finally:
            scheduler.shutdown(wait=False)
            await self.gamma.close()
            await self.llm.close()
            await self.bot.session.close()
            self.conn.close()


async def amain() -> None:
    cfg = load_config()
    setup_logging(cfg)
    app = App(cfg)
    try:
        await app.run()
    except Exception:
        log.exception("Необработанная ошибка верхнего уровня")
        await app.notify_admin("🔴 PolyPulse упал с необработанной ошибкой, systemd перезапустит. См. логи.")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
