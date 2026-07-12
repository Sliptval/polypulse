"""Telegram-бот (aiogram v3): команды + инлайн-кнопки.

Команды: /start /help /watch /unwatch /list /threshold /status
Кнопки: справка, список подписок, отписка в один тап.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import db, texts
from .config import Config
from .gamma import GammaClient

log = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 20
RATE_LIMIT_PER_MIN = 10

# slug: либо голый, либо из ссылки polymarket.com/event/<slug> или /market/<slug>
_SLUG_RE = re.compile(
    r"(?:polymarket\.com/(?:event|market)/)?([a-z0-9][a-z0-9-]*[a-z0-9])/?\s*$",
    re.IGNORECASE,
)


class AppState:
    """Живое состояние процесса — для /status."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self.last_cycle_ts: str | None = None
        self.markets_monitored = 0
        self.cycles_completed = 0


class RateLimiter:
    def __init__(self, per_minute: int = RATE_LIMIT_PER_MIN) -> None:
        self._per_minute = per_minute
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, chat_id: int) -> bool:
        now = time.monotonic()
        q = self._hits[chat_id]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self._per_minute:
            return False
        q.append(now)
        return True


def extract_slug(arg: str | None) -> str | None:
    if not arg:
        return None
    match = _SLUG_RE.search(arg.strip())
    return match.group(1).lower() if match else None


