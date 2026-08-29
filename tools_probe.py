"""临时探测：候选标的数据覆盖范围。用完即删。"""
import sys
from datetime import date

sys.path.insert(0, ".")
from server.fuyao_client import FuyaoClient, FuyaoError  # noqa: E402

CODES = [
    "510300.SH", "510500.SH", "159915.SZ", "518880.SH", "511260.SH",
    "512480.SH", "588000.SH", "513100.SH",
]

c = FuyaoClient()
start, end = date(2023, 1, 1), date(2026, 8, 27)

for code in CODES:
    for is_fund in (True, False):
        try:
            fn = c.fund_historical if is_fund else c.a_share_historical
            bars = fn(code, start, end)
            if bars:
                print(f"{code:12s} {'FUND' if is_fund else 'ASHARE'} "
                      f"n={len(bars):4d} {bars[0]['date']} ~ {bars[-1]['date']} "
                      f"last_close={bars[-1]['close']}")
                break
        except FuyaoError as e:
            last = e
    else:
        print(f"{code:12s} FAILED: {last}")

try:
    idx = c.index_historical("000001.SH", start, end)
    print(f"000001.SH    INDEX n={len(idx)} {idx[0]['date']} ~ {idx[-1]['date']}")
except FuyaoError as e:
    print("index failed", e)
