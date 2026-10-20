# KMI Scanner: how to use it

A halal PSX scanner. It only ever shows stocks on the KMI All Share list, and
it shows a **Buy plan** only when a setup that passed the honest backtest fires.

## Every day (automatic once scheduled)

`run_daily.py` runs after the PSX close. It updates prices, runs the engine,
locks new Buy plans in the track record, grades old ones and sends phone alerts.
Plans are for the **next trading session only**.

PSX hours (Pakistan time): Mon–Thu 09:32–15:30; Friday 09:17–12:00 and
14:32–16:30. The runner never uses an unfinished day's candle, and makes no
alerts once the session a plan was meant for has opened.

## One-time setup

1. **Schedule the daily run** (Mon–Fri 17:00):
   `powershell -ExecutionPolicy Bypass -File install_schedule.ps1`
2. **Phone alerts:** install the free **ntfy** app, pick a long random topic name
   (e.g. `kmi-scanner-8f3k2q9x`), subscribe to it in the app, and put the same
   name in `config.json` as `"ntfy_topic"`. Anyone who knows the topic can read
   the alerts, so keep it hard to guess. Test: `python run_daily.py --test-push`
3. **Open the app:** `python serve.py`, then open the address it prints. On the
   phone (same Wi-Fi), open it in Chrome and choose "Add to Home screen". Put
   that address in `config.json` as `"app_url"` so alerts open the stock directly.

## Files

| File | What it does |
|---|---|
| `step1_download_data.py` | Downloads daily prices for KMI stocks only |
| `step2_refresh_kmi.py` | Checks PSX for a new KMI list; shows adds/removes before saving |
| `kmi.py` | The allow-list: every part loads stocks only through it |
| `backtest.py` | The honest backtester; writes `results/` and `results/passed_setups.json` |
| `engine.py` | Status, lights, reasons and trade plans (tested setups only) |
| `ledger.py` | Locks and grades every alert: the honest track record |
| `run_daily.py` | The daily job |
| `serve.py`, `app/` | The app |
| `test_step*.py` | The "done when" checks for each step |

## Re-check the setups

Run `python backtest.py` after a few months of new data, or when the KMI list
changes. Only setups it marks PASS can create Buy plans.

## Honest limits

- No setup is "accurate". In the 2024–26 test years the washout setup won 56%
  of trades and trend hold 45%. Both are **losing in 2026 so far**.
- Costs use PSX's minimum brokerage (0.15% or Rs 0.03/share). If your broker
  charges more, the results are worse; the backtest's double-cost check covers
  up to about 0.3% per side.
- The data has no delisted stocks, and today's KMI list is used for past years.
  Both make the backtest look better than reality.
- Start with a small fixed amount. Judge after 20 trades, and compare with the
  backtest after 30. Not investment advice.
