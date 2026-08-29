"""回测引擎：回合制时钟 + 当日撮合 + 账户结算。

核心循环（交易日 D）：
  1. Agent 查询截至 D 的行情 / 账户（open 模式：D 即昨日，当日数据不可见）
  2. Agent 下单，当日以开盘价市价成交（open 模式，仅市价单）
  3. Agent 调 finish_day() —— “今日操作完毕”
  4. 结算：未成交委托作废、T+1 解冻、净值快照、时钟推进到下一交易日
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from . import config, db
from .fuyao_client import FuyaoClient, FuyaoError

LOOKBACK_DAYS = 400  # 会话开始前多拉的行情，供 Agent 回溯
INDEX_CODE = "000001.SH"  # 上证指数：用于生成交易日历

_default_client: FuyaoClient | None = None


def fuyao() -> FuyaoClient:
    global _default_client
    if _default_client is None:
        _default_client = FuyaoClient()
    return _default_client


class EngineError(Exception):
    pass


# ---------------------------------------------------------------- 工具

def limit_pct(thscode: str) -> float:
    ticker = thscode.split(".")[0]
    if ticker.startswith(("300", "301", "688", "689")):
        return config.LIMIT_PCT_CHINEXT_STAR
    return config.LIMIT_PCT_MAIN


def vwap(bar: sqlite3.Row) -> float:
    """当日成交均价（已按复权因子校准），无成交量时退化为收盘价。"""
    if "vwap" in bar.keys() and bar["vwap"]:
        return bar["vwap"]
    if bar["volume"] and bar["volume"] > 0:
        return bar["turnover"] / bar["volume"]
    return bar["close"]


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 会话

def create_session(
    name: str,
    thscodes: list[str] | None,
    start: date,
    end: date,
    init_cash: float,
    fill_price: str = "open",
    fund_thscodes: list[str] | None = None,
    client: FuyaoClient | None = None,
) -> int:
    """创建回测会话：拉取指数生成交易日历，预拉初始标的（可选）。

    会话不锁定股票池——Agent 之后可通过 kline/quote/orders
    对任意 A 股代码按需查询和交易（首次访问时自动拉取落库）。
    """
    if fill_price != "open":
        # 平台统一约束：仅 open 模式（看盘屏蔽当日，按开盘价市价成交），杜绝未来函数
        raise EngineError("本平台仅支持 open 模式（fill_price 必须是 open）")
    client = client or FuyaoClient()
    db.init_db()
    conn = db.connect()
    try:
        fetch_start = start - timedelta(days=int(LOOKBACK_DAYS * 1.6))
        # 交易日历：上证指数
        idx_bars = client.index_historical(INDEX_CODE, fetch_start, end)
        _store_bars(conn, INDEX_CODE, idx_bars)
        days = sorted(b["date"] for b in idx_bars
                      if start.isoformat() <= b["date"] <= end.isoformat())
        if not days:
            raise EngineError(f"区间内无交易日数据: {start} ~ {end}")

        # 初始观察池（可选，仅预拉数据，不构成交易限制）
        for code in list(thscodes or []) + list(fund_thscodes or []):
            if code == INDEX_CODE:
                continue
            try:
                try:
                    bars = _fetch_bars_consistent(client, code, fetch_start, end,
                                                  is_fund=code in (fund_thscodes or []))
                except FuyaoError:
                    # A股接口查不到时按基金（ETF）再试一次
                    bars = _fetch_bars_consistent(client, code, fetch_start, end,
                                                  is_fund=True)
                _store_bars(conn, code, bars)
            except FuyaoError as e:
                raise EngineError(f"预拉 {code} 行情失败: {e}")

        universe = list(thscodes or []) + list(fund_thscodes or [])
        # 会话编号复用：取当前最小的空闲正整数（删除旧会话后小号可被再次使用）
        used = {r[0] for r in conn.execute("SELECT id FROM session")}
        new_id = 1
        while new_id in used:
            new_id += 1
        cur = conn.execute(
            """INSERT INTO session(id, name, thscodes, start_date, end_date, current_date,
                                   init_cash, fill_price)
               VALUES (?,?,?,?,?,?,?,?)""",
            (new_id, name, json.dumps(universe), start.isoformat(), end.isoformat(),
             days[0], init_cash, fill_price),
        )
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO account(session_id, name, init_cash, cash) VALUES (?,?,?,?)",
            (sid, "default", init_cash, init_cash),
        )
        conn.commit()
        return sid
    finally:
        conn.close()


def ensure_symbol(conn: sqlite3.Connection, sid: int, thscode: str) -> None:
    """按需拉取任意标的的历史行情落库（覆盖回测区间+回溯段）。

    数据落库后所有查询仍按 current_date 截断，不会泄露未来数据。
    """
    if thscode == INDEX_CODE:
        return
    sess = get_session(conn, sid)
    need_start = (date.fromisoformat(sess["start_date"])
                  - timedelta(days=int(LOOKBACK_DAYS * 1.6))).isoformat()
    row = conn.execute(
        "SELECT MIN(date) mn, MAX(date) mx FROM kline_daily WHERE thscode=?", (thscode,)
    ).fetchone()
    if row["mn"] and row["mn"] <= need_start and row["mx"] >= sess["end_date"]:
        return
    s, e = date.fromisoformat(need_start), date.fromisoformat(sess["end_date"])
    try:
        bars = _fetch_bars_consistent(fuyao(), thscode, s, e, is_fund=False)
    except FuyaoError:
        # A股查不到时按基金（ETF）再试一次
        bars = _fetch_bars_consistent(fuyao(), thscode, s, e, is_fund=True)
    if not bars:
        raise EngineError(f"无法获取 {thscode} 的行情数据（代码有误或无覆盖）")
    _store_bars(conn, thscode, bars)
    conn.commit()


def _fetch_bars_consistent(client: FuyaoClient, code: str,
                           start: date, end: date, is_fund: bool) -> list[dict]:
    """拉取前复权K线，并把 VWAP 校准到同一复权口径。

    上游 adjust=forward 只调整价格，volume/turnover 仍是原始值，
    直接 turnover/volume 会与复权价脱节。这里再拉一次 adjust=none，
    按每日 factor = close_forward / close_none 校准 VWAP。
    """
    fetch_fwd = client.fund_historical if is_fund else client.a_share_historical
    fwd = fetch_fwd(code, start, end)
    if is_fund:
        raw = []
    else:
        raw = client.a_share_historical(code, start, end, adjust="none")
    raw_close = {b["date"]: b["close"] for b in raw}
    for b in fwd:
        rc = raw_close.get(b["date"])
        factor = (b["close"] / rc) if rc else 1.0
        b["vwap"] = round(b["turnover"] / b["volume"] * factor, 6) if b["volume"] else b["close"]
        for k in ("open", "high", "low", "close"):
            b[k] = round(b[k], 6)
    return fwd


def _store_bars(conn: sqlite3.Connection, thscode: str, bars: list[dict]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO kline_daily(thscode, date, open, high, low, close, volume, turnover, vwap)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(thscode, b["date"], b["open"], b["high"], b["low"], b["close"],
          b["volume"], b["turnover"], b.get("vwap")) for b in bars],
    )


# ---------------------------------------------------------------- 时钟 / 日历

def get_session(conn: sqlite3.Connection, sid: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    if not row:
        raise EngineError(f"会话不存在: {sid}")
    return row


def trading_days(conn: sqlite3.Connection, sid: int) -> list[str]:
    """交易日历：来自上证指数K线（与个股停牌无关）。"""
    sess = get_session(conn, sid)
    rows = conn.execute(
        """SELECT date FROM kline_daily
           WHERE thscode=? AND date BETWEEN ? AND ? ORDER BY date""",
        (INDEX_CODE, sess["start_date"], sess["end_date"]),
    ).fetchall()
    return [r["date"] for r in rows]


def clock_info(conn: sqlite3.Connection, sid: int) -> dict:
    sess = get_session(conn, sid)
    days = trading_days(conn, sid)
    idx = days.index(sess["current_date"]) if sess["current_date"] in days else -1
    finished = conn.execute(
        "SELECT 1 FROM day_finish WHERE session_id=? AND sim_date=?",
        (sid, sess["current_date"]),
    ).fetchone() is not None
    # universe = 已缓存（可查）的标的，供看板/Agent 参考；不构成交易限制
    cached = [r["thscode"] for r in conn.execute(
        "SELECT DISTINCT thscode FROM kline_daily ORDER BY thscode").fetchall()]
    return {
        "session_id": sid,
        "status": sess["status"],
        "current_date": sess["current_date"],
        "day_index": idx + 1,
        "total_days": len(days),
        "start_date": sess["start_date"],
        "end_date": sess["end_date"],
        "today_finished": finished,
        "fill_price": sess["fill_price"],
        "universe": cached,
    }


# ---------------------------------------------------------------- 行情查询（截断到当前日）

def kline(conn: sqlite3.Connection, sid: int, thscode: str, limit: int = 60) -> list[dict]:
    """截至昨日（不含当日）的 K 线（升序）。

    平台仅 open 模式：看盘阶段屏蔽当日 bar，只暴露截至昨日的数据，
    强制 Agent「盘前决策只用历史」。当日行情在 finish_day 后才揭示
    （时钟推进到下一交易日后自然可见），从机制上杜绝未来函数。
    """
    ensure_symbol(conn, sid, thscode)
    sess = get_session(conn, sid)
    rows = conn.execute(
        """SELECT date, open, high, low, close, volume, turnover FROM kline_daily
           WHERE thscode=? AND date < ? ORDER BY date DESC LIMIT ?""",
        (thscode, sess["current_date"], limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def quote(conn: sqlite3.Connection, sid: int, thscode: str) -> dict:
    """截至昨日（不含当日）的最新行情快照。

    平台仅 open 模式：今日行情尚未揭示，返回截至昨日的最新一根 bar；
    vwap 等字段仅为参考，不参与撮合（成交价固定为当日开盘价）。
    """
    ensure_symbol(conn, sid, thscode)
    sess = get_session(conn, sid)
    # 今日行情尚未揭示（open 模式）：返回截至昨日的最新快照
    bar = conn.execute(
        "SELECT * FROM kline_daily WHERE thscode=? AND date<? ORDER BY date DESC LIMIT 1",
        (thscode, sess["current_date"]),
    ).fetchone()
    if not bar:
        raise EngineError(f"{thscode} 今日行情尚未揭示（无历史数据）")
    prev = conn.execute(
        "SELECT close FROM kline_daily WHERE thscode=? AND date<? ORDER BY date DESC LIMIT 1",
        (thscode, sess["current_date"]),
    ).fetchone()
    d = dict(bar)
    d["thscode"] = thscode
    d["prev_close"] = prev["close"] if prev else None
    d["vwap"] = round(vwap(bar), 4)
    return d


# ---------------------------------------------------------------- 下单 / 撮合

def place_order(
    conn: sqlite3.Connection,
    sid: int,
    thscode: str,
    side: str,
    qty: int,
    order_type: str = "MARKET",
    limit_price: float | None = None,
) -> dict:
    sess = get_session(conn, sid)
    if sess["status"] != "running":
        raise EngineError("回测已结束")
    side = side.upper()
    order_type = order_type.upper()
    if side not in ("BUY", "SELL"):
        raise EngineError("side 只能是 BUY 或 SELL")
    if order_type != "MARKET":
        # 平台统一约束：仅允许市价单，避免挂单与当日高低价相关的未来信息泄露
        raise EngineError("本平台仅允许市价单（MARKET），不支持限价单（LIMIT）")
    if qty <= 0:
        raise EngineError("qty 必须为正")
    if side == "BUY" and qty % config.LOT_SIZE != 0:
        raise EngineError(f"买入必须为 {config.LOT_SIZE} 股整手")
    if sess["current_date"] > sess["end_date"]:
        raise EngineError("已超出回测区间")

    account = conn.execute(
        "SELECT * FROM account WHERE session_id=? ORDER BY id LIMIT 1", (sid,)
    ).fetchone()
    aid = account["id"]
    today = sess["current_date"]

    ensure_symbol(conn, sid, thscode)
    bar = conn.execute(
        "SELECT * FROM kline_daily WHERE thscode=? AND date=?", (thscode, today)
    ).fetchone()
    if not bar:
        return _reject(conn, sid, aid, today, thscode, side, qty, order_type,
                       limit_price, "当日无行情（停牌）")

    prev = conn.execute(
        "SELECT close FROM kline_daily WHERE thscode=? AND date<? ORDER BY date DESC LIMIT 1",
        (thscode, today),
    ).fetchone()
    prev_close = prev["close"] if prev else bar["open"]
    pct = limit_pct(thscode)
    limit_up = round(prev_close * (1 + pct), 2)
    limit_down = round(prev_close * (1 - pct), 2)

    # 涨跌停：整日封死涨停不能买（最低价==涨停价），封死跌停不能卖
    if side == "BUY" and bar["low"] >= limit_up - 1e-9:
        return _reject(conn, sid, aid, today, thscode, side, qty, order_type,
                       limit_price, f"涨停封死（{limit_up}），无法买入")
    if side == "SELL" and bar["high"] <= limit_down + 1e-9:
        return _reject(conn, sid, aid, today, thscode, side, qty, order_type,
                       limit_price, f"跌停封死（{limit_down}），无法卖出")

    # 卖出：T+1 可用数量检查
    if side == "SELL":
        pos = conn.execute(
            "SELECT * FROM position WHERE account_id=? AND thscode=?", (aid, thscode)
        ).fetchone()
        available = pos["available_qty"] if pos else 0
        if qty > available:
            return _reject(conn, sid, aid, today, thscode, side, qty, order_type,
                           limit_price, f"可卖数量不足（T+1 可用 {available} 股）")

    # 定价：平台固定 open 模式 + 市价单，按当日开盘价（集合竞价/开盘首个可成交价）撮合。
    # 决策用截至昨日的数据，今日开盘成交——盘前决策与盘中成交之间存在时滞，彻底无未来函数。
    price = bar["open"]
    # 成交价夹在当日真实范围内，并受涨跌停约束
    price = min(max(price, bar["low"]), bar["high"], limit_up)
    price = max(price, limit_down)
    if config.SLIPPAGE:
        price *= (1 + config.SLIPPAGE) if side == "BUY" else (1 - config.SLIPPAGE)
    price = round(price, 4)

    amount = price * qty
    fee = max(amount * config.COMMISSION_RATE, config.COMMISSION_MIN)
    tax = amount * config.STAMP_TAX_RATE if side == "SELL" else 0.0

    if side == "BUY":
        cost = amount + fee
        if cost > account["cash"] + 1e-9:
            return _reject(conn, sid, aid, today, thscode, side, qty, order_type,
                           limit_price, f"资金不足（需 {cost:.2f}，可用 {account['cash']:.2f}）")
        conn.execute("UPDATE account SET cash=cash-? WHERE id=?", (cost, aid))
        pos = conn.execute(
            "SELECT * FROM position WHERE account_id=? AND thscode=?", (aid, thscode)
        ).fetchone()
        if pos:
            new_qty = pos["qty"] + qty
            new_cost = (pos["avg_cost"] * pos["qty"] + amount) / new_qty
            conn.execute(
                "UPDATE position SET qty=?, avg_cost=? WHERE account_id=? AND thscode=?",
                (new_qty, new_cost, aid, thscode),
            )
        else:
            conn.execute(
                "INSERT INTO position(account_id, thscode, qty, available_qty, avg_cost)"
                " VALUES (?,?,?,0,?)",
                (aid, thscode, qty, amount / qty),
            )
    else:  # SELL
        proceeds = amount - fee - tax
        conn.execute("UPDATE account SET cash=cash+? WHERE id=?", (proceeds, aid))
        conn.execute(
            "UPDATE position SET qty=qty-?, available_qty=available_qty-?"
            " WHERE account_id=? AND thscode=?",
            (qty, qty, aid, thscode),
        )
        conn.execute(
            "DELETE FROM position WHERE account_id=? AND thscode=? AND qty<=0",
            (aid, thscode),
        )

    cur = conn.execute(
        """INSERT INTO orders(session_id, account_id, sim_date, thscode, side, qty, type,
                              limit_price, status, fill_price, message)
           VALUES (?,?,?,?,?,?,?,?, 'FILLED', ?, ?)""",
        (sid, aid, today, thscode, side, qty, order_type, limit_price, price,
         f"成交价 {price}（开盘价市价成交）"),
    )
    oid = cur.lastrowid
    conn.execute(
        """INSERT INTO trade(order_id, account_id, session_id, sim_date, thscode, side,
                             price, qty, fee, tax)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (oid, aid, sid, today, thscode, side, price, qty, round(fee, 2), round(tax, 2)),
    )
    conn.commit()
    return {"order_id": oid, "status": "FILLED", "fill_price": price, "qty": qty,
            "fee": round(fee, 2), "tax": round(tax, 2)}


