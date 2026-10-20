"""
Step 3: honest backtester for PSX daily data (KMI stocks only).

    python backtest.py

The 8 honest-test rules from the manual, and how this file follows them:
 1. Split time    settings are chosen on 2019-2023 only, then judged on 2024+
 2. Full costs    every trade pays PSX's real cost stack (below); also re-run at 2x
 3. Rupees        PKR profit per trade on a fixed PKR 100,000 per trade
 4. No peeking    signals use data up to today's close; fills happen the next day
 5. Circuit days  no buy fill on an upper-lock day, no sell fill on a lower-lock day
 6. Luck check    result re-checked without the best 1% of trades
 7. Neighbours    every one-step change to the chosen settings is tested too
 8. Survivorship  noted in the report: the data has no delisted stocks

Costs, per side (AKD Trade's published tiers; confirm against your contract):
    brokerage   price under Rs 5: Rs 0.03 per share
                Rs 5 and up: 0.15% of value, or Rs 0.05 per share if that is more
    sales tax   15% of the brokerage
    exchange    ~0.009% of value (PSX, SECP, NCCPL and CDC fees together)
and capital gains tax: 15% of each tax year's (July-June) net gain.
The 2x-cost run covers a broker or account that charges more.

Extra data rules (the PSX prices from psxdata are NOT adjusted for bonus
shares or splits):
 - A daily move bigger than the circuit limit (10% or Rs 1, whichever is
   bigger) is impossible in real trading, so it marks a corporate action or a
   data gap. Such a jump, or a gap of more than 10 calendar days, is a break:
   no signal looks back across it and no trade is held across it.
 - Bars with a missing open/high/low (PSX left them 0) never give a fill.
 - Days with zero volume never give a fill.

Outputs in results/:
    summary.csv               one row per setup: the report the manual asks for
    grid_<setup>.csv          every setting tried, train-years result
    trades_<setup>.csv        every trade of the chosen setting and its neighbours
    passed_setups.json        the setups (and settings) the engine may use
"""
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from kmi import load_all_prices

# ---- costs ------------------------------------------------------------------
BROKERAGE_RATE = 0.0015       # AKD Trade: 0.15% of value ...
BROKERAGE_PER_SHARE = 0.05    # ... or Rs 0.05 per share, whichever is more
LOW_PRICE = 5.0               # under Rs 5 a share:
LOW_PRICE_PER_SHARE = 0.03    # Rs 0.03 per share
SALES_TAX_ON_BROKERAGE = 0.15
EXCHANGE_FEES = 0.00009       # PSX + SECP + NCCPL + CDC, share of value
CGT_RATE = 0.15               # on each tax year's net gain (filer rate)

TRADE_PKR = 100_000
TRAIN_START = pd.Timestamp("2019-01-01")
TRAIN_END = pd.Timestamp("2023-12-31")
TEST_START = pd.Timestamp("2024-01-01")
MIN_TRADED_VALUE = 2_000_000  # 20-day median daily traded value, PKR
WARMUP_BARS = 60              # bars needed after a break before any signal
MIN_TRAIN_TRADES = 30
NEIGHBOUR_PASS_SHARE = 0.75   # share of neighbours that must be positive in test

RESULTS_DIR = Path(__file__).parent / "results"
PASSED_FILE = RESULTS_DIR / "passed_setups.json"


def side_cost_rate(price, cost_mult=1.0):
    """Cost of one side (buy or sell) as a share of trade value."""
    price = np.asarray(price, dtype=float)
    brokerage = np.where(price < LOW_PRICE, LOW_PRICE_PER_SHARE / price,
                         np.maximum(BROKERAGE_RATE, BROKERAGE_PER_SHARE / price))
    return cost_mult * (brokerage * (1 + SALES_TAX_ON_BROKERAGE) + EXCHANGE_FEES)


