"""
Step 5: the daily runner. Run once after each PSX close (the scheduled task
does this at 17:00 PKT, Monday-Friday):

    python run_daily.py              normal run
    python run_daily.py --test-push  send one test alert to your phone and stop
    python run_daily.py --no-fetch   skip downloading, use the saved prices

PSX hours (Pakistan time, UTC+5): Mon-Thu 09:32-15:30; Friday 09:17-12:00 and
14:32-16:30 (prayer break in between); closed weekends and holidays. Every
tested setup works on daily candles and needs the FINAL closing price, so:
 - before the day's close (+ time for PSX to publish), today's candle is
   incomplete: it is never saved and no alerts are made
 - weekends: nothing to fetch, no alerts
 - a weekday with no candle for today (holiday): no alerts
Alerts are plans for the NEXT trading session.

Each run:
 1. updates daily prices for KMI stocks (and the KSE-100) from psxdata
 2. runs the engine on every KMI stock -> app/data/signals.json
 3. locks new Buy plans in the ledger and grades all open alerts
 4. pushes new alerts to your phone (if config.json has an ntfy topic)
 5. writes what it saw and why things were skipped -> app/data/status.json
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import psxdata

import backtest as bt
import ledger as lg
from engine import evaluate, load_passed
from kmi import DATA_DIR, DAILY_DIR, load_kmi_list
from step1_download_data import drop_no_trade_rows

ROOT = Path(__file__).parent
APP_DATA = ROOT / "app" / "data"
CONFIG_FILE = ROOT / "config.json"
INDEX_FILE = DATA_DIR / "index" / "KSE100.csv"
PKT = dt.timezone(dt.timedelta(hours=5))
# a candle counts as final only after this time (close + time for PSX to publish)
FINAL_AFTER = {0: dt.time(16, 15), 1: dt.time(16, 15), 2: dt.time(16, 15),
               3: dt.time(16, 15), 4: dt.time(17, 0)}
DEFAULT_CONFIG = {"risk_pkr": 2000, "ntfy_server": "https://ntfy.sh", "ntfy_topic": None,
                  "app_url": None}


class RunLog:
    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        line = f"{dt.datetime.now(PKT):%H:%M:%S}  {msg}"
        print(line)
        self.lines.append(line)


def load_config():
    """config.json, with NTFY_TOPIC / APP_URL environment variables taking
    priority (online, the topic is a GitHub secret, never in the repository)."""
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    config = {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    for key, env in (("ntfy_topic", "NTFY_TOPIC"), ("app_url", "APP_URL")):
        if os.environ.get(env):
            config[key] = os.environ[env]
    return config


def market_state(now):
    """What today's date means for daily candles."""
    if now.weekday() >= 5:
        return "weekend", None
    final_after = FINAL_AFTER[now.weekday()]
    if now.time() < final_after:
        return "before_final", final_after
    return "after_close", final_after


def next_session_open(day):
    """Opening time of the first weekday after `day` (holidays unknown, so a
    holiday only makes this earlier, never later: the safe side)."""
    d = day + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(9, 15), PKT)


def merge_new_bars(old, new, keep_today):
    new = new.copy()
    new["date"] = pd.to_datetime(new["date"])
    if not keep_today:
        new = new[new["date"].dt.date < dt.datetime.now(PKT).date()]
    merged = pd.concat([old, new]).drop_duplicates("date", keep="last")
    merged = drop_no_trade_rows(merged.sort_values("date").reset_index(drop=True))
    return merged


def update_prices(symbols, keep_today, log):
    ok = failed = 0
    for symbol in symbols:
        path = DAILY_DIR / f"{symbol}.csv"
        old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame()
        start = (old["date"].max() - pd.Timedelta(days=10)).date() if len(old) else bt.TRAIN_START.date()
        try:
            new = psxdata.stocks(symbol, start=start, cache=False)
        except Exception as e:
            log(f"fetch failed {symbol}: {e}")
            failed += 1
            continue
        if new is None or new.empty:
            log(f"no data returned for {symbol}")
            failed += 1
            continue
        merged = merge_new_bars(old, new, keep_today)
        if merged.empty:                              # nothing ever traded: don't save
            continue
        merged.to_csv(path, index=False)
        ok += 1
        time.sleep(0.3)
    try:
        k = psxdata.stocks("KSE100", start="2025-01-01", cache=False)
        k["date"] = pd.to_datetime(k["date"])
        if not keep_today:
            k = k[k["date"].dt.date < dt.datetime.now(PKT).date()]
        old = pd.read_csv(INDEX_FILE, parse_dates=["date"]) if INDEX_FILE.exists() else pd.DataFrame()
        INDEX_FILE.parent.mkdir(exist_ok=True)
        pd.concat([old, k]).drop_duplicates("date", keep="last").sort_values("date") \
            .to_csv(INDEX_FILE, index=False)
    except Exception as e:
        log(f"KSE-100 fetch failed: {e}")
    return ok, failed


