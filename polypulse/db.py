"""SQLite: соединение, миграции, хелперы."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,                -- ISO-8601 UTC
    condition_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    question TEXT NOT NULL,
    yes_price REAL,                  -- вероятность Yes (0..1), NULL для небинарных
    volume_total REAL,
    volume_24h REAL,
    liquidity REAL,
    end_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_market_ts ON snapshots(condition_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,        -- price_move | volume_spike | new_market | closing_soon
    payload TEXT NOT NULL,           -- JSON с деталями
    message_text TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    threshold REAL NOT NULL DEFAULT 0.05,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, condition_id)
);

CREATE TABLE IF NOT EXISTS alert_cooldowns (
    condition_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    last_ts TEXT NOT NULL,
    PRIMARY KEY (condition_id, alert_type)
);

-- последний личный алерт по подписке (лимит: 1 раз в 2 часа)
CREATE TABLE IF NOT EXISTS personal_alert_state (
    chat_id INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    last_ts TEXT NOT NULL,
    last_yes_price REAL,
    PRIMARY KEY (chat_id, condition_id)
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- снапшоты ---

def insert_snapshots(conn: sqlite3.Connection, ts: str, rows: list[dict]) -> None:
    conn.executemany(
        """INSERT INTO snapshots
           (ts, condition_id, slug, question, yes_price, volume_total, volume_24h, liquidity, end_date)
           VALUES (:ts, :condition_id, :slug, :question, :yes_price, :volume_total, :volume_24h, :liquidity, :end_date)""",
        [{**r, "ts": ts} for r in rows],
    )
    conn.commit()


def snapshot_near(
    conn: sqlite3.Connection, condition_id: str, target_ts: str, tolerance_min: int = 30
) -> sqlite3.Row | None:
    """Ближайший к target_ts снапшот рынка в пределах ±tolerance_min минут."""
    # ts хранится как ISO с 'T'/'Z' — сравнивать строки с выводом datetime() нельзя,
    # поэтому все сравнения времени в этом модуле идут через epoch (strftime('%s'))
    row = conn.execute(
        """SELECT *, ABS(strftime('%s', ts) - strftime('%s', :target)) AS dist
           FROM snapshots WHERE condition_id = :cid
             AND ABS(strftime('%s', ts) - strftime('%s', :target)) <= :tol
           ORDER BY dist LIMIT 1""",
        {"cid": condition_id, "target": target_ts, "tol": tolerance_min * 60},
    ).fetchone()
    return row


def market_seen_before(conn: sqlite3.Connection, condition_id: str, before_ts: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE condition_id = ? AND ts < ? LIMIT 1",
        (condition_id, before_ts),
    ).fetchone()
    return row is not None


def volume_deltas_24h(conn: sqlite3.Connection, condition_id: str, now_ts: str) -> list[float]:
    """Приросты volume_total между последовательными снапшотами за последние 24 ч."""
    rows = conn.execute(
        """SELECT volume_total FROM snapshots
           WHERE condition_id = ?
             AND strftime('%s', ts) >= strftime('%s', ?) - 86400
             AND volume_total IS NOT NULL
           ORDER BY ts""",
        (condition_id, now_ts),
    ).fetchall()
    vols = [r["volume_total"] for r in rows]
    return [b - a for a, b in zip(vols, vols[1:])]


def delete_old_snapshots(conn: sqlite3.Connection, days: int = 30) -> int:
    cur = conn.execute(
        "DELETE FROM snapshots WHERE strftime('%s', ts) < strftime('%s', 'now') - ?",
        (days * 86400,),
    )
    conn.commit()
    return cur.rowcount


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


# --- алерты и кулдауны ---

def insert_alert(
    conn: sqlite3.Connection,
    condition_id: str,
    alert_type: str,
    payload: dict,
    message_text: str,
    published: bool,
) -> int:
    cur = conn.execute(
        "INSERT INTO alerts (ts, condition_id, alert_type, payload, message_text, published) VALUES (?,?,?,?,?,?)",
        (utcnow_iso(), condition_id, alert_type, json.dumps(payload, ensure_ascii=False), message_text, int(published)),
    )
    conn.commit()
    return cur.lastrowid


def cooldown_active(
    conn: sqlite3.Connection, condition_id: str, alert_type: str, hours: float = 6
) -> bool:
    row = conn.execute(
        """SELECT 1 FROM alert_cooldowns
           WHERE condition_id = ? AND alert_type = ?
             AND strftime('%s', last_ts) > strftime('%s', 'now') - ?""",
        (condition_id, alert_type, int(hours * 3600)),
    ).fetchone()
    return row is not None


def ever_alerted(conn: sqlite3.Connection, condition_id: str, alert_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM alert_cooldowns WHERE condition_id = ? AND alert_type = ?",
        (condition_id, alert_type),
    ).fetchone()
    return row is not None


def touch_cooldown(conn: sqlite3.Connection, condition_id: str, alert_type: str) -> None:
    conn.execute(
        """INSERT INTO alert_cooldowns (condition_id, alert_type, last_ts) VALUES (?,?,?)
           ON CONFLICT(condition_id, alert_type) DO UPDATE SET last_ts = excluded.last_ts""",
        (condition_id, alert_type, utcnow_iso()),
    )
    conn.commit()


def published_last_hour(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM alerts
           WHERE published = 1 AND strftime('%s', ts) > strftime('%s', 'now') - 3600"""
    ).fetchone()
    return row["n"]


# --- подписки ---

def add_subscription(
    conn: sqlite3.Connection, chat_id: int, condition_id: str, slug: str, threshold: float = 0.05
) -> bool:
    """True — добавлена, False — уже была."""
    try:
        conn.execute(
            "INSERT INTO subscriptions (chat_id, condition_id, slug, threshold, created_at) VALUES (?,?,?,?,?)",
            (chat_id, condition_id, slug, threshold, utcnow_iso()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_subscription(conn: sqlite3.Connection, chat_id: int, slug: str) -> bool:
    cur = conn.execute(
        "DELETE FROM subscriptions WHERE chat_id = ? AND slug = ?", (chat_id, slug)
    )
    conn.commit()
    return cur.rowcount > 0


def list_subscriptions(conn: sqlite3.Connection, chat_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE chat_id = ? ORDER BY created_at", (chat_id,)
    ).fetchall()


def count_subscriptions(conn: sqlite3.Connection, chat_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM subscriptions WHERE chat_id = ?", (chat_id,)
    ).fetchone()["n"]


def all_subscriptions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM subscriptions").fetchall()


def set_threshold(conn: sqlite3.Connection, chat_id: int, slug: str, threshold: float) -> bool:
    cur = conn.execute(
        "UPDATE subscriptions SET threshold = ? WHERE chat_id = ? AND slug = ?",
        (threshold, chat_id, slug),
    )
    conn.commit()
    return cur.rowcount > 0


# --- личные алерты ---

def personal_state(conn: sqlite3.Connection, chat_id: int, condition_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM personal_alert_state WHERE chat_id = ? AND condition_id = ?",
        (chat_id, condition_id),
    ).fetchone()


def touch_personal_state(
    conn: sqlite3.Connection, chat_id: int, condition_id: str, yes_price: float | None
) -> None:
    conn.execute(
        """INSERT INTO personal_alert_state (chat_id, condition_id, last_ts, last_yes_price)
           VALUES (?,?,?,?)
           ON CONFLICT(chat_id, condition_id)
           DO UPDATE SET last_ts = excluded.last_ts, last_yes_price = excluded.last_yes_price""",
        (chat_id, condition_id, utcnow_iso(), yes_price),
    )
    conn.commit()
