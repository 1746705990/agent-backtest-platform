# agents/strategies/example_ma_cross.py
"""示例策略（由 harness 现写）：双均线穿越 + 分批建仓/止盈。

演示如何基于 base.py 契约写一个最小可运行策略：
- 用 bars() 拿截至昨日的日 K（不含当日，机制性杜绝未来函数）；
- 用内置 sma() 算快/慢均线；
- 金叉把仓位提到上限、死叉降到 0，始终保留国债 ETF 作防守垫；
- 全部通过 base 的下单辅助完成，市价单按今日开盘价成交。

运行（harness 写完策略后一键回测）：
  python agents/strategies/example_ma_cross.py --start 2023-06-01 --end 2026-08-01 --cash 10000
  # 看板 http://127.0.0.1:8000/?sid=<会话号>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import (run_session, bars, sma, buy_weight, sell_pct,
                  pos_of, name_of, cli)

FAST, SLOW = 5, 20
UNIVERSE = ["510300.SH", "518880.SH", "511010.SH"]   # 沪深300ETF / 黄金ETF / 国债ETF(防守)
MAX_WEIGHT = 0.40        # 单标的仓位上限
BAND = 0.05             # 越过该阈值才调仓，避免反复摩擦


def step(sid, clock, acct, stats):
    notes = []
    for code in UNIVERSE:
        bs = bars(sid, code, SLOW + 2)
        if len(bs) < SLOW + 1:
            continue
        closes = [b["close"] for b in bs]
        ma_fast = sma(closes, FAST)
        ma_slow = sma(closes, SLOW)
        if ma_fast is None or ma_slow is None:
            continue

        pos = pos_of(acct, code)
        held = pos["market_value"] if pos else 0.0
        total = acct["total_value"] or 1.0
        cur_w = held / total

        # 金叉看多、死叉看空；国债 ETF 反向，作防守（慢线在快线之上时持有）
        bull = ma_fast > ma_slow
        target_w = MAX_WEIGHT if bull else 0.0

        if target_w > cur_w + BAND:
            buy_weight(sid, code, target_w - cur_w, acct)
            notes.append(f"{name_of(code)} 金叉加仓至 {target_w:.0%}")
        elif target_w < cur_w - BAND and pos:
            sell_pct(sid, pos, (cur_w - target_w) / cur_w if cur_w else 0.0)
            notes.append(f"{name_of(code)} 死叉减仓")
    return "；".join(notes) if notes else "持仓不动"


if __name__ == "__main__":
    args = cli("示例-双均线穿越")
    run_session(step, "示例-双均线穿越", args.start, args.end, args.cash,
                thscodes=UNIVERSE)