def push(config, title, message, log, click=None):
    topic = config.get("ntfy_topic")
    if not topic:
        log("push skipped: no ntfy_topic in config.json")
        return False
    req = urllib.request.Request(f"{config['ntfy_server'].rstrip('/')}/{topic}",
                                 data=message.encode("utf-8"), method="POST")
    req.add_header("Title", title.encode("utf-8").decode("latin-1", "ignore"))
    if click:
        req.add_header("Click", click)
    try:
        urllib.request.urlopen(req, timeout=20).read()
        log(f"push sent: {title}")
        return True
    except Exception as e:
        log(f"push FAILED: {e}")
        return False


def alert_text(entry, plan, kmi_date):
    lines = [f"{entry['setup_label']}",
             f"{plan['order']} - valid next trading session only",
             f"Stop {plan['stop_pct']:.0%} below your fill (about Rs {plan['stop_price_est']:,})"]
    if plan["target_pct"]:
        lines.append(f"Target {plan['target_pct']:.0%} above your fill (about Rs {plan['target_price_est']:,})")
    lines += [f"Sell after {plan['max_hold_days']} trading days if neither is hit",
              f"Size: PKR {plan['position_pkr']:,} (about {plan['shares_est']:,} shares), "
              f"risking PKR {plan['risk_pkr']:,}",
              f"KMI All Share, list of {kmi_date}"]
    return "\n".join(lines)


def build_signals(stocks, kse, passed, kmi, risk_pkr, ledger_rows):
    names = dict(zip(kmi["symbol"], kmi["name"]))
    sectors = dict(zip(kmi["symbol"], kmi["sector"]))
    as_of = max(str(pd.Timestamp(a["date"][-1]).date()) for a in stocks.values())
    active = {(e["symbol"]): e for e in ledger_rows if lg.is_active(e)}
    rows = []
    for symbol, a in stocks.items():
        s = evaluate(symbol, a, passed=passed, kse=kse, risk_pkr=risk_pkr)
        s["name"], s["sector"] = names.get(symbol), sectors.get(symbol)
        s["stale"] = s["date"] != as_of
        if symbol in active:
            s["active_plan"] = active[symbol]
            if s["status"] == "buy_plan" and active[symbol]["signal_date"] != as_of:
                s["status"], s["status_text"] = "in_plan", "Plan active: already alerted, follow that plan"
        rows.append(s)
    return {"date": as_of, "stocks": rows}


