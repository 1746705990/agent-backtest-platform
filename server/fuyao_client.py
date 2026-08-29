"""扶摇（同花顺）金融数据 API 客户端。

认证：请求头 X-api-key
时间参数：start/end 为毫秒 Unix 时间戳；date_ms 按 UTC 零点编码（上海交易日）。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta, date

import requests

from . import config

# 上游 date_ms 按 Asia/Shanghai 零点编码（文档：交易日期按 Asia/Shanghai 解释）
SH_TZ = timezone(timedelta(hours=8))


class FuyaoError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"fuyao error {code}: {message}")


def date_to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=SH_TZ).timestamp() * 1000)


def ms_to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=SH_TZ).date()


class FuyaoClient:
    def __init__(self, api_key: str | None = None, base_url: str = config.FUYAO_BASE_URL):
        self.base_url = base_url
        self.http = requests.Session()
        self.http.headers["X-api-key"] = api_key or config.FUYAO_API_KEY

    def _get(self, path: str, params: dict, retries: int = 4) -> dict:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                r = self.http.get(self.base_url + path, params=params, timeout=30)
                payload = r.json()
            except Exception as e:  # 网络层错误重试
                last_err = e
                time.sleep(1 + attempt)
                continue
            code = payload.get("code")
            if code == 0:
                return payload["data"]
            if code == 429:  # 限流：退避重试
                time.sleep(3 * (attempt + 1))
                continue
            raise FuyaoError(code, payload.get("message", ""))
        raise FuyaoError(429, "request limit exceeded（重试后仍限流）")

    # ---------------- A 股 ----------------

    def a_share_historical(
        self, thscode: str, start: date, end: date, adjust: str = "forward"
    ) -> list[dict]:
        """返回 [{date, open, high, low, close, volume, turnover}]，按日期升序。"""
        data = self._get(
            "/api/a-share/prices/historical",
            {
                "thscode": thscode,
                "interval": "1d",
                "start": date_to_ms(start),
                "end": date_to_ms(end),
                "adjust": adjust,
            },
        )
        items = data.get("item") or []
        return [
            {
                "date": ms_to_date(it["date_ms"]).isoformat(),
                "open": it["open_price"],
                "high": it["high_price"],
                "low": it["low_price"],
                "close": it["close_price"],
                "volume": it["volume"],
                "turnover": it["turnover"],
            }
            for it in items
        ]

    def a_share_snapshot(self, thscodes: list[str]) -> list[dict]:
        data = self._get(
            "/api/a-share/prices/snapshot", {"thscodes": ",".join(thscodes)}
        )
        return data.get("item") or []

    # ---------------- 基金（ETF） ----------------

    def fund_historical(self, thscode: str, start: date, end: date) -> list[dict]:
        data = self._get(
            "/api/fund/market/historical",
            {
                "thscode": thscode,
                "interval": "1d",
                "start": date_to_ms(start),
                "end": date_to_ms(end),
            },
        )
        items = data.get("item") or []
        return [
            {
                "date": ms_to_date(it["date_ms"]).isoformat(),
                "open": it["open_price"],
                "high": it["high_price"],
                "low": it["low_price"],
                "close": it["close_price"],
                "volume": it["volume"],
                "turnover": it["turnover"],
            }
            for it in items
        ]

    # ---------------- 指数 ----------------

    def index_historical(self, thscode: str, start: date, end: date) -> list[dict]:
        """指数历史K线（如 000001.SH 上证指数），用于生成交易日历。"""
        data = self._get(
            "/api/a-share-index/prices/historical",
            {
                "thscode": thscode,
                "interval": "1d",
                "start": date_to_ms(start),
                "end": date_to_ms(end),
            },
        )
        items = data.get("item") or []
        return [
            {
                "date": ms_to_date(it["date_ms"]).isoformat(),
                "open": it["open_price"],
                "high": it["high_price"],
                "low": it["low_price"],
                "close": it["close_price"],
                "volume": it.get("volume"),
                "turnover": it.get("turnover"),
            }
            for it in items
        ]
