"""Клиент Polymarket Gamma API.

Фактический маппинг полей — docs/api_notes.md. Ключевые отличия от «ожидаемого»:
outcomes/outcomePrices — JSON-строки; volume/liquidity рынка — строки, но есть
числовые volumeNum/liquidityNum; volume24hr — уже float.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://gamma-api.polymarket.com"
TIMEOUT = 15.0
RETRIES = 3


@dataclass
class Market:
    condition_id: str
    slug: str
    question: str
    yes_price: float | None      # None для небинарных
    volume_total: float | None
    volume_24h: float | None
    liquidity: float | None
    end_date: str | None         # ISO-8601
    created_at: str | None
    is_binary: bool

    def as_snapshot_row(self) -> dict:
        return {
            "condition_id": self.condition_id,
            "slug": self.slug,
            "question": self.question,
            "yes_price": self.yes_price,
            "volume_total": self.volume_total,
            "volume_24h": self.volume_24h,
            "liquidity": self.liquidity,
            "end_date": self.end_date,
        }


def _to_float(value) -> float | None:
    """Числовые поля Gamma приходят то строками, то числами; мусор -> None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # отсекаем NaN


def _parse_json_list(value) -> list | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_market(raw: dict) -> Market | None:
    condition_id = raw.get("conditionId")
    slug = raw.get("slug")
    question = raw.get("question")
    if not condition_id or not slug or not question:
        return None

    outcomes = _parse_json_list(raw.get("outcomes")) or []
    prices = _parse_json_list(raw.get("outcomePrices")) or []
    is_binary = [str(o).lower() for o in outcomes] == ["yes", "no"]
    yes_price = None
    if is_binary and prices:
        yes_price = _to_float(prices[0])
        if yes_price is not None and not (0.0 <= yes_price <= 1.0):
            yes_price = None

    return Market(
        condition_id=condition_id,
        slug=slug,
        question=question,
        yes_price=yes_price,
        volume_total=_to_float(raw.get("volumeNum")) or _to_float(raw.get("volume")),
        volume_24h=_to_float(raw.get("volume24hr")),
        liquidity=_to_float(raw.get("liquidityNum")) or _to_float(raw.get("liquidity")),
        end_date=raw.get("endDate"),
        created_at=raw.get("createdAt"),
        is_binary=is_binary,
    )


class GammaClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict) -> list:
        last_exc: Exception | None = None
        for attempt in range(RETRIES):
            try:
                resp = await self._client.get(path, params=params)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    raise ValueError(f"ожидался JSON-массив, получен {type(data).__name__}")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                delay = 2**attempt
                log.warning("Gamma %s: попытка %d/%d не удалась (%s), пауза %ds",
                            path, attempt + 1, RETRIES, exc, delay)
                if attempt < RETRIES - 1:
                    await asyncio.sleep(delay)
        raise RuntimeError(f"Gamma API недоступен: {path}") from last_exc

    async def fetch_top_markets(self, limit: int = 100) -> list[Market]:
        """Рынки из топ-N активных событий по суточному объёму (1 запрос)."""
        events = await self._get(
            "/events",
            {"closed": "false", "order": "volume24hr", "ascending": "false", "limit": limit},
        )
        markets: list[Market] = []
        seen: set[str] = set()
        for event in events:
            for raw in event.get("markets") or []:
                if raw.get("closed") or not raw.get("active", True):
                    continue
                m = parse_market(raw)
                if m and m.condition_id not in seen:
                    seen.add(m.condition_id)
                    markets.append(m)
        return markets

    async def find_market_by_slug(self, slug: str) -> Market | None:
        """Поиск рынка по slug: сначала /markets, затем как slug события."""
        rows = await self._get("/markets", {"slug": slug})
        if rows:
            return parse_market(rows[0])
        events = await self._get("/events", {"slug": slug})
        for event in events:
            for raw in event.get("markets") or []:
                m = parse_market(raw)
                if m:
                    return m
        return None