def _reject(conn, sid, aid, today, thscode, side, qty, otype, limit_price, msg) -> dict:
    cur = conn.execute(
        """INSERT INTO orders(session_id, account_id, sim_date, thscode, side, qty, type,
                              limit_price, status, message)
           VALUES (?,?,?,?,?,?,?,?, 'REJECTED', ?)""",
        (sid, aid, today, thscode, side, qty, otype, limit_price, msg),
    )
    conn.commit()
    return {"order_id": cur.lastrowid, "status": "REJECTED", "message": msg}


# ---------------------------------------------------------------- "今日操作完毕"

def finish_day(conn: sqlite3.Connection, sid: int, sim_date: str | None = None) -> dict:
    """结算当日并推进时钟到下一交易日。

    幂等保护：调用方应携带它认为正在结束的日期 sim_date；
    若与时钟当前日不一致（说明时钟已推进过），拒绝并返回 409 语义错误，
    防止 Agent/用户重复点击导致跳日。
    """
    sess = get_session(conn, sid)
    if sess["status"] != "running":
        return clock_info(conn, sid) | {"message": "回测已结束"}
    today = sess["current_date"]

    if sim_date and sim_date != today:
        raise EngineError(
            f"确认日期 {sim_date} 与时钟当前日 {today} 不一致，"
            f"该日可能已结算过，时钟未推进（请重新查询时钟）"
        )

    already = conn.execute(
        "SELECT 1 FROM day_finish WHERE session_id=? AND sim_date=?", (sid, today)
    ).fetchone()
    if already:
        return clock_info(conn, sid) | {"message": f"{today} 已确认过，时钟未重复推进"}

    account = conn.execute(
        "SELECT * FROM account WHERE session_id=? ORDER BY id LIMIT 1", (sid,)
    ).fetchone()
    aid = account["id"]

    # 当日未成交挂单作废
    expired = conn.execute(
        """UPDATE orders SET status='EXPIRED', message='日终未成交自动作废'
           WHERE session_id=? AND sim_date=? AND status='PENDING'""",
        (sid, today),
    ).rowcount

    # T+1 解冻
    conn.execute("UPDATE position SET available_qty=qty WHERE account_id=?", (aid,))

    # 净值快照（按当日收盘价计持仓市值）
    positions = conn.execute(
        "SELECT thscode, qty FROM position WHERE account_id=?", (aid,)
    ).fetchall()
    pos_value = 0.0
    for p in positions:
        bar = conn.execute(
            "SELECT close FROM kline_daily WHERE thscode=? AND date<=? ORDER BY date DESC LIMIT 1",
            (p["thscode"], today),
        ).fetchone()
        if bar:
            pos_value += p["qty"] * bar["close"]
    cash = conn.execute("SELECT cash FROM account WHERE id=?", (aid,)).fetchone()["cash"]
    conn.execute(
        """INSERT OR REPLACE INTO equity_snapshot(account_id, sim_date, cash, position_value, total_value)
           VALUES (?,?,?,?,?)""",
        (aid, today, cash, pos_value, cash + pos_value),
    )

    conn.execute(
        "INSERT INTO day_finish(session_id, account_id, sim_date) VALUES (?,?,?)",
        (sid, aid, today),
    )

    # 推进时钟
    days = trading_days(conn, sid)
    idx = days.index(today) if today in days else -1
    if idx >= 0 and idx + 1 < len(days):
        conn.execute("UPDATE session SET current_date=? WHERE id=?", (days[idx + 1], sid))
        msg = f"{today} 结算完成，时钟推进到 {days[idx + 1]}"
    else:
        conn.execute("UPDATE session SET status='finished' WHERE id=?", (sid,))
        msg = f"{today} 是最后一个交易日，回测结束"
    conn.commit()
    return clock_info(conn, sid) | {"message": msg, "expired_orders": expired}