def _main_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📋 Мои подписки", callback_data="list"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ]
    ]
    if channel_id.startswith("@"):
        rows.insert(0, [InlineKeyboardButton(
            text="📡 Канал с алертами", url=f"https://t.me/{channel_id[1:]}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _list_keyboard(subs) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"❌ {s['slug'][:48]}",
                              callback_data=f"unwatch:{s['slug'][:56]}")]
        for s in subs
    ]
    rows.append([InlineKeyboardButton(text="❓ Помощь", callback_data="help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _list_text(subs) -> str:
    if not subs:
        return "Подписок нет. Отправьте /watch со ссылкой на рынок — пришлю личный алерт, когда вероятность сдвинется."
    lines = ["📋 <b>Ваши подписки</b> (кнопка — отписаться):", ""]
    for s in subs:
        lines.append(f"• {s['slug']} — порог {s['threshold'] * 100:.0f} п.п.")
    return "\n".join(lines)


def build_router(cfg: Config, conn: sqlite3.Connection, gamma: GammaClient,
                 state: AppState) -> Router:
    router = Router()
    limiter = RateLimiter()

    def _allowed(message: Message) -> bool:
        return message.chat is not None and limiter.allow(message.chat.id)

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if not _allowed(message):
            return
        await message.answer(texts.START_MESSAGE, parse_mode="HTML",
                             reply_markup=_main_keyboard(cfg.telegram_channel_id))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if not _allowed(message):
            return
        await message.answer(texts.HELP_MESSAGE, parse_mode="HTML",
                             reply_markup=_main_keyboard(cfg.telegram_channel_id))

    @router.message(Command("watch"))
    async def cmd_watch(message: Message, command: CommandObject) -> None:
        if not _allowed(message):
            return
        slug = extract_slug(command.args)
        if not slug:
            await message.answer(
                "Пришлите ссылку на рынок или его slug:\n"
                "<code>/watch https://polymarket.com/event/...</code>",
                parse_mode="HTML")
            return
        if db.count_subscriptions(conn, message.chat.id) >= MAX_SUBSCRIPTIONS:
            await message.answer(f"Лимит {MAX_SUBSCRIPTIONS} подписок. Удалите лишние: /list")
            return
        try:
            market = await gamma.find_market_by_slug(slug)
        except RuntimeError:
            await message.answer("Polymarket API сейчас недоступен, попробуйте позже.")
            return
        if market is None:
            await message.answer(f"Рынок «{slug}» не найден. Проверьте slug или ссылку.")
            return
        added = db.add_subscription(conn, message.chat.id, market.condition_id, market.slug)
        prob = (f"Текущая вероятность «Да»: {market.yes_price * 100:.0f}%"
                if market.yes_price is not None
                else "Рынок небинарный — личные алерты по нему пока не поддерживаются.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Мои подписки", callback_data="list"),
            InlineKeyboardButton(text="❌ Отписаться",
                                 callback_data=f"unwatch:{market.slug[:56]}"),
        ]])
        if added:
            await message.answer(
                f"✅ Подписка оформлена: {market.question}\n{prob}\n"
                f"Пришлю личный алерт при сдвиге на 5 п.п. "
                f"(изменить: /threshold {market.slug} 3)",
                reply_markup=kb)
        else:
            await message.answer(f"Вы уже подписаны на этот рынок.\n{prob}", reply_markup=kb)

    @router.message(Command("unwatch"))
    async def cmd_unwatch(message: Message, command: CommandObject) -> None:
        if not _allowed(message):
            return
        slug = extract_slug(command.args)
        if not slug:
            await message.answer("Использование: /unwatch <slug>")
            return
        if db.remove_subscription(conn, message.chat.id, slug):
            await message.answer(f"❎ Подписка на «{slug}» удалена.")
        else:
            await message.answer(f"Подписки на «{slug}» не было. Список: /list")

    @router.message(Command("list"))
    async def cmd_list(message: Message) -> None:
        if not _allowed(message):
            return
        subs = db.list_subscriptions(conn, message.chat.id)
        await message.answer(_list_text(subs), parse_mode="HTML",
                             reply_markup=_list_keyboard(subs))

    @router.message(Command("threshold"))
    async def cmd_threshold(message: Message, command: CommandObject) -> None:
        if not _allowed(message):
            return
        parts = (command.args or "").split()
        usage = ("Использование: /threshold <slug> <порог в п.п., 1–50>\n"
                 "Например: /threshold btc-100k 3")
        if len(parts) != 2:
            await message.answer(usage)
            return
        slug = extract_slug(parts[0])
        try:
            pp = float(parts[1].replace(",", "."))
        except ValueError:
            await message.answer(usage)
            return
        if not slug or not (1 <= pp <= 50):
            await message.answer(usage)
            return
        if db.set_threshold(conn, message.chat.id, slug, pp / 100):
            await message.answer(f"Порог для «{slug}» теперь {pp:.0f} п.п.")
        else:
            await message.answer(f"Подписки на «{slug}» не найдено. Список: /list")

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not _allowed(message):
            return
        uptime = datetime.now(timezone.utc) - state.started_at
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        base = f"🟢 PolyPulse работает. Аптайм: {hours}ч {rem // 60}м."
        if message.chat.id == cfg.admin_chat_id:
            await message.answer(
                f"{base}\n"
                f"Последний цикл: {state.last_cycle_ts or 'ещё не было'}\n"
                f"Рынков в мониторинге: {state.markets_monitored}\n"
                f"Циклов выполнено: {state.cycles_completed}\n"
                f"DRY_RUN: {cfg.dry_run}"
            )
        else:
            await message.answer(base)

    # --- инлайн-кнопки ---

    @router.callback_query(F.data == "help")
    async def cb_help(query: CallbackQuery) -> None:
        if query.message is None or not limiter.allow(query.message.chat.id):
            await query.answer()
            return
        await query.message.answer(texts.HELP_MESSAGE, parse_mode="HTML",
                                   reply_markup=_main_keyboard(cfg.telegram_channel_id))
        await query.answer()

    @router.callback_query(F.data == "list")
    async def cb_list(query: CallbackQuery) -> None:
        if query.message is None or not limiter.allow(query.message.chat.id):
            await query.answer()
            return
        subs = db.list_subscriptions(conn, query.message.chat.id)
        await query.message.answer(_list_text(subs), parse_mode="HTML",
                                   reply_markup=_list_keyboard(subs))
        await query.answer()

    @router.callback_query(F.data.startswith("unwatch:"))
    async def cb_unwatch(query: CallbackQuery) -> None:
        if query.message is None or not limiter.allow(query.message.chat.id):
            await query.answer()
            return
        slug = query.data.split(":", 1)[1]
        chat_id = query.message.chat.id
        if db.remove_subscription(conn, chat_id, slug):
            await query.answer(f"Отписал от {slug}")
        else:
            await query.answer("Такой подписки уже нет")
        # обновляем список в том же сообщении
        subs = db.list_subscriptions(conn, chat_id)
        try:
            await query.message.edit_text(_list_text(subs), parse_mode="HTML",
                                          reply_markup=_list_keyboard(subs))
        except Exception:  # noqa: BLE001 — сообщение могло быть не списком
            pass

    return router


def build_dispatcher(cfg: Config, conn: sqlite3.Connection, gamma: GammaClient,
                     state: AppState) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(cfg, conn, gamma, state))
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    """Меню команд (кнопка «/» в клиенте Telegram)."""
    from aiogram.types import BotCommand

    await bot.set_my_commands([
        BotCommand(command="start", description="Что умеет бот"),
        BotCommand(command="help", description="Справка: подписки и типы алертов"),
        BotCommand(command="watch", description="Подписаться на рынок (ссылка или slug)"),
        BotCommand(command="list", description="Мои подписки"),
        BotCommand(command="unwatch", description="Отписаться от рынка"),
        BotCommand(command="threshold", description="Свой порог сдвига, п.п."),
        BotCommand(command="status", description="Состояние сервиса"),
    ])
