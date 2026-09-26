"""Keep stocks.json in sync with NSE's official lists (replaces the old bootstrap_* scripts).

    python update_universe.py              # add new listings, flag symbols NSE no longer lists
    python update_universe.py --dry-run    # show what would change
    python update_universe.py --prune      # actually remove symbols flagged as unlisted

Sources: EQUITY_L.csv (main board, series EQ), SME_EQUITY_L.csv, eq_etfseclist.csv (downloaded from
nsearchives.nseindia.com). Indices / REITs / InvITs come from the static lists in universe_data.py.
If a download fails, that category is left untouched (nothing is flagged or removed because of it).
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

import smc_engine as eng
import universe_data as U
from common import LockBusy, fmt_ts, get_logger, load_stocks, modify_stocks, norm_symbol, pipeline_lock

log = get_logger("universe")

URLS = {
    "EQUITY": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "SME": "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
    "ETF": "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Referer": "https://www.nseindia.com/", "Accept": "text/csv,*/*"}
MIN_ROWS = {"EQUITY": 1500, "SME": 100, "ETF": 50}        # sanity check before trusting a list


def fetch_text(url: str) -> str:
    import requests
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def parse_master(text: str, group: str, series_filter=("EQ",)) -> List[Dict[str, str]]:
    df = pd.read_csv(StringIO(text), dtype=str, skipinitialspace=True)
    df.columns = [c.strip().upper() for c in df.columns]
    sym_c = next((c for c in df.columns if c in ("SYMBOL",)), None)
    name_c = next((c for c in df.columns if "NAME" in c or "SECURITY" in c), None)
    ser_c = next((c for c in df.columns if c == "SERIES"), None)
    if not sym_c:
        raise ValueError(f"{group}: no SYMBOL column in {list(df.columns)}")
    out = []
    for _, row in df.iterrows():
        sym = norm_symbol(row.get(sym_c))
        if not sym or sym == "NAN":
            continue
        series = norm_symbol(row.get(ser_c)) if ser_c else ""
        if group == "EQUITY" and ser_c and series not in series_filter:
            continue
        name = str(row.get(name_c) or sym).strip() if name_c else sym
        out.append({"symbol": sym, "company_name": name, "series": series or ("EQ" if group == "EQUITY" else group)})
    return out


def new_record(symbol: str, name: str, series: str, group: str) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "symbol": norm_symbol(symbol), "company_name": name, "series": series, "instrument_group": group,
        "is_regular_equity": group == "EQUITY" and series == "EQ", "exclude_keyword_match": "NONE",
        "current_price": "NA", "last_updated": "NA", "added_at": fmt_ts(),
    }
    rec.update(eng.empty_fields("", "1D", "PENDING_SCAN"))
    rec.update(eng.empty_fields("_1h", "1H", "PENDING_SCAN"))
    return rec


def collect(fetch: Callable[[str], str] = fetch_text) -> Dict[str, Optional[List[Dict[str, str]]]]:
    """category -> list of {symbol, company_name, series}; None when the download/parse failed."""
    lists: Dict[str, Optional[List[Dict[str, str]]]] = {}
    for cat, url in URLS.items():
        try:
            rows = parse_master(fetch(url), cat)
            if len(rows) < MIN_ROWS[cat]:
                raise ValueError(f"only {len(rows)} rows - looks wrong")
            lists[cat] = rows
            log.info(f"  {cat}: {len(rows)} symbols from NSE")
        except Exception as e:                                   # noqa: BLE001
            lists[cat] = None
            log.warning(f"  {cat}: download failed ({type(e).__name__}: {str(e)[:80]}) - category left unchanged")
    lists["INDEX"] = [{"symbol": x["symbol"], "company_name": x["company_name"], "series": "INDEX"} for x in U.NSE_INDICES]
    lists["REIT"] = [{"symbol": x["symbol"], "company_name": x["company_name"], "series": "EQ"} for x in U.NSE_REITS]
    lists["INVIT"] = [{"symbol": x["symbol"], "company_name": x["company_name"], "series": "EQ"} for x in U.NSE_INVITS]
    return lists


def plan_changes(stocks: List[Dict[str, Any]], lists) -> Dict[str, Any]:
    existing = {norm_symbol(s["symbol"]): s for s in stocks}
    add, unlist, relist = [], [], []
    for cat, rows in lists.items():
        if rows is None:
            continue
        seen = set()
        for r in rows:
            seen.add(r["symbol"])
            cur = existing.get(r["symbol"])
            if cur is None:
                add.append((cat, r))
            elif cur.get("listed") is False:
                relist.append(r["symbol"])
        if cat in ("EQUITY", "SME", "ETF"):                      # only categories with a live official list
            for sym, s in existing.items():
                if str(s.get("instrument_group", "")).upper() == cat and sym not in seen and s.get("listed") is not False:
                    unlist.append(sym)
    return {"add": add, "unlist": unlist, "relist": relist}


def apply_changes(stocks: List[Dict[str, Any]], plan, prune: bool = False) -> None:
    idx = {norm_symbol(s["symbol"]): s for s in stocks}
    for cat, r in plan["add"]:
        stocks.append(new_record(r["symbol"], r["company_name"], r["series"], cat))
    for sym in plan["relist"]:
        idx[sym].pop("listed", None)
    for sym in plan["unlist"]:
        idx[sym]["listed"] = False
    if prune:
        stocks[:] = [s for s in stocks if s.get("listed") is not False]


def main(argv=None, fetch=fetch_text) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true", help="delete symbols flagged as unlisted")
    args = ap.parse_args(argv)
    try:
        with pipeline_lock():
            lists = collect(fetch)
            plan = plan_changes(load_stocks(), lists)
            log.info(f"new: {len(plan['add'])} | no longer listed: {len(plan['unlist'])} | re-listed: {len(plan['relist'])}")
            for cat, r in plan["add"][:25]:
                log.info(f"  + {r['symbol']:<14} {cat:<6} {r['company_name']}")
            for sym in plan["unlist"][:25]:
                log.info(f"  - {sym}")
            if args.dry_run:
                return 0
            modify_stocks(lambda stocks: apply_changes(stocks, plan, args.prune))
            log.info("stocks.json updated. Run the daily sync to scan the new symbols.")
            return 0
    except LockBusy:
        log.error("Another pipeline run is in progress.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
