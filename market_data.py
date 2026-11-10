"""Yahoo Finance access. One place, thread-safe (Ticker.history, not the global-state yf.download).

Candles are returned as DataFrame[Date, Open, High, Low, Close, Volume], tz-naive IST.

CLOSED CANDLES ONLY (new): Yahoo includes the candle that is still forming (today's partial daily
candle, or the current hour). Market-structure breaks are decided on closes, so a forming candle can
create a signal that vanishes a few minutes later. fetch_history() therefore drops the last candle
unless it has really closed:

    1H candle : closed when its end time has passed. NSE hourly candles start 09:15, 10:15 ... 15:15,
                so the last one is only 15 minutes long and ends at the 15:30 close.
    1D candle : closed only after config.FINAL_BAR_READY (15:45 IST), when Yahoo has the final bar.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import pandas as pd

import config
from common import IST, is_rate_limit, now_ist, retry, yahoo_candidates  # noqa: F401 (IST/is_rate_limit kept for importers)

_OHLC = ["Open", "High", "Low", "Close"]
IST_OFFSET = timedelta(hours=5, minutes=30)  # India has no daylight saving
BAR = pd.Timedelta(hours=1)


@dataclass
class Fetch:
    status: str  # OK | NO_DATA | ERROR
    df: Optional[pd.DataFrame] = None
    ticker: str = ""
    error: str = ""
    dropped_forming: bool = False  # True when an unfinished last candle was removed


def normalize(raw: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """-> DataFrame[Date, Open, High, Low, Close, Volume], tz-naive IST, sorted, unique, no NaN prices."""
    if raw is None or len(raw) == 0:
        return None
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    if not all(c in df.columns for c in _OHLC):
        return None
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(IST).tz_localize(None)
    out = df[_OHLC].apply(pd.to_numeric, errors="coerce")
    if "Volume" in df.columns:
        out["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    else:
        out["Volume"] = 0.0
    out.insert(0, "Date", idx)
    out = out.dropna(subset=_OHLC).reset_index(drop=True)
    out = out.drop_duplicates(subset="Date", keep="last").sort_values("Date").reset_index(drop=True)
    return out if len(out) else None


# ---------------------------------------------------------------------------
# Closed-candle logic (pure functions, no network - easy to test)
# ---------------------------------------------------------------------------
def ist_naive_now(now_utc: Optional[datetime] = None) -> pd.Timestamp:
    """Current time as a tz-naive IST timestamp (the same form as the candle Date column)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return pd.Timestamp((now_utc.astimezone(timezone.utc) + IST_OFFSET).replace(tzinfo=None))


def bar_end(start: pd.Timestamp, interval: str) -> pd.Timestamp:
    """When the candle that STARTS at `start` (naive IST) is final (naive IST)."""
    day = start.normalize()
    if interval == "1d":
        return day + pd.Timedelta(hours=config.FINAL_BAR_READY[0], minutes=config.FINAL_BAR_READY[1])
    end = start + BAR
    close = day + pd.Timedelta(hours=config.MARKET_CLOSE[0], minutes=config.MARKET_CLOSE[1])
    if start < close < end:  # the 15:15 candle ends at the 15:30 close, not 16:15
        end = close
    return end


def is_bar_closed(start: pd.Timestamp, interval: str, now_utc: Optional[datetime] = None) -> bool:
    return ist_naive_now(now_utc) >= bar_end(start, interval)


def drop_forming_candle(df: Optional[pd.DataFrame], interval: str,
                        now_utc: Optional[datetime] = None) -> Tuple[Optional[pd.DataFrame], bool]:
    """Remove the last candle if it has not closed yet. Returns (df, dropped?)."""
    if df is None or len(df) == 0:
        return df, False
    if is_bar_closed(df["Date"].iloc[-1], interval, now_utc):
        return df, False
    return df.iloc[:-1].reset_index(drop=True), True


# ---------------------------------------------------------------------------
# Yahoo access
# ---------------------------------------------------------------------------
def _history(ticker: str, interval: str, start: str):
    import yfinance as yf  # imported lazily so tests can run without it
    t = yf.Ticker(ticker)
    kw = dict(start=start, interval=interval, auto_adjust=False, actions=False, timeout=config.REQUEST_TIMEOUT)
    try:
        return t.history(raise_errors=True, **kw)
    except TypeError:  # very old yfinance without raise_errors
        return t.history(**kw)


def _is_no_data(e: Exception) -> bool:
    n = type(e).__name__
    return any(k in n for k in ("PricesMissing", "TickerMissing", "TzMissing", "InvalidPeriod")) or \
        "delisted" in str(e).lower() or "no data found" in str(e).lower() or "no price data" in str(e).lower()


def fetch_one(ticker: str, interval: str, lookback_days: int, history_fn=None,
              now_utc: Optional[datetime] = None, drop_forming: Optional[bool] = None) -> Fetch:
    history_fn = history_fn or _history  # looked up at call time (patchable)
    if drop_forming is None:
        drop_forming = config.CLOSED_CANDLES_ONLY
    start = (now_ist() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    def call():
        return history_fn(ticker, interval, start)

    try:
        raw = retry(call, tries=config.MAX_RETRIES, base=config.RETRY_BASE,
                    retryable=lambda e: not _is_no_data(e),
                    sleep=lambda s: time.sleep(s + random.uniform(0, config.RETRY_BASE)))
    except Exception as e:  # noqa: BLE001
        if _is_no_data(e):
            return Fetch("NO_DATA", ticker=ticker, error=str(e)[:160])
        return Fetch("ERROR", ticker=ticker, error=f"{type(e).__name__}: {str(e)[:160]}")
    df = normalize(raw)
    if df is None:
        return Fetch("NO_DATA", ticker=ticker, error="empty history")
    dropped = False
    if drop_forming:
        df, dropped = drop_forming_candle(df, interval, now_utc)
        if df is None or len(df) == 0:
            return Fetch("NO_DATA", ticker=ticker, error="no closed candles", dropped_forming=dropped)
    return Fetch("OK", df=df, ticker=ticker, dropped_forming=dropped)


def fetch_history(symbol: str, group: str, interval: str, lookback_days: int, history_fn=None,
                  now_utc: Optional[datetime] = None, drop_forming: Optional[bool] = None) -> Fetch:
    """Try each Yahoo candidate for the symbol. ERROR wins over NO_DATA so transient failures are visible."""
    last = Fetch("NO_DATA", error="no candidates")
    saw_error: Optional[Fetch] = None
    for ticker in yahoo_candidates(symbol, group):
        r = fetch_one(ticker, interval, lookback_days, history_fn, now_utc, drop_forming)
        if r.status == "OK":
            return r
        if r.status == "ERROR":
            saw_error = r
        last = r
    return saw_error or last
