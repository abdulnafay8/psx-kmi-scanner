"""
Step 4 "done when": running the engine on any day of history gives the same
signals the backtester took on that day (checked on 2024-26).

    python test_step4_engine.py
"""
import numpy as np
import pandas as pd

import backtest as bt
from engine import evaluate, load_passed
from kmi import load_all_prices


def main():
    passed = load_passed()
    assert passed, "no passed setups in results/passed_setups.json; run backtest.py"
    prices, _ = load_all_prices()
    full = {s: bt.prepare(df) for s, df in prices.items()}

    for setup, info in passed.items():
        sig, ex = bt.split_params(setup, info["params"])
        all_trades = bt.simulate(full, setup, sig, ex)   # incl. 2023 trades still open in 2024
        trades = all_trades[all_trades["period"] == "test"]

        # 1. every backtest trade: engine on history cut at the signal day
        #    gives a Buy plan for this setup with the same order
        for _, tr in trades.iterrows():
            df = prices[tr["symbol"]]
            cut = bt.prepare(df[df["date"] <= tr["signal_date"]])
            out = evaluate(tr["symbol"], cut, passed=passed)
            plan = next((p for p in out["plans"] if p["setup"] == setup), None)
            assert plan, f"{setup}: engine missed {tr['symbol']} on {tr['signal_date']}"
            if plan["order_type"] != "open":
                kind, price = bt.entry_order(cut, len(cut["c"]) - 1, bt.SETUPS[setup]["entry"])
                assert abs(plan["order_price"] - price) < 0.006, (tr["symbol"], plan, price)
        print(f"PASS  {setup}: all {len(trades)} test-year trades get the same Buy plan "
              "from the engine on cut-off history")

        # 2. every test-year day of every stock: engine Buy plan <=> backtest signal,
        #    and each signal the backtest didn't trade is explained
        engine_days = signal_days = unexplained = 0
        traded = set(zip(all_trades["symbol"], pd.to_datetime(all_trades["signal_date"])))
        for symbol, a in full.items():
            fired = bt.SETUPS[setup]["signal"](a, **sig)
            test_idx = np.flatnonzero(a["date"] >= np.datetime64(bt.TEST_START))
            held_until = -1
            mine = all_trades[all_trades["symbol"] == symbol]
            for _, row in mine[pd.to_datetime(mine["signal_date"]) < bt.TEST_START].iterrows():
                held_until = int(np.flatnonzero(a["date"] == np.datetime64(row["exit_date"]))[0])
            for t in test_idx:
                has_plan = any(p["setup"] == setup for p in
                               evaluate(symbol, a, t, passed=passed)["plans"]) if fired[t] else False
                assert has_plan == bool(fired[t]), (setup, symbol, t)
                engine_days += has_plan
                signal_days += bool(fired[t])
                if fired[t] and (symbol, pd.Timestamp(a["date"][t])) not in traded:
                    in_position = t <= held_until
                    not_filled = bt.entry_fill(a, t, bt.SETUPS[setup]["entry"]) is None
                    unexplained += not (in_position or not_filled)
                if (symbol, pd.Timestamp(a["date"][t])) in traded:
                    row = mine[pd.to_datetime(mine["signal_date"]) == pd.Timestamp(a["date"][t])]
                    held_until = int(np.flatnonzero(a["date"] == np.datetime64(row["exit_date"].iloc[0]))[0])
        assert unexplained == 0, f"{unexplained} signals neither traded nor explained"
        print(f"PASS  {setup}: engine gave {engine_days} Buy plans on {signal_days} signal days; "
              f"{len(trades)} were traded, the rest were already-in-a-position or not filled")

        # 3. a sample of no-signal days on cut-off history: no Buy plan
        rng = np.random.default_rng(0)
        for symbol in rng.choice(sorted(full), 15, replace=False):
            df, a = prices[symbol], full[symbol]
            fired = bt.SETUPS[setup]["signal"](a, **sig)
            idx = np.flatnonzero((a["date"] >= np.datetime64(bt.TEST_START)) & ~fired)
            for t in rng.choice(idx, min(5, len(idx)), replace=False):
                cut = bt.prepare(df.iloc[:t + 1])
                assert not any(p["setup"] == setup for p in evaluate(symbol, cut, passed=passed)["plans"])
        print(f"PASS  {setup}: no Buy plan on sampled no-signal days (cut-off history)")

    print("\nStep 4 done-when check passed.")


if __name__ == "__main__":
    main()
