"""Scan every symbol on the DAILY and HOURLY timeframe and store the results in stocks.json.

    python scan_market_structure_combined.py                 # smart: only what is stale
    python scan_market_structure_combined.py --force         # rescan everything now
    python scan_market_structure_combined.py --timeframes 1h # hourly only
    python scan_market_structure_combined.py --limit 50      # quick test
    python scan_market_structure_combined.py --symbols TCS INFY
    python scan_market_structure_combined.py --workers 4     # gentler on Yahoo rate limits

Freshness rules (IST):
  daily  : rescanned when its last scan is older than the most recent market close (15:45) - i.e. once
           per session, and once more after the close so the final candle is included.
  hourly : rescanned on every run while the market is open; after the close only once.
Failures never wipe good data: if Yahoo errors or returns nothing for a symbol that was scanned OK
before, the old result is kept and marked scanner_status = STALE.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

import config
import market_data
import smc_engine as eng
from common import (LockBusy, by_symbol, fmt_ts, get_logger, is_market_open, last_market_close,
                    load_stocks, modify_stocks, norm_symbol, now_ist, parse_ts, pipeline_lock)

log = get_logger("scan")

TF = {
    "1d": dict(suffix="", label="1D", interval="1d", lookback=config.DAILY_LOOKBACK_DAYS,
               fmt=config.DAILY_DATE_FMT, stamp="scanned_at"),
    "1h": dict(suffix="_1h", label="1H", interval="1h", lookback=config.HOURLY_LOOKBACK_DAYS,
               fmt=config.HOURLY_DATE_FMT, stamp="scanned_at_1h"),
}
GOOD = "OK"


# ---------------------------------------------------------------------------
def needs_scan(stock: Dict[str, Any], tf: str, now=None, force: bool = False) -> bool:
    if force:
        return True
    cfg = TF[tf]
    stamp = parse_ts(stock.get(cfg["stamp"]))
    if stamp is None:
        return True
    close = last_market_close(now)
    if stamp < close:
        return True                                        # no scan yet that includes the last final candle
    return tf == "1h" and is_market_open(now)              # hourly: always while the market is open


def scan_symbol(stock: Dict[str, Any], tfs: List[str], history_fn=None) -> Dict[str, Dict[str, Any]]:
    """Download + analyse one symbol. Returns {tf: {"fields":..., "status":..., "error":...}}."""
    sym = norm_symbol(stock.get("symbol"))
    group = stock.get("instrument_group", "")
    out: Dict[str, Dict[str, Any]] = {}
    for tf in tfs:
        cfg = TF[tf]
        kw = {"history_fn": history_fn} if history_fn else {}
        r = market_data.fetch_history(sym, group, cfg["interval"], cfg["lookback"], **kw)
        if r.status == "ERROR":
            out[tf] = {"status": "ERROR", "error": r.error, "fields": None}
        elif r.status == "NO_DATA":
            out[tf] = {"status": "NO_DATA", "error": r.error,
                       "fields": eng.empty_fields(cfg["suffix"], cfg["label"], "INSUFFICIENT_DATA")}
        else:
            try:
                fields = eng.analyze_frame(r.df, cfg["suffix"], cfg["label"], cfg["fmt"])
                out[tf] = {"status": fields[f"scanner_status{cfg['suffix']}"], "error": "", "fields": fields}
            except Exception as e:                        # noqa: BLE001 - one bad symbol must not stop the run
                out[tf] = {"status": "ERROR", "error": f"{type(e).__name__}: {e}", "fields": None}
    return out


def apply_result(stock: Dict[str, Any], tf: str, res: Dict[str, Any], stamp: str) -> str:
    """Write one timeframe's result into the stock record. Returns the final scanner_status."""
    cfg = TF[tf]
    s = cfg["suffix"]
    prev_ok = stock.get(f"scanner_status{s}") in (GOOD, "STALE") and stock.get(f"scan_date{s}") not in (None, "NA")
    fields = res["fields"]
    if fields is None or (res["status"] in ("NO_DATA", "INSUFFICIENT_DATA") and prev_ok):
        if prev_ok:                                       # keep the last good analysis, flag it
            stock[f"scanner_status{s}"] = "STALE"
            stock[f"scan_error{s}"] = res["error"] or res["status"]
            return "STALE"
        if fields is None:                                # never scanned OK: record the error state
            stock.update(eng.empty_fields(s, cfg["label"], "ERROR"))
            stock[f"scan_error{s}"] = res["error"]
            return "ERROR"
    stock.update(fields)
    stock.pop(f"scan_error{s}", None)
    stock[cfg["stamp"]] = stamp
    stock[f"last_updated{s}"] = stamp
    return stock[f"scanner_status{s}"]


