# NSE SMC + Pattern Scanner and Dashboard

Scans the whole NSE universe on two timeframes and shows the result in a static dashboard:

* **Market structure (Smart Money Concepts):** swing highs/lows, BOS / CHoCH, Strong/Weak High/Low.
* **Candlestick patterns (60) and 15 other signals:** squeeze, volume, trend and structure events, each scored 0-100.

| Timeframe | Scans per day | When (IST) | Candle used |
|---|---|---|---|
| **1D** | **1** | 15:50 (the daily candle is final at 15:45) | closed daily candle |
| **1H** | **7** | 10:20 11:20 12:20 13:20 14:20 15:20 15:35 | the hourly candle that just closed (10:15 ... 15:15, plus the 15-minute 15:15-15:30 candle) |

Only **closed** candles are analysed, so a signal never appears and then vanishes when the candle closes.

## Layout

```
config.py                            all settings, incl. the per-timeframe PROFILES (1D / 1H)
common.py                            IST clock, atomic strict-JSON store, locks, retry, logging
smc_engine.py                        the structure engine (pure functions, no I/O)
indicators.py                        ATR, Bollinger, Keltner, ADX, OBV, CMF, Ichimoku ...
patterns.py                          60 candlestick pattern detectors
signals.py                           15 events, current-state readings, confirmation rule, 0-100 score
schedule.py                          exactly when each timeframe is due
detail_store.py                      per-stock detail files  shared-data/detail/<SYMBOL>.json
market_data.py                       Yahoo access; drops the unfinished candle; keeps Volume
scan_market_structure_combined.py    1D + 1H scan: structure + patterns + signals
refresh_all_prices.py                fast batch price refresh
enrich_stocks.py                     sector / market cap / index membership / cap class
update_universe.py                   keep stocks.json in sync with NSE's official lists
update_index_lists.py                refresh the index-constituent lists
index_data.py, universe_data.py      static fallback lists
add_stock.py                         add one symbol
validate_data.py                     strict publish gate (see below)
master_sync_all.py                   runs the pipeline steps in order
cleanup_legacy_child_pages.py        one-off: remove old per-stock HTML folders
index.html                           home page linking both dashboards
signals/index.html, detail.html      Signals dashboard: top setups, filters, chart, score breakdown
signals/signals.css, signals.js      its stylesheet and helpers
main-dashboard/index.html            the US-style dashboard (tabs, Top 20 boards, SMC + pattern, 1D/1H switch)
stock/index.html                     one per-stock page
assets/style.css, common.js          the dashboard's stylesheet and data adapter
levels.py                            support / resistance level finder (1H)
build_board.py                       dashboard extras per timeframe -> shared-data/board_1d.json, board_1h.json
shared-data/stocks.json              the data (seed is [] - run the universe step; stocks_1h.json, detail/, board_*.json are generated)
shared-data/holidays.txt             optional NSE holiday list
tests/                               unit + pipeline + schedule + signal + validation tests (no network)
.github/workflows/scan.yml           the 8 daily GitHub runs, history trim, site deploy
.github/workflows/pages.yml          publishes the site when you push changes
.github/workflows/tests.yml          tests on every push
.nojekyll                            lets GitHub Pages serve the folders as they are
*.bat                                Windows launchers
```

## The dashboard (US-style)

`index.html` opens `main-dashboard/index.html`, the same dashboard as the US scanner, on NSE data:

* **Tabs:** SMC + pattern, SMC signals, Pattern signals, All symbols, Signal feed, Watchlist, with the Top 20 boards and filters.
* **1D / 1H switch** in the header. The choice is remembered. "Signals (detailed)" opens the separate Signals pages.
* **SMC + pattern tab, candle strips, relative volume:** read from `shared-data/board_1d.json` / `board_1h.json`,
  built after every scan by `build_board.py` from the per-stock detail files. `stocks.json` itself stays small.
* **Support / resistance levels:** found on 1H candles by `levels.py` during the hourly scan and shown on the stock page.
  Switch off with `LEVELS_ON` in `config.py`.
* `stock/index.html` is the per-stock page (candle chart, signals, indicators, levels).

Currency is shown in rupees. Prices come from Yahoo, so values can differ slightly from NSE's own.

## Deploying to a new GitHub account

1. Create a new repository and upload everything in this folder (keep the folder structure; the `.github`
   folder must stay at the top level).
2. **Settings > Actions > General > Workflow permissions:** choose *Read and write permissions*.
3. **Settings > Pages > Build and deployment > Source: choose "GitHub Actions"** (not "Deploy from a branch").
   The `deploy` job in `scan.yml` publishes the site after every scan, and `pages.yml` publishes when you push
   changes yourself (scan commits made by the bot cannot trigger other workflows, which is why the scan workflow
   publishes by itself). The site opens at `https://<user>.github.io/<repo>/`.
4. **Actions > NSE scan > Run workflow** once, with steps `universe scan prices enrich board validate` and *force* ticked. The first
   run covers the whole universe and is the slow one (up to about two hours). After that, each run only does
   what a new candle made due.
5. The scheduled runs (Monday to Friday) take over. The workflow file lists the exact cron lines.

`shared-data/stocks.json` ships as an empty seed (`[]`). The `universe` step fills it with the real NSE lists. If NSE blocks GitHub's servers,
run `update_nse_universe.bat` (or `python update_universe.py`) on your own computer once and commit the result.
To rebuild from nothing: set `stocks.json` to `[]` and run `python master_sync_all.py --steps universe scan prices enrich board validate`.

## Running locally

