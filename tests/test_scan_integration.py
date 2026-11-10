"""Scan -> stocks.json + detail files, end to end with a fake Yahoo."""
import json
from datetime import datetime

from tests.scan_helpers import NOW_UTC, env, fake_history  # noqa: F401  (env is a pytest fixture)

import pytest

import config
import detail_store
import market_data
import scan_market_structure_combined as scan
import validate_data
from common import IST


def stock(sym="TCS", group="EQ"):
    return {"symbol": sym, "instrument_group": group}


def test_scan_symbol_returns_smc_summary_and_detail_for_both_timeframes():
    out = scan.scan_symbol(stock(), ["1d", "1h"], history_fn=fake_history, now_utc=NOW_UTC)
    assert set(out) == {"1d", "1h"}
    for tf, s in (("1d", ""), ("1h", "_1h")):
        r = out[tf]
        assert r["status"] == "OK"
        assert r["fields"][f"scanner_status{s}"] == "OK"
        assert r["summary"][f"signal_status{s}"] == "OK"
        assert len(r["summary"][f"sig_top{s}"]) <= 3
        d = r["detail"]
        assert len(d["candles"]) == config.PROFILES[tf].recent_candles
        assert d["candles"][-1][0] == d["last_bar_time"]
        json.dumps(d, allow_nan=False)


def test_debt_instruments_get_structure_only():
    r = scan.scan_symbol(stock("GOVBOND", "DEBT"), ["1d"], history_fn=fake_history, now_utc=NOW_UTC)["1d"]
    assert r["fields"]["scanner_status"] == "OK"
    assert r["summary"]["signal_status"] == "SKIPPED"
    assert "detail" not in r


def test_no_signals_switch():
    r = scan.scan_symbol(stock(), ["1h"], history_fn=fake_history, now_utc=NOW_UTC, with_signals=False)["1h"]
    assert r["summary"]["signal_status_1h"] == "SKIPPED" and "detail" not in r


