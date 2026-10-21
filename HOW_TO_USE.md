# KMI Scanner: how to use it

A halal PSX scanner. It only ever shows stocks on the KMI All Share list, and
it shows a **Buy plan** only when a setup that passed the honest backtest fires.

**App:** https://abdulnafay8.github.io/psx-kmi-scanner (open in Chrome on your
phone and choose "Add to Home screen").

## What runs by itself

GitHub runs `run_daily.py` Monday-Friday at 17:00 Pakistan time, after the PSX
close, with a backup run at 07:30. Your computer can stay off. Each run updates
prices for KMI stocks, makes Buy plans for the **next trading session only**,
locks them in the track record, grades earlier alerts, sends phone alerts and
publishes the app. If PSX can't be reached, the run shows a red X on GitHub and
you get an email.

PSX hours (Pakistan time): Mon-Thu 09:32-15:30; Friday 09:17-12:00 and
14:32-16:30. The runner never uses an unfinished day's candle, and makes no
alerts once the session a plan was meant for has opened.

## Phone alerts

Two channels, both set up:
- **Chrome notifications (Firebase):** in the app, Settings -> "Turn on alerts on
  this device", then save the code it shows as the GitHub secret `FCM_TOKENS`.
  If the app shows a red banner saying the code changed, repeat this.
- **ntfy app:** subscribe to the topic name in `ntfy_topic.txt` (kept on this
  computer only; anyone who knows it can read the alerts).

## Files

| File | What it does |
|---|---|
| `step1_download_data.py` | Downloads daily prices for KMI stocks only |
| `step2_refresh_kmi.py` | Checks PSX for a new KMI list; shows adds/removes before saving |
| `kmi.py` | The allow-list: every part loads stocks only through it |
| `backtest.py` | The honest backtester; writes `results/` and `results/passed_setups.json` |
| `engine.py` | Status, lights, reasons and trade plans (tested setups only) |
| `ledger.py` | Locks and grades every alert, and keeps each setup's confidence score |
| `research.py` | Seasonal and dividend tests; refreshes dividend dates weekly |
| `run_daily.py` | The daily job |
| `app/` | The app |
| `.github/workflows/daily.yml` | The schedule GitHub runs |
| `test_step*.py` | The "done when" checks for each step |

## Re-check the setups

Run `python backtest.py` after a few months of new data, or when the KMI list
changes. Only setups it marks PASS can create Buy plans.

## Honest limits

- No setup is "accurate". In the 2024-26 test years the washout setup won 56%
  of trades and trend hold 45%. Both are **losing in 2026 so far**.
- Costs use AKD Trade's published rates (Rs 0.03/share under Rs 5; otherwise
  0.15% or Rs 0.05/share, whichever is more). Check them against your contract.
- The data has no delisted stocks, and today's KMI list is used for past years.
  Both make the backtest look better than reality.
- Seasonal and dividend patterns were tested and failed as signals; they appear
  only as context notes.
- Start with a small fixed amount. Judge after 20 trades, and compare with the
  backtest after 30. Not investment advice.
