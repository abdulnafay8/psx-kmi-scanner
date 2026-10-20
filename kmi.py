"""
The halal allow-list. Every other part of the project loads stocks through
this file, so a stock that is not in data/kmi_list.csv never shows up anywhere.

    from kmi import load_kmi_symbols, load_prices, load_all_prices

It fails safe: if kmi_list.csv is missing or empty, loading stops with an
error instead of falling back to all PSX stocks.
"""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
KMI_FILE = DATA_DIR / "kmi_list.csv"
DAILY_DIR = DATA_DIR / "daily"

STATUS_SUFFIXES = ("XD", "XB", "XR", "NC")  # PSX tags like ex-dividend, glued onto tickers


class NotOnKMIList(Exception):
    pass


def clean_kmi_symbol(raw, known):
    """Strip a PSX status suffix (OGDCXD -> OGDC), but only when the raw ticker is
    unknown and the stripped one is a real symbol. Never invents a new symbol."""
    if raw in known:
        return raw
    for suffix in STATUS_SUFFIXES:
        if raw.endswith(suffix) and raw[:-len(suffix)] in known:
            return raw[:-len(suffix)]
    return raw


def load_kmi_list(path=KMI_FILE):
    """The KMI All Share list: symbol, raw_ticker, name, sector, list_date."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run step2_refresh_kmi.py first. "
                                "No stocks load without the KMI list.")
    kmi = pd.read_csv(path, dtype={"symbol": str})
    kmi["symbol"] = kmi["symbol"].str.strip()
    kmi = kmi[kmi["symbol"].notna() & (kmi["symbol"] != "")]
    if kmi.empty:
        raise ValueError(f"{path} has no stocks in it. Refusing to load anything.")
    return kmi.reset_index(drop=True)


def load_kmi_symbols(path=KMI_FILE):
    return set(load_kmi_list(path)["symbol"])


def load_prices(symbol, path=KMI_FILE, daily_dir=DAILY_DIR):
    """Daily OHLCV for one KMI stock. Raises NotOnKMIList for anything else."""
    if symbol not in load_kmi_symbols(path):
        raise NotOnKMIList(f"{symbol} is not on the KMI All Share list.")
    return pd.read_csv(Path(daily_dir) / f"{symbol}.csv", parse_dates=["date"])


def load_all_prices(path=KMI_FILE, daily_dir=DAILY_DIR):
    """{symbol: DataFrame} for every KMI stock that has a price file.
    Returns (prices, missing) where missing lists KMI stocks with no file."""
    prices, missing = {}, []
    for symbol in sorted(load_kmi_symbols(path)):
        file = Path(daily_dir) / f"{symbol}.csv"
        if file.exists():
            prices[symbol] = pd.read_csv(file, parse_dates=["date"])
        else:
            missing.append(symbol)
    return prices, missing