# ---------------------------------------------------------------------------
# data preparation
# ---------------------------------------------------------------------------
def prepare(df):
    """Arrays and indicators for one stock. Every indicator at bar t uses
    only bars up to t (rule 4)."""
    d = df.sort_values("date").reset_index(drop=True)
    o, h, l, c, v = (d[k].astype(float).copy() for k in ("open", "high", "low", "close", "volume"))
    bad = (o <= 0) | (h <= 0) | (l <= 0)
    o[bad] = np.nan
    h[bad] = np.nan
    l[bad] = np.nan

    prev = c.shift(1)
    limit = np.maximum(0.10, 1.0 / prev) + 0.01
    gap_days = d["date"].diff().dt.days
    brk = ((c / prev - 1).abs() > limit) | (gap_days > 10)
    brk.iloc[0] = True
    seg = brk.cumsum()
    bars_in_seg = d.groupby(seg).cumcount()

    avgvol20 = v.rolling(20).mean().shift(1)
    tv20 = (c * v).rolling(20).median()
    sma200 = c.rolling(200).mean().where(bars_in_seg >= 200)
    return {
        "date": d["date"].to_numpy(),
        "o": o.to_numpy(), "h": h.to_numpy(), "l": l.to_numpy(),
        "c": c.to_numpy(), "v": v.to_numpy(),
        "seg": seg.to_numpy(),
        "bars_in_seg": bars_in_seg.to_numpy(),
        "locked_up": ((h == l) & (c > prev)).to_numpy(),
        "locked_down": ((h == l) & (c < prev)).to_numpy(),
        "tv20": tv20.to_numpy(),
        "ok": ((bars_in_seg >= WARMUP_BARS) & (tv20 >= MIN_TRADED_VALUE) & ~bad).to_numpy(),
        "sma5": c.rolling(5).mean().to_numpy(),
        "sma20": c.rolling(20).mean().to_numpy(),
        "sma50": c.rolling(50).mean().to_numpy(),
        "sma200": sma200.to_numpy(),
        "volratio": (v / avgvol20).to_numpy(),
        "spike15": (v / avgvol20).rolling(15, min_periods=10).max().to_numpy(),
        "hi15": h.rolling(15, min_periods=10).max().to_numpy(),
        "lo45": l.rolling(45, min_periods=30).min().to_numpy(),
        "hh20_prev": h.rolling(20, min_periods=15).max().shift(1).to_numpy(),
        "hh60": c.rolling(60).max().to_numpy(),
        "down3": ((c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))).to_numpy(),
        "ret1": (c / prev - 1).to_numpy(),
        "ret5": (c / c.shift(5) - 1).to_numpy(),
    }


# ---------------------------------------------------------------------------
# setups: signal at today's close, order for tomorrow
# ---------------------------------------------------------------------------
def washout_signal(a, drop):
    """Hot stock (3x volume spike, 30%+ run-up) that fell `drop` from its
    recent high and closes well under its 20-day average."""
    with np.errstate(invalid="ignore"):
        return (a["ok"] & (a["spike15"] >= 3) & (a["hi15"] >= 1.3 * a["lo45"])
                & (a["c"] <= a["hi15"] * (1 - drop)) & (a["c"] < 0.92 * a["sma20"]))


def pullback_signal(a, light_vol):
    with np.errstate(invalid="ignore"):
        return (a["ok"] & (a["c"] > a["sma50"]) & (a["l"] <= a["sma20"] * 1.01)
                & (a["c"] >= a["sma20"] * 0.98) & (a["volratio"] < light_vol))


def breakout_signal(a, vol_mult):
    with np.errstate(invalid="ignore"):
        return a["ok"] & (a["c"] > a["hh20_prev"]) & (a["volratio"] >= vol_mult)


def momentum_signal(a, min_rise):
    with np.errstate(invalid="ignore"):
        return a["ok"] & (a["ret1"] >= min_rise)


def dip_uptrend_signal(a, depth):
    """Long-term uptrend (above the 200-day average), three down days in a row,
    closing `depth` under the 5-day average."""
    with np.errstate(invalid="ignore"):
        return (a["ok"] & (a["c"] > a["sma200"]) & a["down3"]
                & (a["c"] < a["sma5"] * (1 - depth)))


def trend_hold_signal(a, min_gap):
    """New 60-day closing high while the 50-day average is `min_gap` above
    the 200-day average. Held for weeks, so costs matter less."""
    with np.errstate(invalid="ignore"):
        return (a["ok"] & (a["c"] >= a["hh60"]) & (a["sma50"] > a["sma200"] * (1 + min_gap)))


SHORT_EXITS = {"target": [0.02, 0.03, 0.05, 0.08, 0.10],
               "stop": [0.06, 0.08, 0.10, 0.12], "hold": [3, 5, 10, 15]}

