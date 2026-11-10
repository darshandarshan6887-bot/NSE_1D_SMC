"""Shared test helpers: synthetic NSE-like data, a fake Yahoo, and an isolated project folder."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

import config
import market_data

NOW_UTC = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)  # Friday 11:30 IST


def frame(tf: str, n_days: int = 420, seed: int = 1) -> pd.DataFrame:
    """Random-walk OHLCV. 1D: one candle a day. 1H: seven candles a day starting 09:15 (naive IST)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-01", periods=n_days)
    if tf == "1d":
        times = list(days)
    else:
        times = [d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(hours=k) for d in days for k in range(7)]
    n = len(times)
    ret = rng.normal(0.0002, 0.012 if tf == "1d" else 0.005, n)
    close = 500 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = rng.integers(50_000, 500_000, n).astype(float)
    return pd.DataFrame({"Date": times, "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})


def fake_history(ticker: str, interval: str, start: str) -> pd.DataFrame:
    """Looks like yfinance's Ticker.history(): tz-aware Asia/Kolkata index, OHLCV columns."""
    tf = "1d" if interval == "1d" else "1h"
    df = frame(tf, 420 if tf == "1d" else 300, seed=sum(map(ord, ticker)) % 1000)
    out = df.drop(columns="Date")
    out.index = pd.DatetimeIndex(df["Date"]).tz_localize("Asia/Kolkata")
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated shared-data folder with three symbols, and a fake Yahoo."""
    data = tmp_path / "shared-data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "STOCKS_JSON", data / "stocks.json")
    monkeypatch.setattr(config, "STOCKS_1H_JSON", data / "stocks_1h.json")
    monkeypatch.setattr(config, "DETAIL_DIR", data / "detail")
    monkeypatch.setattr(config, "WRITE_LOCK", data / ".write.lock")
    monkeypatch.setattr(config, "PIPELINE_LOCK", data / ".pipeline.lock")
    monkeypatch.setattr(config, "HOLIDAYS_FILE", data / "holidays.txt")
    monkeypatch.setattr(config, "BOARD_FILE", str(data / "board_{tf}.json"))
    stocks = [{"symbol": "TCS", "instrument_group": "EQ"}, {"symbol": "INFY", "instrument_group": "EQ"},
              {"symbol": "M&M", "instrument_group": "EQ"}, {"symbol": "GOVBOND", "instrument_group": "DEBT"}]
    (data / "stocks.json").write_text(json.dumps(stocks), encoding="utf-8")
    monkeypatch.setattr(market_data, "_history", fake_history)
    monkeypatch.setattr(market_data.time, "sleep", lambda s: None)
    return data
