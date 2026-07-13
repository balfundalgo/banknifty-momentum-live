"""
Dhan API layer for the BANKNIFTY Momentum trader.
Matches the proven Balfund Renko pattern: raw Dhan v2 REST (no dhanhq SDK, so it
never breaks on SDK version bumps), TOTP token manager (verify -> renew -> generate),
option-chain strike resolution, REST order placement, and the binary WebSocket feed
for live LTP.
"""
from __future__ import annotations
import json
import struct
import threading
import time
from datetime import datetime, date
from typing import Optional

import requests

BASE_URL = "https://api.dhan.co/v2"
AUTH_GENERATE_URL = "https://auth.dhan.co/app/generateAccessToken"
AUTH_RENEW_URL = "https://api.dhan.co/v2/RenewToken"
AUTH_VERIFY_URL = "https://api.dhan.co/v2/profile"
WS_URL = "wss://api-feed.dhan.co?version=2&token={token}&clientId={cid}&authType=2"

IDX_SEG = "IDX_I"
FNO_SEG = "NSE_FNO"
BANKNIFTY_IDX_SID = 25
REQ_SUB_TICKER = 15
RESP_TICKER = 2


class TokenManager:
    def __init__(self, client_id, pin, totp_secret, existing_token="", log=print):
        self.client_id = str(client_id).strip()
        self.pin = str(pin).strip()
        self.totp_secret = str(totp_secret).strip()
        self.existing_token = str(existing_token).strip()
        self.log = log

    def verify(self, token) -> bool:
        if not token:
            return False
        try:
            r = requests.get(AUTH_VERIFY_URL,
                             headers={"access-token": token, "client-id": self.client_id}, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def renew(self, token) -> Optional[str]:
        try:
            d = requests.get(AUTH_RENEW_URL,
                             headers={"access-token": token, "dhanClientId": self.client_id,
                                      "Content-Type": "application/json"}, timeout=15).json()
            return d.get("accessToken")
        except Exception:
            return None

    def generate(self, max_retries=2) -> Optional[str]:
        try:
            import pyotp
        except ImportError:
            self.log("pyotp not installed - cannot auto-generate token.")
            return None
        for a in range(max_retries):
            rem = 30 - (int(time.time()) % 30)
            if a > 0 or rem < 10:           # use a fresh TOTP window, not one about to expire
                time.sleep(rem + 1)
            totp = pyotp.TOTP(self.totp_secret).now()
            self.log(f"TOTP attempt {a+1}: {totp}")
            try:
                d = requests.post(AUTH_GENERATE_URL,
                                  params={"dhanClientId": self.client_id, "pin": self.pin,
                                          "totp": totp}, timeout=15).json()
                if "accessToken" in d:
                    return d["accessToken"]
                msg = str(d.get("message", ""))
                self.log(f"Generate failed: {d}")
                if "2 minute" in msg or "once every" in msg:
                    self.log("Dhan allows one token every 2 minutes. Wait ~2 min and retry, "
                             "or paste an existing Access Token.")
                    return None             # do NOT hammer the rate limit
                # otherwise (e.g. invalid TOTP) loop waits for a fresh window and retries once
            except Exception as e:
                self.log(f"Generate error: {e}")
                return None
        return None

    def ensure_token(self) -> Optional[str]:
        if self.existing_token:
            if self.verify(self.existing_token):
                self.log("Existing token valid.")
                return self.existing_token
            r = self.renew(self.existing_token)
            if r:
                self.log("Token renewed.")
                return r
        return self.generate()


class DhanAPI:
    def __init__(self, client_id, log=print):
        self.client_id = str(client_id).strip()
        self.log = log
        self.token = ""
        self.headers = {}
        self.connected = False
        self._ltps: dict[str, float] = {}
        self._sub: set[str] = set()
        self._chain_cache = None          # (expiry, ts, oc) -- shared by CE+PE arming
        self._last_tick = 0.0             # feed health: time of the last WS tick
        self._blocked = False
        self._err_logged = False
        self._rest_cache: dict[str, tuple] = {}   # sid -> (ts, ltp) for the REST fallback
        self._rest_last = 0.0
        self._ws = None
        self._ws_thread = None
        self._ws_open = threading.Event()
        self._stop = threading.Event()

    def set_token(self, token: str):
        self.token = str(token).strip()
        self.headers = {"Content-Type": "application/json", "Accept": "application/json",
                        "access-token": self.token, "client-id": self.client_id}

    def verify(self) -> bool:
        try:
            r = requests.get(AUTH_VERIFY_URL,
                             headers={"access-token": self.token, "client-id": self.client_id}, timeout=10)
            self.connected = (r.status_code == 200)
            return self.connected
        except Exception as e:
            self.log(f"Verify error: {e}")
            return False

    def _post(self, ep, payload, retries=2):
        for a in range(retries + 1):
            try:
                r = requests.post(f"{BASE_URL}{ep}", headers=self.headers, json=payload, timeout=15)
                if r.status_code == 200:
                    return r.json()
                self.log(f"API {ep} -> {r.status_code}: {r.text[:160]}")
            except Exception as e:
                self.log(f"API {ep}: {e}")
            if a < retries:
                time.sleep(1)
        return None

    def _get(self, ep, retries=2):
        for a in range(retries + 1):
            try:
                r = requests.get(f"{BASE_URL}{ep}", headers=self.headers, timeout=15)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            if a < retries:
                time.sleep(1)
        return None

    def nearest_expiry(self) -> Optional[str]:
        resp = self._post("/optionchain/expirylist",
                          {"UnderlyingScrip": BANKNIFTY_IDX_SID, "UnderlyingSeg": IDX_SEG}, retries=1)
        if not resp or resp.get("status") != "success":
            return None
        today = date.today()
        valid = []
        for e in resp.get("data", []):
            try:
                d = datetime.strptime(e, "%Y-%m-%d").date()
                if d >= today:
                    valid.append((d, e))
            except Exception:
                pass
        valid.sort()
        if not valid:
            return None
        first = valid[0][0]
        same_month = [e for d, e in valid if (d.year, d.month) == (first.year, first.month)]
        return same_month[-1] if same_month else valid[0][1]

    def _chain(self, expiry: str):
        """Fetch /optionchain at most once per ~4s (it returns ALL strikes + both CE/PE),
        so arming CE then PE reuses ONE response. The endpoint is rate-limited (~1 req/3s,
        and aggressive 805 blocking), so no retries here."""
        now = time.time()
        c = self._chain_cache
        if c and c[0] == expiry and (now - c[1]) < 4.0:
            return c[2]
        resp = self._post("/optionchain",
                          {"UnderlyingScrip": BANKNIFTY_IDX_SID, "UnderlyingSeg": IDX_SEG,
                           "Expiry": expiry}, retries=0)
        if not resp or resp.get("status") != "success":
            return None
        oc = resp["data"]["oc"]
        self._chain_cache = (expiry, now, oc)
        return oc

    def pick_option_by_premium(self, expiry: str, right: str, target_premium: float):
        oc = self._chain(expiry)
        if not oc:
            return None
        ok = "ce" if right.upper().startswith("C") else "pe"
        best = None
        for strike_str, node in oc.items():
            leg = node.get(ok)
            if not leg:
                continue
            ltp = float(leg.get("last_price", 0) or 0)
            sid = str(leg.get("security_id", "") or "")
            if ltp <= 0 or not sid:
                continue
            d = abs(ltp - target_premium)
            if best is None or d < best[3]:
                best = (sid, float(strike_str), ltp, d)
        return best[:3] if best else None

    def place_market(self, security_id, side, qty, paper, max_retries=3):
        if paper:
            self.log(f"[PAPER] {side} {qty} sid={security_id}")
            return f"PAPER-{datetime.now():%H%M%S}", float(self.last_ltp(security_id) or 0)
        payload = {"dhanClientId": self.client_id, "transactionType": side,
                   "exchangeSegment": FNO_SEG, "productType": "INTRADAY", "orderType": "MARKET",
                   "validity": "DAY", "securityId": str(security_id), "quantity": int(qty),
                   "price": 0, "triggerPrice": 0, "disclosedQuantity": 0, "afterMarketOrder": False}
        order_id = None
        for a in range(max_retries):
            resp = self._post("/orders", payload, retries=0)
            if isinstance(resp, list):
                resp = resp[0] if resp else {}
            if resp and resp.get("orderId"):
                order_id = str(resp["orderId"]); break
            self.log(f"Order failed: {resp.get('errorMessage') if resp else 'no response'}")
            time.sleep(1)
        if not order_id:
            return None, 0.0
        fill = 0.0
        for _ in range(10):
            time.sleep(0.5)
            tr = self._get(f"/trades/{order_id}", retries=0)
            tl = tr if isinstance(tr, list) else ([tr] if tr else [])
            tq = tv = 0.0
            for t in tl:
                if isinstance(t, dict):
                    q = int(t.get("tradedQuantity", 0))
                    tq += q; tv += q * float(t.get("tradedPrice", 0))
            if tq > 0:
                fill = tv / tq; break
        return order_id, fill

    def get_positions(self):
        resp = self._get("/positions")
        return [p for p in resp if isinstance(p, dict) and int(p.get("netQty", 0)) != 0] \
            if isinstance(resp, list) else []

    def start_feed(self):
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._stop.clear()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()

    def stop_feed(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def subscribe(self, security_id):
        sid = str(security_id)
        self._sub.add(sid)
        if self._ws_open.is_set():
            self._send_sub([sid])

    def last_ltp(self, security_id) -> Optional[float]:
        """Live price. Prefers the WS feed; if the feed is stale/down (no tick for
        >8s), falls back to the REST quote so the strategy never goes blind."""
        sid = str(security_id)
        fresh = (time.time() - getattr(self, "_last_tick", 0)) < 8.0
        px = self._ltps.get(sid)
        if px is not None and fresh:
            return px
        rp = self.rest_ltp(sid)          # feed is stale -> poll REST
        return rp if rp is not None else px

    def rest_ltp(self, security_id) -> Optional[float]:
        """REST LTP via /marketfeed/ltp. Throttled to <=1 call/sec across all
        instruments (Dhan limit) and cached, so the fallback can't trip 429."""
        sid = str(security_id)
        now = time.time()
        c = self._rest_cache.get(sid)
        if c and (now - c[0]) < 1.2:
            return c[1]
        if (now - self._rest_last) < 1.0:        # global throttle
            return c[1] if c else None
        self._rest_last = now
        resp = self._post("/marketfeed/ltp", {FNO_SEG: [int(s) for s in self._sub]}, retries=0)
        if not resp or resp.get("status") != "success":
            return c[1] if c else None
        data = (resp.get("data") or {}).get(FNO_SEG, {}) or {}
        out = None
        for k, v in data.items():
            try:
                px = float(v.get("last_price", 0) or 0)
                if px > 0:
                    self._rest_cache[str(k)] = (now, px)
                    if str(k) == sid:
                        out = px
            except Exception:
                pass
        return out

    def _send_sub(self, sids):
        try:
            self._ws.send(json.dumps({
                "RequestCode": REQ_SUB_TICKER, "InstrumentCount": len(sids),
                "InstrumentList": [{"ExchangeSegment": FNO_SEG, "SecurityId": str(s)} for s in sids]}))
        except Exception as e:
            self.log(f"WS subscribe error: {e}")

    def _ws_loop(self):
        import websocket
        url = WS_URL.format(token=self.token, cid=self.client_id)
        backoff = 5.0            # grows on repeated failures; resets after a healthy session
        while not self._stop.is_set():
            self._blocked = False
            self._err_logged = False
            started = time.time()
            try:
                def _open(ws):
                    self._ws_open.set()
                    self._last_tick = time.time()
                    self.log("Live feed connected.")
                    if self._sub:
                        self._send_sub(list(self._sub))

                def _msg(ws, message):
                    if isinstance(message, str) or len(message) < 8:
                        return
                    code = message[0]
                    sid = str(struct.unpack_from("<I", message, 4)[0])
                    if code == RESP_TICKER:
                        self._ltps[sid] = float(struct.unpack_from("<f", message, 8)[0])
                        self._last_tick = time.time()

                def _err(ws, err):
                    txt = str(err)
                    if "429" in txt or "Too many requests" in txt or "blocked" in txt:
                        self._blocked = True
                    if not self._err_logged:          # one line per cycle, not hundreds
                        self._err_logged = True
                        self.log(f"Feed disconnected: {txt[:110]}")

                def _close(ws, *a):
                    self._ws_open.clear()

                self._ws = websocket.WebSocketApp(url, on_open=_open, on_message=_msg,
                                                  on_error=_err, on_close=_close)
                # NOTE: NO client-side ping. Dhan's feed does not reliably answer WS ping
                # frames, and our 20s keepalive was killing a healthy socket every time.
                self._ws.run_forever(ping_interval=0)
            except Exception as e:
                self.log(f"WS loop: {e}")

            if self._stop.is_set():
                break

            if (time.time() - started) > 60:
                backoff = 5.0                      # healthy session -> reset
            if self._blocked:
                backoff = max(backoff, 90.0)       # Dhan is blocking: back right off
                self.log(f"Dhan is rate-limiting this client id. Waiting {backoff:.0f}s. "
                         f"(Strategy keeps running on REST prices.)")
            else:
                backoff = min(backoff * 1.8, 120.0)
                self.log(f"Reconnecting to feed in {backoff:.0f}s...")
            self._stop.wait(backoff)              # interruptible sleep
