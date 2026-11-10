"""Build the dashboard's extra data: shared-data/board_1d.json and board_1h.json.

    python build_board.py

stocks.json stays small (one flat summary per stock). The dashboard's "SMC + pattern" tab, the candle strip, the
relative-volume column and the signal windows need more: every recent signal, ATR, relative volume and the last candles.
That data already exists in shared-data/detail/<SYMBOL>.json; this step collects it, per timeframe, into one compact file
that the dashboard loads next to stocks.json.

    {"tf": "1h", "n": 1234, "stocks": {"TCS": {"signals": [...], "context": {...}, "candles": [...]}, ...}}
"""
from __future__ import annotations

import sys
from typing import Any, Dict

import config
import detail_store
from common import atomic_write_text, dumps_strict, get_logger, load_stocks, norm_symbol

log = get_logger("board")
SIG_KEYS = ("name", "group", "dir", "len", "stop", "since", "time", "confirm", "score")
CTX_KEYS = ("squeeze_state", "compression_layers", "atr", "atr_pct", "rel_volume")


def compact(block: Dict[str, Any]) -> Dict[str, Any]:
    ctx = block.get("context") or {}
    return {
        "signals": [{k: g.get(k) for k in SIG_KEYS} for g in block.get("signals") or []],
        "context": {k: ctx.get(k) for k in CTX_KEYS if k in ctx},
        "candles": block.get("candles") or [],
    }


def build(tf: str, stocks=None) -> Dict[str, Any]:
    prof = config.PROFILES[tf]
    s = prof.suffix
    out: Dict[str, Any] = {}
    for rec in sorted(stocks if stocks is not None else load_stocks(), key=lambda r: str(r.get("symbol"))):
        if rec.get("scanner_status" + s) not in ("OK", "STALE"):
            continue
        sym = norm_symbol(rec.get("symbol"))
        block = detail_store.read_detail(sym).get(tf)
        if block:
            out[sym] = compact(block)
    return {"tf": tf, "n": len(out), "stocks": out}


def main(argv=None) -> int:
    stocks = load_stocks()
    for tf in config.TIMEFRAMES:
        data = build(tf, stocks)
        path = config.BOARD_FILE.format(tf=tf)
        atomic_write_text(path, dumps_strict(data, False))
        log.info(f"{path}: {data['n']} stocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
