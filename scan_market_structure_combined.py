"""Scan every symbol on the DAILY and HOURLY timeframe: market structure (SMC) + candlestick patterns +
squeeze / volume / trend signals. Results go to stocks.json (summary) and shared-data/detail/<SYMBOL>.json.

    python scan_market_structure_combined.py                 # smart: only what is due
    python scan_market_structure_combined.py --force         # rescan everything now
    python scan_market_structure_combined.py --timeframes 1h # hourly only
    python scan_market_structure_combined.py --limit 50      # quick test
    python scan_market_structure_combined.py --symbols TCS INFY
    python scan_market_structure_combined.py --workers 4     # gentler on Yahoo rate limits
    python scan_market_structure_combined.py --no-signals    # SMC only (the old behaviour)

Schedule (see schedule.py), IST:
    1D : once per trading day, after the daily candle is final (15:45).
    1H : seven times per trading day, a few minutes after each hourly candle closes
         (10:15 11:15 12:15 13:15 14:15 15:15 15:30).
Only CLOSED candles are analysed (see market_data.py).

Failures never wipe good data: if Yahoo errors or returns nothing for a symbol that was scanned OK
before, the old result is kept and marked scanner_status = STALE.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd

import config
import detail_store
import levels as levels_mod
import market_data
import schedule
import signals as sg
import smc_engine as eng
from common import (LockBusy, by_symbol, fmt_ts, get_logger, is_market_open, load_stocks, modify_stocks,
                    norm_symbol, now_ist, parse_ts, pipeline_lock)

log = get_logger("scan")

TF = {k: dict(suffix=p.suffix, label=p.label, interval=p.interval, lookback=p.lookback_days,
              fmt=p.date_fmt, stamp=p.stamp) for k, p in config.PROFILES.items()}
GOOD = "OK"


# ---------------------------------------------------------------------------
# WHAT IS DUE
# ---------------------------------------------------------------------------
def needs_scan(stock: Dict[str, Any], tf: str, now=None, force: bool = False, holidays=None) -> bool:
    """True when a closed candle exists that this stock's last scan did not include (see schedule.py)."""
    if force:
        return True
    return schedule.is_due(parse_ts(stock.get(TF[tf]["stamp"])), tf, now, holidays)


# ---------------------------------------------------------------------------
# SIGNAL SUMMARY (small, goes into stocks.json) and DETAIL (large, goes into detail/<SYMBOL>.json)
# ---------------------------------------------------------------------------
def empty_signal_fields(suffix: str, status: str) -> Dict[str, Any]:
    s = suffix
    return {
        f"signal_status{s}": status, f"bars{s}": None, f"last_bar_time{s}": None,
        f"sig_best_score{s}": 0, f"sig_bull_score{s}": 0, f"sig_bear_score{s}": 0,
        f"sig_best_name{s}": None, f"sig_best_dir{s}": None, f"sig_best_since{s}": None,
        f"sig_bull_name{s}": None, f"sig_bull_since{s}": None, f"sig_bear_name{s}": None, f"sig_bear_since{s}": None,
        f"sig_bull_count{s}": 0, f"sig_bear_count{s}": 0, f"sig_top{s}": [],
        f"squeeze_state{s}": None, f"compression_layers{s}": 0,
    }


def candle_rows(df: pd.DataFrame, prof: config.Profile) -> List[list]:
    """[time, open, high, low, close, volume] for the newest candles (child-page chart)."""
    t = df.tail(prof.recent_candles)
    return [[d.strftime(prof.date_fmt), round(float(o), 4), round(float(h), 4), round(float(l), 4),
             round(float(c), 4), int(v) if pd.notna(v) else 0]
            for d, o, h, l, c, v in zip(t["Date"], t["Open"], t["High"], t["Low"], t["Close"], t["Volume"])]


