"""
Live strategy engine for the BANKNIFTY Options Momentum strategy.
Mirrors the validated backtest (banknifty_momentum_backtest): BUY the monthly CE+PE
strike closest to target premium, enter on a momentum % jump above the 09:16 reference,
manage a trailing stop, re-enter after a stop-out up to the cap, hard square-off at 15:25.

Premium feed: REST LTP polling (~1s) on just the two option instruments -- robust and
accurate, matching the Balfund REST-polling hybrid pattern. No WebSocket binary parsing.

The engine runs in its own thread and reports to the GUI via callbacks:
  on_log(str), on_status(dict), on_trade(dict)
"""
from __future__ import annotations
import math
import threading
import time

from tradelog import TradeLogger
from datetime import datetime, time as dtime, timezone, timedelta

# India has no DST, so a fixed +05:30 offset is bulletproof and needs no tzdata
# (important inside a PyInstaller EXE on a UTC VPS). All session-time checks use IST,
# never the server's local clock.
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    return datetime.now(IST)


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class Leg:
    """One side (CE or PE): handles entry arming, trailing stop, re-entries."""
    def __init__(self, right, engine):
        self.right = right
        self.e = engine
        self.security_id = None
        self.strike = None
        self.ref_px = None          # reference premium captured at arm time
        self.in_pos = False
        self.entry_px = None
        self.peak = None
        self.reentries_used = 0
        self.realized = 0.0
        self.last_px = None
        self.status = "idle"
        self.entry_time = None
        self.entry_oid = None
        self.target_px = None      # absolute premium price that closes the trade

    def arm(self):
        """Pick the strike closest to target premium, then capture the momentum
        reference from the FIRST LIVE tick (not the option-chain snapshot) so entry
        always requires a genuine +momentum% rise on the same price series."""
        cfg = self.e.cfg
        res = self.e.api.pick_option_by_premium(self.e.expiry, self.right, cfg["target_premium"])
        if not res:
            self.e.log(f"{self.right}: could not resolve a strike near Rs {cfg['target_premium']}")
            self.status = "no-strike"
            return False
        self.security_id, self.strike, chain_px = res
        self.ref_px = None                          # set from the first live tick below
        self.e.api.subscribe(self.security_id)      # live LTP via WS feed
        self.status = "arming"
        tag = "RE-ENTRY " if self.reentries_used else ""
        self.e.log(f"{self.right}: {tag}armed {int(self.strike)} (sid {self.security_id}) "
                   f"chain premium ~{chain_px:.2f}; capturing live reference...")
        return True

    def on_price(self, px, now):
        self.last_px = px
        cfg = self.e.cfg
        if not self.in_pos:
            if self.status == "arming":
                # first live tick becomes the reference; require a real rise from here
                self.ref_px = px
                self.status = "waiting"
                self.e.log(f"{self.right}: live reference {px:.2f}; "
                           f"trigger >= {px*(1+cfg['momentum_pct']/100):.2f}")
                return
            if self.status != "waiting":
                return
            trigger = self.ref_px * (1 + cfg["momentum_pct"] / 100.0)
            if px >= trigger and now < _hhmm(cfg["exit_time"]):
                self._enter(px)
        else:
            self._manage(px, now)

    def _enter(self, px):
        oid, fill = self.e.api.place_market(self.security_id, "BUY", self.e.qty, self.e.cfg["paper_mode"])
        self.in_pos = True
        self.entry_px = fill if fill and fill > 0 else px
        self.peak = self.entry_px
        self.entry_time = now_ist().strftime("%H:%M:%S")
        self.entry_oid = oid
        self.status = "in-position"
        tp = float(self.e.cfg.get("target_points", 0) or 0)
        self.target_px = (self.entry_px + tp) if tp > 0 else None
        tgt = f"  target {self.target_px:.2f} (+{tp:g} pts)" if self.target_px else "  target: off"
        self.e.log(f"{self.right}: ENTER {int(self.strike)} @ {self.entry_px:.2f}  "
                   f"qty {self.e.qty}{tgt}")
        self.e.on_trade({"leg": self.right, "action": "ENTER", "strike": int(self.strike),
                         "price": round(px, 2), "time": now_ist().strftime("%H:%M:%S")})

    def _manage(self, px, now):
        cfg = self.e.cfg
        if px > self.peak:
            self.peak = px
        base_sl = self.entry_px * (1 - cfg["sl_pct"] / 100.0)
        if cfg["trail_sl_move"] > 0:
            steps = math.floor(max(0.0, (self.peak - self.entry_px) / cfg["trail_sl_move"]))
            sl = base_sl + steps * cfg["trail_sl_by"]
        else:
            sl = base_sl
        self.base_sl, self.trail_sl = base_sl, sl
        if self.target_px is not None and px >= self.target_px:
            self._exit(px, "target")          # premium-points target hit
        elif px <= sl:
            self._exit(px, "sl")
        elif now >= _hhmm(cfg["exit_time"]):
            self._exit(px, "eod")

    def _exit(self, px, reason):
        oid, fill = self.e.api.place_market(self.security_id, "SELL", self.e.qty, self.e.cfg["paper_mode"])
        exit_px = fill if fill and fill > 0 else px
        pnl = (exit_px - self.entry_px) * self.e.qty
        self.realized += pnl
        self.in_pos = False
        self.e.tl.trade({
            "leg": self.right, "strike": int(self.strike), "security_id": self.security_id,
            "qty": self.e.qty, "entry_time": self.entry_time, "entry_price": round(self.entry_px, 2),
            "exit_time": now_ist().strftime("%H:%M:%S"), "exit_price": round(exit_px, 2),
            "exit_reason": reason, "points": round(exit_px - self.entry_px, 2),
            "pnl": round(pnl, 2), "base_sl": round(getattr(self, "base_sl", 0.0), 2),
            "peak": round(self.peak or 0.0, 2), "trail_sl": round(getattr(self, "trail_sl", 0.0), 2),
            "target_price": round(self.target_px, 2) if self.target_px else "",
            "reentry_no": self.reentries_used, "order_id_entry": self.entry_oid or "",
            "order_id_exit": oid or "",
            "mode": "PAPER" if self.e.cfg["paper_mode"] else "LIVE"})
        self.e.log(f"{self.right}: EXIT {int(self.strike)} @ {exit_px:.2f}  ({reason})  P&L {pnl:,.0f}")
        self.e.on_trade({"leg": self.right, "action": f"EXIT-{reason}", "strike": int(self.strike),
                         "price": round(exit_px, 2), "pnl": round(pnl, 2),
                         "time": now_ist().strftime("%H:%M:%S")})
        cfg = self.e.cfg
        if reason == "sl" and self.reentries_used < cfg["max_reentries_on_sl"] \
                and now_ist().time() < _hhmm(cfg["no_reentry_after"]):
            self.reentries_used += 1
            self.arm()                      # re-pick strike + new reference, wait again
        else:
            self.status = "done"

    def unrealized(self):
        if self.in_pos and self.last_px is not None:
            return (self.last_px - self.entry_px) * self.e.qty
        return 0.0


