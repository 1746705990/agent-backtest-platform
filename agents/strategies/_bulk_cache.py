"""Bulk-cache main-board K-lines into data/sim.db (resumable, threaded).

Each code is fetched once (forward + none for VWAP calibration) and stored.
Subsequent backtests read from the local SQLite cache (ensure_symbol returns
early when the window is covered), so no per-code network calls at run time.

Run in background; progress printed to stderr.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

sys.path.insert(0, ".")
from server.engine import _fetch_bars_consistent, _store_bars, FuyaoClient  # noqa
from server import db, config  # noqa

CODES = json.load(open("agents/strategies/main_board_codes.json", encoding="utf-8"))["codes"]

# Coverage target: must include the backtest lookback before 2025-01-02 and the
# end date. 2023-01-01 -> 2026-08-27 (~1334d) stays under the ~1780d upstream limit.
FETCH_START = date(2023, 1, 1)
FETCH_END = date(2026, 8, 27)
NEED_START = (date(2025, 1, 2) - timedelta(days=int(400 * 1.6))).isoformat()

WORKERS = 10
_write_lock = threading.Lock()


def covered(conn, code: str) -> bool:
    row = conn.execute(
        "SELECT MIN(date) mn, MAX(date) mx FROM kline_daily WHERE thscode=?", (code,)
    ).fetchone()
    return bool(row["mn"] and row["mn"] <= NEED_START and row["mx"] >= FUYAO_END)


# FUYAO_END placeholder (real end)
FUYAO_END = FETCH_END.isoformat()


def fetch_one(code: str) -> tuple[str, int]:
    client = FuyaoClient()
    bars = _fetch_bars_consistent(client, code, FETCH_START, FETCH_END, is_fund=False)
    conn = db.connect()
    try:
        with _write_lock:
            _store_bars(conn, code, bars)
            conn.commit()
    finally:
        conn.close()
    return code, len(bars)


def main():
    conn = db.connect()
    db.init_db()
    todo = [c for c in CODES if not covered(conn, c)]
    conn.close()
    print(f"total={len(CODES)} todo={len(todo)} (already cached="
          f"{len(CODES)-len(todo)})", file=sys.stderr)

    ok = err = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_one, c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                _, n = fut.result()
                ok += 1
            except Exception as e:
                err += 1
                print(f"ERR {c}: {e!r}", file=sys.stderr)
            done = ok + err
            if done % 200 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)} ok={ok} err={err} "
                      f"elapsed={time.time()-t0:.0f}s", file=sys.stderr)

    print(f"DONE ok={ok} err={err} total={time.time()-t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