def compute_signals(prof: config.Profile, df: pd.DataFrame, smc_trend: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """-> (summary fields for stocks.json, detail block for the detail file)."""
    s = prof.suffix
    sigs, ctx, scores = sg.analyze(df, prof, smc_trend)
    bull = [g for g in sigs if g["dir"] == "bull"]
    bear = [g for g in sigs if g["dir"] == "bear"]
    best = max(sigs, key=lambda g: (g["score"], -g["since"]), default=None)
    best_bull = max(bull, key=lambda g: (g["score"], -g["since"]), default=None)
    best_bear = max(bear, key=lambda g: (g["score"], -g["since"]), default=None)
    top = sorted(sigs, key=lambda g: (-g["score"], g["since"], g["name"]))[:3]
    last_bar = df["Date"].iloc[-1].strftime(prof.date_fmt)
    summary = {
        f"signal_status{s}": GOOD, f"bars{s}": int(len(df)), f"last_bar_time{s}": last_bar,
        f"sig_best_score{s}": scores["best"], f"sig_bull_score{s}": scores["bull"], f"sig_bear_score{s}": scores["bear"],
        f"sig_best_name{s}": best["name"] if best else None,
        f"sig_best_dir{s}": best["dir"] if best else None,
        f"sig_best_since{s}": best["since"] if best else None,
        f"sig_bull_name{s}": best_bull["name"] if best_bull else None,
        f"sig_bull_since{s}": best_bull["since"] if best_bull else None,
        f"sig_bear_name{s}": best_bear["name"] if best_bear else None,
        f"sig_bear_since{s}": best_bear["since"] if best_bear else None,
        f"sig_bull_count{s}": len(bull), f"sig_bear_count{s}": len(bear),
        f"sig_top{s}": [{"name": g["name"], "dir": g["dir"], "since": g["since"], "score": g["score"],
                         "confirm": g.get("confirm")} for g in top],
        f"squeeze_state{s}": ctx["squeeze_state"], f"compression_layers{s}": len(ctx["compression_layers"]),
    }
    detail = {"last_bar_time": last_bar, "bars": int(len(df)), "signals": sigs, "context": ctx,
              "scores": scores, "candles": candle_rows(df, prof)}
    return summary, detail


# ---------------------------------------------------------------------------
# SUPPORT / RESISTANCE LEVELS (1H only; shown on the stock page)
# ---------------------------------------------------------------------------
def levels_block(sym: str, df: pd.DataFrame, detail: Dict[str, Any]) -> None:
    """Find the support / resistance levels of one symbol and store them in its detail block. Never raises."""
    try:
        lv = levels_mod.analyse(df)
    except Exception as e:  # noqa: BLE001 - a failed level search must not cost the symbol its scan result
        log.warning(f"{sym}: levels failed - {type(e).__name__}: {e}")
        lv = {"levels": [], "meta": {"error": f"{type(e).__name__}: {e}"[:120]}}
    detail["levels"], detail["levels_meta"] = lv["levels"], lv["meta"]


# ---------------------------------------------------------------------------
# SCAN ONE SYMBOL
# ---------------------------------------------------------------------------
def scan_symbol(stock: Dict[str, Any], tfs: List[str], history_fn=None, now_utc=None,
                with_signals: bool = True) -> Dict[str, Dict[str, Any]]:
    """Download + analyse one symbol. Returns {tf: {"fields", "status", "error", "summary", "detail"}}."""
    sym = norm_symbol(stock.get("symbol"))
    group = stock.get("instrument_group", "")
    skip_signals = norm_symbol(group) in config.SIGNAL_SKIP_GROUPS
    out: Dict[str, Dict[str, Any]] = {}
    for tf in tfs:
        cfg, prof = TF[tf], config.PROFILES[tf]
        kw: Dict[str, Any] = {}
        if history_fn:
            kw["history_fn"] = history_fn
        if now_utc is not None:
            kw["now_utc"] = now_utc
        r = market_data.fetch_history(sym, group, cfg["interval"], cfg["lookback"], **kw)
        if r.status == "ERROR":
            out[tf] = {"status": "ERROR", "error": r.error, "fields": None}
            continue
        if r.status == "NO_DATA":
            out[tf] = {"status": "NO_DATA", "error": r.error,
                       "fields": eng.empty_fields(cfg["suffix"], cfg["label"], "INSUFFICIENT_DATA")}
            continue
        try:
            fields = eng.analyze_frame(r.df, cfg["suffix"], cfg["label"], cfg["fmt"])
        except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the run
            out[tf] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}", "fields": None}
            continue
        res: Dict[str, Any] = {"status": fields[f"scanner_status{cfg['suffix']}"], "error": "", "fields": fields}
        s = cfg["suffix"]
        if not with_signals or skip_signals:
            res["summary"] = empty_signal_fields(s, "SKIPPED")
        elif len(r.df) < config.MIN_BARS:
            res["summary"] = empty_signal_fields(s, "INSUFFICIENT_DATA")
        else:
            try:
                res["summary"], res["detail"] = compute_signals(prof, r.df, fields[f"trend{s}"])
                if config.LEVELS_ON and tf == config.LEVELS_TF:
                    levels_block(sym, r.df, res["detail"])
            except Exception as e:  # noqa: BLE001 - the SMC result is still good
                res["summary"] = empty_signal_fields(s, "ERROR")
                res["summary"][f"signal_error{s}"] = f"{type(e).__name__}: {e}"[:160]
        out[tf] = res
    return out


