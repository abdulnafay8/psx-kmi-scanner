"""
Step 4: the setup engine. Gives each KMI stock one status, a few lights,
the reasons with numbers, and a trade plan ONLY for setups that passed the
backtest (results/passed_setups.json).

    from engine import load_passed, evaluate

The buy rules are not re-written here: they are the backtester's own signal
functions with the tested settings, so the engine cannot drift from the test.
"""
import json
import math

import numpy as np
import pandas as pd

import backtest as bt

DEFAULT_RISK_PKR = 2_000      # PKR you accept losing if the stop is hit

STATUS_TEXT = {
    "buy_plan": "Buy plan",
    "running": "Running: strong move up. Don't chase.",
    "fading": "Fading: lost its trend. Avoid or exit.",
    "no_setup": "No setup: nothing to do.",
}


def load_passed(path=bt.PASSED_FILE):
    """{setup: {"label", "params", "test", "train"}} for setups that passed."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())["setups"]


def _pct(x):
    return f"{x * 100:.1f}%"


def _pkr(x):
    return f"PKR {x:,.0f}"


def _num(x, digits=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), digits)


# ---- reasons with numbers, one function per setup --------------------------
def washout_reasons(a, t, drop):
    off_high = 1 - a["c"][t] / a["hi15"][t]
    return [
        (f"Down {_pct(off_high)} from its 15-day high", f"needs {_pct(drop)}+",
         off_high >= drop),
        (f"Biggest volume day in 15 days was {a['spike15'][t]:.1f}x normal", "needs 3x+",
         a["spike15"][t] >= 3),
        (f"Ran up {_pct(a['hi15'][t] / a['lo45'][t] - 1)} before the drop", "needs 30%+",
         a["hi15"][t] >= 1.3 * a["lo45"][t]),
        (f"Close is {_pct(1 - a['c'][t] / a['sma20'][t])} under its 20-day average",
         "needs 8%+", a["c"][t] < 0.92 * a["sma20"][t]),
    ]


def trend_hold_reasons(a, t, min_gap):
    gap = a["sma50"][t] / a["sma200"][t] - 1
    return [
        (f"Close {a['c'][t]:,.2f} vs 60-day closing high {a['hh60'][t]:,.2f}",
         "needs a new 60-day high", a["c"][t] >= a["hh60"][t]),
        (f"50-day average is {_pct(gap)} above the 200-day average",
         f"needs {_pct(min_gap)}+", gap > min_gap),
    ]


REASONS = {"washout": washout_reasons, "trend_hold": trend_hold_reasons}


def _common_reasons(a, t):
    return [
        (f"Traded {_pkr(a['tv20'][t])} a day (20-day median)",
         f"needs {_pkr(bt.MIN_TRADED_VALUE)}+", a["tv20"][t] >= bt.MIN_TRADED_VALUE),
        (f"{a['bars_in_seg'][t]} clean trading days since the last split/bonus or gap",
         f"needs {bt.WARMUP_BARS}+", a["bars_in_seg"][t] >= bt.WARMUP_BARS),
    ]


def trade_plan(setup, info, a, t, risk_pkr):
    spec = bt.SETUPS[setup]
    p = info["params"]
    kind, price = bt.entry_order(a, t, spec["entry"])
    ref = price if price is not None else a["c"][t]
    stop, target, hold = p["stop"], p.get("target"), p["hold"]
    position = risk_pkr / stop
    order = {"limit": f"Limit buy at Rs {ref:,.2f}",
             "stop": f"Buy if it trades at Rs {ref:,.2f} or higher",
             "open": "Buy at the opening price"}[kind]
    test = info["test"]
    return {
        "setup": setup,
        "setup_label": info["label"],
        "order": order,
        "order_type": kind,
        "order_price": _num(price),
        "valid": "next trading session only; cancel if not filled",
        "skip_if": "the stock opens locked at its upper limit" if kind != "limit" else None,
        "stop_pct": stop,
        "target_pct": target,
        "max_hold_days": hold,
        "stop_price_est": _num(ref * (1 - stop)),
        "target_price_est": _num(ref * (1 + target)) if target else None,
        "risk_pkr": risk_pkr,
        "position_pkr": round(position),
        "shares_est": int(position // ref),
        "how_to": [
            f"{order}. Valid for the next trading session only.",
            f"If filled, put a stop {_pct(stop)} below your fill price"
            + (f" and a target {_pct(target)} above it." if target else
               " (no fixed target)."),
            f"If neither is hit, sell at the close after {hold} trading days.",
        ],
        "backtest": {"test_pkr_per_trade": test.get("pkr_per_trade"),
                     "test_win_rate": test.get("win_rate"),
                     "test_trades": test.get("trades"),
                     "test_trades_per_month": test.get("trades_per_month")},
    }


def lights(a, t, kse=None):
    out = []
    vs20 = a["c"][t] / a["sma20"][t] - 1
    out.append({"name": "Above 20-day average", "on": bool(vs20 > 0),
                "text": f"{_pct(vs20)} vs 20-day average"})
    vr = a["volratio"][t]
    out.append({"name": "Volume vs normal", "on": bool(vr >= 1),
                "text": f"{vr:.1f}x normal" if not np.isnan(vr) else "no data"})
    if kse is not None and len(kse):
        k = kse[kse["date"] <= pd.Timestamp(a["date"][t])].tail(50)
        if len(k) == 50:
            up = k["close"].iloc[-1] > k["close"].mean()
            out.append({"name": "KSE-100 trend", "on": bool(up),
                        "text": f"KSE-100 {'above' if up else 'below'} its 50-day average"})
    room = a["hh60"][t] / a["c"][t] - 1
    out.append({"name": "Room to 60-day high", "on": bool(room >= 0.05),
                "text": "at the 60-day high" if room <= 0 else
                f"the 60-day high is {_pct(room)} above today's price"})
    return out


def evaluate(symbol, a, t=-1, passed=None, kse=None, risk_pkr=DEFAULT_RISK_PKR):
    """Status, lights, reasons and (for tested setups only) a trade plan for
    `symbol` as of bar t, using only data up to bar t."""
    t = t % len(a["c"])
    passed = load_passed() if passed is None else passed
    plans, reasons = [], {}
    for setup, info in passed.items():
        sig_params, _ = bt.split_params(setup, info["params"])
        spec = bt.SETUPS[setup]
        fired = bool(spec["signal"](a, **sig_params)[t])
        fn = REASONS.get(setup)
        checks = (fn(a, t, **sig_params) if fn else []) + _common_reasons(a, t)
        reasons[setup] = [{"check": c, "rule": r, "ok": bool(ok)} for c, r, ok in checks]
        if fired:
            plans.append(trade_plan(setup, info, a, t, risk_pkr))

    clean = a["bars_in_seg"][t]          # days since the last split/bonus or gap
    with np.errstate(invalid="ignore"):
        running = clean >= 20 and a["ret5"][t] >= 0.10 and a["c"][t] > a["sma20"][t]
        fading = (clean >= 25 and a["c"][t] < a["sma20"][t]
                  and a["sma20"][t] < a["sma20"][t - 5])
    status = "buy_plan" if plans else "running" if running else "fading" if fading else "no_setup"
    context = []
    if not np.isnan(a["ret5"][t]):
        context.append(f"{_pct(a['ret5'][t])} over the last 5 days")
    lo60 = np.nanmin(a["c"][max(0, t - 59):t + 1])
    return {
        "symbol": symbol,
        "date": str(pd.Timestamp(a["date"][t]).date()),
        "close": _num(a["c"][t]),
        "change_pct": _num(a["ret1"][t], 4),
        "status": status,
        "status_text": STATUS_TEXT[status],
        "tested": status == "buy_plan",
        "context": context,
        "plans": plans,
        "reasons": reasons,
        "lights": lights(a, t, kse),
        # averages only once they span clean days (unadjusted prices across a
        # split or bonus issue would mix two price scales)
        "levels": {"sma20": _num(a["sma20"][t]) if clean >= 20 else None,
                   "sma50": _num(a["sma50"][t]) if clean >= 50 else None,
                   "sma200": _num(a["sma200"][t]), "high60": _num(a["hh60"][t]) if clean >= 60 else None,
                   "low60": _num(lo60) if clean >= 60 else None},
        "traded_value_pkr": _num(a["tv20"][t], 0),
        "news_url": f"https://dps.psx.com.pk/company/{symbol}",
    }
