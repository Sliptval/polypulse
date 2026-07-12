"""Ручная проверка публикации: собирает тестовый алерт и шлёт его.

По умолчанию уважает DRY_RUN из .env (true => только лог).
Флаг --to-admin шлёт сообщение в ADMIN_CHAT_ID вместо канала (безопасная проверка).

Запуск: venv/bin/python scripts/send_test_alert.py [--to-admin] [--no-llm]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402

from polypulse import texts  # noqa: E402
from polypulse.config import load_config  # noqa: E402
from polypulse.gamma import GammaClient  # noqa: E402
from polypulse.llm import DeepSeekClient  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to-admin", action="store_true",
                        help="слать в ADMIN_CHAT_ID вместо канала")
    parser.add_argument("--no-llm", action="store_true",
                        help="не звать DeepSeek, чистый шаблон")
    args = parser.parse_args()

    cfg = load_config()
    gamma = GammaClient()
    llm = DeepSeekClient(cfg)
    try:
        markets = await gamma.fetch_top_markets(limit=5)
        market = next((m for m in markets if m.yes_price is not None), None)
        if market is None:
            print("Не нашлось бинарного рынка в топе — странно, проверьте API.")
            return

        payload = {
            "old_prob": max(0.0, market.yes_price - 0.08),
            "new_prob": market.yes_price,
            "delta": 0.08,
            "window_min": 60,
            "direction": "up",
            "volume_24h": market.volume_24h,
        }
        comment = None
        if not args.no_llm:
            print("Запрашиваю комментарий у DeepSeek...")
            comment = await llm.generate_comment(
                "price_move", market.question, payload, market.volume_total, market.end_date
            )
            print(f"LLM-комментарий: {comment!r}\n")

        message = texts.format_channel_message(
            "price_move", market.question, market.slug, payload,
            market.volume_total, market.end_date, comment,
        )
        print("=== Сообщение ===")
        print(message)
        print("=================\n")

        if cfg.dry_run:
            print("DRY_RUN=true — никуда не отправляю.")
            return

        target = cfg.admin_chat_id if args.to_admin else cfg.telegram_channel_id
        bot = Bot(token=cfg.telegram_bot_token)
        try:
            await bot.send_message(target, message, parse_mode="HTML",
                                   disable_web_page_preview=True)
            print(f"Отправлено в {'ADMIN_CHAT_ID' if args.to_admin else 'канал'}.")
        finally:
            await bot.session.close()
    finally:
        await gamma.close()
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
