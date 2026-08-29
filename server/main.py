"""FastAPI 入口：Agent REST API + 极简看板（含“今日操作完毕”按钮）。

启动：python -m uvicorn server.main:app --port 8000
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, engine
from .fuyao_client import FuyaoError

# 统一默认参数来源：项目根目录 config.py
import os, sys
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
while _PROJ != os.path.dirname(_PROJ) and not os.path.exists(os.path.join(_PROJ, "config.py")):
    _PROJ = os.path.dirname(_PROJ)
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)
from config import (DEFAULT_START, DEFAULT_END, DEFAULT_CASH,
                    DEFAULT_CODES, DEFAULT_STOCK)

app = FastAPI(title="agent-backtest-platform", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def conn():
    return db.connect()


def wrap(fn, *args, **kwargs):
    c = conn()
    try:
        return fn(c, *args, **kwargs)
    except (engine.EngineError, FuyaoError) as e:
        raise HTTPException(400, str(e))
    finally:
        c.close()


# ---------------- 会话 ----------------

class SessionIn(BaseModel):
    name: str = "backtest"
    thscodes: list[str] = []                   # 初始观察池（可选，仅预拉数据，不构成交易限制）
    fund_thscodes: list[str] = []              # ETF代码（可选）
    start: date
    end: date
    init_cash: float = DEFAULT_CASH
    fill_price: str = "open"                   # 平台仅支持 open


@app.post("/api/sim/session")
def create_session(body: SessionIn):
    try:
        # 平台统一约束：仅 open 模式，忽略客户端传入的其它值
        sid = engine.create_session(
            body.name, body.thscodes, body.start, body.end,
            body.init_cash, "open", body.fund_thscodes,
        )
    except (engine.EngineError, FuyaoError) as e:
        raise HTTPException(400, str(e))
    return {"session_id": sid}


@app.get("/api/sim/sessions")
def sessions():
    """查询所有现有会话。"""
    return {"items": wrap(engine.list_sessions)}


@app.delete("/api/sim/{sid}")
def delete_session(sid: int):
    """删除会话及其交易数据（行情缓存保留，跨会话共享）。"""
    return wrap(engine.delete_session, sid)


# ---------------- 时钟 / 行情 ----------------

@app.get("/api/sim/{sid}/clock")
def clock(sid: int):
    return wrap(engine.clock_info, sid)


@app.get("/api/sim/{sid}/calendar")
def calendar(sid: int):
    return {"days": wrap(engine.trading_days, sid)}


@app.get("/api/sim/{sid}/kline")
def kline(sid: int, thscode: str, limit: int = 60):
    return {"thscode": thscode, "items": wrap(engine.kline, sid, thscode, limit)}


@app.get("/api/sim/{sid}/quote")
def quote(sid: int, thscode: str):
    return wrap(engine.quote, sid, thscode)


# ---------------- 交易 ----------------

class OrderIn(BaseModel):
    thscode: str
    side: str                                # BUY | SELL
    qty: int
    type: str = "MARKET"                     # 平台仅支持 MARKET（市价单）
    limit_price: float | None = None


@app.post("/api/sim/{sid}/orders")
def place_order(sid: int, body: OrderIn):
    # 平台统一约束：仅市价单，忽略客户端传入的其它订单类型
    return wrap(engine.place_order, sid, body.thscode, body.side, body.qty,
                "MARKET", body.limit_price)


@app.get("/api/sim/{sid}/orders")
def orders(sid: int, sim_date: str | None = None):
    return {"items": wrap(engine.list_orders, sid, sim_date)}


@app.get("/api/sim/{sid}/trades")
def trades(sid: int):
    return {"items": wrap(engine.list_trades, sid)}


# ---------------- 账户 ----------------

@app.get("/api/sim/{sid}/account")
def account(sid: int):
    return wrap(engine.account_info, sid)


@app.get("/api/sim/{sid}/equity")
def equity(sid: int):
    return {"items": wrap(engine.equity_curve, sid)}


# ---------------- Agent 决策日志 ----------------

class LogIn(BaseModel):
    note: str                                # 当日决策理由
    actions: list[dict] = []                 # 当日动作摘要


@app.post("/api/sim/{sid}/log")
def add_log(sid: int, body: LogIn):
    return wrap(engine.add_log, sid, body.note, body.actions)


@app.get("/api/sim/{sid}/logs")
def logs(sid: int):
    return {"items": wrap(engine.list_logs, sid)}


# ---------------- “今日操作完毕” ----------------

class FinishIn(BaseModel):
    sim_date: str | None = None   # 调用方认为正在结束的日期；不一致时拒绝推进（防重复点击跳日）


@app.post("/api/sim/{sid}/day/finish")
def finish(sid: int, body: FinishIn | None = None):
    """确认今日操作完毕：结算 → 时钟推进一天。建议携带 sim_date 防重复提交。"""
    return wrap(engine.finish_day, sid, body.sim_date if body else None)


# ---------------- 配置（前端启动时动态拉取，实现默认值完全同步） ----------------

@app.get("/api/config")
def get_config():
    """返回回测默认参数，数据源为项目根 config.py。

    前端在启动时调用本接口自动填充表单，避免默认值写死在 HTML 中，
    做到「改 config.py 即全端生效」。
    """
    return {
        "start": DEFAULT_START,
        "end": DEFAULT_END,
        "cash": DEFAULT_CASH,
        "codes": DEFAULT_CODES,     # 多股票入口默认观察/交易池
        "stock": DEFAULT_STOCK,     # 单股票策略默认标的（前端 K 线查询兜底）
    }


# ---------------- 极简看板 ----------------

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico")
def favicon():
    """内置图标，避免浏览器请求 /favicon.ico 时 404。"""
    return FileResponse(
        Path(__file__).parent / "static" / "favicon.ico",
        media_type="image/x-icon",
    )