SETUPS = {
    "washout": {
        "label": "Washout dip in a hot stock",
        "signal": washout_signal, "entry": "limit",   # limit 1% under the close
        "signal_grid": {"drop": [0.14, 0.16, 0.18, 0.20, 0.22, 0.25, 0.30, 0.35]},
        "exit_grid": SHORT_EXITS,
    },
    "pullback": {
        "label": "Pullback in an uptrend",
        "signal": pullback_signal, "entry": "stop",   # buy the break of today's high
        "signal_grid": {"light_vol": [0.6, 0.8, 1.0]},
        "exit_grid": SHORT_EXITS,
    },
    "breakout": {
        "label": "Breakout on volume",
        "signal": breakout_signal, "entry": "open",
        "signal_grid": {"vol_mult": [1.5, 2.0, 2.5, 3.0]},
        "exit_grid": SHORT_EXITS,
    },
    "momentum": {
        "label": "Momentum chase",
        "signal": momentum_signal, "entry": "open",   # sell when close < 5-day avg
        "signal_grid": {"min_rise": [0.04, 0.06, 0.08, 0.095]},
        "exit_grid": {"target": [None], "stop": [0.04, 0.06, 0.08], "hold": [5, 10, 20]},
    },
    "dip_uptrend": {
        "label": "Dip in a long-term uptrend",
        "signal": dip_uptrend_signal, "entry": "limit",
        "signal_grid": {"depth": [0.01, 0.02, 0.03, 0.05]},
        "exit_grid": SHORT_EXITS,
    },
    "trend_hold": {
        "label": "Trend hold (weeks)",
        "signal": trend_hold_signal, "entry": "open",
        "signal_grid": {"min_gap": [0.0, 0.05, 0.10]},
        "exit_grid": {"target": [0.15, 0.25, None], "stop": [0.10, 0.15, 0.20],
                      "hold": [20, 40, 60]},
    },
}


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------
def entry_order(a, t, kind):
    """The order placed after bar t's close: (kind, price). 'open' has no price."""
    if kind == "limit":
        return kind, a["c"][t] * 0.99
    if kind == "stop":
        return kind, a["h"][t] * 1.001
    return kind, None


def entry_fill(a, t, kind):
    """Fill price on bar t+1 for an order placed at bar t's close, or None."""
    e = t + 1
    if e >= len(a["c"]) or a["seg"][e] != a["seg"][t] or a["v"][e] <= 0:
        return None
    o, h, l = a["o"][e], a["h"][e], a["l"][e]
    if np.isnan(l):                                   # missing range: no fill
        return None
    kind, price = entry_order(a, t, kind)
    if kind == "limit":
        return min(o, price) if l <= price else None
    if a["locked_up"][e]:                             # rule 5: no buy at upper lock
        return None
    if kind == "stop":
        return max(o, price) if h >= price else None
    return o                                          # "open"


def exit_trade(a, e, entry, target, stop, hold, fade):
    """Walk forward from entry bar e. Returns (exit_bar, price, reason).
    If one bar touches both stop and target, the stop counts."""
    stop_px = entry * (1 - stop)
    target_px = entry * (1 + target) if target else math.inf
    n = len(a["c"])
    # entry day: only the stop can hit (we can't know if target came after fill)
    if a["l"][e] <= stop_px and not a["locked_down"][e]:
        return e, stop_px, "stop"
    for k in range(e + 1, n):
        if a["seg"][k] != a["seg"][e]:
            return k - 1, a["c"][k - 1], "data_break"
        if a["v"][k] <= 0 or a["locked_down"][k]:     # rule 5: no sell at lower lock
            continue
        o, h, l, c = a["o"][k], a["h"][k], a["l"][k], a["c"][k]
        if not np.isnan(l):
            if o <= stop_px:
                return k, o, "stop"
            if l <= stop_px:
                return k, stop_px, "stop"
            if o >= target_px:
                return k, o, "target"
            if h >= target_px:
                return k, target_px, "target"
        if fade and c < a["sma5"][k]:
            return k, c, "fade"
        if k - e >= hold:
            return k, c, "time"
    return n - 1, a["c"][n - 1], "open_at_end"


def split_params(setup, params):
    spec = SETUPS[setup]
    return ({k: params[k] for k in spec["signal_grid"]},
            {k: params[k] for k in spec["exit_grid"]})


def simulate(stocks, setup, sig_params, exit_params):
    spec = SETUPS[setup]
    fade = setup == "momentum"
    rows = []
    for symbol, a in stocks.items():
        signal = spec["signal"](a, **sig_params)
        free_from = 0                                 # one position per stock
        for t in np.flatnonzero(signal):
            if t < free_from:
                continue
            entry = entry_fill(a, t, spec["entry"])
            if entry is None:
                continue
            e = t + 1
            x, price, reason = exit_trade(a, e, entry, fade=fade, **exit_params)
            rows.append((symbol, a["date"][t], a["date"][e], entry,
                         a["date"][x], price, reason, x - e))
            free_from = x + 1
    trades = pd.DataFrame(rows, columns=["symbol", "signal_date", "entry_date", "entry",
                                         "exit_date", "exit", "exit_reason", "bars_held"])
    trades["period"] = np.where(trades["entry_date"] <= TRAIN_END, "train", "test")
    return trades


