"""
Checks that backtest.py follows the honest-test rules it can break silently.

    python test_step3_backtest.py
"""
import itertools

import numpy as np
import pandas as pd

import backtest as bt
from kmi import load_prices


def synthetic(closes, highs=None, lows=None, volume=1_000_000):
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=n),
        "open": c, "high": highs if highs is not None else c * 1.01,
        "low": lows if lows is not None else c * 0.99,
        "close": c, "volume": volume, "is_anomaly": False,
    })


def test_no_peeking():
    """Signals up to day t must not change when the future is cut off."""
    for symbol in ["OGDC", "LUCK", "SYS"]:
        df = load_prices(symbol)
        full = bt.prepare(df)
        for t in [400, 900, 1500]:
            cut = bt.prepare(df.iloc[:t + 1])
            for name, spec in bt.SETUPS.items():
                grid = spec["signal_grid"]
                for combo in itertools.product(*grid.values()):
                    params = dict(zip(grid, combo))
                    a = spec["signal"](full, **params)[:t + 1]
                    b = spec["signal"](cut, **params)
                    assert (a == b).all(), f"{name} peeks ahead on {symbol} at bar {t}"
    print("PASS  no setup signal uses future data")


def test_circuit_locks():
    closes = [100.0] * 80 + [110.0, 110.0, 99.0, 99.0, 99.0]
    df = synthetic(closes)
    # bar 80: locked at the upper limit (one price all day, up from 100)
    df.loc[80, ["open", "high", "low"]] = 110.0
    # bar 82: locked at the lower limit
    df.loc[82, ["open", "high", "low"]] = 99.0
    a = bt.prepare(df)
    assert a["locked_up"][80] and a["locked_down"][82]
    assert bt.entry_fill(a, 79, "open") is None, "bought into an upper-lock day"
    assert bt.entry_fill(a, 79, "stop") is None, "buy-stop filled on an upper-lock day"
    # holding from bar 81 with a 5% stop: bar 82 is lower-locked, so the
    # sale has to wait for bar 83
    x, price, reason = bt.exit_trade(a, 81, 110.0, target=0.5, stop=0.05, hold=50, fade=False)
    assert x == 83 and reason == "stop", (x, reason)
    print("PASS  no buys at an upper lock, no sells at a lower lock")


def test_data_break():
    # a 50% overnight drop is beyond any circuit limit: a bonus/split, not a trade
    closes = [100.0] * 80 + [50.0] * 20
    a = bt.prepare(synthetic(closes))
    assert a["seg"][80] != a["seg"][79]
    x, price, reason = bt.exit_trade(a, 70, 100.0, target=0.5, stop=0.2, hold=50, fade=False)
    assert reason == "data_break" and x == 79 and price == 100.0, (x, price, reason)
    assert not a["ok"][80:].any(), "signals allowed right after a break, before warm-up"
    print("PASS  trades never cross a split/bonus jump, signals restart after warm-up")


def test_missing_open_no_fill():
    df = synthetic([100.0] * 80)
    df.loc[70, ["open", "high", "low"]] = 0.0          # PSX left the range blank
    a = bt.prepare(df)
    assert bt.entry_fill(a, 69, "open") is None and bt.entry_fill(a, 69, "limit") is None
    print("PASS  bars with a missing open/high/low never give a fill")


if __name__ == "__main__":
    test_no_peeking()
    test_circuit_locks()
    test_data_break()
    test_missing_open_no_fill()
    print("\nAll Step 3 rule checks passed.")
