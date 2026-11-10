"""Patterns, indicators and signals under both timeframe profiles."""
import json

from tests.scan_helpers import frame  # also puts the project folder on sys.path

import numpy as np
import pandas as pd
import pytest

import config
import indicators as ind
import patterns as P
import signals as sg


@pytest.mark.parametrize("tf", ["1d", "1h"])
def test_analyze_respects_the_profile_windows(tf):
    prof = config.PROFILES[tf]
    df = frame(tf, 420 if tf == "1d" else 300)
    sigs, ctx, scores = sg.analyze(df, prof, "BULLISH")
    assert sigs, "synthetic data should produce at least one signal"
    assert len(sigs) <= config.MAX_SIGNALS
    for g in sigs:
        assert 0 <= g["since"] < prof.store_bars
        assert 0 <= g["score"] <= 100
        assert g["dir"] in ("bull", "bear", "neutral")
        assert "stars" not in g
        if g["group"] == "range":
            assert g["since"] <= prof.range_keep_bars
    assert scores["best"] == max(scores["bull"], scores["bear"])
    json.dumps({"s": sigs, "c": ctx, "k": scores}, allow_nan=False)  # strict JSON: no NaN / Infinity


def test_both_timeframes_can_run_in_one_process():
    d = sg.analyze(frame("1d"), config.PROFILES["1d"], "BEARISH")
    h = sg.analyze(frame("1h", 300), config.PROFILES["1h"], "BEARISH")
    assert max(g["since"] for g in d[0]) < 20 <= config.PROFILES["1h"].store_bars
    assert max(g["since"] for g in h[0]) < 35


def test_every_signal_dir_and_group_is_known():
    sigs, _, _ = sg.analyze(frame("1h", 300), config.PROFILES["1h"], "UNKNOWN")
    assert {g["group"] for g in sigs} <= set(sg.GROUPS)


def test_smc_alignment_changes_the_score():
    df = frame("1h", 300)
    bull = sg.analyze(df, config.PROFILES["1h"], "BULLISH")[0]
    bear = sg.analyze(df, config.PROFILES["1h"], "BEARISH")[0]
    key = lambda g: (g["time"], g["name"], g["dir"])
    b1 = {key(g): g["score"] for g in bull if g["dir"] == "bull"}
    b2 = {key(g): g["score"] for g in bear if g["dir"] == "bull"}
    assert b1 and all(b1[k] > b2[k] for k in b1)  # a bullish signal scores higher inside a bullish structure


def test_works_without_volume_data():
    df = frame("1d").assign(Volume=0.0)
    sigs, ctx, scores = sg.analyze(df, config.PROFILES["1d"], "UNKNOWN")
    assert ctx["rel_volume"] is None
    assert 0 <= scores["best"] <= 100


# ---- indicators ---------------------------------------------------------------
def test_hourly_volatility_is_annualised_with_seven_candles_a_day():
    c = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0, 0.01, 200)))
    d = ind.hist_vol(c, config.PROFILES["1d"].periods_per_year)[-1]
    h = ind.hist_vol(c, config.PROFILES["1h"].periods_per_year)[-1]
    assert h / d == pytest.approx(np.sqrt(7), rel=1e-9)


def test_relative_volume_on_daily_candles_is_volume_over_the_50_day_average():
    times = pd.DatetimeIndex(pd.bdate_range("2026-01-01", periods=60))
    v = np.full(60, 100.0)
    v[-1] = 300.0
    assert ind.slot_rel_volume(times, v, 50)[-1] == pytest.approx(3.0)


def test_relative_volume_on_hourly_candles_compares_the_same_time_of_day():
    days = pd.bdate_range("2026-01-01", periods=12)
    times = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=15 + 60 * k) for d in days for k in range(7)])
    v = np.tile([1000, 200, 200, 200, 200, 300, 100], 12).astype(float)  # heavy open, quiet midday
    out = ind.slot_rel_volume(times, v, 50)
    assert out[-7] == pytest.approx(1.0)  # the 09:15 candle is normal FOR 09:15
    assert out[-6] == pytest.approx(1.0)  # and so is the quiet 10:15 candle


def test_pct_rank_needs_enough_history():
    x = np.arange(10, dtype=float)
    assert np.isnan(ind.pct_rank(x, 9, 126))


# ---- patterns -----------------------------------------------------------------
def _engulfing_frame():
    n = 68
    close = 200 - 1.0 * np.arange(n)
    open_ = close + 0.6
    rows = list(zip(open_, close + 0.0))
    o = list(open_) + [133.5, 129.8]
    c = list(close) + [130.0, 134.5]
    h = list(open_ + 0.5) + [134.0, 135.0]
    l = list(close - 0.5) + [129.5, 129.5]
    dates = pd.bdate_range("2026-01-01", periods=len(c))
    return pd.DataFrame({"Date": dates, "Open": o, "High": h, "Low": l, "Close": c, "Volume": 1000.0})


def test_bullish_engulfing_is_detected_after_a_downtrend():
    x = P.Candles(_engulfing_frame())
    names = [h.name for h in P.candle_hits(x, x.n - 1)]
    assert "Bullish Engulfing" in names
    assert P.META["Bullish Engulfing"].direction == "bull"


def test_pattern_needs_the_right_prior_trend():
    df = _engulfing_frame()
    flat = df.copy()
    flat.loc[:67, ["Open", "High", "Low", "Close"]] = flat.loc[67, ["Open", "High", "Low", "Close"]].to_numpy()
    flat.loc[:67, "High"] += 0.5
    flat.loc[:67, "Low"] -= 0.5
    x = P.Candles(flat)
    assert "Bullish Engulfing" not in [h.name for h in P.candle_hits(x, x.n - 1)]


def test_confirmation_rule():
    x = P.Candles(_engulfing_frame())
    i = x.n - 1
    assert sg._confirm(x, "bull", i, 135.0, 129.5, 129.5) == "pending"  # last candle: never confirmed
    assert sg._confirm(x, "neutral", 10, 1.0, 0.5, None) is None
    assert sg._confirm(x, "bull", 66, float(x.c[67]) - 5, 0.0, 0.0) == "confirmed"  # a later close above the high


def test_pattern_metadata_is_complete():
    assert len(P.META) == 60
    assert all(m.stars in (2, 3, 4, 5) for m in P.META.values())
    assert len(sg.EVENT_META) == 15