# ---------------------------------------------------------------------------
# measuring
# ---------------------------------------------------------------------------
def pkr(trades, cost_mult=1.0):
    """Net PKR per trade on TRADE_PKR after trading costs, before CGT."""
    sell_value = TRADE_PKR * trades["exit"].to_numpy() / trades["entry"].to_numpy()
    buy_cost = TRADE_PKR * side_cost_rate(trades["entry"], cost_mult)
    sell_cost = sell_value * side_cost_rate(trades["exit"], cost_mult)
    return sell_value - TRADE_PKR - buy_cost - sell_cost


def after_tax_total(trades, p):
    """Total PKR after CGT on each tax year's (July-June) net gain."""
    exit_dates = pd.to_datetime(trades["exit_date"])
    tax_year = exit_dates.dt.year + (exit_dates.dt.month >= 7)
    yearly = pd.Series(p, index=trades.index).groupby(tax_year.values).sum()
    return p.sum() - CGT_RATE * yearly.clip(lower=0).sum()


def report(trades, months, cost_mult=1.0):
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    t = trades.sort_values("exit_date")
    p = pkr(t, cost_mult)
    losing = streak = 0
    for x in p:
        streak = streak + 1 if x <= 0 else 0
        losing = max(losing, streak)
    equity = np.cumsum(p)
    drawdown = (np.maximum.accumulate(np.maximum(equity, 0)) - equity).max()
    cut = math.ceil(n * 0.01)
    keep = np.argsort(p)[:-cut] if n > cut else None
    return {
        "trades": n,
        "pkr_per_trade": round(after_tax_total(t, p) / n),
        "win_rate": round((p > 0).mean(), 3),
        "trades_per_month": round(n / months, 1),
        "worst_losing_streak": losing,
        "biggest_drop_pkr": round(drawdown),
        "pkr_per_trade_without_best_1pct":
            round(after_tax_total(t.iloc[keep], p[keep]) / len(keep)) if keep is not None else None,
        "total_pkr": round(after_tax_total(t, p)),
    }


def months_between(start, end):
    return max((end - start).days / 30.44, 1)


def neighbours(choice, grid):
    """Settings one step away from `choice` in exactly one parameter."""
    out = []
    for key, values in grid.items():
        i = values.index(choice[key])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                out.append({**choice, key: values[j]})
    return out


