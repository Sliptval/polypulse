"""Шаблоны сообщений и промпты для DeepSeek."""
from __future__ import annotations

from datetime import datetime
from html import escape

from .gamma import parse_iso

EMOJI = {
    "price_move": "⚡",
    "volume_spike": "🐋",
    "new_market": "🆕",
    "closing_soon": "⏰",
}

ALERT_TYPE_RU = {
    "price_move": "резкий сдвиг вероятности",
    "volume_spike": "всплеск объёма торгов",
    "new_market": "крупный новый рынок",
    "closing_soon": "рынок скоро разрешается, исход не определён",
}

HEADLINE = {
    "price_move": "Резкий сдвиг вероятности",
    "volume_spike": "Всплеск объёма",
    "new_market": "Крупный новый рынок",
    "closing_soon": "Скоро развязка",
}

SYSTEM_PROMPT = """Ты — редактор Telegram-канала о рынках предсказаний Polymarket для русскоязычной аудитории. Твоя задача — написать короткий комментарий к алерту о движении на рынке.

Правила:
- Пиши на русском, 2–4 предложения, максимум 500 символов.
- Тон: сдержанный, информативный, без кликбейта и восклицаний. Допустима лёгкая ирония.
- Объясни, что означает это движение простыми словами: что рынок теперь считает более/менее вероятным.
- НИКОГДА не выдумывай причины движения, новости или события, которых нет во входных данных. Если причина неизвестна — так и пиши: «причина сдвига пока не очевидна» или предложи читателю следить за новостями по теме.
- Не давай финансовых советов, не используй слова «ставь», «покупай», «продавай», «зарабатывай».
- Не используй markdown, заголовки, списки, эмодзи и ссылки. Только чистый текст.
- Цифры вероятностей пиши в процентах без десятых, если во входных данных не указано иное."""

USER_PROMPT_TEMPLATE = """Тип алерта: {alert_type_ru}
Вопрос рынка (переведи смысл на русский в комментарии, если он на английском): {question}
{change_line}
Объём за 24 часа: ${volume_24h}
Общий объём рынка: ${volume_total}
Дата разрешения рынка: {end_date}

Напиши комментарий по правилам из системного промпта."""


def _pct(p: float | None) -> str:
    return "?" if p is None else f"{p * 100:.0f}"


def _usd(v: float | None) -> str:
    if v is None:
        return "?"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


def _fmt_date(iso: str | None) -> str:
    dt = parse_iso(iso)
    return dt.strftime("%d.%m.%Y %H:%M UTC") if dt else "неизвестно"


def build_user_prompt(alert_type: str, question: str, payload: dict,
                      volume_total: float | None, end_date: str | None) -> str:
    if alert_type == "new_market":
        change_line = (f"Новый рынок, создан {payload.get('hours_ago', '?')} часов назад, "
                       f"уже набрал ${_usd(volume_total)}")
    elif alert_type == "closing_soon":
        change_line = (f"Рынок разрешается через {payload.get('hours_left', '?')} часов, "
                       f"вероятность {_pct(payload.get('new_prob'))}% — исход не определён")
    else:
        change_line = (f"Вероятность «Да»: было {_pct(payload.get('old_prob'))}%, "
                       f"стало {_pct(payload.get('new_prob'))}% "
                       f"(изменение за {payload.get('window_min', '?')} минут)")
    return USER_PROMPT_TEMPLATE.format(
        alert_type_ru=ALERT_TYPE_RU[alert_type],
        question=question,
        change_line=change_line,
        volume_24h=_usd(payload.get("volume_24h")),
        volume_total=_usd(volume_total),
        end_date=_fmt_date(end_date),
    )


