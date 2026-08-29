"""策略公共基础库：平台 API、技术指标、下单辅助、绩效统计。

所有策略共享同一套标的池、资金规则和绩效口径，保证横向对比是公平的。
每个策略文件只负责「今天该怎么操作」的判断，跑批、记账、报告都在 base 里。

注意资金约束：本金只有 1 万，而佣金有「最低 5 元」的下限。
单笔买入 1500 元时，实际费率是 5/1500 = 0.33%，是名义万 2.5 的 13 倍。
所以小本金下换手率会被手续费显著惩罚，这是回测结果的一部分，不是噪声。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

BASE = os.environ.get("SIM_API", "http://127.0.0.1:8000")

# 当前会话的撮合模式（由 run_session 设置）。策略通过 current_fill_price() 实时读取，
# 据此决定是否需要在信号计算里额外排除「今日」（open 模式下引擎已屏蔽当日 bar）。
FILL_PRICE = "open"


def current_fill_price() -> str:
    """返回当前回测会话的 fill_price 模式（平台固定为 open）。"""
    return FILL_PRICE

# 统一标的池：6 只低相关宽基/商品 ETF
POOL = {
    "510300.SH": "沪深300",
    "510500.SH": "中证500",
    "159915.SZ": "创业板",
    "588000.SH": "科创50",
    "513100.SH": "纳指",
    "518880.SH": "黄金",
}
DEFENSIVE = "511010.SH"
NAMES = {**POOL, DEFENSIVE: "国债"}
CODES = list(POOL)
BENCH = "510300.SH"          # 统一基准

# 统一默认参数来源：项目根目录 config.py（修改一处即可全局生效）
import os, sys
_PROJ = os.path.dirname(os.path.abspath(__file__))
while _PROJ != os.path.dirname(_PROJ) and not os.path.exists(os.path.join(_PROJ, "config.py")):
    _PROJ = os.path.dirname(_PROJ)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)
from config import (DEFAULT_START, DEFAULT_END, DEFAULT_CASH,
                    DEFAULT_CODES, DEFAULT_STOCK)


# ---------------------------------------------------------------- 平台 API

def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    r = requests.request(method, BASE + path, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


def bars(sid: int, code: str, limit: int = 250) -> list[dict]:
    """截至当前模拟日的最近 limit 根日 K（升序）。"""
    return api(f"/api/sim/{sid}/kline?thscode={code}&limit={limit}")["items"]


def closes(sid: int, code: str, limit: int = 250) -> list[float]:
    return [b["close"] for b in bars(sid, code, limit)]


def account(sid: int) -> dict:
    return api(f"/api/sim/{sid}/account")


def order(sid: int, code: str, side: str, qty: int) -> dict:
    return api(f"/api/sim/{sid}/orders", "POST",
               {"thscode": code, "side": side, "qty": qty, "type": "MARKET"})


def add_log(sid: int, note: str) -> None:
    api(f"/api/sim/{sid}/log", "POST", {"note": note, "actions": []})


def finish(sid: int, sim_date: str) -> dict:
    return api(f"/api/sim/{sid}/day/finish", "POST", {"sim_date": sim_date})


# ---------------------------------------------------------------- 技术指标

def sma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rsi(vals: list[float], n: int = 14) -> float | None:
    """Wilder 简化版 RSI（用简单平均而非平滑平均，够用且直观）。"""
    if len(vals) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = vals[i] - vals[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + (gains / n) / (losses / n))


def atr(bs: list[dict], n: int = 14) -> float | None:
    if len(bs) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h, l, pc = bs[i]["high"], bs[i]["low"], bs[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def highest(bs: list[dict], n: int, key: str = "high") -> float | None:
    return max(b[key] for b in bs[-n:]) if len(bs) >= n else None


def lowest(bs: list[dict], n: int, key: str = "low") -> float | None:
    return min(b[key] for b in bs[-n:]) if len(bs) >= n else None


def name_of(code: str) -> str:
    return NAMES.get(code, code)


# ---------------------------------------------------------------- 下单辅助

def last_close(sid: int, code: str) -> float | None:
    try:
        return api(f"/api/sim/{sid}/quote?thscode={code}")["close"]
    except Exception:
        return None


def buy_value(sid: int, code: str, amount: float, cash: float) -> dict | None:
    """按金额买入（折算整手，且不超可用现金）。返回下单结果或 None。"""
    price = last_close(sid, code)
    if not price:
        return None
    qty = int(min(amount, cash) * 0.99 / price / 100) * 100
    if qty < 100:
        return None
    return order(sid, code, "BUY", qty)


def buy_weight(sid: int, code: str, weight: float, acct: dict) -> dict | None:
    """按总资产权重买入。"""
    return buy_value(sid, code, acct["total_value"] * weight, acct["cash"])


def sell_all(sid: int, pos: dict) -> dict | None:
    """清空某持仓的可卖部分（T+1 约束）。"""
    avail = pos["available_qty"]
    qty = int(avail // 100) * 100
    if avail - qty >= 100:
        qty += 100
    if qty <= 0:
        return None
    return order(sid, pos["thscode"], "SELL", qty)


def sell_pct(sid: int, pos: dict, pct: float) -> dict | None:
    """卖出持仓的一定比例，剩余不足一手则全卖。"""
    avail = pos["available_qty"]
    qty = int(avail * pct / 100) * 100
    if 0 < avail - qty < 100:
        qty = avail
    if qty <= 0:
        return None
    return order(sid, pos["thscode"], "SELL", qty)


def filled(r: dict | None) -> bool:
    return bool(r) and r.get("status") == "FILLED"


def trim_to(sid: int, pos: dict, want: float, threshold: float = 0.15) -> bool:
    """持仓市值超过 want 一定幅度时减回目标。返回是否真的成交。"""
    mv = pos["market_value"]
    if mv <= 0 or mv - want <= want * threshold:
        return False
    return filled(sell_pct(sid, pos, 1 - want / mv))


def fill_to(sid: int, code: str, want: float, acct: dict,
            threshold: float = 0.15) -> bool:
    """持仓市值不足 want 一定幅度时补到目标。返回是否真的成交。"""
    gap = want - mv_of(acct, code)
    if gap <= want * threshold:
        return False
    return filled(buy_value(sid, code, gap, acct["cash"]))


def mv_of(acct: dict, code: str) -> float:
    return next((p["market_value"] for p in acct["positions"]
                 if p["thscode"] == code), 0.0)


def pos_of(acct: dict, code: str) -> dict | None:
    return next((p for p in acct["positions"] if p["thscode"] == code), None)


# ---------------------------------------------------------------- 绩效

def bench_return(sid: int, start: str, end: str) -> tuple[float, float] | None:
    """同期买入持有沪深300ETF 的收益率与最大回撤。"""
    bs = [b for b in bars(sid, BENCH, 3000) if start <= b["date"] <= end]
    if len(bs) < 2:
        return None
    ret = (bs[-1]["close"] / bs[0]["close"] - 1) * 100
    peak, mdd = bs[0]["close"], 0.0
    for b in bs:
        peak = max(peak, b["close"])
        mdd = min(mdd, b["close"] / peak - 1)
    return ret, mdd * 100


def report(sid: int, name: str, start: str, end: str,
           seconds: float, extra: str = "") -> dict:
    acct = account(sid)
    eq = api(f"/api/sim/{sid}/equity")["items"]
    trades = api(f"/api/sim/{sid}/trades")["items"]

    ret = acct["return_pct"]
    ann = mdd = ratio = None
    if eq:
        vals = [e["total_value"] for e in eq]
        years = len(vals) / 244
        ann = ((vals[-1] / vals[0]) ** (1 / years) - 1) * 100 if years > 0 and vals[0] else 0
        peak, mdd = vals[0], 0.0
        for v in vals:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        mdd *= 100
        ratio = ann / abs(mdd) if mdd else None

    fees = sum((t.get("fee") or 0) + (t.get("tax") or 0) for t in trades)
    bench = bench_return(sid, start, end)

    print("\n" + "=" * 64)
    print(f"策略：{name}   会话 #{sid}   {start} ~ {end}")
    if extra:
        print(extra)
    print("=" * 64)
    print(f"期末资产     {acct['total_value']:>14,.2f}     收益率 {ret:>8.2f}%")
    if ann is not None:
        ratio_s = f"{ratio:.2f}" if ratio is not None else "  n/a"
        print(f"年化收益     {ann:>14.2f}%     最大回撤 {mdd:>7.2f}%"
              f"     回撤比 {ratio_s}")
    print(f"成交笔数     {len(trades):>14d}     手续费+税 {fees:>9.2f}"
          f"（占本金 {fees/ acct['init_cash'] * 100:.2f}%）")
    if bench:
        print(f"基准沪深300  {bench[0]:>14.2f}%     最大回撤 {bench[1]:>7.2f}%"
              f"     超额 {ret - bench[0]:>7.2f}%")
    print(f"耗时 {seconds:.0f}s   看板 {BASE}/?sid={sid}")
    print("=" * 64, flush=True)

    return {"sid": sid, "name": name, "return": ret, "ann": ann or 0,
            "mdd": mdd or 0, "ratio": ratio or 0, "trades": len(trades),
            "fee": fees, "bench": bench[0] if bench else None,
            "bench_mdd": bench[1] if bench else None,
            "final": acct["total_value"]}


# ---------------------------------------------------------------- 主循环

def run_session(step, name: str, start: str = DEFAULT_START,
                end: str = DEFAULT_END, cash: float = DEFAULT_CASH,
                sid: int | None = None, every: int = 1,
                fill_price: str = "open", thscodes: list[str] | None = None) -> dict:
    """跑一个策略会话。

    step(sid, clock, acct, stats) -> str  返回当日决策说明，内部完成下单。
    every: 每隔多少天才调用一次 step（其余日子直接推钟），用于低频策略提速。
    fill_price：固定为 open（平台唯一模式）。open 即「看盘屏蔽当日 bar、MARKET 单按开盘价成交」，
                确保决策仅用截至昨日的数据，无未来函数。
    thscodes: 自定义标的池（个股选股策略用）。默认 CODES+[DEFENSIVE]（6 宽基 ETF+国债）。
    """
    global FILL_PRICE
    FILL_PRICE = fill_price
    universe = thscodes if thscodes is not None else CODES + [DEFENSIVE]
    if not sid:
        r = api("/api/sim/session", "POST", {
            "name": name, "thscodes": universe,
            "start": start, "end": end, "init_cash": cash, "fill_price": fill_price,
        })
        sid = r["session_id"]
        print(f"[{name}] 会话 #{sid} 创建：{start} ~ {end}，本金 {cash:,.0f}"
              f"，撮合 {fill_price}", flush=True)

    stats: dict = {}
    t0 = time.time()
    while True:
        clock = api(f"/api/sim/{sid}/clock")
        if clock["status"] != "running":
            break
        d, i = clock["current_date"], clock["day_index"]
        if (i - 1) % every == 0:
            note = step(sid, clock, account(sid), stats) or "持有不动"
            add_log(sid, note)
        finish(sid, d)
        if i % 50 == 0:
            a = account(sid)
            print(f"  [{i}/{clock['total_days']}] {d}  {a['total_value']:>10,.0f}"
                  f"  {a['return_pct']:+7.2f}%", flush=True)

    return report(sid, name, start, end, time.time() - t0)


def cli(desc: str, every: int = 1):
    """各策略共用的命令行参数。"""
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--sid", type=int)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--cash", type=float, default=DEFAULT_CASH)
    ap.add_argument("--fill-price", default="open",
                   choices=["open"],
                   help="撮合模式：平台仅支持 open（看盘屏蔽当日，按开盘价市价成交，无未来函数）")
    return ap.parse_args()
