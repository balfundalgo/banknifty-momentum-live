"""
Logging for the BANKNIFTY Momentum trader.

Writes two files into a `logs/` folder next to the EXE (or this file in dev):

  logs/app_YYYY-MM-DD.log     - every line shown in the GUI (timestamped, IST)
  logs/trades_YYYY-MM-DD.csv  - one row PER COMPLETED TRADE with full detail

The trade CSV is the audit trail to reconcile against the broker's contract note:
  date, leg, strike, security_id, qty, entry_time, entry_price, exit_time, exit_price,
  exit_reason, points, pnl, base_sl, peak, trail_sl, target_price, reentry_no,
  order_id_entry, order_id_exit, mode
"""
from __future__ import annotations
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

TRADE_COLUMNS = [
    "date", "leg", "strike", "security_id", "qty",
    "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason",
    "points", "pnl", "base_sl", "peak", "trail_sl", "target_price",
    "reentry_no", "order_id_entry", "order_id_exit", "mode",
]


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def logs_dir() -> str:
    d = os.path.join(_base_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


class TradeLogger:
    """Append-only app log + trade CSV. Safe to call from the engine thread."""

    def __init__(self):
        day = datetime.now(IST).strftime("%Y-%m-%d")
        self.app_path = os.path.join(logs_dir(), f"app_{day}.log")
        self.trade_path = os.path.join(logs_dir(), f"trades_{day}.csv")
        if not os.path.exists(self.trade_path):
            with open(self.trade_path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=TRADE_COLUMNS).writeheader()

    # ---- app log -----------------------------------------------------------
    def line(self, msg: str):
        stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.app_path, "a", encoding="utf-8") as fh:
                fh.write(f"{stamp} IST  {msg}\n")
        except Exception:
            pass        # logging must never break trading

    # ---- trade log ---------------------------------------------------------
    def trade(self, row: dict):
        out = {k: row.get(k, "") for k in TRADE_COLUMNS}
        out["date"] = datetime.now(IST).strftime("%Y-%m-%d")
        try:
            with open(self.trade_path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=TRADE_COLUMNS).writerow(out)
        except Exception:
            pass
