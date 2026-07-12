"""Цикл сбора снапшотов рынков в SQLite."""
from __future__ import annotations

import logging
import sqlite3

from . import db
from .gamma import GammaClient, Market

log = logging.getLogger(__name__)


async def collect_once(conn: sqlite3.Connection, gamma: GammaClient) -> tuple[str, list[Market]]:
    """Один цикл: тянем топ рынков, пишем снапшоты. Возвращает (ts, markets)."""
    markets = await gamma.fetch_top_markets(limit=100)
    ts = db.utcnow_iso()
    db.insert_snapshots(conn, ts, [m.as_snapshot_row() for m in markets])
    binary = sum(1 for m in markets if m.is_binary)
    log.info("Снапшот %s: %d рынков (%d бинарных)", ts, len(markets), binary)
    return ts, markets
