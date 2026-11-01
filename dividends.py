"""
Dividend calendar for KMI stocks, from the PSX data portal:

  meetings      AGM / EOGM / ARM calendar (shareholder meetings ahead)
  book closure  dividends announced whose share register closes soon
  payout        dividends whose book closure has started or just ended
                (PSX doesn't publish the payment date; companies set it)

    from dividends import build_dividends
    data = build_dividends(kmi_symbols, last_close)   # -> dict for app/data/dividends.json

Halal rule: only KMI All Share stocks are kept.
"""
import datetime as dt
import io
import re
import time

import pandas as pd
import requests

PSX = "https://dps.psx.com.pk"
FACE_VALUE = 10.0          # nearly all PSX shares; Rs/share figures assume it
PKT = dt.timezone(dt.timedelta(hours=5))


def _session():
    from psxdata.scrapers.token import extract_token
    from psxdata import constants as C
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
    token = extract_token(s.get(PSX + "/", timeout=30).text)
    s.headers[C.TOKEN_HEADER] = token
    return s


def fetch_meetings(s, start, end):
    r = s.post(PSX + "/calendar", data={"from": str(start), "to": str(end)}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_recent_payouts(s, since, max_pages=40):
    """Market-wide dividend announcements, newest first, back to `since`."""
    rows = []
    for page in range(max_pages):
        r = s.post(PSX + "/payouts", data={"symbol": "", "count": 25, "offset": page * 25}, timeout=30)
        r.raise_for_status()
        t = pd.read_html(io.StringIO(r.text), flavor="lxml")[0]
        if t.empty:
            break
        t["announced"] = pd.to_datetime(t["Date / Time of Announcement"], format="%B %d, %Y %I:%M %p", errors="coerce")
        rows.append(t)
        if t["announced"].min() < pd.Timestamp(since):
            break
        time.sleep(0.4)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def trading_days_before(day, n):
    """The weekday n trading days before `day` (PSX holidays aren't known)."""
    d = day
    while n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def describe(details):
    """'185%(iii) (D)' -> kind, percent, label."""
    m = re.search(r"([\d.]+)\s*%", details)
    pct = float(m.group(1)) if m else None
    kind = "cash" if "(D)" in details else "bonus shares" if "(B)" in details else "right shares" if "(R)" in details else "other"
    stage = "final" if "(F)" in details else "interim" if re.search(r"\((i{1,3}|iv)\)", details) else ""
    return kind, pct, " ".join(x for x in [stage, kind] if x)


def build_dividends(kmi_symbols, last_close, today=None, s=None):
    today = today or dt.datetime.now(PKT).date()
    s = s or _session()
    out = {"generated_at": dt.datetime.now(PKT).isoformat(timespec="seconds"),
           "face_value_assumed": FACE_VALUE, "meetings": [], "book_closures": [], "payouts": []}

    seen = set()
    for ev in fetch_meetings(s, today, today + dt.timedelta(days=60)):
        key = (ev.get("symbol"), ev.get("type"), ev.get("date"), ev.get("time"))
        if ev.get("symbol") in kmi_symbols and key not in seen:     # PSX lists some meetings twice
            seen.add(key)
            out["meetings"].append({"symbol": ev["symbol"], "name": ev.get("name"), "type": ev.get("type"),
                                    "date": ev.get("date"), "time": ev.get("time"),
                                    "city": (ev.get("city") or "").strip().title(),
                                    "period_end": ev.get("period_end")})
    out["meetings"].sort(key=lambda e: (e["date"], e.get("time") or ""))

    p = fetch_recent_payouts(s, today - dt.timedelta(days=90))
    for _, r in p.iterrows():
        sym = r["Symbol"]
        if sym not in kmi_symbols:
            continue
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", str(r["Book Closure Date"]))
        if not dates:
            continue
        start = dt.datetime.strptime(dates[0], "%d/%m/%Y").date()
        end = dt.datetime.strptime(dates[-1], "%d/%m/%Y").date()
        kind, pct, label = describe(str(r["Dividend Announcement"]))
        per_share = FACE_VALUE * pct / 100 if (pct is not None and kind == "cash") else None
        close = last_close.get(sym)
        item = {
            "symbol": sym, "name": r["Company"], "sector": r["Sector"],
            "announcement": r["Dividend Announcement"], "kind": kind, "label": label, "percent": pct,
            "rs_per_share": round(per_share, 2) if per_share else None,
            "yield": round(per_share / close, 4) if per_share and close else None,
            "announced": r["announced"].date().isoformat() if pd.notna(r["announced"]) else None,
            "closure_start": start.isoformat(), "closure_end": end.isoformat(),
            # T+1 settlement: own the shares by the close 2 trading days before books close
            "last_day_to_buy": trading_days_before(start, 2).isoformat(),
            "ex_date": trading_days_before(start, 1).isoformat(),
        }
        if start > today:
            out["book_closures"].append(item)
        elif end >= today - dt.timedelta(days=45):
            out["payouts"].append(item)
    out["book_closures"].sort(key=lambda e: e["closure_start"])
    out["payouts"].sort(key=lambda e: e["closure_end"], reverse=True)
    return out


if __name__ == "__main__":
    import json
    from kmi import load_kmi_symbols
    d = build_dividends(load_kmi_symbols(), {})
    print({k: len(v) for k, v in d.items() if isinstance(v, list)})
    print(json.dumps(d["book_closures"][:3], indent=1))
