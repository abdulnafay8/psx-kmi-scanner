"""
Step 7 check: replay 2024-26 day by day through the ledger (lock alerts,
grade them) and compare every graded alert with the backtester's trade.

    python test_step7_ledger.py

Runs on an in-memory ledger; data/ledger.json is not touched.
"""
import datetime as dt

import numpy as np
import pandas as pd

import backtest as bt
import ledger as lg
from engine import load_passed
from kmi import load_all_prices


def main():
    passed = load_passed()
    prices, _ = load_all_prices()
    stocks = {s: bt.prepare(df) for s, df in prices.items()}
    fired = {setup: {s: bt.SETUPS[setup]["signal"](a, **bt.split_params(setup, info["params"])[0])
                     for s, a in stocks.items()} for setup, info in passed.items()}
    # replay from the first day so positions opened before 2024 carry over
    days = sorted({d for a in stocks.values() for d in a["date"]})

    for setup, info in passed.items():
        rows = []
        for d in days:
            day = str(pd.Timestamp(d).date())
            sig = {"date": day, "stocks": []}
            for s, a in stocks.items():
                idx = np.flatnonzero(a["date"] == d)
                if not len(idx) or not fired[setup][s][idx[0]]:
                    continue
                t = idx[0]
                kind, price = bt.entry_order(a, t, bt.SETUPS[setup]["entry"])
                p = info["params"]
                sig["stocks"].append({"symbol": s, "date": day, "close": float(a["c"][t]), "plans": [{
                    "setup": setup, "setup_label": info["label"], "order": kind, "order_type": kind,
                    "order_price": price, "stop_pct": p["stop"], "target_pct": p.get("target"),
                    "max_hold_days": p["hold"]}]})
            # as run_daily.py does: grade with data up to this day first,
            # then lock today's new alerts
            cut = {s: {k: (v[: int(np.searchsorted(a["date"], d, side="right"))]
                           if isinstance(v, np.ndarray) else v) for k, v in a.items()}
                   for s, a in stocks.items() if s in {r["symbol"] for r in rows if lg.is_active(r)}}
            lg.grade_all(rows, cut)
            lg.add_alerts(rows, sig, "test", dt.datetime(2026, 1, 1))
        lg.grade_all(rows, stocks)

        sig_p, ex_p = bt.split_params(setup, info["params"])
        trades = bt.simulate(stocks, setup, sig_p, ex_p)
        trades = trades[pd.to_datetime(trades["signal_date"]) >= bt.TEST_START]
        bt_pkr = dict(zip(zip(trades["symbol"], pd.to_datetime(trades["signal_date"]).dt.date.astype(str)),
                          bt.pkr(trades)))
        rows = [r for r in rows if r["signal_date"] >= str(bt.TEST_START.date())]
        filled = {(r["symbol"], r["signal_date"]): r for r in rows if r["result"]["state"] in ("open", "closed")}
        missing = set(bt_pkr) - set(filled)
        extra = set(filled) - set(bt_pkr)
        diff = [k for k in set(bt_pkr) & set(filled)
                if filled[k]["result"]["state"] == "closed" and abs(filled[k]["result"]["pkr"] - bt_pkr[k]) > 1]
        st = lg.stats(rows)
        print(f"{setup}: ledger {st['total_alerts']} alerts, {st['filled']} filled, {st['not_filled']} not filled, "
              f"{st['closed']} closed, avg {st['avg_pkr_per_trade']} PKR | backtest {len(trades)} trades")
        assert not missing and not extra, (sorted(missing)[:5], sorted(extra)[:5])
        assert not diff, f"PKR differs for {diff[:5]}"
        print(f"PASS  {setup}: the ledger filled exactly the backtest's {len(trades)} trades, "
              "each to the rupee")
    print("\nStep 7 ledger check passed.")


if __name__ == "__main__":
    main()
