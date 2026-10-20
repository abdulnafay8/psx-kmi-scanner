"""
Step 2: check PSX for a newer KMI All Share list and show what changed
before saving it.

Run from your project folder:
    python step2_refresh_kmi.py

It prints stocks added to and removed from the list, then asks before saving.
The old list is copied to data/kmi_history/ first, so nothing is lost.
Nothing is saved unless you type y.
"""
import datetime as dt
import shutil

import pandas as pd
import psxdata

from kmi import KMI_FILE, DATA_DIR, clean_kmi_symbol

HISTORY_DIR = DATA_DIR / "kmi_history"


def fetch_new_list():
    syms = psxdata.symbols()
    known = set(syms["symbol"])
    raw = psxdata.tickers(index="KMIALLSHR")
    new = pd.DataFrame({"raw_ticker": raw})
    new["symbol"] = [clean_kmi_symbol(t, known) for t in new["raw_ticker"]]
    new = new.drop_duplicates("symbol")
    new = new.merge(syms[["symbol", "name", "sector_name"]], on="symbol", how="left")
    new = new.rename(columns={"sector_name": "sector"})
    new["list_date"] = dt.date.today().isoformat()
    unmatched = new.loc[~new["symbol"].isin(known), "raw_ticker"].tolist()
    return new[["symbol", "raw_ticker", "name", "sector", "list_date"]], unmatched


def show(title, df):
    print(f"\n{title}: {len(df)}")
    if len(df):
        print(df[["symbol", "name", "sector"]].to_string(index=False))


def main():
    print("Fetching the latest KMI All Share list from PSX...")
    new, unmatched = fetch_new_list()
    if new.empty:
        print("PSX returned an empty list. Not saving anything.")
        return

    if not KMI_FILE.exists():
        old = new.iloc[0:0]
        print(f"No current list at {KMI_FILE}; everything below counts as added.")
    else:
        old = pd.read_csv(KMI_FILE)
        print(f"Current list: {len(old)} stocks, dated {old['list_date'].iloc[0]}.")
    print(f"PSX list now: {len(new)} stocks.")

    added = new[~new["symbol"].isin(old["symbol"])]
    removed = old[~old["symbol"].isin(new["symbol"])]
    show("ADDED (would become scannable)", added)
    show("REMOVED (would disappear everywhere)", removed)
    if unmatched:
        print(f"\nWarning: {len(unmatched)} KMI tickers match no PSX symbol and will have "
              f"no price data: {', '.join(unmatched)}")
    if len(old) and len(new) < 0.8 * len(old):
        print(f"\nWARNING: the new list is {len(old) - len(new)} stocks shorter. "
              "That can mean the PSX page failed to load fully. Check before saving.")

    if added.empty and removed.empty:
        print("\nNo changes. Current list kept as it is.")
        return

    try:
        answer = input("\nSave the new list? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "y":
        print("Not saved. Current list unchanged.")
        return

    if KMI_FILE.exists():
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        backup = HISTORY_DIR / f"kmi_list_{old['list_date'].iloc[0]}.csv"
        shutil.copy2(KMI_FILE, backup)
        print(f"Old list backed up to {backup}")
    new.to_csv(KMI_FILE, index=False)
    print(f"Saved {len(new)} stocks to {KMI_FILE}.")
    if len(added):
        print("Run step1_download_data.py to fetch prices for the added stocks.")


if __name__ == "__main__":
    main()
