"""Central settings. Everything tunable lives here."""
import os
from pathlib import Path

# NSE_DASH_HOME lets the tests (or you) point the whole system at another folder.
CODE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("NSE_DASH_HOME") or CODE_DIR)
DATA_DIR = BASE_DIR / "shared-data"
LOG_DIR = BASE_DIR / "logs"
STOCKS_JSON = DATA_DIR / "stocks.json"
STOCKS_1H_JSON = DATA_DIR / "stocks_1h.json"          # derived view, always regenerated
INDEX_LISTS_JSON = DATA_DIR / "index_lists.json"      # optional, written by update_index_lists.py
PIPELINE_LOCK = DATA_DIR / ".pipeline.lock"
WRITE_LOCK = DATA_DIR / ".write.lock"

# ---- Market-structure engine (same defaults as before) ----------------------
SWING_LEN = 50                    # LuxAlgo "swings length"
MIN_BARS = SWING_LEN * 2 + 20     # fewer bars than this -> INSUFFICIENT_DATA
DAILY_LOOKBACK_DAYS = 540
HOURLY_LOOKBACK_DAYS = 729        # Yahoo serves at most ~730 days of 1h data
DAILY_DATE_FMT = "%Y-%m-%d"
HOURLY_DATE_FMT = "%Y-%m-%d %H:%M"

# ---- Market clock (IST, no DST) ---------------------------------------------
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
FINAL_BAR_READY = (15, 45)        # after this the day's candle is final on Yahoo

# ---- Downloading ------------------------------------------------------------
SCAN_WORKERS = 8
ENRICH_WORKERS = 6
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BASE = float(os.environ.get("NSE_RETRY_BASE", 2.0))   # seconds; exponential backoff base
PRICE_BATCH_SIZE = 100
CHECKPOINT_EVERY = 400            # save partial scan results every N symbols

# ---- Cap classification ------------------------------------------------------
CAP_METHOD = "rank"               # "rank" (SEBI/AMFI: top100 large, next150 mid, rest small) or "threshold"
LARGE_CAP_CR = 20_000             # only used when CAP_METHOD == "threshold"
MID_CAP_CR = 5_000
RANK_LARGE = 100
RANK_MID = 250
ENRICH_REFRESH_DAYS = 7
ENRICH_RETRY_DAYS = 2

NON_EQUITY_GROUPS = {"INDEX", "ETF", "FUND", "DEBT", "REIT", "INVIT"}