class StrategyEngine:
    def __init__(self, api, cfg, on_log=print, on_status=lambda s: None, on_trade=lambda t: None):
        self.api = api
        self.cfg = cfg
        self.on_log = on_log
        self.tl = TradeLogger()
        self.on_status = on_status
        self.on_trade = on_trade
        self.qty = int(cfg["lots"]) * int(cfg["lot_size"])
        self.expiry = None
        self.legs = []
        self._stop = threading.Event()
        self._thread = None
        self.running = False

    def log(self, msg):
        self.tl.line(msg)                      # persist to logs/app_YYYY-MM-DD.log
        self.on_log(f"{now_ist():%H:%M:%S} IST  {msg}")

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.log("Stop requested - squaring off any open positions.")

    # ---- main loop ---------------------------------------------------------
    def _run(self):
        self.running = True
        try:
            self.api.start_feed()
            self.expiry = self.api.nearest_expiry()
            if not self.expiry:
                self.log("Could not determine monthly expiry. Aborting.")
                return
            self.log(f"Logs: {self.tl.app_path}")
            self.log(f"Trade log: {self.tl.trade_path}")
            self.log(f"Monthly expiry: {self.expiry}   qty/leg: {self.qty}   "
                     f"mode: {'PAPER' if self.cfg['paper_mode'] else 'LIVE'}   "
                     f"clock: {now_ist():%H:%M:%S} IST")
            if self.cfg["trade_ce"]:
                self.legs.append(Leg("CE", self))
            if self.cfg["trade_pe"]:
                self.legs.append(Leg("PE", self))

            self._wait_until(_hhmm(self.cfg["entry_time"]))
            if self._stop.is_set():
                self.log("Stopped before entry window - no legs armed.")
                return
            self.log(f"Entry window reached ({self.cfg['entry_time']} IST) - arming legs.")
            for lg in self.legs:
                lg.arm()

            no_price_since = time.time()
            while not self._stop.is_set():
                now = now_ist().time()
                got_any = False
                for lg in self.legs:
                    if lg.status in ("arming", "waiting", "in-position"):
                        px = self.api.last_ltp(lg.security_id)
                        if px is not None:
                            got_any = True
                            lg.on_price(px, now)
                if got_any:
                    no_price_since = time.time()
                elif time.time() - no_price_since > 60:
                    self.log("WARNING: no live price for 60s (feed down and REST fallback "
                             "failing). Strategy cannot arm or manage trades until prices return.")
                    no_price_since = time.time()
                self._push_status()
                # daily max loss guard
                if self.cfg["daily_max_loss"] and self._total_pnl() <= -abs(self.cfg["daily_max_loss"]):
                    self.log("Daily max loss hit - flattening and stopping.")
                    break
                if now >= _hhmm(self.cfg["exit_time"]):
                    break
                time.sleep(1.0)

            # square off everything
            for lg in self.legs:
                if lg.in_pos and lg.last_px is not None:
                    lg._exit(lg.last_px, "eod")
            self._push_status()
            self.log(f"Session done. Total P&L: Rs {self._total_pnl():,.0f}")
        except Exception as e:
            self.log(f"Engine error: {e}")
        finally:
            self.running = False
            try:
                self.api.stop_feed()
            except Exception:
                pass
            self.on_status({"running": False})

    def _wait_until(self, t: dtime):
        while not self._stop.is_set() and now_ist().time() < t:
            self.on_status({"running": True,
                            "phase": f"waiting for {t.strftime('%H:%M')} IST  (now {now_ist():%H:%M:%S} IST)"})
            time.sleep(1.0)

    def _total_pnl(self):
        return sum(lg.realized + lg.unrealized() for lg in self.legs)

    def _push_status(self):
        self.on_status({
            "running": True,
            "expiry": str(self.expiry),
            "total_pnl": round(self._total_pnl(), 2),
            "legs": [{"right": lg.right, "strike": lg.strike, "status": lg.status,
                      "ltp": lg.last_px, "entry": lg.entry_px,
                      "pnl": round(lg.realized + lg.unrealized(), 2),
                      "reentries": lg.reentries_used} for lg in self.legs],
        })
