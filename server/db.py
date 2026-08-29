"""SQLite 存储层。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    thscodes TEXT NOT NULL,          -- JSON list
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    current_date TEXT NOT NULL,
    init_cash REAL NOT NULL,
    fill_price TEXT NOT NULL DEFAULT 'open',  -- 平台仅支持 open（看盘屏蔽当日，按开盘价成交）
    status TEXT NOT NULL DEFAULT 'running',   -- running | finished
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kline_daily (
    thscode TEXT NOT NULL,
    date TEXT NOT NULL,              -- YYYY-MM-DD
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, turnover REAL,
    vwap REAL,                       -- 按复权因子校准后的当日成交均价
    PRIMARY KEY (thscode, date)
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    init_cash REAL NOT NULL,
    cash REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS position (
    account_id INTEGER NOT NULL,
    thscode TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    available_qty INTEGER NOT NULL DEFAULT 0,  -- T+1：当日买入不可卖
    avg_cost REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, thscode)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    sim_date TEXT NOT NULL,
    thscode TEXT NOT NULL,
    side TEXT NOT NULL,              -- BUY | SELL
    qty INTEGER NOT NULL,
    type TEXT NOT NULL,              -- MARKET | LIMIT
    limit_price REAL,
    status TEXT NOT NULL,            -- FILLED | PENDING | REJECTED | EXPIRED
    fill_price REAL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    sim_date TEXT NOT NULL,
    thscode TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty INTEGER NOT NULL,
    fee REAL NOT NULL,
    tax REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshot (
    account_id INTEGER NOT NULL,
    sim_date TEXT NOT NULL,
    cash REAL NOT NULL,
    position_value REAL NOT NULL,
    total_value REAL NOT NULL,
    PRIMARY KEY (account_id, sim_date)
);

CREATE TABLE IF NOT EXISTS day_finish (
    session_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    sim_date TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, account_id, sim_date)
);

CREATE TABLE IF NOT EXISTS agent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    sim_date TEXT NOT NULL,
    note TEXT NOT NULL,              -- Agent 当日决策理由
    actions TEXT                     -- JSON：当日实际执行的动作摘要
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
