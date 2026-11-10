"""The publish gate must stop broken data and let good data through."""
import json

from tests.scan_helpers import NOW_UTC, env  # noqa: F401

import config
import detail_store
import scan_market_structure_combined as scan
import validate_data as vd


def scanned(env):
    stocks = json.loads((env / "stocks.json").read_text())
    scan.run_scan(stocks, lambda s: ["1d", "1h"], workers=2, now_utc=NOW_UTC)
    return json.loads((env / "stocks.json").read_text())


def save(env, stocks):
    (env / "stocks.json").write_text(json.dumps(stocks), encoding="utf-8")


def test_good_output_passes(env):
    scanned(env)
    errors, warns = vd.validate()
    assert errors == [] and warns == []
    assert vd.main([]) == 0


def test_missing_file(env):
    (env / "stocks.json").unlink()
    assert vd.validate()[0]


def test_nan_token_is_rejected(env):
    (env / "stocks.json").write_text('[{"symbol":"TCS","x":NaN}]', encoding="utf-8")
    assert any("not valid strict JSON" in e for e in vd.validate()[0])


def test_must_be_a_list(env):
    (env / "stocks.json").write_text('{"stocks": []}', encoding="utf-8")
    assert any("array" in e for e in vd.validate()[0])


def test_duplicate_and_missing_symbols(env):
    s = scanned(env)
    save(env, s + [dict(s[0]), {"name": "no symbol"}])
    errs = vd.validate()[0]
    assert any("duplicate symbol" in e for e in errs) and any("no symbol" in e for e in errs)


def test_bad_values_are_caught(env):
    s = scanned(env)
    s[0]["current_price"] = "0"
    s[0]["trend"] = "SIDEWAYS"
    s[0]["signal_rank_score"] = 500
    s[0]["bars_since_signal"] = -5
    s[0]["scanner_status_1h"] = "WHATEVER"
    save(env, s)
    errs = " | ".join(vd.validate()[0])
    for needle in ("current_price", "trend", "signal_rank_score", "bars_since_signal", "unknown scanner_status"):
        assert needle in errs


def test_missing_detail_file_is_caught(env):
    scanned(env)
    detail_store.detail_path("TCS").unlink()
    assert any("detail file" in e for e in vd.validate()[0])


def test_bad_signal_in_detail_is_caught(env):
    scanned(env)
    p = detail_store.detail_path("INFY")
    d = json.loads(p.read_text())
    sigs = d["1h"]["signals"]
    assert sigs, "synthetic data should have signals"
    sigs[0]["since"] = config.PROFILES["1h"].store_bars + 5
    sigs[0]["dir"] = "sideways"
    sigs[0]["score"] = 250
    d["1h"]["candles"] = d["1h"]["candles"][:2]
    p.write_text(json.dumps(d))
    errs = " | ".join(vd.validate()[0])
    assert "since=" in errs and "bad direction" in errs and "outside 0..100" in errs and "fewer than 5 candles" in errs


def test_nothing_scanned_ok_blocks_publishing(env):
    s = scanned(env)
    for r in s:
        r["scanner_status"] = r["scanner_status_1h"] = "ERROR"
    save(env, s)
    assert any("no symbol scanned OK" in e for e in vd.validate()[0])


def test_stale_is_a_warning_and_an_error_only_in_strict_mode(env):
    s = scanned(env)
    s[0]["scanner_status"] = "STALE"
    save(env, s)
    errors, warns = vd.validate()
    assert errors == [] and any("STALE" in w for w in warns)
    assert vd.validate(strict=True)[0]
    assert vd.main(["--strict"]) == 1 and vd.main([]) == 0


def test_records_from_an_older_scanner_without_signal_fields_still_pass(env):
    s = scanned(env)
    for r in s:
        for k in [k for k in r if k.startswith(("sig_", "signal_status", "squeeze_state", "compression_layers",
                                                  "bars", "last_bar_time"))]:
            if k not in ("bars_since_signal", "bars_since_signal_1h"):
                r.pop(k)
    save(env, s)
    assert vd.validate()[0] == []
