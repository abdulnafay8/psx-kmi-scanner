"""
Step 1: download daily price history for every stock on the KMI All Share
list. Stocks not on data/kmi_list.csv are never downloaded.

Run from your project folder:
    python step1_download_data.py

Outputs:
    data/kmi_list.csv         KMI All Share list, created on the first run only
                              (after that, step2_refresh_kmi.py updates it)
    data/daily/<SYMBOL>.csv   daily OHLCV, one file per KMI stock
    data/summary.csv          rows and date range per stock
    data/download_log.txt     stocks that failed or had no data

Safe to re-run: stocks already downloaded are skipped.
Use  python step1_download_data.py --refresh  to download everything again.
"""
import sys
import time
import datetime as dt

import pandas as pd
import psxdata

from kmi import DATA_DIR, DAILY_DIR, KMI_FILE, clean_kmi_symbol, load_kmi_symbols

START = "2019-01-01"          # train years 2019-23, test years 2024-26
PAUSE_SECONDS = 1.0           # be polite to the PSX site
RETRIES = 3

LOG_FILE = DATA_DIR / "download_log.txt"
DAILY_DIR.mkdir(parents=True, exist_ok=True)

REFRESH = "--refresh" in sys.argv


def log(message):
    """Print a message and also append it to the log file."""
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")


def fetch_with_retries(symbol):
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            return psxdata.stocks(symbol, start=START)
        except Exception as e:  # network or site problems
            last_error = e
            time.sleep(2 * attempt)
    raise last_error


def drop_no_trade_rows(df):
    """Drop days PSX reports with a zero price and zero volume (nothing traded).
    Zero-price rows WITH volume are real trades missing an open; they stay,
    already marked is_anomaly by psxdata."""
    zero_price = (df[["open", "high", "low", "close"]] == 0).any(axis=1)
    return df[~(zero_price & (df["volume"] == 0))].reset_index(drop=True)


def download_all(symbols):
    summary = []
    failed = []
    total = len(symbols)
    for i, symbol in enumerate(symbols):
        path = DAILY_DIR / f"{symbol}.csv"
        if path.exists() and not REFRESH:
            df = pd.read_csv(path, parse_dates=["date"])
            cleaned = drop_no_trade_rows(df)
            if cleaned.empty:
                log(f"NO DATA {symbol} (only no-trade rows; file left in place)")
                failed.append(symbol)
                continue
            if len(cleaned) < len(df):
                cleaned.to_csv(path, index=False)
                status = f"cleaned ({len(df) - len(cleaned)} no-trade rows dropped)"
            else:
                status = "skipped (already saved)"
            df = cleaned
        else:
            try:
                df = fetch_with_retries(symbol)
            except Exception as e:
                log(f"FAILED  {symbol}: {e}")
                failed.append(symbol)
                continue
            if df is not None and not df.empty:
                df = drop_no_trade_rows(df)
            if df is None or df.empty:
                log(f"NO DATA {symbol}")
                failed.append(symbol)
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df.to_csv(path, index=False)
            status = "saved"
            time.sleep(PAUSE_SECONDS)

        summary.append({
            "symbol": symbol,
            "rows": len(df),
            "first_date": df["date"].min().date(),
            "last_date": df["date"].max().date(),
        })
        print(f"[{i + 1}/{total}] {symbol}: {len(df)} rows, {status}")
    return pd.DataFrame(summary), failed


def create_kmi_list():
    """Create kmi_list.csv from PSX on the first run only. After that,
    step2_refresh_kmi.py owns the list so every change is reviewed first.
    Returns False if the list could not be fetched."""
    if KMI_FILE.exists():
        print(f"Using {KMI_FILE} (update it with step2_refresh_kmi.py).")
        return True
    print("No KMI list yet; fetching it from PSX...")
    try:
        syms = psxdata.symbols()
        kmi_symbols = psxdata.tickers(index="KMIALLSHR")
    except Exception as e:
        log(f"FAILED  KMI list: {e}")
        return False
    known = set(syms["symbol"])
    kmi = pd.DataFrame({"raw_ticker": kmi_symbols})
    kmi["symbol"] = [clean_kmi_symbol(t, known) for t in kmi["raw_ticker"]]
    unmatched = kmi.loc[~kmi["symbol"].isin(known), "raw_ticker"].tolist()
    if unmatched:
        log(f"KMI tickers not matched to a PSX symbol: {', '.join(unmatched)}")
    kmi = kmi[["symbol", "raw_ticker"]]
    kmi = kmi.merge(syms[["symbol", "name", "sector_name"]], on="symbol", how="left")
    kmi = kmi.rename(columns={"sector_name": "sector"})
    kmi["list_date"] = dt.date.today().isoformat()
    kmi.to_csv(KMI_FILE, index=False)
    print(f"Saved {len(kmi)} stocks to {KMI_FILE}.")
    return True


def main():
    if not create_kmi_list():
        print("Could not get the KMI list, so nothing was downloaded. "
              "Without the list there is no way to tell which stocks are allowed.")
        return
    symbols = sorted(load_kmi_symbols())
    print(f"{len(symbols)} KMI All Share stocks. Downloading since {START}...\n")

    summary, failed = download_all(symbols)
    summary.to_csv(DATA_DIR / "summary.csv", index=False)

    print("\n" + "=" * 60)
    print(f"Done. {len(summary)} stocks saved, {len(failed)} failed or empty.")
    if failed:
        print("Failed/empty:", ", ".join(failed[:30]),
              "..." if len(failed) > 30 else "")
        print(f"Details are in {LOG_FILE}")

    if len(summary):
        sample = summary.iloc[0]["symbol"]
        print(f"\n5 sample rows ({sample}):")
        print(pd.read_csv(DAILY_DIR / f"{sample}.csv").head(5).to_string(index=False))
        print("\nDate range per stock (first 10):")
        print(summary.head(10).to_string(index=False))
        short = summary[pd.to_datetime(summary["first_date"]) > pd.Timestamp("2020-01-01")]
        print(f"\n{len(short)} stocks start after 2020 (newer listings or missing history).")


if __name__ == "__main__":
    main()
