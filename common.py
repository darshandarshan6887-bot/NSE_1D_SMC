"""Shared helpers: UTF-8 console, IST clock, strict JSON I/O, locks, logging, tickers."""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

import config

# ---- Windows consoles default to cp1252 and crash on characters like "->" ----
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# TIME  (India has no DST, so a fixed offset is exact and needs no tz database)
# ---------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
TS_FMT = "%Y-%m-%d %H:%M:%S"


def now_ist() -> datetime:
    return datetime.now(IST)


def fmt_ts(dt: Optional[datetime] = None) -> str:
    return (dt or now_ist()).strftime(TS_FMT) + " IST"


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD HH:MM:SS IST' (or without the suffix) -> aware IST datetime."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace(" IST", "")
    try:
        return datetime.strptime(s, TS_FMT).replace(tzinfo=IST)
    except ValueError:
        return None


def _at(day: datetime, hm: tuple) -> datetime:
    return day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def last_market_close(now: Optional[datetime] = None) -> datetime:
    """Most recent weekday 15:45 IST that is <= now (holidays are not modelled)."""
    now = now or now_ist()
    day = now
    if not (day.weekday() < 5 and now >= _at(day, config.FINAL_BAR_READY)):
        day = day - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return _at(day, config.FINAL_BAR_READY)


def is_market_open(now: Optional[datetime] = None) -> bool:
    now = now or now_ist()
    return now.weekday() < 5 and _at(now, config.MARKET_OPEN) <= now < _at(now, config.MARKET_CLOSE)


# ---------------------------------------------------------------------------
# NUMBERS / JSON
# ---------------------------------------------------------------------------
def safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "NA"):
            return None
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def sanitize(obj: Any) -> Any:
    """NaN / +-Infinity are legal in Python's json but break the browser's JSON.parse."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):   # numpy scalar
        try:
            return sanitize(obj.item())
        except Exception:
            return None
    return obj


def atomic_write_text(path, text: str) -> None:
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    for attempt in range(8):                     # Windows: target may be briefly locked by AV/browser
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.25 * (attempt + 1))
    os.replace(tmp, path)


def dumps_strict(obj: Any, pretty: bool = False) -> str:
    kw = dict(indent=2) if pretty else dict(separators=(",", ":"))
    return json.dumps(sanitize(obj), allow_nan=False, ensure_ascii=False, **kw)


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(config.LOG_DIR / f"{name}_{now_ist():%Y-%m-%d}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    return log


# ---------------------------------------------------------------------------
# LOCKS
# ---------------------------------------------------------------------------
class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def file_lock(path, wait: float = 0.0, stale_after: float = 3 * 3600, poll: float = 0.2):
    """Cross-platform lock file. wait=0 -> fail immediately if held (raises LockBusy)."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale_after:
                    os.remove(path)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise LockBusy(f"lock held: {path}")
            time.sleep(poll)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


@contextlib.contextmanager
def pipeline_lock():
    """One pipeline run at a time. Child scripts started by master_sync inherit the lock."""
    if os.environ.get("NSE_PIPELINE_LOCKED") == "1":
        yield
        return
    with file_lock(config.PIPELINE_LOCK, wait=0):
        os.environ["NSE_PIPELINE_LOCKED"] = "1"
        try:
            yield
        finally:
            os.environ.pop("NSE_PIPELINE_LOCKED", None)


# ---------------------------------------------------------------------------
# RETRY
# ---------------------------------------------------------------------------
def retry(fn: Callable, tries: int = config.MAX_RETRIES, base: float = 1.5,
          retryable: Callable[[Exception], bool] = lambda e: True, sleep=time.sleep):
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:                                   # noqa: BLE001
            last = e
            if not retryable(e) or i == tries - 1:
                raise
            sleep(base ** (i + 1) + (i * 0.37))
    raise last  # pragma: no cover


def is_rate_limit(e: Exception) -> bool:
    n = type(e).__name__
    msg = str(e).lower()
    return "RateLimit" in n or "429" in msg or "too many requests" in msg


