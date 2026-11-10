"""Central settings. Everything tunable lives here."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

# NSE_DASH_HOME lets the tests (or you) point the whole system at another folder.
CODE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("NSE_DASH_HOME") or CODE_DIR)
DATA_DIR = BASE_DIR / "shared-data"
LOG_DIR = BASE_DIR / "logs"
STOCKS_JSON = DATA_DIR / "stocks.json"
STOCKS_1H_JSON = DATA_DIR / "stocks_1h.json"  # derived view, always regenerated
INDEX_LISTS_JSON = DATA_DIR / "index_lists.json"  # optional, written by update_index_lists.py
DETAIL_DIR = DATA_DIR / "detail"  # one file per stock: signals, context, scores, candles (both timeframes)
HOLIDAYS_FILE = DATA_DIR / "holidays.txt"  # optional: one YYYY-MM-DD per line, NSE trading holidays
PIPELINE_LOCK = DATA_DIR / ".pipeline.lock"
WRITE_LOCK = DATA_DIR / ".write.lock"

# ---- Market-structure engine (same defaults as before) ----------------------
SWING_LEN = 50  # LuxAlgo "swings length"
MIN_BARS = SWING_LEN * 2 + 20  # fewer bars than this -> INSUFFICIENT_DATA
DAILY_LOOKBACK_DAYS = 540
# Yahoo refuses 1h requests that reach back more than ~730 days. The start date is a calendar date
# counted in IST, which can add up to a day (plus the 5h30 offset from UTC) to the real range, so 729
# can be rejected. 700 leaves a safe margin and still gives ~3,300 hourly bars.
HOURLY_LOOKBACK_DAYS = 700
DAILY_DATE_FMT = "%Y-%m-%d"
HOURLY_DATE_FMT = "%Y-%m-%d %H:%M"

# Only analyse candles that have finished. Set to False to restore the old behaviour (not recommended:
# a still-forming candle can show a break of structure that disappears when the candle closes).
CLOSED_CANDLES_ONLY = True

# ---- Market clock (IST, no DST) ---------------------------------------------
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
FINAL_BAR_READY = (15, 45)  # after this the day's candle is final on Yahoo
SCAN_DELAY_MIN = 3  # an hourly candle is scanned this many minutes after it closes (Yahoo publish lag)

# ---- Signals: shared by both timeframes -------------------------------------
SQUEEZE_PCT = 0.20  # compression = in the lowest 20% of its own recent history
BB_LEN, BB_MULT = 20, 2.0
KC_MULT = 1.5  # Keltner channel width for the TTM squeeze
BREAKOUT_LOOKBACK = 20  # "recent range" used by breakout rules
VDU_RATIO = 0.6  # volume dry-up: typical relative volume below this
VOL_SPIKE_RATIO = 2.0  # volume spike: relative volume at or above this
PIVOT_VOL_LOOKBACK = 10  # pocket pivot: compare against down bars in the last N candles
FVG_MIN_ATR = 0.25  # fair value gap must be at least this many ATRs wide
MAX_SIGNALS = 60  # hard cap per symbol and timeframe
TOP_BOARD_SIZE = 20
# Instrument groups that only get the market-structure (SMC) scan: their volume/price data is not
# suitable for candlestick and volume signals.
SIGNAL_SKIP_GROUPS = {"DEBT", "FUND"}
STALE_WARN_FRACTION = 0.25  # validate step warns when more than this share of symbols is STALE/ERROR


@dataclass(frozen=True)
class Profile:
    """Everything that depends on the timeframe. One profile is passed through the signal code."""
    key: str  # "1d" | "1h"
    label: str  # "1D" | "1H"
    interval: str  # Yahoo interval
    suffix: str  # field-name suffix in stocks.json ("" for daily, "_1h" for hourly)
    stamp: str  # field that holds the last scan time
    lookback_days: int  # how far back to download
    date_fmt: str
    calc_bars: int  # newest candles fed to the pattern / indicator engine
    recent_candles: int  # candles saved per stock for the child-page chart
    store_bars: int  # keep every signal formed within the last N candles
    fresh_fade_bars: int  # freshness score falls from full marks to 0 over this many candles
    range_keep_bars: int  # Inside Bar / NR4 / NR7 are one-candle triggers: keep them only this long
    pct_lookback: int  # window for "lowest X% of recent history"
    div_lookback: int  # OBV / A-D divergence window
    periods_per_year: int  # candles per year, for annualised volatility
    top_board_window: int  # the Top bullish / Top bearish boards look at this many candles
    window_options: Tuple[int, ...]  # "formed within last" choices for the dashboard
    slot_vol_days: int = 50  # relative volume = volume / average of the same time-of-day slot over N sessions


PROFILES = {
    # 1 candle per day. The "same time-of-day slot" volume comparison becomes a plain 50-day average.
    "1d": Profile(key="1d", label="1D", interval="1d", suffix="", stamp="scanned_at",
                  lookback_days=DAILY_LOOKBACK_DAYS, date_fmt=DAILY_DATE_FMT,
                  calc_bars=700, recent_candles=60,
                  store_bars=20, fresh_fade_bars=10, range_keep_bars=3,
                  pct_lookback=126, div_lookback=20, periods_per_year=252,
                  top_board_window=10, window_options=(1, 2, 3, 5, 7, 10, 20)),
    # 7 candles per day on NSE (09:15 ... 15:15, the last one is 15 minutes long) - same count as the US scanner.
    "1h": Profile(key="1h", label="1H", interval="1h", suffix="_1h", stamp="scanned_at_1h",
                  lookback_days=HOURLY_LOOKBACK_DAYS, date_fmt=HOURLY_DATE_FMT,
                  calc_bars=700, recent_candles=40,
                  store_bars=35, fresh_fade_bars=20, range_keep_bars=7,
                  pct_lookback=441, div_lookback=30, periods_per_year=7 * 252,
                  top_board_window=20, window_options=(3, 6, 7, 10, 14, 20, 35)),
}
TIMEFRAMES = ("1d", "1h")

# ---- Downloading ------------------------------------------------------------
SCAN_WORKERS = 8
ENRICH_WORKERS = 6
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BASE = float(os.environ.get("NSE_RETRY_BASE", 2.0))  # seconds; exponential backoff base
PRICE_BATCH_SIZE = 100
CHECKPOINT_EVERY = 400  # save partial scan results every N symbols

# ---- Cap classification ------------------------------------------------------
CAP_METHOD = "rank"  # "rank" (SEBI/AMFI: top100 large, next150 mid, rest small) or "threshold"
LARGE_CAP_CR = 20_000  # only used when CAP_METHOD == "threshold"
MID_CAP_CR = 5_000
RANK_LARGE = 100
RANK_MID = 250
ENRICH_REFRESH_DAYS = 7
ENRICH_RETRY_DAYS = 2

NON_EQUITY_GROUPS = {"INDEX", "ETF", "FUND", "DEBT", "REIT", "INVIT"}

# ---- Support / resistance levels (1H candles only; shown on the stock page) ----
LEVELS_ON = True                    # False = skip the level finder (levels.py)
LEVELS_TF = "1h"                    # the timeframe levels are found on
BOARD_FILE = str(DATA_DIR / "board_{tf}.json")  # dashboard extras per timeframe (signals, candles, ATR) built by build_board.py
