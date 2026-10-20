"""
Step 7: the ledger, an honest track record.

Every Buy plan alert is locked the moment it is made and graded automatically
from daily candles with the backtester's own fill and exit rules:
 - entry, stop, target and time are recorded at alert time and never edited
 - a limit buy that never fills is "not filled" (no result), not a win
 - if one candle touches both the stop and the target, the stop counts
 - full round-trip costs are deducted; results in PKR per PKR 100,000 trade

Two lists, kept apart:
    data/ledger.json     tested setups' alerts: the public track record
    data/research.json   "if you had bought here" for Running / Fading statuses
"""
import json
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
from kmi import DATA_DIR

LEDGER_FILE = DATA_DIR / "ledger.json"
RESEARCH_FILE = DATA_DIR / "research.json"
RESEARCH_HOLD_DAYS = 5
RESEARCH_COOLDOWN_DAYS = 5


def load(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else []


def save(path, rows):
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1, default=str))
    tmp.replace(path)


def _bar(a, date):
    idx = np.flatnonzero(a["date"] == np.datetime64(pd.Timestamp(date)))
    return int(idx[0]) if len(idx) else None


def is_active(entry):
    return entry["result"]["state"] in ("waiting", "open")


def add_alerts(ledger, signals, kmi_date, now):
    """Lock a new alert for every Buy plan whose stock+setup has no active
    entry (the backtester's one-position rule). Returns the new entries."""
    # like the backtester: a trade that exits today still blocks today's signal
    active = {(e["symbol"], e["setup"]) for e in ledger
              if is_active(e) or e["result"].get("exit_date", "") >= signals["date"]}
    alerted_today = {e["symbol"] for e in ledger if e["signal_date"] == signals["date"]}
    new = []
    for s in signals["stocks"]:
        for plan in s["plans"]:
            key = (s["symbol"], plan["setup"])
            if key in active or s["symbol"] in alerted_today or s["date"] != signals["date"]:
                continue
            entry = {
                "id": f"{s['date']}-{s['symbol']}-{plan['setup']}",
                "created_at": now.isoformat(timespec="seconds"),
                "signal_date": s["date"],
                "symbol": s["symbol"],
                "setup": plan["setup"],
                "setup_label": plan["setup_label"],
                "close_at_signal": s["close"],
                "order": plan["order"],
                "order_type": plan["order_type"],
                "order_price": plan["order_price"],
                "stop_pct": plan["stop_pct"],
                "target_pct": plan["target_pct"],
                "max_hold_days": plan["max_hold_days"],
                "kmi_list_date": kmi_date,
                "result": {"state": "waiting"},
            }
            ledger.append(entry)
            new.append(entry)
            active.add(key)
            alerted_today.add(s["symbol"])
    return new


def grade(entry, a):
    """Grade one ledger entry with the backtester's rules. Never changes the
    locked plan, only entry['result']."""
    t = _bar(a, entry["signal_date"])
    if t is None:
        return {"state": "waiting", "note": "signal day not in price data"}
    if t + 1 >= len(a["c"]):
        return {"state": "waiting", "note": "order is for the next session"}
    kind = entry["order_type"]
    fill = bt.entry_fill(a, t, kind)
    if fill is None:
        return {"state": "not_filled", "session": str(pd.Timestamp(a["date"][t + 1]).date())}
    e = t + 1
    x, price, reason = bt.exit_trade(a, e, fill, target=entry["target_pct"],
                                     stop=entry["stop_pct"], hold=entry["max_hold_days"],
                                     fade=entry["setup"] == "momentum")
    trade = pd.DataFrame({"entry": [fill], "exit": [price]})
    out = {
        "fill_date": str(pd.Timestamp(a["date"][e]).date()),
        "fill_price": round(float(fill), 2),
        "stop_price": round(float(fill) * (1 - entry["stop_pct"]), 2),
        "target_price": round(float(fill) * (1 + entry["target_pct"]), 2) if entry["target_pct"] else None,
        "days_held": int(x - e),
    }
    if reason == "open_at_end":
        out.update(state="open", last_price=round(float(price), 2),
                   pkr_if_sold_now=round(float(bt.pkr(trade)[0])))
    else:
        out.update(state="closed", exit_date=str(pd.Timestamp(a["date"][x]).date()),
                   exit_price=round(float(price), 2), exit_reason=reason,
                   pkr=round(float(bt.pkr(trade)[0])))
    return out


def grade_all(rows, stocks):
    for entry in rows:
        if entry["result"]["state"] in ("closed", "not_filled"):
            continue                                   # final results never change
        a = stocks.get(entry["symbol"])
        if a is not None:
            entry["result"] = grade(entry, a)


def stats(rows):
    closed = [e for e in rows if e["result"]["state"] == "closed"]
    filled = [e for e in rows if e["result"]["state"] in ("open", "closed")]
    not_filled = [e for e in rows if e["result"]["state"] == "not_filled"]
    pkr = [e["result"]["pkr"] for e in closed]
    return {
        "total_alerts": len(rows),
        "waiting": sum(e["result"]["state"] == "waiting" for e in rows),
        "filled": len(filled),
        "not_filled": len(not_filled),
        "open": len(filled) - len(closed),
        "closed": len(closed),
        "win_rate": round(sum(p > 0 for p in pkr) / len(pkr), 3) if pkr else None,
        "avg_pkr_per_trade": round(sum(pkr) / len(pkr)) if pkr else None,
        "total_pkr": round(sum(pkr)) if pkr else 0,
    }


# ---- research list: untested statuses, admin only ---------------------------
def add_research(research, signals, now):
    recent = {}
    for r in research:
        recent[(r["symbol"], r["status"])] = max(recent.get((r["symbol"], r["status"]), ""), r["signal_date"])
    cutoff = str((pd.Timestamp(signals["date"]) - pd.Timedelta(days=RESEARCH_COOLDOWN_DAYS)).date())
    for s in signals["stocks"]:
        if s["status"] not in ("running", "fading") or s["date"] != signals["date"]:
            continue
        if recent.get((s["symbol"], s["status"]), "") > cutoff:
            continue
        research.append({
            "id": f"{s['date']}-{s['symbol']}-{s['status']}",
            "created_at": now.isoformat(timespec="seconds"),
            "signal_date": s["date"], "symbol": s["symbol"], "status": s["status"],
            "setup": s["status"], "order_type": "open", "stop_pct": 0.99, "target_pct": None,
            "max_hold_days": RESEARCH_HOLD_DAYS, "result": {"state": "waiting"},
        })


if __name__ == "__main__":
    for name, path in (("Track record", LEDGER_FILE), ("Research", RESEARCH_FILE)):
        print(name, json.dumps(stats(load(path)), indent=1))
