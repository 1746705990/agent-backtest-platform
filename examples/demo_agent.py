"""示例 Agent：演示回合制回测闭环。

策略（仅为演示，无任何投资价值）：
  - 第 1 天：满仓买入第一只标的
  - 中间日：持有不动
  - 最后 1 天：清仓卖出
每天操作完都调用 finish，时钟才推进。

运行（先启动服务）：python examples/demo_agent.py
"""
from __future__ import annotations

import sys

import requests

BASE = "http://127.0.0.1:8000"

# 统一默认参数来源：项目根目录 config.py
import os, sys
_PROJ = os.path.dirname(os.path.abspath(__file__))
while _PROJ != os.path.dirname(_PROJ) and not os.path.exists(os.path.join(_PROJ, "config.py")):
    _PROJ = os.path.dirname(_PROJ)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)
from config import (DEFAULT_START, DEFAULT_END, DEFAULT_CASH,
                    DEFAULT_CODES, DEFAULT_STOCK)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    r = requests.request(method, BASE + path, json=body, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


def main() -> None:
    # 1. 创建回测会话（会自动从扶摇拉取历史K线落库）
    sess = api("/api/sim/session", "POST", {
        "name": "demo",
        "thscodes": ["600519.SH", "000001.SZ"],
        "start": DEFAULT_START,
        "end": DEFAULT_END,
        "init_cash": DEFAULT_CASH,
        "fill_price": "open",
    })
    sid = sess["session_id"]
    print(f"会话 #{sid} 已创建")

    target = "600519.SH"
    while True:
        clock = api(f"/api/sim/{sid}/clock")
        if clock["status"] != "running":
            print(f"== 回测结束：{clock['message'] if 'message' in clock else ''}")
            break
        d = clock["current_date"]
        total = clock["total_days"]
        idx = clock["day_index"]
        print(f"\n== 交易日 {d}（第 {idx}/{total} 天）==")

        # 2. 看盘：截至昨日的K线 + 当日快照 + 账户（open 模式屏蔽了当日 bar）
        bars = api(f"/api/sim/{sid}/kline?thscode={target}&limit=5")["items"]
        q = api(f"/api/sim/{sid}/quote?thscode={target}")
        acct = api(f"/api/sim/{sid}/account")
        print(f"  {target} 收盘 {q['close']}  VWAP {q['vwap']}  "
              f"| 现金 {acct['cash']:,.0f}  总资产 {acct['total_value']:,.0f}")
        print("  近5日收盘:", [b["close"] for b in bars])

        # 3. 决策与下单
        if idx == 1:
            qty = int(acct["cash"] / q["vwap"] / 100) * 100
            r = api(f"/api/sim/{sid}/orders", "POST",
                    {"thscode": target, "side": "BUY", "qty": qty, "type": "MARKET"})
            print(f"  买入 {qty} 股 -> {r}")
        elif idx == total:
            pos = next((p for p in acct["positions"] if p["thscode"] == target), None)
            if pos and pos["available_qty"] > 0:
                r = api(f"/api/sim/{sid}/orders", "POST",
                        {"thscode": target, "side": "SELL",
                         "qty": pos["available_qty"], "type": "MARKET"})
                print(f"  卖出 {pos['available_qty']} 股 -> {r}")
        else:
            print("  持有不动")

        # 4. “今日操作完毕” —— 时钟推进（携带日期防重复提交跳日）
        fin = api(f"/api/sim/{sid}/day/finish", "POST", {"sim_date": d})
        print(f"  [finish] {fin['message']}")

    # 5. 结果
    acct = api(f"/api/sim/{sid}/account")
    curve = api(f"/api/sim/{sid}/equity")["items"]
    print(f"\n最终总资产 {acct['total_value']:,.2f}，收益率 {acct['return_pct']}%")
    print("净值曲线:", [(e["sim_date"], e["total_value"]) for e in curve])
    print("成交明细:", api(f"/api/sim/{sid}/trades")["items"])


if __name__ == "__main__":
    try:
        main()
    except requests.ConnectionError:
        sys.exit("服务未启动，请先运行: python -m uvicorn server.main:app --port 8000")
