"""
Tests two trading ideas on history before they're allowed near the signals.

    python research.py seasonal     sector-by-month returns vs the KMI market
    python research.py dividends    price moves around dividend book closures
                                    (downloads payout history once, ~5 minutes)

Same honesty rules as the backtester: KMI stocks only, no trade across a
split/bonus jump, results judged per year and on 2024+ separately, and
market-adjusted (a stock rising in a rising market is not an edge).
Results are saved in results/research_*.csv.
"""
import io
import re
import sys
import time

import numpy as np
import pandas as pd
import requests

import backtest as bt
from kmi import DATA_DIR, load_all_prices, load_kmi_list

RESULTS = bt.RESULTS_DIR
PAYOUTS_FILE = DATA_DIR / "payouts.csv"
PAYOUTS_STAMP = DATA_DIR / "payouts_fetched.txt"


def daily_returns():
    """Long table: symbol, date, ret (close-to-close, never across a break)."""
    rows = []
    for symbol, df in load_all_prices()[0].items():
        a = bt.prepare(df)
        r = pd.Series(a["c"]).pct_change().to_numpy()
        same = np.r_[False, a["seg"][1:] == a["seg"][:-1]]
        rows.append(pd.DataFrame({"symbol": symbol, "date": a["date"], "ret": np.where(same, r, np.nan)}))
    out = pd.concat(rows)
    out["date"] = pd.to_datetime(out["date"])
    return out.dropna()


# ---------------------------------------------------------------------------
def seasonal():
    kmi = load_kmi_list()
    sector = dict(zip(kmi["symbol"], kmi["sector"]))
    r = daily_returns()
    r["sector"] = r["symbol"].map(sector)
    r["ym"] = r["date"].dt.to_period("M")
    # each stock's monthly return, then equal-weight averages
    m = r.groupby(["symbol", "sector", "ym"])["ret"].apply(lambda x: (1 + x).prod() - 1).reset_index()
    market = m.groupby("ym")["ret"].mean().rename("market")
    sec = m.groupby(["sector", "ym"]).agg(ret=("ret", "mean"), stocks=("symbol", "nunique")).reset_index()
    sec = sec.join(market, on="ym")
    sec = sec[sec["stocks"] >= 3]                       # a sector needs 3+ stocks that month
    sec["excess"] = sec["ret"] - sec["market"]
    sec["year"], sec["month"] = sec["ym"].dt.year, sec["ym"].dt.month

    summary = sec.groupby(["sector", "month"]).agg(
        years=("excess", "size"),
        avg_excess=("excess", "mean"),
        years_beating_market=("excess", lambda x: int((x > 0).sum())),
        avg_excess_2019_23=("excess", lambda x: x[sec.loc[x.index, "year"] <= 2023].mean()),
        avg_excess_2024_26=("excess", lambda x: x[sec.loc[x.index, "year"] >= 2024].mean()),
    ).reset_index()
    # a seasonal pattern worth using: same sign in nearly every year, and in
    # both the older and newer years
    summary["consistent"] = (
        (summary["years"] >= 6)
        & ((summary["years_beating_market"] >= summary["years"] - 1) | (summary["years_beating_market"] <= 1))
        & (np.sign(summary["avg_excess_2019_23"]) == np.sign(summary["avg_excess_2024_26"]))
    )
    RESULTS.mkdir(exist_ok=True)
    summary.to_csv(RESULTS / "research_seasonal.csv", index=False)
    sec.to_csv(RESULTS / "research_seasonal_by_year.csv", index=False)

    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    print("CEMENT vs the KMI market, by month (excess return, each year):")
    cem = sec[sec["sector"].str.contains("CEMENT", case=False, na=False)]
    piv = cem.pivot_table(index="year", columns="month", values="excess")
    piv.columns = [months[c - 1] for c in piv.columns]
    print((piv * 100).round(1).to_string())
    s = summary[summary["sector"].str.contains("CEMENT", case=False, na=False)].copy()
    s["month"] = [months[x - 1] for x in s["month"]]
    print("\n" + s[["month", "years", "avg_excess", "years_beating_market", "avg_excess_2019_23",
                    "avg_excess_2024_26", "consistent"]].round(4).to_string(index=False))
    c = summary[summary["consistent"]].copy()
    c["month"] = [months[x - 1] for x in c["month"]]
    print(f"\nConsistent sector-months across ALL sectors: {len(c)} of {len(summary)} tested")
    print(c.sort_values("avg_excess")[["sector", "month", "years", "years_beating_market",
                                       "avg_excess"]].round(4).to_string(index=False))
    print(f"\nBy chance alone, about {len(summary) * 0.03:.0f} of {len(summary)} would look "
          "this consistent (the chance of 6+ of 7 years on the same side is ~6%, ~3% for one side).")


# ---------------------------------------------------------------------------
def fetch_payouts():
    """Every KMI stock's dividend history from the PSX data portal."""
    from psxdata.scrapers.token import extract_token
    from psxdata import constants as C
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
    token = extract_token(s.get("https://dps.psx.com.pk/", timeout=30).text)
    rows = []
    for symbol in sorted(load_kmi_list()["symbol"]):
        try:
            r = s.post("https://dps.psx.com.pk/company/payouts", data={"symbol": symbol},
                       headers={C.TOKEN_HEADER: token}, timeout=30)
            t = pd.read_html(io.StringIO(r.text))[0]
            t["symbol"] = symbol
            rows.append(t)
        except Exception:
            pass                                       # no payouts table: never paid
        time.sleep(0.4)
    p = pd.concat(rows, ignore_index=True)
    p.to_csv(PAYOUTS_FILE, index=False)
    PAYOUTS_STAMP.write_text(pd.Timestamp.now().date().isoformat())
    return p