def run_setup(name, stocks, last_date):
    spec = SETUPS[name]
    grid = {**spec["signal_grid"], **spec["exit_grid"]}
    keys = list(grid)
    train_months = months_between(TRAIN_START, TRAIN_END)
    test_months = months_between(TEST_START, last_date)

    # rule 1: choose on train years only
    combos = list(itertools.product(*grid.values()))
    results, cache = [], {}
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        trades = simulate(stocks, name, *split_params(name, params))
        cache[combo] = trades
        train = trades[trades["period"] == "train"]
        r = report(train, train_months)
        results.append({"combo": i, **params, **{f"train_{k}": v for k, v in r.items()}})
    table = pd.DataFrame(results)
    table.to_csv(RESULTS_DIR / f"grid_{name}.csv", index=False)

    eligible = table[table["train_trades"] >= MIN_TRAIN_TRADES]
    if eligible.empty:
        return {"setup": name, "verdict": f"FAIL: no setting had {MIN_TRAIN_TRADES}+ train trades"}, None
    best = eligible.sort_values("train_pkr_per_trade", ascending=False).iloc[0]
    choice = dict(zip(keys, combos[int(best["combo"])]))

    # rule 7: neighbours, judged on test years
    chosen = cache[tuple(choice[k] for k in keys)].assign(role="chosen")
    saved, neigh_results = [chosen], []
    for nb in neighbours(choice, grid):
        tr = cache[tuple(nb[k] for k in keys)]
        test = tr[tr["period"] == "test"]
        r = report(test, test_months)
        neigh_results.append((nb, r.get("trades", 0), r.get("pkr_per_trade", float("nan"))))
        saved.append(tr.assign(role="neighbour " + ", ".join(f"{k}={nb[k]}" for k in keys)))
    trades_out = pd.concat(saved)
    trades_out["pkr_full_cost"] = pkr(trades_out).round(0)
    trades_out["pkr_double_cost"] = pkr(trades_out, 2).round(0)
    trades_out.to_csv(RESULTS_DIR / f"trades_{name}.csv", index=False)

    train = chosen[chosen["period"] == "train"]
    test = chosen[chosen["period"] == "test"]
    tr_full, te_full = report(train, train_months), report(test, test_months)
    te_double = report(test, test_months, 2)
    positive_nb = sum(1 for _, n, p in neigh_results if n > 0 and p > 0)
    nb_share = positive_nb / len(neigh_results) if neigh_results else 0

    checks = {
        "test positive at full cost": te_full.get("pkr_per_trade", 0) > 0,
        "test positive at double cost": (te_double.get("pkr_per_trade") or 0) > 0,
        "survives luck check (test, no best 1%)":
            (te_full.get("pkr_per_trade_without_best_1pct") or 0) > 0,
        f"neighbours positive in test >= {NEIGHBOUR_PASS_SHARE:.0%}": nb_share >= NEIGHBOUR_PASS_SHARE,
    }
    failed = [k for k, ok in checks.items() if not ok]
    row = {
        "setup": name,
        "settings": ", ".join(f"{k}={choice[k]}" for k in keys),
        **{f"train_{k}": v for k, v in tr_full.items()},
        **{f"test_{k}": v for k, v in te_full.items()},
        "test_pkr_per_trade_double_cost": te_double.get("pkr_per_trade"),
        "neighbours_positive_in_test": f"{positive_nb}/{len(neigh_results)}",
        "verdict": "PASS" if not failed else "FAIL: " + "; ".join(failed),
    }
    years = pd.to_datetime(chosen["entry_date"]).dt.year
    by_year = {int(y): report(chosen[years == y], 12) for y in sorted(years.unique())}
    by_year = {y: {"trades": r["trades"], "pkr_per_trade": r["pkr_per_trade"],
                   "win_rate": r["win_rate"]} for y, r in by_year.items()}
    return row, {"params": choice, "test": te_full, "train": tr_full, "by_year": by_year,
                 "test_double_cost_pkr_per_trade": te_double.get("pkr_per_trade"),
                 "neighbours_positive_in_test": row["neighbours_positive_in_test"]}


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    prices, _ = load_all_prices()
    stocks = {s: prepare(df) for s, df in prices.items()}
    last_date = max(pd.Timestamp(a["date"][-1]) for a in stocks.values())
    print(f"{len(stocks)} KMI stocks, data to {last_date.date()}. Costs: AKD Trade "
          f"brokerage + tax + fees (about {2 * side_cost_rate(100.0):.2%} round trip at Rs 100, "
          f"{2 * side_cost_rate(10.0):.2%} at Rs 10), {CGT_RATE:.0%} CGT on yearly net gains, "
          f"PKR {TRADE_PKR:,} per trade.\n")

    rows, passed = [], {}
    for name in SETUPS:
        print(f"Testing {name}...")
        row, info = run_setup(name, stocks, last_date)
        rows.append(row)
        print(f"  chosen on 2019-23: {row.get('settings', '-')}")
        if info is not None:
            print(f"  train: {row['train_trades']} trades, PKR {row['train_pkr_per_trade']:,}/trade, "
                  f"win {row['train_win_rate']:.0%}")
            print(f"  test:  {row.get('test_trades', 0)} trades, "
                  f"PKR {row.get('test_pkr_per_trade', 0):,}/trade, "
                  f"win {row.get('test_win_rate', 0):.0%}, "
                  f"{row.get('test_trades_per_month', 0)}/month, "
                  f"without best 1%: PKR {row.get('test_pkr_per_trade_without_best_1pct')}, "
                  f"at 2x cost: PKR {row['test_pkr_per_trade_double_cost']}")
            print(f"  neighbours positive in test: {row['neighbours_positive_in_test']}")
        print(f"  -> {row['verdict']}\n")
        if row["verdict"] == "PASS":
            passed[name] = {"label": SETUPS[name]["label"], **info}

    pd.DataFrame(rows).to_csv(RESULTS_DIR / "summary.csv", index=False)
    PASSED_FILE.write_text(json.dumps({
        "tested_on": str(last_date.date()), "setups": passed}, indent=2, default=str))
    print("Survivorship: the data has no delisted stocks, and the KMI list is "
          "today's list applied to past years. Both flatter the results.")
    print("\nPASSED:", ", ".join(passed) if passed else
          "nothing. Per the manual: don't build alerts yet; test more ideas.")


if __name__ == "__main__":
    main()
