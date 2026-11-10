"""Gate that runs BEFORE anything is committed or published: python validate_data.py [--strict]

Exit code 0 = safe to publish, 1 = something is broken (the workflow stops before the commit).

Rules (errors block publishing; warnings only block with --strict)
  FILE      stocks.json exists, is valid strict JSON (no NaN / Infinity), and is a list of objects.
  RECORD    every record has a symbol; no symbol appears twice.
  PER TIMEFRAME (1D and 1H), for every record that has been scanned:
            scanner_status is a known status;
            when OK or STALE: trend is BULLISH/BEARISH/UNKNOWN, signal_rank_score is 0..110,
            bars_since_signal is an integer >= -1, current_price is a number > 0, scan_date parses.
  SIGNALS   when signal_status is OK: sig_best_score is 0..100, sig_top is a well-formed list of at most 3,
            and the detail file shared-data/detail/<SYMBOL>.json has a block for that timeframe whose signals
            have a valid direction, 'since' inside the stored window, a score 0..100, a name and a time,
            with at least 5 candles of 6 numbers each.
  RUN       at least one symbol must have scanned OK (otherwise Yahoo was probably unreachable);
  WARNINGS  STALE records; more than STALE_WARN_FRACTION of the scanned symbols STALE/ERROR/NO_DATA.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

import config
import detail_store

VALID_STATUS = {"OK", "STALE", "NO_PIVOTS", "INSUFFICIENT_DATA", "NO_DATA", "ERROR", "PENDING_SCAN"}
VALID_SIGNAL_STATUS = {"OK", "SKIPPED", "INSUFFICIENT_DATA", "ERROR"}
TRENDS = {"BULLISH", "BEARISH", "UNKNOWN"}
DIRS = {"bull", "bear", "neutral"}
MAX_RANK_SCORE = 110  # 100 (strong) + 10 (CHoCH bonus); freshness only subtracts


def _reject_constant(name: str):
    raise ValueError(f"invalid JSON constant {name} (browsers cannot parse it)")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_block(sym: str, prof: config.Profile, block: Any, errors: List[str]) -> None:
    tag = f"{sym} [{prof.label}] detail"
    if not isinstance(block, dict):
        errors.append(f"{tag}: block is not an object")
        return
    sigs = block.get("signals")
    if not isinstance(sigs, list):
        errors.append(f"{tag}: signals is not a list")
        sigs = []
    if len(sigs) > config.MAX_SIGNALS:
        errors.append(f"{tag}: {len(sigs)} signals (max {config.MAX_SIGNALS})")
    for g in sigs:
        if not isinstance(g, dict) or not g.get("name") or not g.get("time"):
            errors.append(f"{tag}: signal without name/time")
            continue
        if g.get("dir") not in DIRS:
            errors.append(f"{tag}: '{g.get('name')}' has bad direction {g.get('dir')!r}")
        if not _is_int(g.get("since")) or not 0 <= g["since"] < prof.store_bars:
            errors.append(f"{tag}: '{g.get('name')}' since={g.get('since')!r} outside 0..{prof.store_bars - 1}")
        sc = _num(g.get("score"))
        if sc is None or not 0 <= sc <= 100:
            errors.append(f"{tag}: '{g.get('name')}' score={g.get('score')!r} outside 0..100")
    candles = block.get("candles")
    if not isinstance(candles, list) or len(candles) < 5:
        errors.append(f"{tag}: fewer than 5 candles")
    else:
        for row in candles:
            if not isinstance(row, list) or len(row) != 6 or any(_num(v) is None for v in row[1:]):
                errors.append(f"{tag}: malformed candle row {row!r}")
                break
    if not isinstance(block.get("context"), dict) or "squeeze_state" not in block["context"]:
        errors.append(f"{tag}: context missing")
    sc = block.get("scores")
    if not isinstance(sc, dict) or not {"best", "bull", "bear"} <= set(sc):
        errors.append(f"{tag}: scores missing")


def _check_timeframe(rec: dict, sym: str, prof: config.Profile, errors: List[str], warns: List[str],
                     counts: dict) -> None:
    s, label = prof.suffix, prof.label
    status = rec.get(f"scanner_status{s}")
    if status is None:
        return  # never scanned yet
    counts["scanned"] += 1
    if status not in VALID_STATUS:
        errors.append(f"{sym} [{label}]: unknown scanner_status {status!r}")
        return
    if status == "OK":
        counts["ok"] += 1
    if status in ("STALE", "ERROR", "NO_DATA"):
        counts["bad"] += 1
    if status == "STALE":
        warns.append(f"{sym} [{label}]: STALE ({rec.get(f'scan_error{s}')})")
    if status not in ("OK", "STALE"):
        return

    if rec.get(f"trend{s}") not in TRENDS:
        errors.append(f"{sym} [{label}]: trend={rec.get(f'trend{s}')!r}")
    rs = _num(rec.get(f"signal_rank_score{s}"))
    if rs is None or not 0 <= rs <= MAX_RANK_SCORE:
        errors.append(f"{sym} [{label}]: signal_rank_score={rec.get(f'signal_rank_score{s}')!r}")
    bs = rec.get(f"bars_since_signal{s}")
    if not _is_int(bs) or bs < -1:
        errors.append(f"{sym} [{label}]: bars_since_signal={bs!r}")
    price = _num(rec.get(f"current_price{s}"))
    if price is None or price <= 0:
        errors.append(f"{sym} [{label}]: current_price={rec.get(f'current_price{s}')!r}")
    sd = rec.get(f"scan_date{s}")
    try:
        datetime.strptime(str(sd), prof.date_fmt)
    except ValueError:
        errors.append(f"{sym} [{label}]: scan_date={sd!r} does not match {prof.date_fmt}")

    sstatus = rec.get(f"signal_status{s}")
    if sstatus is None:
        return  # record written by an older scanner version: SMC fields only
    if sstatus not in VALID_SIGNAL_STATUS:
        errors.append(f"{sym} [{label}]: signal_status={sstatus!r}")
        return
    if sstatus != "OK":
        return
    best = _num(rec.get(f"sig_best_score{s}"))
    if best is None or not 0 <= best <= 100:
        errors.append(f"{sym} [{label}]: sig_best_score={rec.get(f'sig_best_score{s}')!r}")
    top = rec.get(f"sig_top{s}")
    if not isinstance(top, list) or len(top) > 3:
        errors.append(f"{sym} [{label}]: sig_top must be a list of at most 3")
    else:
        for g in top:
            if not (isinstance(g, dict) and g.get("name") and g.get("dir") in DIRS and _is_int(g.get("since"))
                    and _num(g.get("score")) is not None):
                errors.append(f"{sym} [{label}]: malformed sig_top entry {g!r}")
                break
    detail = detail_store.read_detail(sym)
    if prof.key not in detail:
        errors.append(f"{sym} [{label}]: detail file or '{prof.key}' block is missing")
    else:
        _check_block(sym, prof, detail[prof.key], errors)


def validate(stocks_path=None, strict: bool = False) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warns: List[str] = []
    path = Path(stocks_path or config.STOCKS_JSON)
    if not path.exists():
        return [f"{path} does not exist"], warns
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (ValueError, OSError) as e:
        return [f"{path.name} is not valid strict JSON: {e}"], warns
    if not isinstance(data, list):
        return [f"{path.name} must contain a JSON array"], warns

    seen = set()
    totals = {p.key: {"scanned": 0, "ok": 0, "bad": 0} for p in config.PROFILES.values()}
    for idx, rec in enumerate(data):
        if not isinstance(rec, dict):
            errors.append(f"record #{idx} is not an object")
            continue
        sym = rec.get("symbol")
        if not sym or not isinstance(sym, str):
            errors.append(f"record #{idx} has no symbol")
            continue
        if sym in seen:
            errors.append(f"duplicate symbol {sym}")
        seen.add(sym)
        for prof in config.PROFILES.values():
            _check_timeframe(rec, sym, prof, errors, warns, totals[prof.key])

    if data and sum(t["ok"] for t in totals.values()) == 0:
        errors.append("no symbol scanned OK in either timeframe (Yahoo unreachable?)")
    for key, t in totals.items():
        if t["scanned"] and t["bad"] / t["scanned"] > config.STALE_WARN_FRACTION:
            warns.append(f"[{key}] {t['bad']}/{t['scanned']} symbols are STALE/ERROR/NO_DATA "
                         f"(> {config.STALE_WARN_FRACTION:.0%})")
    if strict:
        errors += [f"(strict) {w}" for w in warns]
    return errors, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--path", help="stocks.json to check (default: shared-data/stocks.json)")
    args = ap.parse_args(argv)
    errors, warns = validate(args.path, args.strict)
    for w in warns[:20]:
        print(f"WARN  {w}")
    if len(warns) > 20:
        print(f"WARN  ... and {len(warns) - 20} more warnings")
    if errors:
        for e in errors[:50]:
            print(f"ERROR {e}")
        if len(errors) > 50:
            print(f"ERROR ... and {len(errors) - 50} more errors")
        print(f"VALIDATION FAILED: {len(errors)} error(s)")
        return 1
    print(f"Validation OK ({len(warns)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
