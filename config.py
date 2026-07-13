"""
Configuration for the BANKNIFTY Options Momentum live trader.
Defaults are the MOST-PROFITABLE combination from the 810k-combo grid search
(prem800_mom5_sl7_tr5-1_re0). The client can change any of these in the GUI and
press Save; values persist to a JSON file next to the executable.

Balfund Trading Pvt. Ltd.
"""
from __future__ import annotations
import json
import os
import sys

APP_NAME = "BANKNIFTY Momentum"
ORG = "Balfund Trading Pvt. Ltd."


def _app_dir() -> str:
    """Folder next to the EXE (frozen) or this file (dev) - where settings.json lives."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(_app_dir(), "settings.json")

# ---- DEFAULTS: the most-profitable grid result -----------------------------
DEFAULTS = {
    # --- Dhan credentials (entered by the client) ---
    "client_id": "",
    "pin": "",
    "totp_secret": "",
    "access_token": "",         # optional existing token; auto-generated from PIN+TOTP if blank

    # --- strategy parameters (most-profitable combo) ---
    "target_premium": 800,      # pick CE/PE strike closest to this premium
    "target_points": 0,         # profit target in premium POINTS (0 = disabled).
                                # e.g. entry 800 + 100 => exit at 900.
                                # NOTE: the most-profitable backtest combo used NO target.
    "momentum_pct": 5,          # enter when premium rises this % above the 09:16 reference
    "sl_pct": 7,                # initial stop = entry - this %
    "trail_sl_move": 5,         # premium must gain this many pts to ratchet the stop
    "trail_sl_by": 1,           # ...and the stop moves up this many pts per ratchet
    "max_reentries_on_sl": 0,   # max re-entries per leg after a stop-out (0 = single entry)

    # --- legs / sizing ---
    "trade_ce": True,
    "trade_pe": True,
    "lots": 1,                  # client sizing -- 1 lot default for safety (BANKNIFTY lot = 30)
    "lot_size": 30,

    # --- session timing (IST) ---
    "entry_time": "09:16",
    "exit_time": "15:25",       # hard square-off
    "no_reentry_after": "15:00",

    # --- safety ---
    "paper_mode": True,         # PAPER by default -- no real orders until the client switches to LIVE
    "daily_max_loss": 0,        # 0 = disabled; else stop the day if cumulative P&L <= -this (Rs)
}

# field metadata for the GUI (label, type, optional choices)
PARAM_FIELDS = [
    ("target_premium",      "Target premium (Rs)",        "int"),
    ("target_points",       "Target (premium pts, 0=off)", "float"),
    ("momentum_pct",        "Momentum trigger (%)",       "float"),
    ("sl_pct",              "Initial stop-loss (%)",      "float"),
    ("trail_sl_move",       "Trail: gain per step (pts)", "float"),
    ("trail_sl_by",         "Trail: stop step (pts)",     "float"),
    ("max_reentries_on_sl", "Max re-entries on SL",       "int"),
    ("lots",                "Lots",                       "int"),
    ("lot_size",            "Lot size",                   "int"),
    ("entry_time",          "Entry time (HH:MM)",         "str"),
    ("no_reentry_after",    "No re-entry after (HH:MM)",  "str"),
    ("exit_time",           "Square-off (HH:MM)",         "str"),
    ("daily_max_loss",      "Daily max loss (Rs, 0=off)", "int"),
]


def load_config() -> dict:
    """Return DEFAULTS overlaid with any saved settings.json."""
    cfg = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            cfg.update({k: saved[k] for k in saved if k in DEFAULTS})
        except Exception:
            pass     # corrupt file -> fall back to defaults
    return cfg


def save_config(cfg: dict) -> str:
    """Persist only known keys; returns the path written."""
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    return SETTINGS_PATH


def save_token(token: str) -> str:
    """Persist just the working access token (RA4 pattern) so the next run verifies
    the saved token instead of regenerating via TOTP - no Invalid-TOTP / 2-min limit."""
    cfg = load_config()
    cfg["access_token"] = token
    return save_config(cfg)


def reset_to_defaults() -> dict:
    """Most-profitable defaults, preserving saved Dhan credentials."""
    cur = load_config()
    fresh = dict(DEFAULTS)
    for k in ("client_id", "pin", "totp_secret", "access_token"):
        fresh[k] = cur.get(k, "")
    return fresh
