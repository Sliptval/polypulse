"""Разовый прогон детектора по накопленной истории снапшотов — для тюнинга порогов.

Идёт по всем историческим меткам времени (ts) в snapshots и для каждой
запускает детектор так, как если бы цикл происходил в тот момент.
Кулдауны и лимиты канала игнорируются — показываются все сработки.

Запуск: venv/bin/python scripts/backfill_check.py [--since HOURS]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polypulse import db  # noqa: E402
from polypulse.config import load_config  # noqa: E402
from polypulse.detector import detect  # noqa: E402
from polypulse.gamma import Market  # noqa: E402


def market_from_snapshot(row) -> Market:
    return Market(
        condition_id=row["condition_id"],
        slug=row["slug"],
        question=row["question"],
        yes_price=row["yes_price"],
        volume_total=row["volume_total"],
        volume_24h=row["volume_24h"],
        liquidity=row["liquidity"],
        end_date=row["end_date"],
        created_at=None,  # в снапшотах нет createdAt — new_market в бэкфилле не проверяем
        is_binary=row["yes_price"] is not None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=int, default=48,
                        help="глубина истории в часах (по умолчанию 48)")
    args = parser.parse_args()

    cfg = load_config()
    conn = db.connect(cfg.db_path)

    timestamps = [
        r["ts"] for r in conn.execute(
            """SELECT DISTINCT ts FROM snapshots
               WHERE strftime('%s', ts) > strftime('%s', 'now') - ? ORDER BY ts""",
            (args.since * 3600,),
        )
    ]
    if len(timestamps) < 2:
        print(f"Недостаточно истории: {len(timestamps)} снапшот(ов) за {args.since} ч. "
              "Дайте коллектору поработать подольше.")
        return

    print(f"Прогон детектора по {len(timestamps)} снапшотам "
          f"(окно {cfg.price_delta_window_min} мин, порог {cfg.price_delta_threshold:.2f}, "
          f"спайк x{cfg.volume_spike_multiplier})\n")

    total = 0
    # кулдауны/дедупликацию отключаем — интересны все сработки
    with patch.object(db, "cooldown_active", return_value=False), \
         patch.object(db, "ever_alerted", return_value=False), \
         patch.object(db, "market_seen_before", return_value=True):
        for ts in timestamps[1:]:
            rows = conn.execute("SELECT * FROM snapshots WHERE ts = ?", (ts,)).fetchall()
            markets = [market_from_snapshot(r) for r in rows]
            for a in detect(conn, cfg, ts, markets):
                total += 1
                p = a.payload
                if a.alert_type == "price_move":
                    detail = (f"{p['old_prob'] * 100:.0f}% -> {p['new_prob'] * 100:.0f}% "
                              f"за {p['window_min']} мин")
                elif a.alert_type == "volume_spike":
                    detail = f"+${p['window_delta']:,.0f} (x{p['multiplier']:.1f} к медиане)"
                elif a.alert_type == "closing_soon":
                    detail = f"через {p['hours_left']} ч, вероятность {p['new_prob'] * 100:.0f}%"
                else:
                    detail = str(p)
                print(f"{ts}  [{a.alert_type:12s}] {a.market.slug:60s} {detail}")

    print(f"\nИтого сработок: {total}")
    if total == 0:
        print("Ни одной — возможно, пороги слишком строгие или истории мало.")


if __name__ == "__main__":
    main()
