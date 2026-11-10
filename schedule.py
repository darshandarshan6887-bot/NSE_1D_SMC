"""When is a timeframe due for a scan?

A timeframe is due when a NEW closed candle exists that the last scan did not include:

    1D : one candle per trading day, final at config.FINAL_BAR_READY (15:45 IST)        -> 1 scan per day
    1H : seven candles per trading day, closing at 10:15 11:15 12:15 13:15 14:15 15:15  -> 7 scans per day
         and 15:30 (the 15:15 candle is only 15 minutes long); each is scanned
         config.SCAN_DELAY_MIN minutes after it closes (Yahoo publishes with a small lag).

The rule is "last scan older than the newest candle-close moment", not "run at fixed clock times", so it
repairs itself: if a run is skipped or delayed, the next run catches up, and running the script ten times
in a row scans only once.

Weekends are skipped. NSE holidays are skipped only if listed in shared-data/holidays.txt (one YYYY-MM-DD
per line, '#' starts a comment); on an unlisted holiday Yahoo simply returns no new candle.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set

import config
from common import IST


def load_holidays(path=None) -> Set[date]:
    path = Path(path or config.HOLIDAYS_FILE)
    out: Set[date] = set()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        try:
            out.add(datetime.strptime(s, "%Y-%m-%d").date())
        except ValueError:
            continue
    return out


def is_trading_day(d: date, holidays: Optional[Iterable[date]] = None) -> bool:
    return d.weekday() < 5 and d not in set(holidays or ())


def hourly_bar_ends() -> List[tuple]:
    """(hour, minute) at which each hourly candle of a session closes: 7 on NSE."""
    open_m = config.MARKET_OPEN[0] * 60 + config.MARKET_OPEN[1]
    close_m = config.MARKET_CLOSE[0] * 60 + config.MARKET_CLOSE[1]
    ends, start = [], open_m
    while start < close_m:
        end = min(start + 60, close_m)
        ends.append((end // 60, end % 60))
        start += 60
    return ends


def scans_per_day(tf: str) -> int:
    return len(hourly_bar_ends()) if tf == "1h" else 1


def ready_times(tf: str, day: date) -> List[datetime]:
    """The moments on `day` at which a new closed candle can be scanned (aware, IST)."""
    if tf == "1d":
        h, m = config.FINAL_BAR_READY
        return [datetime(day.year, day.month, day.day, h, m, tzinfo=IST)]
    delay = timedelta(minutes=config.SCAN_DELAY_MIN)
    return [datetime(day.year, day.month, day.day, h, m, tzinfo=IST) + delay for h, m in hourly_bar_ends()]


def last_ready(tf: str, now: Optional[datetime] = None, holidays: Optional[Iterable[date]] = None) -> datetime:
    """Newest candle-ready moment that is <= now."""
    now = (now or datetime.now(IST)).astimezone(IST)
    hol = set(holidays or ())
    for back in range(0, 16):
        d = now.date() - timedelta(days=back)
        if not is_trading_day(d, hol):
            continue
        done = [t for t in ready_times(tf, d) if t <= now]
        if done:
            return max(done)
    return now - timedelta(days=30)  # unreachable in practice (15 days without a trading day)


def is_due(last_scan: Optional[datetime], tf: str, now: Optional[datetime] = None,
           holidays: Optional[Iterable[date]] = None) -> bool:
    """True when the last scan happened before the newest candle became available."""
    if last_scan is None:
        return True
    return last_scan < last_ready(tf, now, holidays)
