"""1D is scanned once a day, 1H seven times a day - exactly when a new closed candle exists."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, datetime, timedelta

import config
import schedule
from common import IST


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


# 2026-11-05 is a Thursday, 11-06 Friday, 11-07 Saturday, 11-09 Monday
def test_nse_has_seven_hourly_candles_per_day():
    assert schedule.hourly_bar_ends() == [(10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 15), (15, 30)]
    assert schedule.scans_per_day("1h") == 7
    assert schedule.scans_per_day("1d") == 1


def test_one_trading_day_has_exactly_one_daily_and_seven_hourly_scan_moments():
    day = date(2026, 11, 6)
    assert len(schedule.ready_times("1d", day)) == 1
    assert len(schedule.ready_times("1h", day)) == 7


def test_daily_candle_ready_at_1545():
    assert schedule.last_ready("1d", at(2026, 11, 6, 15, 44)) == at(2026, 11, 5, 15, 45)
    assert schedule.last_ready("1d", at(2026, 11, 6, 15, 45)) == at(2026, 11, 6, 15, 45)
    assert schedule.last_ready("1d", at(2026, 11, 6, 9, 0)) == at(2026, 11, 5, 15, 45)


def test_weekend_and_monday_morning_use_friday():
    assert schedule.last_ready("1d", at(2026, 11, 7, 12)) == at(2026, 11, 6, 15, 45)
    assert schedule.last_ready("1d", at(2026, 11, 9, 9, 0)) == at(2026, 11, 6, 15, 45)
    assert schedule.last_ready("1h", at(2026, 11, 9, 9, 0)) == at(2026, 11, 6, 15, 33)


def test_hourly_candle_ready_three_minutes_after_close():
    assert config.SCAN_DELAY_MIN == 3
    assert schedule.last_ready("1h", at(2026, 11, 6, 10, 17)) == at(2026, 11, 5, 15, 33)
    assert schedule.last_ready("1h", at(2026, 11, 6, 10, 18)) == at(2026, 11, 6, 10, 18)
    assert schedule.last_ready("1h", at(2026, 11, 6, 15, 32)) == at(2026, 11, 6, 14, 18) + timedelta(hours=1)  # 15:18
    assert schedule.last_ready("1h", at(2026, 11, 6, 15, 33)) == at(2026, 11, 6, 15, 33)


def test_full_day_triggers_seven_hourly_scans_and_one_daily_scan():
    """Run the script every 5 minutes all day; count how many runs actually have something to scan."""
    runs = {"1h": 0, "1d": 0}
    last = {"1h": None, "1d": None}
    t = at(2026, 11, 6, 9, 0)
    while t <= at(2026, 11, 6, 18, 0):
        for tf in runs:
            if schedule.is_due(last[tf], tf, t):
                runs[tf] += 1
                last[tf] = t
        t += timedelta(minutes=5)
    # Friday: the first run of the day also catches up the last candle of Thursday, so allow +1 for that
    assert runs["1h"] == 7 + 1
    assert runs["1d"] == 1 + 1


def test_after_scanning_nothing_is_due_until_next_candle():
    last = at(2026, 11, 6, 10, 20)
    assert not schedule.is_due(last, "1h", at(2026, 11, 6, 11, 0))
    assert not schedule.is_due(last, "1h", at(2026, 11, 6, 11, 17))
    assert schedule.is_due(last, "1h", at(2026, 11, 6, 11, 18))
    assert schedule.is_due(None, "1h", at(2026, 11, 6, 11, 0))


def test_daily_scan_at_1550_is_good_for_the_whole_next_session():
    last = at(2026, 11, 6, 15, 50)
    assert not schedule.is_due(last, "1d", at(2026, 11, 9, 14, 0))
    assert schedule.is_due(last, "1d", at(2026, 11, 9, 15, 45))


def test_scan_before_close_is_repeated_after_close():
    last = at(2026, 11, 6, 15, 35)  # candle not final yet
    assert schedule.is_due(last, "1d", at(2026, 11, 6, 15, 46))


def test_holidays_are_skipped(tmp_path):
    f = tmp_path / "holidays.txt"
    f.write_text("# NSE holidays\n2026-11-09  # pretend Monday is a holiday\n\nnot-a-date\n", encoding="utf-8")
    hol = schedule.load_holidays(f)
    assert hol == {date(2026, 11, 9)}
    assert schedule.last_ready("1d", at(2026, 11, 9, 16, 0), hol) == at(2026, 11, 6, 15, 45)
    assert schedule.last_ready("1d", at(2026, 11, 9, 16, 0)) == at(2026, 11, 9, 15, 45)  # no holiday file


def test_missing_holidays_file_is_fine(tmp_path):
    assert schedule.load_holidays(tmp_path / "nope.txt") == set()