# ---------------------------------------------------------------------------
# SYMBOLS / URLS / TICKERS
# ---------------------------------------------------------------------------
def norm_symbol(s: Any) -> str:
    return str(s or "").strip().upper()


def tradingview_url(symbol: str, group: str = "") -> str:
    return f"https://www.tradingview.com/chart/?symbol=NSE:{quote(symbol.lstrip('^'), safe='')}"


def dashboard_url(symbol: str) -> str:
    return f"../stock/index.html?symbol={quote(symbol, safe='')}"


# Yahoo index tickers that differ from a plain "^" + symbol rule.
YAHOO_INDEX = {"^NSEI", "^NSEBANK", "^CNXIT", "^CNXFIN", "^CNXPHARMA", "^CNXAUTO", "^CNXFMCG",
               "^CNXREALTY", "^CNXMEDIA", "^CNXMETAL", "^CNXENERGY", "^CNXINFRA", "^CNXPSUBANK",
               "^CNXCMDT", "^CNXMNC", "^CNXPSE", "^CNXCONSUM", "^NSMIDCP", "^CNXSC", "^CRSLDX",
               "^NSE200", "^NIFTY100", "^NIFMDCP100", "^NIFSMCP250"}


def yahoo_candidates(symbol: str, group: str = "") -> List[str]:
    """Ordered Yahoo tickers to try.

    Equities: SYMBOL.NS ONLY. The old code also tried the bare SYMBOL, which is a US ticker on
    Yahoo (TCS, STAR, SIS, MAZDA ... are all real US listings) and could silently return the
    price history of a completely different company whenever the .NS request failed.
    """
    s = norm_symbol(symbol)
    if not s:
        return []
    if s.startswith("^") or norm_symbol(group) == "INDEX":
        core = s.lstrip("^")
        return list(dict.fromkeys([f"^{core}", s]))
    return [f"{s}.NS"]


# ---------------------------------------------------------------------------
# STOCK STORE  (stocks.json is the single source of truth; stocks_1h.json is derived)
# ---------------------------------------------------------------------------
def load_stocks(path=None) -> List[Dict[str, Any]]:
    path = path or config.STOCKS_JSON
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)                    # tolerates legacy NaN/Infinity tokens
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return data


def normalize_record(s: Dict[str, Any]) -> Dict[str, Any]:
    sym = norm_symbol(s.get("symbol"))
    s["symbol"] = sym
    s["tradingview_url"] = tradingview_url(sym, s.get("instrument_group", ""))
    s["dashboard_url"] = dashboard_url(sym)
    return s


def derive_1h_view(stocks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Plain-named copy of every record where the *_1h fields replace the daily ones."""
    out = []
    for s in stocks:
        rec = {k: v for k, v in s.items() if not k.endswith("_1h")}
        for k, v in s.items():
            if k.endswith("_1h"):
                rec[k[:-3]] = v
        out.append(rec)
    return out


def save_stocks(stocks: List[Dict[str, Any]], pretty: bool = False) -> None:
    """Validate, de-duplicate, normalise, then write stocks.json AND stocks_1h.json atomically."""
    seen: Dict[str, Dict[str, Any]] = {}
    for s in stocks:
        normalize_record(s)
        if not s["symbol"]:
            continue
        seen[s["symbol"]] = s                  # last one wins
    clean = list(seen.values())
    atomic_write_text(config.STOCKS_JSON, dumps_strict(clean, pretty))
    atomic_write_text(config.STOCKS_1H_JSON, dumps_strict(derive_1h_view(clean), pretty))


def modify_stocks(mutator: Callable[[List[Dict[str, Any]]], Any]) -> Any:
    """Short critical section: lock -> load fresh -> mutate -> save. Never clobbers other scripts."""
    with file_lock(config.WRITE_LOCK, wait=120):
        stocks = load_stocks()
        result = mutator(stocks)
        save_stocks(stocks)
        return result


def by_symbol(stocks: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {norm_symbol(s.get("symbol")): s for s in stocks}
