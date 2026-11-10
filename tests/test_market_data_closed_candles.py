import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Closed-candle-only behaviour of market_data (no network)."""
from datetime import datetime, timezone

import pandas as pd

import config
import market_data as md


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def ts(s):
    return pd.Timestamp(s)


def make_raw(starts, tz="Asia/Kolkata", volume=True):
    idx = pd.DatetimeIndex([pd.Timestamp(s, tz=tz) for s in starts])
    n = len(idx)
    data = {"Open": [100.0 + i for i in range(n)], "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)], "Close": [100.5 + i for i in range(n)]}
    if volume:
        data["Volume"] = [1000 + i for i in range(n)]
    return pd.DataFrame(data, index=idx)


# ---- bar_end / is_bar_closed -------------------------------------------------
def test_hourly_bar_ends_one_hour_after_start():
    assert md.bar_end(ts("2026-11-06 09:15"), "1h") == ts("2026-11-06 10:15")
    assert md.bar_end(ts("2026-11-06 14:15"), "1h") == ts("2026-11-06 15:15")


def test_last_hourly_bar_ends_at_market_close_not_1615():
    assert md.bar_end(ts("2026-11-06 15:15"), "1h") == ts("2026-11-06 15:30")


def test_hourly_bar_closed_exactly_at_its_end():
    # 09:15 bar ends 10:15 IST = 04:45 UTC
    assert not md.is_bar_closed(ts("2026-11-06 09:15"), "1h", utc(2026, 11, 6, 4, 44))
    assert md.is_bar_closed(ts("2026-11-06 09:15"), "1h", utc(2026, 11, 6, 4, 45))


def test_last_hourly_bar_closed_after_1530_ist():
    # 15:30 IST = 10:00 UTC
    assert not md.is_bar_closed(ts("2026-11-06 15:15"), "1h", utc(2026, 11, 6, 9, 59))
    assert md.is_bar_closed(ts("2026-11-06 15:15"), "1h", utc(2026, 11, 6, 10, 0))


def test_daily_bar_final_only_after_1545_ist():
    # 15:45 IST = 10:15 UTC
    assert not md.is_bar_closed(ts("2026-11-06"), "1d", utc(2026, 11, 6, 10, 14))
    assert md.is_bar_closed(ts("2026-11-06"), "1d", utc(2026, 11, 6, 10, 15))


def test_daily_bar_of_a_past_day_is_closed():
    assert md.is_bar_closed(ts("2026-11-05"), "1d", utc(2026, 11, 6, 4, 0))


# ---- drop_forming_candle -----------------------------------------------------
def test_drop_forming_removes_only_the_last_candle():
    df = md.normalize(make_raw(["2026-11-04", "2026-11-05", "2026-11-06"]))
    out, dropped = md.drop_forming_candle(df, "1d", utc(2026, 11, 6, 6, 0))  # 11:30 IST
    assert dropped and len(out) == 2
    assert out["Date"].iloc[-1] == ts("2026-11-05")


def test_drop_forming_keeps_everything_after_final_bar_ready():
    df = md.normalize(make_raw(["2026-11-04", "2026-11-05", "2026-11-06"]))
    out, dropped = md.drop_forming_candle(df, "1d", utc(2026, 11, 6, 10, 30))  # 16:00 IST
    assert not dropped and len(out) == 3


def test_drop_forming_handles_empty():
    out, dropped = md.drop_forming_candle(None, "1h")
    assert out is None and dropped is False


# ---- normalize ---------------------------------------------------------------
def test_normalize_keeps_volume_and_converts_to_naive_ist():
    df = md.normalize(make_raw(["2026-11-06 09:15", "2026-11-06 10:15"]))
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert df["Date"].iloc[0] == ts("2026-11-06 09:15")
    assert df["Date"].dt.tz is None
    assert df["Volume"].tolist() == [1000, 1001]


def test_normalize_without_volume_column_gives_zero_volume():
    df = md.normalize(make_raw(["2026-11-06 09:15"], volume=False))
    assert df["Volume"].tolist() == [0.0]


def test_normalize_converts_utc_index_to_ist():
    df = md.normalize(make_raw(["2026-11-06 03:45"], tz="UTC"))  # 03:45 UTC = 09:15 IST
    assert df["Date"].iloc[0] == ts("2026-11-06 09:15")


# ---- fetch_history end to end (fake Yahoo) -----------------------------------
def test_fetch_history_drops_forming_hour():
    raw = make_raw(["2026-11-06 09:15", "2026-11-06 10:15", "2026-11-06 11:15"])
    r = md.fetch_history("RELIANCE", "EQ", "1h", 700, history_fn=lambda t, i, s: raw,
                         now_utc=utc(2026, 11, 6, 6, 0))  # 11:30 IST: the 11:15 bar is forming
    assert r.status == "OK" and r.dropped_forming
    assert r.df["Date"].iloc[-1] == ts("2026-11-06 10:15")


def test_fetch_history_keeps_all_when_everything_closed():
    raw = make_raw(["2026-11-05 14:15", "2026-11-05 15:15"])
    r = md.fetch_history("TCS", "EQ", "1h", 700, history_fn=lambda t, i, s: raw,
                         now_utc=utc(2026, 11, 6, 6, 0))
    assert r.status == "OK" and not r.dropped_forming and len(r.df) == 2


def test_fetch_history_can_disable_closed_only():
    raw = make_raw(["2026-11-06 09:15", "2026-11-06 11:15"])
    r = md.fetch_history("INFY", "EQ", "1h", 700, history_fn=lambda t, i, s: raw,
                         now_utc=utc(2026, 11, 6, 6, 0), drop_forming=False)
    assert r.status == "OK" and len(r.df) == 2 and not r.dropped_forming


def test_fetch_history_only_forming_candle_is_no_data():
    raw = make_raw(["2026-11-06"])
    r = md.fetch_history("SBIN", "EQ", "1d", 540, history_fn=lambda t, i, s: raw,
                         now_utc=utc(2026, 11, 6, 6, 0))
    assert r.status == "NO_DATA" and "no closed candles" in r.error


def test_config_switch_is_on_by_default():
    assert config.CLOSED_CANDLES_ONLY is True


# ---- config safety -----------------------------------------------------------
def test_hourly_lookback_stays_inside_yahoos_730_day_limit():
    # start date is midnight IST of (today - lookback); allow +1 day for date rounding and the
    # 5h30 UTC offset, as the US scanner's own test does.
    assert config.HOURLY_LOOKBACK_DAYS + 1 + 5.5 / 24 < 730