# ---------------------------------------------------------------- 账户查询

def account_info(conn: sqlite3.Connection, sid: int) -> dict:
    sess = get_session(conn, sid)
    account = conn.execute(
        "SELECT * FROM account WHERE session_id=? ORDER BY id LIMIT 1", (sid,)
    ).fetchone()
    aid = account["id"]
    positions = conn.execute(
        "SELECT * FROM position WHERE account_id=?", (aid,)
    ).fetchall()
    pos_list = []
    pos_value = 0.0
    for p in positions:
        # open 模式（K线训练助手）下，当日 bar 对 Agent 不可见；若某持仓标的历史尚未揭示
        # （如首日刚买入），quote 会抛错，这里降级用持仓成本估算市值，避免中断回测。
        try:
            q = quote(conn, sid, p["thscode"])
            q_close = q["close"] or p["avg_cost"]
        except EngineError:
            q_close = p["avg_cost"]
        mv = p["qty"] * q_close
        pos_value += mv
        pos_list.append({
            "thscode": p["thscode"], "qty": p["qty"],
            "available_qty": p["available_qty"], "avg_cost": round(p["avg_cost"], 4),
            "last_close": q_close, "market_value": round(mv, 2),
            "pnl": round((q_close - p["avg_cost"]) * p["qty"], 2),
        })
    return {
        "account_id": aid,
        "sim_date": sess["current_date"],
        "init_cash": account["init_cash"],
        "cash": round(account["cash"], 2),
        "position_value": round(pos_value, 2),
        "total_value": round(account["cash"] + pos_value, 2),
        "return_pct": round((account["cash"] + pos_value) / account["init_cash"] * 100 - 100, 4),
        "positions": pos_list,
    }