def format_channel_message(alert_type: str, question: str, slug: str, payload: dict,
                           volume_total: float | None, end_date: str | None,
                           llm_comment: str | None) -> str:
    """Итоговое сообщение для канала, HTML parse_mode."""
    lines = [f"{EMOJI[alert_type]} <b>{HEADLINE[alert_type]}</b>", ""]
    if llm_comment:
        lines += [escape(llm_comment), ""]

    lines.append(f"📊 {escape(question)}")

    old_p, new_p = payload.get("old_prob"), payload.get("new_prob")
    if alert_type == "price_move" and old_p is not None and new_p is not None:
        delta_pp = (new_p - old_p) * 100
        arrow = "▲" if delta_pp > 0 else "▼"
        lines.append(f"Вероятность: {_pct(old_p)}% → {_pct(new_p)}% ({arrow}{abs(delta_pp):.0f} п.п.)")
    elif new_p is not None:
        lines.append(f"Вероятность: {_pct(new_p)}%")

    if alert_type == "volume_spike":
        lines.append(f"Прирост за окно: ${_usd(payload.get('window_delta'))} "
                     f"(×{payload.get('multiplier', 0):.1f} к типичному)")
    if alert_type == "new_market":
        lines.append(f"Создан {payload.get('hours_ago', '?')} ч назад, "
                     f"объём уже ${_usd(volume_total)}")
    if alert_type == "closing_soon":
        lines.append(f"До разрешения: ~{payload.get('hours_left', '?')} ч")

    if payload.get("volume_24h") is not None:
        lines.append(f"Объём: ${_usd(payload['volume_24h'])} за 24ч")
    lines.append(f"⏳ Разрешение: {_fmt_date(end_date)}")
    lines += ["", f"🔗 polymarket.com/event/{slug}"]
    return "\n".join(lines)


def format_personal_message(question: str, slug: str, old_p: float | None,
                            new_p: float | None) -> str:
    """Личный алерт подписчику: чистый шаблон, без LLM."""
    lines = ["🔔 <b>Движение по вашей подписке</b>", "", f"📊 {escape(question)}"]
    if old_p is not None and new_p is not None:
        delta_pp = (new_p - old_p) * 100
        arrow = "▲" if delta_pp > 0 else "▼"
        lines.append(f"Вероятность: {_pct(old_p)}% → {_pct(new_p)}% ({arrow}{abs(delta_pp):.0f} п.п.)")
    lines += ["", f"🔗 polymarket.com/event/{slug}"]
    return "\n".join(lines)


START_MESSAGE = """👋 Привет! Это бот канала <b>PolyPulse</b> — алерты по аномалиям на рынках предсказаний Polymarket.

Что я умею:
• В канале автоматически публикуются сигналы: резкие сдвиги вероятности, всплески объёма, крупные новые рынки и рынки накануне развязки.
• Лично вам я могу присылать алерты по конкретным рынкам — подпишитесь через /watch.

Быстрый старт: откройте рынок на polymarket.com, скопируйте ссылку и отправьте мне:
<code>/watch ссылка</code>"""

HELP_MESSAGE = """❓ <b>Как пользоваться ботом</b>

<b>Подписка на рынок</b>
Отправьте <code>/watch ссылка-на-рынок</code> (или его slug — хвост ссылки). Пример:
<code>/watch https://polymarket.com/event/will-spain-win-the-2026-fifa-world-cup-963</code>
Когда вероятность «Да» сдвинется на ваш порог (по умолчанию 5 п.п.), я пришлю сообщение в ЛС. Не чаще раза в 2 часа на рынок, максимум 20 подписок.

<b>Управление</b>
/list — ваши подписки (там же кнопки отписки)
/unwatch slug — отписаться
/threshold slug 3 — свой порог в процентных пунктах (1–50)
/status — работает ли сервис

<b>Что значат алерты в канале</b>
⚡ <b>Сдвиг вероятности</b> — рынок резко изменил мнение (≥7 п.п. за час)
🐋 <b>Всплеск объёма</b> — в рынок пришли большие деньги (×4 к типичному)
🆕 <b>Новый рынок</b> — только создан и уже собрал крупный объём
⏰ <b>Скоро развязка</b> — рынок закрывается в ближайшие 48 ч, а исход всё ещё спорный (35–65%)

Вероятность — это цена исхода «Да» на Polymarket: 20% значит, что рынок оценивает шанс события в ~20%. Это не прогноз и не финансовый совет."""