def main():
    now = dt.datetime.now(PKT)
    log = RunLog()
    config = load_config()
    APP_DATA.mkdir(parents=True, exist_ok=True)
    kmi = load_kmi_list()
    kmi_date = str(kmi["list_date"].iloc[0])

    if "--test-push" in sys.argv:
        ok = push(config, "PSX Scanner test alert",
                  "If you can read this on your phone, alerts work.\n"
                  f"KMI All Share, list of {kmi_date}", log, click=config.get("app_url"))
        sys.exit(0 if ok else 1)

    state, final_after = market_state(now)
    log(f"run started {now:%A %Y-%m-%d %H:%M} PKT; market state: {state}")
    passed = load_passed()
    if not passed:
        log("no setup has passed the backtest: the app shows context only, no Buy plans")

    symbols = sorted(kmi["symbol"])
    if state == "weekend" or "--no-fetch" in sys.argv:
        log("weekend: PSX is closed, nothing new to fetch" if state == "weekend"
            else "--no-fetch: using saved prices")
        fetched = (0, 0)
    else:
        keep_today = state == "after_close"
        if not keep_today:
            log(f"before {final_after:%H:%M}: today's candle isn't final yet, "
                "so it is not saved and no alerts are made")
        fetched = update_prices(symbols, keep_today, log)
        log(f"prices updated for {fetched[0]} stocks, {fetched[1]} failed")

    prices = {s: pd.read_csv(DAILY_DIR / f"{s}.csv", parse_dates=["date"])
              for s in symbols if (DAILY_DIR / f"{s}.csv").exists()}
    stocks = {s: bt.prepare(df) for s, df in prices.items() if len(df) >= 2}
    kse = pd.read_csv(INDEX_FILE, parse_dates=["date"]) if INDEX_FILE.exists() else None

    ledger_rows, research = lg.load(lg.LEDGER_FILE), lg.load(lg.RESEARCH_FILE)
    lg.grade_all(ledger_rows, stocks)
    lg.grade_all(research, stocks)

    signals = build_signals(stocks, kse, passed, kmi, config["risk_pkr"], ledger_rows)
    # A plan is for the session after the last final candle, so alerts are
    # made only while that session hasn't opened yet.
    as_of = dt.date.fromisoformat(signals["date"])
    opens = next_session_open(as_of)
    reasons_no_alerts = []
    if now >= opens:
        reasons_no_alerts.append(
            f"the last final candle is {as_of} and the next session opened at {opens:%a %d %b %H:%M}; "
            + ("today's candle isn't final until after the close" if state == "before_final"
               else "no candle for today: PSX holiday, or data not published yet"))
    else:
        log(f"plans from the {as_of} close are for the session opening {opens:%a %d %b %H:%M}")
    if fetched[1] > len(symbols) / 2:
        reasons_no_alerts.append(f"{fetched[1]} of {len(symbols)} price fetches failed")

    new = []
    if not reasons_no_alerts:
        new = lg.add_alerts(ledger_rows, signals, kmi_date, now)
        lg.grade_all(ledger_rows, stocks)
        lg.add_research(research, signals, now)
        lg.grade_all(research, stocks)
        signals = build_signals(stocks, kse, passed, kmi, config["risk_pkr"], ledger_rows)
        for e in new:
            log(f"NEW ALERT {e['symbol']} {e['setup']}: {e['order']}")
    else:
        log("no new alerts: " + "; ".join(reasons_no_alerts))

    counts = pd.Series([s["status"] for s in signals["stocks"]]).value_counts().to_dict()
    log(f"checked {len(signals['stocks'])} KMI stocks as of {signals['date']}: {counts}")
    for setup in passed:
        near = [s["symbol"] for s in signals["stocks"]
                if sum(not r["ok"] for r in s["reasons"][setup]) == 1]
        log(f"{setup}: {len(near)} stocks miss exactly one check: {', '.join(near[:12])}")

    by_symbol = {s["symbol"]: s for s in signals["stocks"]}
    for e in new:
        plan = next(p for p in by_symbol[e["symbol"]]["plans"] if p["setup"] == e["setup"])
        click = f"{config['app_url'].rstrip('/')}/#stock/{e['symbol']}" if config.get("app_url") else None
        push(config, f"{e['symbol']}: {e['setup_label']}", alert_text(e, plan, kmi_date), log, click)

    lg.save(lg.LEDGER_FILE, ledger_rows)
    lg.save(lg.RESEARCH_FILE, research)
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": signals["date"],
        "market_state": state,
        "alerts_made": not reasons_no_alerts,
        "plans_for_session": f"{opens:%a %d %b %Y}",
        "no_alert_reason": "; ".join(reasons_no_alerts),
        "kmi_list_date": kmi_date,
        "risk_pkr": config["risk_pkr"],
        "min_traded_value": bt.MIN_TRADED_VALUE,
        "tested_setups": passed,
    }
    (APP_DATA / "signals.json").write_text(json.dumps({**meta, "stocks": signals["stocks"]},
                                                      default=str))
    (APP_DATA / "ledger.json").write_text(json.dumps({
        "stats": lg.stats(ledger_rows),
        "by_setup": {k: lg.stats([e for e in ledger_rows if e["setup"] == k]) for k in passed},
        "alerts": sorted(ledger_rows, key=lambda e: e["created_at"], reverse=True)}, default=str))
    (APP_DATA / "research.json").write_text(json.dumps({
        "by_status": {k: lg.stats([e for e in research if e["status"] == k]) for k in ("running", "fading")},
        "entries": sorted(research, key=lambda e: e["created_at"], reverse=True)[:300]}, default=str))
    history = []
    status_file = APP_DATA / "status.json"
    if status_file.exists():
        history = json.loads(status_file.read_text()).get("runs", [])[:29]
    (APP_DATA / "status.json").write_text(json.dumps({"runs": [
        {"at": meta["generated_at"], "state": state, "as_of": signals["date"],
         "new_alerts": len(new), "log": log.lines}] + history}, indent=1))
    log("done")


if __name__ == "__main__":
    main()