def equity_curve(conn: sqlite3.Connection, sid: int) -> list[dict]:
    rows = conn.execute(
        """SELECT e.sim_date, e.cash, e.position_value, e.total_value
           FROM equity_snapshot e JOIN account a ON e.account_id=a.id
           WHERE a.session_id=? ORDER BY e.sim_date""",
        (sid,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- Agent 决策日志

def add_log(conn: sqlite3.Connection, sid: int, note: str, actions: list | None = None) -> dict:
    sess = get_session(conn, sid)
    cur = conn.execute(
        "INSERT INTO agent_log(session_id, sim_date, note, actions) VALUES (?,?,?,?)",
        (sid, sess["current_date"], note, json.dumps(actions or [], ensure_ascii=False)),
    )
    conn.commit()
    return {"log_id": cur.lastrowid, "sim_date": sess["current_date"]}


def list_logs(conn: sqlite3.Connection, sid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_log WHERE session_id=? ORDER BY id", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_orders(conn: sqlite3.Connection, sid: int, sim_date: str | None = None) -> list[dict]:
    sql = "SELECT * FROM orders WHERE session_id=?"
    params: list = [sid]
    if sim_date:
        sql += " AND sim_date=?"
        params.append(sim_date)
    rows = conn.execute(sql + " ORDER BY id", params).fetchall()
    return [dict(r) for r in rows]


def list_trades(conn: sqlite3.Connection, sid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trade WHERE session_id=? ORDER BY id", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 会话管理

def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    """所有回测会话概览（含最新净值，按创建时间倒序）。"""
    rows = conn.execute("SELECT * FROM session ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        acct = conn.execute(
            "SELECT id, init_cash, cash FROM account WHERE session_id=? ORDER BY id LIMIT 1",
            (r["id"],),
        ).fetchone()
        snap = None
        if acct:
            snap = conn.execute(
                """SELECT total_value FROM equity_snapshot
                   WHERE account_id=? ORDER BY sim_date DESC LIMIT 1""",
                (acct["id"],),
            ).fetchone()
        n_trades = conn.execute(
            "SELECT COUNT(*) c FROM trade WHERE session_id=?", (r["id"],)
        ).fetchone()["c"]
        out.append({
            "session_id": r["id"],
            "name": r["name"],
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "current_date": r["current_date"],
            "status": r["status"],
            "init_cash": r["init_cash"],
            "watchlist": json.loads(r["thscodes"]),
            "trade_count": n_trades,
            "last_total_value": round(snap["total_value"], 2) if snap else None,
            "created_at": r["created_at"],
        })
    return out


def delete_session(conn: sqlite3.Connection, sid: int) -> dict:
    """删除会话及其全部交易数据。

    注意：不删 kline_daily——行情缓存是跨会话共享的市场数据。
    """
    get_session(conn, sid)  # 不存在则抛错
    aids = [r["id"] for r in conn.execute(
        "SELECT id FROM account WHERE session_id=?", (sid,)).fetchall()]
    counts = {}
    if aids:
        marks = ",".join("?" * len(aids))
        counts["position"] = conn.execute(
            f"DELETE FROM position WHERE account_id IN ({marks})", aids).rowcount
        counts["equity_snapshot"] = conn.execute(
            f"DELETE FROM equity_snapshot WHERE account_id IN ({marks})", aids).rowcount
        counts["day_finish"] = conn.execute(
            f"DELETE FROM day_finish WHERE account_id IN ({marks})", aids).rowcount
    counts["trade"] = conn.execute(
        "DELETE FROM trade WHERE session_id=?", (sid,)).rowcount
    counts["orders"] = conn.execute(
        "DELETE FROM orders WHERE session_id=?", (sid,)).rowcount
    counts["agent_log"] = conn.execute(
        "DELETE FROM agent_log WHERE session_id=?", (sid,)).rowcount
    counts["account"] = conn.execute(
        "DELETE FROM account WHERE session_id=?", (sid,)).rowcount
    counts["session"] = conn.execute(
        "DELETE FROM session WHERE id=?", (sid,)).rowcount
    conn.commit()
    return {"deleted_session": sid, "rows": counts}