```
install_requirements.bat                      # first time
python master_sync_all.py                     # scan, prices, enrich, validate
python master_sync_all.py --steps scan        # one step
python master_sync_all.py --force --workers 4 # gentler on Yahoo's rate limit
python scan_market_structure_combined.py              # only what is due right now
python scan_market_structure_combined.py --force      # everything, now
python scan_market_structure_combined.py --timeframes 1h --limit 20
python scan_market_structure_combined.py --no-signals # structure only (the old behaviour)
python validate_data.py                       # the publish gate (--strict makes warnings errors)
python update_universe.py [--dry-run] [--prune]
python update_index_lists.py && python enrich_stocks.py --indices-only   # after March/September rebalancing
python add_stock.py TCS "Tata Consultancy Services"
python -m pytest -q tests
```

## Data contract

`stocks.json` is a flat array, so the existing dashboard keeps working. Existing fields are unchanged.
New fields use the same suffix convention (`name` for 1D, `name_1h` for 1H; `stocks_1h.json` exposes the 1H
ones under the plain name):

| Field | Meaning |
|---|---|
| `signal_status` | `OK`, `SKIPPED` (bonds / funds), `INSUFFICIENT_DATA` or `ERROR` |
| `bars`, `last_bar_time` | candles analysed, time of the newest closed candle |
| `sig_best_score`, `sig_bull_score`, `sig_bear_score` | 0-100 confluence scores |
| `sig_best_name`, `sig_best_dir`, `sig_best_since` | the best signal, its direction, candles ago |
| `sig_bull_name`, `sig_bull_since`, `sig_bear_name`, `sig_bear_since` | the best bullish and the best bearish signal and how many candles ago |
| `sig_bull_count`, `sig_bear_count` | number of stored signals per direction |
| `sig_top` | up to 3 best signals `{name, dir, since, score, confirm}` |
| `squeeze_state`, `compression_layers` | `off` / `on` / `fired`, number of compression layers on |

The heavy data is **not** in `stocks.json`. It lives in `shared-data/detail/<SYMBOL>.json`
(`M&M` is stored as `M~26M.json`, see `detail_store.safe_name`):

```json
{"symbol": "TCS", "updated_at": "2026-11-06 15:50:12 IST",
 "1d": {"last_bar_time", "bars", "signals": [...], "context": {...}, "scores": {...},
        "candles": [[time, open, high, low, close, volume], ...]},
 "1h": {...}}
```

## Timeframe settings (`config.PROFILES`) - starting values, tune after you watch real output

| Setting | 1H | 1D |
|---|---|---|
| History downloaded | 700 days | 540 days |
| Signals kept (formed within the last N candles) | 35 (5 sessions) | 20 (about a month) |
| Freshness score fades to 0 over | 20 candles | 10 candles |
| Inside Bar / NR4 / NR7 kept for | 7 candles | 3 candles |
| "Lowest 20% of history" window | 441 candles | 126 candles |
| OBV / A-D divergence window | 30 | 20 |
| Candles saved for the chart | 40 | 60 |
| Annualised-volatility factor | sqrt(7 x 252) | sqrt(252) |

The SMC swing length stays 50 on both timeframes. Bonds and funds (`SIGNAL_SKIP_GROUPS`) get the
structure scan only.

## What `validate_data.py` blocks

It runs before every commit and exits 1 (so nothing is published) when: `stocks.json` is not strict JSON
(NaN / Infinity), a symbol is duplicated or missing, a scanned record has an unknown status, a bad trend, a rank
score outside 0-110, a non-positive price or a malformed date; a signal has a bad direction, a `since`
outside the stored window or a score outside 0-100; a detail file or timeframe block is missing; or not one
symbol scanned OK. Stale records only warn.

## Design notes

* `scanner_status` per record: `PENDING_SCAN`, `OK`, `STALE` (Yahoo failed, showing the last good scan),
  `ERROR`, `INSUFFICIENT_DATA` (fewer than `config.MIN_BARS` candles), `NO_PIVOTS`.
* A failed Yahoo request never wipes good data; the record is kept and marked `STALE`.
* Cap classification defaults to rank (top 100 large, next 150 mid, rest small); change `CAP_METHOD` in `config.py`.
* `NSE_DASH_HOME` overrides where data and logs live (the tests use it to run against a throwaway folder).
* Only one pipeline run at a time (`shared-data/.pipeline.lock`).

## Things to know

* **Repo growth.** The 1H scan changes most detail files seven times a day, and every change is a commit. To stop
  the repository growing without limit, the last step of `scan.yml` keeps only the newest 100 commits (it rewrites
  history and force-pushes). Files are not affected, only old versions of them, so you cannot go back to an old
  data snapshot. This needs the `main` branch to have **no branch protection** (a protected branch refuses the
  force-push). GitHub frees the storage of the dropped commits with a delay, so the repository size shown in
  Settings can lag. The per-stock detail files (instead of one giant `stocks.json`) keep each commit small.
* **GitHub's runners and Yahoo.** Yahoo rate-limits shared cloud IPs more than home connections. The scan retries
  and never wipes good data, but if many rows turn `STALE`, lower the worker count (`--workers`).
* **Daily gap patterns.** Patterns the Handbook describes with a gap use the same relaxed rule on 1D as on 1H
  (see the docstring in `patterns.py`), so 1D may show a few extra, weaker matches.
* **Not modelled:** NSE holidays unless listed in `shared-data/holidays.txt`; special sessions.
* **Two dashboards.** `main-dashboard/` and `stock/` are your original pages and are untouched. The new **Signals**
  dashboard (`signals/`) reads the new fields and the detail files. It has the top ten bullish and bearish setups,
  filters (direction, minimum score, formed within N candles, squeeze, size, sector), a sortable table, and a stock
  page with a candlestick chart marking every signal, the score breakdown, the indicator readings and the full
  signal list. The home page (`index.html`) links to both. To test locally: `python -m http.server` in the project
  folder, then open `http://localhost:8000/`.