def apply_result(stock: Dict[str, Any], tf: str, res: Dict[str, Any], stamp: str) -> str:
    """Write one timeframe's result into the stock record. Returns the final scanner_status."""
    cfg = TF[tf]
    s = cfg["suffix"]
    prev_ok = stock.get(f"scanner_status{s}") in (GOOD, "STALE") and stock.get(f"scan_date{s}") not in (None, "NA")
    fields = res["fields"]
    if fields is None or (res["status"] in ("NO_DATA", "INSUFFICIENT_DATA") and prev_ok):
        if prev_ok:  # keep the last good analysis, flag it
            stock[f"scanner_status{s}"] = "STALE"
            stock[f"scan_error{s}"] = res["error"] or res["status"]
            return "STALE"
        if fields is None:  # never scanned OK: record the error state
            stock.update(eng.empty_fields(s, cfg["label"], "ERROR"))
            stock[f"scan_error{s}"] = res["error"]
            return "ERROR"
    stock.update(fields)
    summary = res.get("summary")
    if summary is not None:
        stock.update(summary)
        if f"signal_error{s}" not in summary:
            stock.pop(f"signal_error{s}", None)
    stock.pop(f"scan_error{s}", None)
    stock[cfg["stamp"]] = stamp
    stock[f"last_updated{s}"] = stamp
    return stock[f"scanner_status{s}"]


# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------
def run_scan(targets: List[Dict[str, Any]], tfs_for: Callable[[Dict[str, Any]], List[str]],
             workers: int, history_fn=None, progress_every: int = 100,
             with_signals: bool = True, now_utc=None) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    saved_upto = 0
    written: Set[Tuple[str, str]] = set()  # (symbol, tf) detail blocks already stored
    t0 = time.time()

    def flush():
        nonlocal saved_upto
        if not results:
            return
        stamp = fmt_ts()
        snapshot = dict(results)

        def mutate(stocks):
            idx = by_symbol(stocks)
            for sym, per_tf in snapshot.items():
                if sym in idx:
                    for tf, res in per_tf.items():
                        apply_result(idx[sym], tf, res, stamp)
        modify_stocks(mutate)
        for sym, per_tf in snapshot.items():  # detail files: only blocks not stored yet
            for tf, res in per_tf.items():
                if res.get("detail") is None or (sym, tf) in written:
                    continue
                try:
                    detail_store.write_detail(sym, tf, res["detail"], stamp)
                    written.add((sym, tf))
                except OSError as e:
                    log.warning(f"{sym}: could not write detail file - {e}")
        saved_upto = len(results)

    counts: Dict[str, int] = {}
    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {pool.submit(scan_symbol, s, tfs_for(s), history_fn, now_utc, with_signals): norm_symbol(s.get("symbol"))
                for s in targets}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                results[sym] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.warning(f"{sym}: scan crashed - {type(e).__name__}: {e}")
                continue
            for tf, res in results[sym].items():
                counts[f"{tf}:{res['status']}"] = counts.get(f"{tf}:{res['status']}", 0) + 1
            done += 1
            if done % progress_every == 0 or done == len(futs):
                log.info(f"  {done}/{len(futs)} symbols ({time.time() - t0:.0f}s)")
            if len(results) - saved_upto >= config.CHECKPOINT_EVERY:
                flush()
    except KeyboardInterrupt:
        log.warning("Interrupted - saving what has been scanned so far ...")
        pool.shutdown(wait=False, cancel_futures=True)
        flush()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        flush()
    return counts


