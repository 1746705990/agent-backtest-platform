"""通用策略运行器：运行任意一份策略脚本，打印回测绩效并与沪深300基准对比。

本平台是「策略代码生成沙盒」——策略本身由外部 harness 根据使用者的需求现写，
放进 agents/strategies/ 后，用本脚本一键回测，结果（K线/买卖点/净值曲线）展示在前端看板。

用法：
  python agents/strategies/run_all.py my_strategy.py
  python agents/strategies/run_all.py my_strategy.py --start 2023-05-29 --end 2026-08-01 --cash 10000
  python agents/strategies/run_all.py a.py b.py c.py        # 多个策略横向对比
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import BASE, BENCH, api, bars, DEFAULT_START, DEFAULT_END, DEFAULT_CASH   # noqa: E402


def resolve(path: str) -> str:
    """脚本名（同目录查找）或完整路径都接受。"""
    if os.path.exists(path):
        return path
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if os.path.exists(cand):
        return cand
    raise SystemExit(f"找不到策略脚本：{path}")


def summarize(sid: int) -> dict | None:
    acct = api(f"/api/sim/{sid}/account")
    eq = api(f"/api/sim/{sid}/equity")["items"]
    trades = api(f"/api/sim/{sid}/trades")["items"]
    if not eq:
        return None
    vals = [e["total_value"] for e in eq]
    years = len(vals) / 244
    ann = ((vals[-1] / vals[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mdd *= 100
    fee = sum((t.get("fee") or 0) + (t.get("tax") or 0) for t in trades)
    return {"ret": acct["return_pct"], "ann": ann, "mdd": mdd,
            "ratio": ann / abs(mdd) if mdd else 0, "trades": len(trades),
            "fee": fee, "final": acct["total_value"], "days": len(eq)}


def bench(start: str, end: str, sid: int) -> tuple[float, float] | None:
    bs = [b for b in bars(sid, BENCH, 3000) if start <= b["date"] <= end]
    if len(bs) < 2:
        return None
    ret = (bs[-1]["close"] / bs[0]["close"] - 1) * 100
    peak, mdd = bs[0]["close"], 0.0
    for b in bs:
        peak = max(peak, b["close"])
        mdd = min(mdd, b["close"] / peak - 1)
    return ret, mdd * 100


def main() -> None:
    ap = argparse.ArgumentParser(description="运行任意策略脚本并汇总回测绩效")
    ap.add_argument("scripts", nargs="+",
                    help="策略脚本路径或文件名（可多个，横向对比）")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--cash", type=float, default=DEFAULT_CASH)
    ap.add_argument("--fill-price", default="open",
                   choices=["open"],
                   help="撮合模式：平台仅支持 open（看盘屏蔽当日，按开盘价成交，无未来函数）")
    args = ap.parse_args()

    t0 = time.time()
    for raw in args.scripts:
        fn = resolve(raw)
        print(f"\n{'#' * 64}\n# {os.path.basename(fn)}\n{'#' * 64}", flush=True)
        subprocess.run([sys.executable, fn,
                        "--start", args.start, "--end", args.end,
                        "--cash", str(args.cash), "--fill-price", args.fill_price])

    sessions = api("/api/sim/sessions")["items"]
    rows = []
    for s in sorted(sessions, key=lambda x: x["session_id"]):
        m = summarize(s["session_id"])
        if m:
            rows.append({"sid": s["session_id"], "name": s["name"], **m})

    b = bench(args.start, args.end, rows[0]["sid"]) if rows else None

    print("\n" + "=" * 92)
    print(f"策略横向对比   {args.start} ~ {args.end}   本金 {args.cash:,.0f} 元"
          f"   （每个策略一个独立会话）")
    print("=" * 92)
    print(f"{'策略':<18}{'会话':>5}{'期末':>11}{'收益率':>10}{'年化':>9}"
          f"{'最大回撤':>10}{'回撤比':>8}{'笔数':>6}{'费用':>8}{'超额':>10}")
    print("-" * 92)
    for r in rows:
        ex = r["ret"] - b[0] if b else None
        print(f"{r['name']:<18}{r['sid']:>5}{r['final']:>11,.0f}{r['ret']:>9.2f}%"
              f"{r['ann']:>8.2f}%{r['mdd']:>9.2f}%{r['ratio']:>8.2f}"
              f"{r['trades']:>6d}{r['fee']:>8.0f}"
              f"{(f'{ex:+.2f}%' if ex is not None else '   n/a'):>10}")
    if b:
        print("-" * 92)
        print(f"{'基准 沪深300':<18}{'—':>5}{'—':>11}{b[0]:>9.2f}%{'—':>9}"
              f"{b[1]:>9.2f}%{'—':>8}{'—':>6}{'—':>8}{'0.00%':>10}")
    print("=" * 92)
    print(f"总耗时 {time.time() - t0:.0f}s   看板 {BASE}/")


if __name__ == "__main__":
    main()
