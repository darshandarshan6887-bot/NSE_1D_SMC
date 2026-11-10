"""Support/resistance levels and the dashboard data files inside the real scan (fake Yahoo, no network)."""
import json

from tests.scan_helpers import NOW_UTC, env, fake_history  # noqa: F401  (env is a pytest fixture)

import build_board
import config
import scan_market_structure_combined as scan


def run(env):
    return scan.main(["--force", "--workers", "2"])


def test_scan_writes_levels_into_the_hourly_detail_only(env):
    assert run(env) == 0
    d = json.loads((env / "detail" / "TCS.json").read_text(encoding="utf-8"))
    assert isinstance(d["1h"]["levels"], list) and "levels_meta" in d["1h"]
    assert "levels" not in d["1d"]                          # levels are found on 1H candles only


def test_levels_can_be_switched_off(env, monkeypatch):
    monkeypatch.setattr(config, "LEVELS_ON", False)
    assert run(env) == 0
    d = json.loads((env / "detail" / "TCS.json").read_text(encoding="utf-8"))
    assert "levels" not in d["1h"]


def test_a_failing_level_search_keeps_the_scan_result(env, monkeypatch):
    monkeypatch.setattr(scan.levels_mod, "analyse", lambda df: (_ for _ in ()).throw(ValueError("boom")))
    assert run(env) == 0
    d = json.loads((env / "detail" / "TCS.json").read_text(encoding="utf-8"))
    assert d["1h"]["levels"] == [] and "boom" in d["1h"]["levels_meta"]["error"]
    assert d["1h"]["signals"] is not None                    # the signals are still there


def test_no_plan_or_backtest_files_are_written(env):
    assert run(env) == 0
    assert build_board.main() == 0
    assert sorted(p.name for p in env.glob("*.json")) == ["board_1d.json", "board_1h.json", "stocks.json", "stocks_1h.json"]


def test_board_files_hold_signals_candles_atr_and_relative_volume(env):
    assert run(env) == 0
    assert build_board.main() == 0
    for tf in ("1d", "1h"):
        b = json.loads((env / f"board_{tf}.json").read_text(encoding="utf-8"))
        assert b["tf"] == tf and b["n"] >= 3
        x = b["stocks"]["TCS"]
        assert set(x) == {"signals", "context", "candles"}
        assert len(x["candles"]) == config.PROFILES[tf].recent_candles
        assert {"atr", "rel_volume", "squeeze_state"} <= set(x["context"])
        assert all({"name", "dir", "len", "since", "time", "score"} <= set(g) for g in x["signals"])


def test_board_skips_symbols_that_were_not_scanned(env):
    assert run(env) == 0
    for tf in ("1d", "1h"):
        assert set(build_board.build(tf)["stocks"]) == {"TCS", "INFY", "M&M"}   # GOVBOND is debt: structure only, no detail block