def print_summary(stocks: List[Dict[str, Any]]) -> None:
    for tf in config.TIMEFRAMES:
        s = TF[tf]["suffix"]
        st: Dict[str, int] = {}
        for x in stocks:
            k = x.get(f"scanner_status{s}", "PENDING_SCAN")
            st[k] = st.get(k, 0) + 1
        log.info(f"  {TF[tf]['label']} status: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
        act = [x for x in stocks if x.get(f"signal{s}") not in (None, "NO_SIGNAL")]
        act.sort(key=lambda x: -float(x.get(f"signal_rank_score{s}") or 0))
        log.info(f"  {TF[tf]['label']} top 10 by structure rank score:")
        for x in act[:10]:
            log.info(f"    {x['symbol']:<14} {x[f'signal{s}']:<12} {x[f'trend{s}']:<8} "
                     f"bars={x[f'bars_since_signal{s}']:<4} score={x[f'signal_rank_score{s}']}")
        sig = [x for x in stocks if (x.get(f"sig_best_score{s}") or 0) > 0]
        sig.sort(key=lambda x: -(x.get(f"sig_best_score{s}") or 0))
        log.info(f"  {TF[tf]['label']} top 10 by pattern/indicator score:")
        for x in sig[:10]:
            log.info(f"    {x['symbol']:<14} {str(x.get(f'sig_best_name{s}')):<28} {str(x.get(f'sig_best_dir{s}')):<5} "
                     f"since={x.get(f'sig_best_since{s}')} score={x.get(f'sig_best_score{s}')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeframes", nargs="+", default=list(config.TIMEFRAMES), choices=list(config.TIMEFRAMES))
    ap.add_argument("--force", action="store_true", help="rescan regardless of the schedule")
    ap.add_argument("--force-daily", action="store_true", help="(compat) force the daily timeframe only")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--workers", type=int, default=config.SCAN_WORKERS)
    ap.add_argument("--no-signals", action="store_true", help="market structure only (skip patterns/indicators)")
    args = ap.parse_args(argv)

    try:
        with pipeline_lock():
            stocks = load_stocks()
            wanted = {norm_symbol(s) for s in args.symbols} if args.symbols else None
            now = now_ist()
            holidays = schedule.load_holidays()
            plan: Dict[str, List[str]] = {}
            for s in stocks:
                sym = norm_symbol(s.get("symbol"))
                if not sym or s.get("listed") is False:
                    continue
                if wanted and sym not in wanted:
                    continue
                tfs = [tf for tf in args.timeframes
                       if needs_scan(s, tf, now, args.force or (args.force_daily and tf == "1d"), holidays)]
                if tfs:
                    plan[sym] = tfs
            targets = [s for s in stocks if norm_symbol(s.get("symbol")) in plan]
            if args.limit > 0:
                targets = targets[:args.limit]
            log.info(f"Market open: {is_market_open(now)} | symbols in file: {len(stocks)} | to scan: {len(targets)}")
            if not targets:
                log.info("Nothing to scan (no new closed candle since the last scan). Use --force to rescan.")
                return 0
            t0 = time.time()
            counts = run_scan(targets, lambda s: plan[norm_symbol(s.get("symbol"))], args.workers,
                              with_signals=not args.no_signals)
            log.info(f"Done in {time.time() - t0:.0f}s | " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            print_summary(load_stocks())
            return 0
    except LockBusy:
        log.error("Another pipeline run is in progress (shared-data/.pipeline.lock). Wait for it or delete the file.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