def run_scan(targets: List[Dict[str, Any]], tfs_for: Callable[[Dict[str, Any]], List[str]],
             workers: int, history_fn=None, progress_every: int = 100) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    saved_upto = 0
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
        saved_upto = len(results)

    counts: Dict[str, int] = {}
    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {pool.submit(scan_symbol, s, tfs_for(s), history_fn): norm_symbol(s.get("symbol")) for s in targets}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                results[sym] = fut.result()
            except Exception as e:                        # noqa: BLE001
                log.warning(f"{sym}: scan crashed - {type(e).__name__}: {e}")
                continue
            for tf, res in results[sym].items():
                counts[f"{tf}:{res['status']}"] = counts.get(f"{tf}:{res['status']}", 0) + 1
            done += 1
            if done % progress_every == 0 or done == len(futs):
                log.info(f"  {done}/{len(futs)} symbols  ({time.time() - t0:.0f}s)")
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
    for tf in ("1d", "1h"):
        s = TF[tf]["suffix"]
        st: Dict[str, int] = {}
        for x in stocks:
            k = x.get(f"scanner_status{s}", "PENDING_SCAN")
            st[k] = st.get(k, 0) + 1
        log.info(f"  {TF[tf]['label']} status: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
        act = [x for x in stocks if x.get(f"signal{s}") not in (None, "NO_SIGNAL")]
        act.sort(key=lambda x: -float(x.get(f"signal_rank_score{s}") or 0))
        log.info(f"  {TF[tf]['label']} top 10 by rank score:")
        for x in act[:10]:
            log.info(f"    {x['symbol']:<14} {x[f'signal{s}']:<12} {x[f'trend{s}']:<8} "
                     f"bars={x[f'bars_since_signal{s}']:<4} score={x[f'signal_rank_score{s}']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeframes", nargs="+", default=["1d", "1h"], choices=["1d", "1h"])
    ap.add_argument("--force", action="store_true", help="rescan regardless of freshness")
    ap.add_argument("--force-daily", action="store_true", help="(compat) force the daily timeframe only")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--workers", type=int, default=config.SCAN_WORKERS)
    args = ap.parse_args(argv)

    try:
        with pipeline_lock():
            stocks = load_stocks()
            wanted = {norm_symbol(s) for s in args.symbols} if args.symbols else None
            now = now_ist()
            plan: Dict[str, List[str]] = {}
            for s in stocks:
                sym = norm_symbol(s.get("symbol"))
                if not sym or s.get("listed") is False:
                    continue
                if wanted and sym not in wanted:
                    continue
                tfs = [tf for tf in args.timeframes
                       if needs_scan(s, tf, now, args.force or (args.force_daily and tf == "1d"))]
                if tfs:
                    plan[sym] = tfs
            targets = [s for s in stocks if norm_symbol(s.get("symbol")) in plan]
            if args.limit > 0:
                targets = targets[:args.limit]
            log.info(f"Market open: {is_market_open(now)} | symbols in file: {len(stocks)} | to scan: {len(targets)}")
            if not targets:
                log.info("Nothing to scan (everything is fresh). Use --force to rescan.")
                return 0
            t0 = time.time()
            counts = run_scan(targets, lambda s: plan[norm_symbol(s.get("symbol"))], args.workers)
            log.info(f"Done in {time.time() - t0:.0f}s  |  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            print_summary(load_stocks())
            return 0
    except LockBusy:
        log.error("Another pipeline run is in progress (shared-data/.pipeline.lock). Wait for it or delete the file.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
