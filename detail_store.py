"""Per-stock detail files: shared-data/detail/<SYMBOL>.json

stocks.json (and its 1H copy) stay small and fast to load: they only carry the flat summary fields.
The heavy data - every signal, the indicator context, the score parts and the recent candles - lives here,
one file per stock, and is loaded by the stock (child) page on demand.

File layout:
    {"symbol": "TCS", "updated_at": "2026-11-06 15:50:12 IST",
     "1d": {"last_bar_time", "bars", "signals", "context", "scores", "candles"},
     "1h": {... same ...}}

A timeframe block is rewritten only when its content changed, so re-running the scan (or scanning on a
holiday, when Yahoo returns no new candle) creates no git diff.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import config
from common import atomic_write_text, dumps_strict, norm_symbol, sanitize


def safe_name(symbol: str) -> str:
    """File-system safe name. 'M&M' -> 'M~26M'. The dashboard must use the same rule."""
    return re.sub(r"[^A-Za-z0-9._-]", lambda m: "~%02x" % ord(m.group()), norm_symbol(symbol))


def detail_path(symbol: str) -> Path:
    return Path(config.DETAIL_DIR) / f"{safe_name(symbol)}.json"


def read_detail(symbol: str) -> Dict[str, Any]:
    p = detail_path(symbol)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_detail(symbol: str, tf: str, payload: Dict[str, Any], stamp: str) -> bool:
    """Store one timeframe block. Returns True when the file was (re)written."""
    data = read_detail(symbol) or {}
    new_block = sanitize(payload)
    if data.get(tf) == new_block and data.get("symbol") == norm_symbol(symbol):
        return False
    data["symbol"] = norm_symbol(symbol)
    data["updated_at"] = stamp
    data[tf] = new_block
    atomic_write_text(detail_path(symbol), dumps_strict(data))
    return True
