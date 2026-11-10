"""Safety net: no test may ever touch the real shared-data folder.

Several tests (pipeline, validation ...) seed fake stocks such as "EQ0", "EQ1", ... and write them with save_stocks().
Without isolation they overwrite the real shared-data/stocks.json, and a scan then finds a universe of fake symbols
that Yahoo has never heard of. Every test therefore runs against its own temporary folder.
"""
import hashlib

import pytest

import config

REAL = config.DATA_DIR


def _digest():
    out = {}
    for p in sorted(REAL.glob("*")) if REAL.exists() else []:
        if p.is_file():
            out[p.name] = hashlib.md5(p.read_bytes()).hexdigest()
    return out


_BEFORE = _digest()


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    data = tmp_path / "shared-data"
    for name, value in {
        "DATA_DIR": data, "LOG_DIR": tmp_path / "logs",
        "STOCKS_JSON": data / "stocks.json", "STOCKS_1H_JSON": data / "stocks_1h.json",
        "INDEX_LISTS_JSON": data / "index_lists.json", "DETAIL_DIR": data / "detail",
        "HOLIDAYS_FILE": data / "holidays.txt", "PIPELINE_LOCK": data / ".pipeline.lock",
        "WRITE_LOCK": data / ".write.lock", "BOARD_FILE": str(data / "board_{tf}.json"),
    }.items():
        if hasattr(config, name):
            monkeypatch.setattr(config, name, value)
    yield


def pytest_sessionfinish(session, exitstatus):
    after = _digest()
    if after != _BEFORE:
        changed = sorted(set(after) ^ set(_BEFORE) | {k for k in after if k in _BEFORE and after[k] != _BEFORE[k]})
        session.exitstatus = 1
        print(f"\nERROR: the tests changed the real shared-data folder: {changed}")
