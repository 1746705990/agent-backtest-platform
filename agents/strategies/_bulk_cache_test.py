"""Timing test: how fast can we fetch main-board K-lines, and do we hit 429?"""
import json
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, ".")
from server.engine import _fetch_bars_consistent, _store_bars, fuyao  # noqa
from server import db  # noqa

codes = json.load(open("agents/strategies/main_board_codes.json", encoding="utf-8"))["codes"]
# take a spread sample of 40
sample = codes[::max(1, len(codes) // 40)][:40]
print(f"sample size={len(sample)}", file=sys.stderr)

START = date(2023, 1, 1)
END = date(2026, 8, 27)
need_start = START - timedelta(days=int(400 * 1.6))

client = fuyao()
conn = db.connect()
db.init_db()

ok = 0
err = 0
t0 = time.time()
for i, code in enumerate(sample):
    try:
        bars = _fetch_bars_consistent(client, code, need_start, END, is_fund=False)
        _store_bars(conn, code, bars)
        conn.commit()
        ok += 1
    except Exception as e:
        err += 1
        print(f"ERR {code}: {e!r}", file=sys.stderr)
    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(sample)} ok={ok} err={err} "
              f"elapsed={time.time()-t0:.1f}s", file=sys.stderr)

dt = time.time() - t0
print(f"DONE ok={ok} err={err} total={dt:.1f}s per_code={dt/len(sample):.2f}s",
      file=sys.stderr)
conn.close()
