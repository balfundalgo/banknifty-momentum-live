# BANKNIFTY Options Momentum - Live Trader (Dhan)

Balfund Trading Pvt. Ltd. White/blue desktop GUI that runs the BANKNIFTY options
momentum strategy live through the Dhan API. Parameters default to the most-profitable
combination from the 810k-combo grid search; the client can edit and **Save** them.

## Files
- `gui.py` - CustomTkinter GUI (white/blue theme), entry point
- `config.py` - defaults + Save/Load (`settings.json` next to the EXE)
- `dhan_api.py` - Dhan connection, scrip-master, strike resolution, orders
- `strategy.py` - live engine (momentum entry, trailing stop, re-entry, square-off)
- `.github/workflows/build.yml` - builds the Windows EXE on every push

## Default parameters (most-profitable grid result)
Target premium 800 | Momentum 5% | Stop 7% | Trail 5-1 | Re-entries 0 | both legs

## Run locally
```
pip install -r requirements.txt
python gui.py
```

## Using it
1. Enter your Dhan **Client ID** and **Access Token** -> Connect.
2. Review parameters (pre-filled with the best defaults). Edit if desired -> **Save**.
3. Keep **PAPER mode** on for testing (no real orders). Switch to LIVE only when ready.
4. **Start**. The engine waits for 09:16, arms the CE/PE strikes nearest Rs 800,
   enters on a momentum jump, trails the stop, and squares off at 15:25.

> Dhan access tokens are valid up to 24h - regenerate daily from web.dhan.co.

## Build the EXE (GitHub Actions)
Push to the `balfundalgo` repo; the workflow builds `BalfundBNMomentum.exe` and
uploads it under the run's Artifacts.

## Logs
Two files are written to a `logs/` folder next to the EXE, per day:
- `app_YYYY-MM-DD.log` - every line shown in the GUI (IST timestamps)
- `trades_YYYY-MM-DD.csv` - one row per completed trade: entry/exit time & price,
  exit reason (sl / target / eod), points, P&L, base SL, peak, trailing SL,
  target price, re-entry number, both order IDs, and PAPER/LIVE mode.

## Target (premium points)
`Target (premium pts)` closes a trade when the premium rises that many points above
the entry. E.g. entry 800 with target 100 -> exits at 900. Set 0 to disable.
A target hit ends that leg for the day (re-entry applies to stop-outs only).