def payouts_age_days():
    """Days since the payout list was downloaded (file dates can't be trusted:
    a fresh git checkout makes every file look new)."""
    if not PAYOUTS_FILE.exists() or not PAYOUTS_STAMP.exists():
        return 999
    return (pd.Timestamp.now().normalize() - pd.Timestamp(PAYOUTS_STAMP.read_text().strip())).days


def parse_payouts(p):
    out = []
    for _, r in p.iterrows():
        details = str(r.get("Details", ""))
        closure = str(r.get("Book Closure", ""))
        m = re.search(r"(\d{2}/\d{2}/\d{4})", closure)
        pct = re.search(r"([\d.]+)\s*%", details)
        if not m or not pct or "(D)" not in details:  # cash dividends only
            continue
        out.append({"symbol": r["symbol"], "closure": pd.to_datetime(m.group(1), dayfirst=True),
                    "pct_of_face": float(pct.group(1))})
    return pd.DataFrame(out).drop_duplicates()


def dividends():
    p = pd.read_csv(PAYOUTS_FILE) if PAYOUTS_FILE.exists() else fetch_payouts()
    ev = parse_payouts(p)
    prices = load_all_prices()[0]
    r = daily_returns()
    market = r.groupby("date")["ret"].mean()
    rows = []
    for _, e in ev.iterrows():
        df = prices.get(e["symbol"])
        if df is None:
            continue
        a = bt.prepare(df)
        dates = pd.to_datetime(a["date"])
        b = int(np.searchsorted(dates, e["closure"]))   # first trading day on/after closure start
        if b - 15 < 0 or b + 10 >= len(dates) or a["seg"][b - 15] != a["seg"][b + 10]:
            continue
        c = a["c"]
        div = 10 * e["pct_of_face"] / 100               # PSX face value is Rs 10 for nearly all stocks
        # ex-date is 1-2 trading days before book closure (T+2 until Feb 2026, then T+1)
        ex = b - (2 if e["closure"] < pd.Timestamp("2026-02-09") else 1)

        def mkt(i, j):
            seg = market.reindex(dates[i + 1:j + 1]).fillna(0)
            return (1 + seg).prod() - 1
        pre = c[ex - 1] / c[ex - 11] - 1 - mkt(ex - 11, ex - 1)             # 10 days into the last cum day
        drop = (c[ex] + div) / c[ex - 1] - 1 - mkt(ex - 1, ex)              # ex day, dividend added back
        post = c[ex + 9] / c[ex] - 1 - mkt(ex, ex + 9)                      # 9 days after ex
        rows.append({"symbol": e["symbol"], "closure": e["closure"], "year": e["closure"].year,
                     "dividend_rs": div, "yield": div / c[ex - 1],
                     "pre_10d_excess": pre, "exday_excess_after_dividend": drop,
                     "post_9d_excess": post,
                     "raw_exday_drop": c[ex] / c[ex - 1] - 1,
                     "tv20": a["tv20"][ex - 1]})
    res = pd.DataFrame(rows)
    res.to_csv(RESULTS / "research_dividends.csv", index=False)
    liquid = res[res["tv20"] >= bt.MIN_TRADED_VALUE]
    print(f"{len(ev)} cash dividends found; {len(res)} usable (clean prices around them); "
          f"{len(liquid)} in stocks trading 2M+ PKR/day\n")
    for label, d in [("all usable", res), ("liquid only", liquid)]:
        print(f"{label}: n={len(d)}")
        for col, name in [("pre_10d_excess", "10 days BEFORE ex-date (run-up?)"),
                          ("exday_excess_after_dividend", "ex-date drop, dividend added back"),
                          ("post_9d_excess", "9 days AFTER ex-date (dip?)")]:
            x = d[col]
            print(f"  {name:38s} mean {x.mean() * 100:+.2f}%  median {x.median() * 100:+.2f}%  "
                  f"positive {(x > 0).mean():.0%}  t={x.mean() / (x.std() / np.sqrt(len(x))):+.1f}")
        print("  raw ex-date move (what the chart shows): "
              f"{d['raw_exday_drop'].mean() * 100:+.2f}% vs average dividend yield {d['yield'].mean() * 100:.2f}%")
        by = d.groupby("year")["pre_10d_excess"].agg(["size", "mean"])
        print("  run-up by year:", {int(y): f"{m * 100:+.2f}% (n={n})" for y, (n, m) in by.iterrows()})
        print()
    # the tradable version: buy 10 days before ex, sell on the last cum day
    cost = 2 * bt.side_cost_rate(100.0)
    x = liquid["pre_10d_excess"]
    print(f"Trade 'buy 10 days before ex-date, sell the day before': market-adjusted "
          f"{x.mean() * 100:+.2f}% gross vs ~{cost * 100:.2f}% round-trip AKD cost "
          f"-> {(x.mean() - cost) * 100:+.2f}% net per trade; 2019-23 {x[liquid['year'] <= 2023].mean() * 100:+.2f}%, "
          f"2024-26 {x[liquid['year'] >= 2024].mean() * 100:+.2f}%")


if __name__ == "__main__":
    {"seasonal": seasonal, "dividends": dividends}[sys.argv[1] if len(sys.argv) > 1 else "seasonal"]()