def test_signal_failure_keeps_the_structure_result(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("indicator bug")
    monkeypatch.setattr(scan.sg, "analyze", boom)
    r = scan.scan_symbol(stock(), ["1d"], history_fn=fake_history, now_utc=NOW_UTC)["1d"]
    assert r["fields"]["scanner_status"] == "OK"
    assert r["summary"]["signal_status"] == "ERROR"
    assert "indicator bug" in r["summary"]["signal_error"]


def test_run_scan_writes_stocks_detail_and_the_1h_view(env):
    stocks = json.loads((env / "stocks.json").read_text())
    counts = scan.run_scan(stocks, lambda s: ["1d", "1h"], workers=2, now_utc=NOW_UTC)
    assert counts["1d:OK"] == 4 and counts["1h:OK"] == 4

    data = {s["symbol"]: s for s in json.loads((env / "stocks.json").read_text())}
    tcs = data["TCS"]
    assert tcs["scanner_status"] == tcs["scanner_status_1h"] == "OK"
    assert tcs["signal_status"] == tcs["signal_status_1h"] == "OK"
    assert tcs["scanned_at"] and tcs["scanned_at_1h"]
    assert data["GOVBOND"]["signal_status"] == "SKIPPED"

    d = detail_store.read_detail("TCS")
    assert set(d) >= {"symbol", "updated_at", "1d", "1h"}
    assert (env / "detail" / "M~26M.json").exists()  # 'M&M' -> file-system safe name
    assert not detail_store.detail_path("GOVBOND").exists()

    view = {s["symbol"]: s for s in json.loads((env / "stocks_1h.json").read_text())}
    assert view["TCS"]["sig_best_score"] == tcs["sig_best_score_1h"]  # the 1H copy exposes plain names


def test_unchanged_detail_is_not_rewritten(env):
    stocks = json.loads((env / "stocks.json").read_text())
    scan.run_scan(stocks, lambda s: ["1d"], workers=2, now_utc=NOW_UTC)
    block = detail_store.read_detail("TCS")["1d"]
    assert detail_store.write_detail("TCS", "1d", block, "2030-01-01 00:00:00 IST") is False
    block2 = dict(block, bars=block["bars"] + 1)
    assert detail_store.write_detail("TCS", "1d", block2, "2030-01-01 00:00:00 IST") is True


def test_failed_download_keeps_old_data_and_marks_stale(env, monkeypatch):
    stocks = json.loads((env / "stocks.json").read_text())
    scan.run_scan(stocks, lambda s: ["1d"], workers=2, now_utc=NOW_UTC)
    before = {s["symbol"]: s for s in json.loads((env / "stocks.json").read_text())}["TCS"]

    def down(ticker, interval, start):  # only TCS fails; the other symbols download fine
        if ticker == "TCS.NS":
            raise RuntimeError("yahoo is down")
        return fake_history(ticker, interval, start)
    monkeypatch.setattr(market_data, "_history", down)
    stocks = json.loads((env / "stocks.json").read_text())
    scan.run_scan(stocks, lambda s: ["1d"], workers=2, now_utc=NOW_UTC)
    after = {s["symbol"]: s for s in json.loads((env / "stocks.json").read_text())}["TCS"]
    assert after["scanner_status"] == "STALE"
    assert after["current_price"] == before["current_price"]
    assert after["sig_best_score"] == before["sig_best_score"]
    assert detail_store.read_detail("TCS")["1d"]["signals"] is not None
    errors, warns = validate_data.validate()
    assert errors == [] and any("TCS" in w and "STALE" in w for w in warns)  # a warning, not an error


def test_everything_stale_blocks_publishing(env, monkeypatch):
    stocks = json.loads((env / "stocks.json").read_text())
    scan.run_scan(stocks, lambda s: ["1d"], workers=2, now_utc=NOW_UTC)

    def down(ticker, interval, start):
        raise RuntimeError("yahoo is down")
    monkeypatch.setattr(market_data, "_history", down)
    scan.run_scan(json.loads((env / "stocks.json").read_text()), lambda s: ["1d"], workers=2, now_utc=NOW_UTC)
    assert any("no symbol scanned OK" in e for e in validate_data.validate()[0])


def test_needs_scan_follows_the_schedule():
    s = stock()
    t = lambda *a: datetime(2026, 11, 6, *a, tzinfo=IST)
    assert scan.needs_scan(s, "1d", t(12, 0))  # never scanned
    scan.apply_result(s, "1h", {"fields": {"scanner_status_1h": "OK", "scan_date_1h": "x"}, "status": "OK",
                                "error": "", "summary": {}}, "2026-11-06 10:20:00 IST")
    assert not scan.needs_scan(s, "1h", t(11, 0))
    assert scan.needs_scan(s, "1h", t(11, 18))
    assert scan.needs_scan(s, "1h", t(11, 0), force=True)


def test_main_scans_once_then_has_nothing_to_do_and_output_validates(env, capsys):
    assert scan.main(["--workers", "2"]) == 0
    errors, _ = validate_data.validate()
    assert errors == []
    mtimes = {p.name: p.stat().st_mtime_ns for p in (env / "detail").glob("*.json")}
    assert len(mtimes) == 3  # TCS, INFY, M&M (GOVBOND is structure-only)
    assert scan.main(["--workers", "2"]) == 0  # nothing new -> nothing to scan
    assert {p.name: p.stat().st_mtime_ns for p in (env / "detail").glob("*.json")} == mtimes


def test_summary_names_the_best_bullish_and_best_bearish_signal():
    out = scan.scan_symbol(stock(), ["1h"], history_fn=fake_history, now_utc=NOW_UTC)["1h"]
    sm, sigs = out["summary"], out["detail"]["signals"]
    for side, d in (("bull", "bull"), ("bear", "bear")):
        pool = [g for g in sigs if g["dir"] == d]
        if pool:
            best = max(pool, key=lambda g: (g["score"], -g["since"]))
            assert sm[f"sig_{side}_name_1h"] == best["name"] and sm[f"sig_{side}_since_1h"] == best["since"]
            assert sm[f"sig_{side}_score_1h"] == best["score"]
        else:
            assert sm[f"sig_{side}_name_1h"] is None and sm[f"sig_{side}_since_1h"] is None
