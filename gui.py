"""
BANKNIFTY Options Momentum - live trading GUI (Dhan).
White/blue theme. Parameters default to the most-profitable grid result; the client
can edit them and press Save to persist. PAPER mode by default.

Balfund Trading Pvt. Ltd.
"""
from __future__ import annotations
import threading
import customtkinter as ctk

import config as C
from dhan_api import DhanAPI
from strategy import StrategyEngine

# ---- white / blue palette --------------------------------------------------
BG        = "#F4F8FD"   # app background (very light blue)
CARD      = "#FFFFFF"   # card surface
BORDER    = "#D6E2F0"
BLUE      = "#1565C0"   # primary
BLUE_DK   = "#0D47A1"   # hover / headers
BLUE_LT   = "#E8F1FB"   # subtle fills
INK       = "#14223A"   # primary text
MUTE      = "#5B6B82"   # secondary text
GREEN     = "#2E7D32"
RED       = "#C62828"
AMBER     = "#B26A00"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{C.APP_NAME}  -  {C.ORG}")
        self.geometry("1060x720")
        self.configure(fg_color=BG)
        self.minsize(960, 640)

        self.cfg = C.load_config()
        self.api: DhanAPI | None = None
        self.engine: StrategyEngine | None = None
        self.param_vars: dict[str, ctk.StringVar] = {}
        self.leg_labels: dict[str, ctk.CTkLabel] = {}

        self._build_header()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self._build_left(body)
        self._build_right(body)
        self._populate()

    # ---- header ------------------------------------------------------------
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color=BLUE, corner_radius=0, height=64)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="BANKNIFTY  Options  Momentum",
                     font=ctk.CTkFont(size=20, weight="bold"), text_color="white").pack(side="left", padx=20)
        ctk.CTkLabel(bar, text="Balfund Trading", font=ctk.CTkFont(size=12),
                     text_color="#CFE3FA").pack(side="left", pady=(6, 0))
        self.mode_badge = ctk.CTkLabel(bar, text="PAPER", fg_color="#0B5C2E", text_color="white",
                                       corner_radius=8, font=ctk.CTkFont(size=12, weight="bold"),
                                       width=86, height=28)
        self.mode_badge.pack(side="right", padx=20)

    # ---- left column: connection + parameters ------------------------------
    def _build_left(self, parent):
        left = ctk.CTkScrollableFrame(parent, fg_color="transparent", width=430)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        # connection card
        con = self._card(left, "Dhan connection")
        self.client_var = ctk.StringVar(value=self.cfg["client_id"])
        self.pin_var = ctk.StringVar(value=self.cfg["pin"])
        self.totp_var = ctk.StringVar(value=self.cfg["totp_secret"])
        self.token_var = ctk.StringVar(value=self.cfg["access_token"])
        self._field(con, "Client ID", self.client_var)
        self._field(con, "PIN", self.pin_var, show="*")
        self._field(con, "TOTP Secret", self.totp_var, show="*")
        self._field(con, "Access Token (optional - auto-generated if blank)", self.token_var, show="*")
        row = ctk.CTkFrame(con, fg_color="transparent"); row.pack(fill="x", padx=14, pady=(4, 12))
        self.connect_btn = self._btn(row, "Generate / Connect", self._on_connect)
        self.connect_btn.pack(side="left")
        self.conn_status = ctk.CTkLabel(row, text="not connected", text_color=MUTE,
                                        font=ctk.CTkFont(size=12))
        self.conn_status.pack(side="left", padx=12)

        # parameters card
        par = self._card(left, "Strategy parameters")
        ctk.CTkLabel(par, text="Defaults = most-profitable grid result. Edit and press Save.",
                     text_color=MUTE, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(0, 6))
        grid = ctk.CTkFrame(par, fg_color="transparent"); grid.pack(fill="x", padx=8)
        grid.grid_columnconfigure((0, 1), weight=1)
        for i, (key, label, _t) in enumerate(C.PARAM_FIELDS):
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=6, pady=5)
            ctk.CTkLabel(cell, text=label, text_color=MUTE,
                         font=ctk.CTkFont(size=11)).pack(anchor="w")
            var = ctk.StringVar()
            self.param_vars[key] = var
            ctk.CTkEntry(cell, textvariable=var, fg_color="white", border_color=BORDER,
                         text_color=INK, height=32).pack(fill="x")

        # toggles
        tog = ctk.CTkFrame(par, fg_color="transparent"); tog.pack(fill="x", padx=14, pady=(8, 4))
        self.ce_var = ctk.BooleanVar(value=self.cfg["trade_ce"])
        self.pe_var = ctk.BooleanVar(value=self.cfg["trade_pe"])
        self.paper_var = ctk.BooleanVar(value=self.cfg["paper_mode"])
        ctk.CTkSwitch(tog, text="Trade CE", variable=self.ce_var, progress_color=BLUE).pack(side="left")
        ctk.CTkSwitch(tog, text="Trade PE", variable=self.pe_var, progress_color=BLUE).pack(side="left", padx=12)
        ctk.CTkSwitch(tog, text="PAPER mode", variable=self.paper_var, progress_color=GREEN,
                      command=self._on_mode).pack(side="right")

        # save / reset
        act = ctk.CTkFrame(par, fg_color="transparent"); act.pack(fill="x", padx=14, pady=(8, 14))
        self._btn(act, "Save", self._on_save).pack(side="left")
        self._btn(act, "Reset to best defaults", self._on_reset, ghost=True).pack(side="left", padx=10)
        self.save_note = ctk.CTkLabel(act, text="", text_color=GREEN, font=ctk.CTkFont(size=12))
        self.save_note.pack(side="left", padx=6)

    # ---- right column: controls + status + log -----------------------------
    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # controls
        ctrl = self._card(right, "Controls", pack=False)
        ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        crow = ctk.CTkFrame(ctrl, fg_color="transparent"); crow.pack(fill="x", padx=14, pady=(4, 14))
        self.start_btn = self._btn(crow, "Start", self._on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = self._btn(crow, "Stop", self._on_stop, danger=True)
        self.stop_btn.pack(side="left", padx=10)
        self.stop_btn.configure(state="disabled")
        self.phase_lbl = ctk.CTkLabel(crow, text="idle", text_color=MUTE, font=ctk.CTkFont(size=12))
        self.phase_lbl.pack(side="left", padx=12)

        # live status
        stat = self._card(right, "Live status", pack=False)
        stat.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        srow = ctk.CTkFrame(stat, fg_color="transparent"); srow.pack(fill="x", padx=14, pady=(0, 8))
        srow.grid_columnconfigure((0, 1, 2), weight=1)
        self.leg_labels["CE"] = self._stat_card(srow, 0, "CE leg")
        self.leg_labels["PE"] = self._stat_card(srow, 1, "PE leg")
        self.pnl_label = self._stat_card(srow, 2, "Total P&L", big=True)

        # log
        logc = self._card(right, "Activity log", pack=False)
        logc.grid(row=2, column=0, sticky="nsew")
        self.log_box = ctk.CTkTextbox(logc, fg_color="#0E2236", text_color="#D7E6F5",
                                      font=ctk.CTkFont(family="Menlo", size=12), border_width=0)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

    # ---- small builders ----------------------------------------------------
    def _card(self, parent, title, pack=True):
        card = ctk.CTkFrame(parent, fg_color=CARD, border_color=BORDER, border_width=1, corner_radius=12)
        if pack:
            card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card, text=title, text_color=BLUE_DK,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))
        return card

    def _field(self, parent, label, var, show=None):
        ctk.CTkLabel(parent, text=label, text_color=MUTE,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14)
        ctk.CTkEntry(parent, textvariable=var, show=show, fg_color="white", border_color=BORDER,
                     text_color=INK, height=34).pack(fill="x", padx=14, pady=(2, 8))

    def _btn(self, parent, text, cmd, danger=False, ghost=False):
        if ghost:
            return ctk.CTkButton(parent, text=text, command=cmd, fg_color="white", hover_color=BLUE_LT,
                                 text_color=BLUE, border_color=BLUE, border_width=1, height=36,
                                 font=ctk.CTkFont(size=13, weight="bold"))
        col, hov = (RED, "#9B1C1C") if danger else (BLUE, BLUE_DK)
        return ctk.CTkButton(parent, text=text, command=cmd, fg_color=col, hover_color=hov,
                             height=36, font=ctk.CTkFont(size=13, weight="bold"))

    def _stat_card(self, parent, col, title, big=False):
        f = ctk.CTkFrame(parent, fg_color=BLUE_LT, corner_radius=10)
        f.grid(row=0, column=col, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(f, text=title, text_color=MUTE, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=(8, 0))
        val = ctk.CTkLabel(f, text="-", text_color=INK,
                           font=ctk.CTkFont(size=20 if big else 13, weight="bold"))
        val.pack(anchor="w", padx=12, pady=(0, 10))
        return val

    # ---- data <-> widgets --------------------------------------------------
    def _populate(self):
        for key, var in self.param_vars.items():
            var.set(str(self.cfg.get(key, C.DEFAULTS[key])))
        self._on_mode()

    def _collect(self) -> dict:
        cfg = dict(self.cfg)
        cfg["client_id"] = self.client_var.get().strip()
        cfg["pin"] = self.pin_var.get().strip()
        cfg["totp_secret"] = self.totp_var.get().strip()
        cfg["access_token"] = self.token_var.get().strip()
        cfg["trade_ce"] = self.ce_var.get()
        cfg["trade_pe"] = self.pe_var.get()
        cfg["paper_mode"] = self.paper_var.get()
        for key, _l, typ in C.PARAM_FIELDS:
            raw = self.param_vars[key].get().strip()
            if typ == "int":
                cfg[key] = int(float(raw))
            elif typ == "float":
                cfg[key] = float(raw)
            else:
                cfg[key] = raw
        return cfg

    # ---- actions -----------------------------------------------------------
    def _on_mode(self):
        live = not self.paper_var.get()
        self.mode_badge.configure(text="LIVE" if live else "PAPER",
                                  fg_color=RED if live else "#0B5C2E")

    def _on_save(self):
        try:
            self.cfg = self._collect()
            path = C.save_config(self.cfg)
            self.save_note.configure(text="Saved", text_color=GREEN)
            self.log(f"Settings saved to {path}")
        except ValueError:
            self.save_note.configure(text="Check numeric fields", text_color=RED)
        self.after(2200, lambda: self.save_note.configure(text=""))

    def _on_reset(self):
        self.cfg = C.reset_to_defaults()
        self._populate()
        self.ce_var.set(self.cfg["trade_ce"]); self.pe_var.set(self.cfg["trade_pe"])
        self.paper_var.set(self.cfg["paper_mode"]); self._on_mode()
        self.save_note.configure(text="Defaults restored (not yet saved)", text_color=AMBER)
        self.after(2600, lambda: self.save_note.configure(text=""))

    def _on_connect(self):
        cfg = self._collect()
        self.conn_status.configure(text="generating token...", text_color=AMBER)

        def work():
            from dhan_api import TokenManager
            tm = TokenManager(cfg["client_id"], cfg["pin"], cfg["totp_secret"],
                              cfg["access_token"], log=self.log)
            token = tm.ensure_token()
            if not token:
                self.after(0, lambda: self.conn_status.configure(text="token failed", text_color=RED))
                return
            self.token_var.set(token)            # show/keep the working token
            self.api = DhanAPI(cfg["client_id"], log=self.log)
            self.api.set_token(token)
            ok = self.api.verify()
            if ok:
                C.save_token(token)              # persist so next run reuses it (no TOTP)
                self.cfg["access_token"] = token
            self.after(0, lambda: self.conn_status.configure(
                text="connected" if ok else "verify failed", text_color=GREEN if ok else RED))
        threading.Thread(target=work, daemon=True).start()

    def _on_start(self):
        try:
            cfg = self._collect()
        except ValueError:
            self.log("Cannot start: check that all numeric parameters are valid."); return
        if self.api is None or not self.api.connected:
            self.log("Connect first - a valid token is needed for the option chain and live feed "
                     "(required even in PAPER mode)."); return
        self.engine = StrategyEngine(self.api, cfg, on_log=self.log,
                                     on_status=self._on_status, on_trade=self._on_trade)
        self.engine.start()
        self.start_btn.configure(state="disabled"); self.stop_btn.configure(state="normal")
        self.phase_lbl.configure(text="running", text_color=GREEN)

    def _on_stop(self):
        if self.engine:
            self.engine.stop()
        self.stop_btn.configure(state="disabled")

    # ---- engine callbacks (marshalled to the Tk thread) --------------------
    def log(self, msg: str):
        def _w():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _w)

    def _on_status(self, st: dict):
        def _w():
            if not st.get("running"):
                self.start_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
                self.phase_lbl.configure(text="idle", text_color=MUTE)
            if "phase" in st:
                self.phase_lbl.configure(text=st["phase"], text_color=AMBER)
            if "total_pnl" in st:
                v = st["total_pnl"]
                self.pnl_label.configure(text=f"Rs {v:,.0f}",
                                         text_color=GREEN if v >= 0 else RED)
            for leg in st.get("legs", []):
                lbl = self.leg_labels.get(leg["right"])
                if lbl:
                    strike = int(leg["strike"]) if leg.get("strike") else "-"
                    ltp = f"{leg['ltp']:.1f}" if leg.get("ltp") else "-"
                    lbl.configure(text=f"{strike} {leg['status']}\nLTP {ltp}   P&L {leg['pnl']:,.0f}")
        self.after(0, _w)

    def _on_trade(self, t: dict):
        pass   # already logged; hook for a trades table later


if __name__ == "__main__":
    App().mainloop()
