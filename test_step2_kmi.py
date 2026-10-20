"""
Step 2 "done when" check: the scanner only loads stocks from kmi_list.csv,
and removing a stock from the file removes it everywhere.

    python test_step2_kmi.py

Works on a temporary copy of the list; the real data/kmi_list.csv is untouched.
"""
import tempfile
from pathlib import Path

import pandas as pd

from kmi import (KMI_FILE, DAILY_DIR, NotOnKMIList,
                 load_kmi_symbols, load_prices, load_all_prices)


def expect_error(error, fn, *args):
    try:
        fn(*args)
    except error:
        return
    raise AssertionError(f"{fn.__name__}{args} should have raised {error.__name__}")


def main():
    real = load_kmi_symbols()
    files = {p.stem for p in DAILY_DIR.glob("*.csv")}
    outside = sorted(files - real)
    print(f"KMI list: {len(real)} stocks. Price files: {len(files)} "
          f"({len(outside)} are NOT on the KMI list).")

    # 1. Only KMI stocks load, even though non-KMI price files sit in the folder.
    prices, missing = load_all_prices()
    assert set(prices) <= real, "a non-KMI stock was loaded"
    assert not set(prices) & set(outside)
    for symbol in outside[:25]:
        expect_error(NotOnKMIList, load_prices, symbol)
    print(f"PASS  load_all_prices gave {len(prices)} KMI stocks and none of the "
          f"{len(outside)} others ({len(missing)} KMI stocks have no price file).")

    with tempfile.TemporaryDirectory() as tmp:
        # 2. Remove one stock from the list: it disappears from every loader.
        kmi = pd.read_csv(KMI_FILE)
        victim = next(s for s in sorted(prices))
        trimmed = Path(tmp) / "kmi_list.csv"
        kmi[kmi["symbol"] != victim].to_csv(trimmed, index=False)
        assert victim not in load_kmi_symbols(trimmed)
        assert victim not in load_all_prices(trimmed)[0]
        expect_error(NotOnKMIList, load_prices, victim, trimmed)
        load_prices(victim)  # still loads from the real, untouched list
        print(f"PASS  removing {victim} from the list removed it from every loader.")

        # A non-KMI price file sitting in the folder is still never loaded.
        daily = Path(tmp) / "daily"
        daily.mkdir()
        (daily / f"{victim}.csv").write_bytes((DAILY_DIR / f"{victim}.csv").read_bytes())
        (daily / "FAKEBANK.csv").write_bytes((DAILY_DIR / f"{victim}.csv").read_bytes())
        assert "FAKEBANK" not in load_all_prices(KMI_FILE, daily)[0]
        expect_error(NotOnKMIList, load_prices, "FAKEBANK", KMI_FILE, daily)
        print("PASS  a non-KMI price file in the folder is never loaded.")

        # 3. Fail safe: no list or an empty list loads nothing.
        expect_error(FileNotFoundError, load_kmi_symbols, Path(tmp) / "nope.csv")
        empty = Path(tmp) / "empty.csv"
        kmi.iloc[0:0].to_csv(empty, index=False)
        expect_error(ValueError, load_all_prices, empty)
        print("PASS  a missing or empty list stops loading instead of loading everything.")

    print("\nStep 2 done-when check passed.")


if __name__ == "__main__":
    main()
