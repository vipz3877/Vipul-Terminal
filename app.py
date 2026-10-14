"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v11.0 INSTITUTIONAL MASTER EDITION
Analysis terminal:
 - Dual-Force Engine (net-capitulation aware, scale-invariant across indices)
 - Dealer Positioning Engine: Signed Net GEX, interpolated Zero-Gamma Flip, Net Vanna (VEX) & Charm (CEX)
 - Put-Call Parity Synthetic Future (F_syn), Live Basis, 25-Delta Risk Reversal (RR25) & Butterfly (BF25)
 - Interactive GEX, Vanna/Charm, IV Skew & Microstructure Visual Profiles
 - Background Dhan v2 Binary WebSocket Worker + Lee-Ready Tick Rule & Bid-Ask Quote-Location Aggression
 - Automated SQLite WAL Signal Calibration Ledger (Live MFE / MAE / R-Multiple & Regime Win-Rate Grader)
 - Exhaustion Monitor + Day Movement Levels + Black-Scholes Scenario Scanner + 5-Agent Groq AI Council
No order execution features: this app places no orders.
"""

import os
import re
import json
import math
import html
import time
import struct
import sqlite3
import hashlib
import tempfile
import threading
from collections import deque
import xml.etree.ElementTree as ET
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import websocket as ws_client
except ImportError:
    ws_client = None

st.set_page_config(
    page_title="Vipul Bloomberg Terminal v11.0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>
.stApp { background-color:#000; color:#ff9800; font-family:'Courier New',monospace; }
section[data-testid="stSidebar"] { background-color:#0b0b0b; border-right:1px solid #333; }
h1,h2,h3,h4 { color:#ff9800 !important; font-family:'Courier New',monospace; letter-spacing:1px; }
[data-testid="stMetricValue"] { color:#fff; }
[data-testid="stMetricLabel"] { color:#ff9800; }
.bbg-panel { border:1px solid #333; background:#0f0f0f; padding:14px; border-radius:4px; margin-bottom:12px; }
.bbg-title { font-size:12px; color:#888; letter-spacing:2px; text-transform:uppercase; }
.bbg-big { font-size:26px; font-weight:800; }
.bbg-desc { font-size:14px; margin-top:6px; color:#ddd; }
.agent { font-size:13px; color:#ddd; margin:4px 0; }
.agent b { color:#ff9800; }
@keyframes flashGreen { 0%,100% {box-shadow:0 0 0 rgba(0,230,118,0);} 50% {box-shadow:0 0 28px rgba(0,230,118,.9);} }
@keyframes flashRed   { 0%,100% {box-shadow:0 0 0 rgba(255,23,68,0);} 50% {box-shadow:0 0 28px rgba(255,23,68,.9);} }
@keyframes flashAmber { 0%,100% {box-shadow:0 0 0 rgba(255,171,0,0);} 50% {box-shadow:0 0 22px rgba(255,171,0,.8);} }
.flash-green { animation: flashGreen 1s infinite; }
.flash-red   { animation: flashRed 1s infinite; }
.flash-amber { animation: flashAmber 1.4s infinite; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# CONFIG
# ============================================================
def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def clean(s):
    """Remove whitespace, quotes and an accidental 'Bearer ' prefix."""
    s = (s or "").strip().strip('"').strip("'").strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s.replace("\n", "").replace("\r", "").replace(" ", "")


DEFAULT_CLIENT_ID = clean(get_secret("DHAN_CLIENT_ID", "1108425500"))
DEFAULT_DHAN_TOKEN = clean(get_secret("DHAN_ACCESS_TOKEN", ""))
DEFAULT_GROQ_KEY = get_secret("GROQ_API_KEY", "").strip()
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

BASE_URL = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{BASE_URL}/optionchain"
EXPIRY_URL = f"{BASE_URL}/optionchain/expirylist"
PROFILE_URL = f"{BASE_URL}/profile"

YEAR_DAYS = 365
TIME_BASIS = {"mode": "calendar"}   # "calendar": T = time/365; "trading": 252 days x 375 min
SKEW_COEF = 0.5                      # IV change per 1 sigma of spot move = SKEW_COEF x IV shock
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0   # Dhan option chain limit: ~1 request / 3 sec
MIN_RR = 1.0              # minimum reward:risk to accept a BUY/SELL
MIN_FLOW_PCT = 2.0        # a side's OI change must be >= this % of its near-spot total OI to count
NOISE_PCT = 0.5           # ignore single strikes whose change is < this % of that side's near-spot OI
DOMINANCE = 1.5           # winning force must be >= this multiple of the opposing force
MIN_CONFIRM = 2           # option-chain checks must net-agree with a BUY/SELL by at least this much
TRAP_STEP_MULT = 0.7      # scale-invariant buffer: 0.7 * step (35 pts on Nifty, 70 on BankNifty, 18 on MidcpNifty)
TRAP_MIN_OI_PCT = 2.5     # single-strike build must be >= 2.5% of near-spot side OI to flag a trap
VEL_TRIGGER_PCT = 1.5     # 3-min unwind >= 1.5% of near-spot OI triggers AI re-evaluation
FLOW_EDGE_PCT = 2.0       # near-spot net flow gap (PE% vs CE%) needed to count as directional
IST = timezone(timedelta(hours=5, minutes=30))

# ---- exhaustion monitor ----
HIST_MIN_GAP_SEC = 60     # minimum spacing between stored snapshots
HIST_MAX = 400            # snapshots kept per session/day
OI_MAP_SPAN = 800         # strikes within +/- this many points of spot are stored for lookback OI deltas
EXH_CONTEXT_SIGMA = 0.4   # a trend must be extended at least this many daily-sigmas to be "exhaustible"
EXH_WATCH = 35            # exhaustion score (0-100) for "early signs"
EXH_ALERT = 60            # exhaustion score for "watch / confirmed"

# ---- premium scanner ----
RISK_FREE = 0.065         # annual rate used in the Black-Scholes / Put-Call Parity engine
MIN_PREMIUM = 5.0         # ignore options trading below this premium (points)

AI_RETRY_SEC = 60             # minimum gap between AI calls after a failure
AI_VERDICT_MAX_AGE_SEC = 600   # never present an AI verdict older than 10 minutes as current

# ---- live news ----
NEWS_MAX_AGE_H = 24       # headlines older than this are ignored
NEWS_TOP_N = 10           # headlines sent to the AI / shown on screen
NEWS_MIN_GAP_SEC = 600    # a news change can re-trigger the AI at most this often
NEWS_EXTRA_FEEDS = tuple(u.strip() for u in get_secret("NEWS_EXTRA_FEEDS", "").split(",") if u.strip())

INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}


def step_buffer(step):
    """Scale-invariant point buffer (35 pts for step=50, 70 pts for step=100, 18 pts for step=25)."""
    return max(10, round(TRAP_STEP_MULT * step))


def auth_headers(token, client_id):
    return {"Content-Type": "application/json", "Accept": "application/json",
            "access-token": token, "client-id": client_id}


def check(r):
    """Raise with Dhan's real error body, not just the status code."""
    if r.ok:
        return
    hint = ""
    if r.status_code == 401:
        hint = (" -> token expired/invalid, or client-id does not match the token. "
                "Make sure you pasted the ACCESS TOKEN (JWT starting with 'eyJ'), not the API key.")
    elif r.status_code == 429:
        hint = " -> rate limited, wait a few seconds."
    raise RuntimeError(f"HTTP {r.status_code} | {r.text[:300]}{hint}")


# ============================================================
# BACKGROUND DHAN v2 WEBSOCKET + LEE-READY MICROSTRUCTURE WORKER
# ============================================================
class DhanLiveStreamWorker:
    """
    Thread-safe background worker for Dhan v2 Live Marketfeed WebSocket (wss://api-feed.dhan.co).
    Decodes binary Ticker (code 2), Quote (code 4), and Full Depth (code 8) packets, and applies
    the Lee-Ready Tick Rule to classify aggressive buyer vs. seller initiation.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.connected = False
        self.thread = None
        self.ws = None
        self.target_key = ""
        self.last_err = ""
        self.last_msg_ts = 0.0
        self.packet_count = 0
        self.latest_ltp = 0.0
        self.last_dir = 1
        self.buy_ticks = 0
        self.sell_ticks = 0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.obi_depth = 0.0
        self.ticks = deque(maxlen=250)

    def start_or_update(self, token, client_id, scrip, seg):
        if ws_client is None:
            with self.lock:
                self.last_err = "websocket-client package not installed (using Chain Microstructure fallback)"
            return
        key = f"{client_id}:{scrip}:{seg}:{token[:12]}"
        with self.lock:
            if self.running and self.target_key == key:
                return
            self.target_key = key
            self.running = True
            self.connected = False
            self.buy_ticks = 0
            self.sell_ticks = 0
            self.buy_vol = 0.0
            self.sell_vol = 0.0
            self.ticks.clear()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.thread = threading.Thread(
            target=self._run_loop, args=(token, client_id, scrip, seg, key), daemon=True
        )
        self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            self.connected = False
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass

    def _run_loop(self, token, client_id, scrip, seg, key):
        url = f"wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"

        def on_open(ws):
            with self.lock:
                self.connected = True
                self.last_err = ""
            sub_msg = {
                "RequestCode": 15,  # 15 = Ticker/Quote stream for index & derivatives
                "InstrumentCount": 1,
                "InstrumentList": [{"ExchangeSegment": seg, "SecurityId": str(scrip)}],
            }
            try:
                ws.send(json.dumps(sub_msg))
            except Exception as exc:
                with self.lock:
                    self.last_err = f"WS send error: {exc}"

        def on_message(_ws, message):
            if not isinstance(message, (bytes, bytearray)) or len(message) < 8:
                return
            try:
                feed_code, _msg_len, _ex_seg, _sec_id = struct.unpack("<BHBI", message[:8])
                now_ts = time.time()
                if feed_code == 2 and len(message) >= 16:
                    # Ticker Packet: float32 LTP (8..12), uint32 LTT (12..16)
                    ltp, _ltt = struct.unpack("<fI", message[8:16])
                    self._record_tick(ltp, 1.0, None, None, now_ts)
                elif feed_code in (4, 8) and len(message) >= 34:
                    # Quote / Full Packet: LTP, LTQ, LTT, ATP, Vol, TotBuyQty, TotSellQty
                    ltp, ltq, _ltt, _atp, _vol, tot_buy, tot_sell = struct.unpack("<fHIfIII", message[8:34])
                    bid_p = ask_p = None
                    if feed_code == 8 and len(message) >= 162:
                        # First 20-byte depth level starts at offset 62
                        _bq, _aq, _bo, _ao, bid_p, ask_p = struct.unpack("<IIHHff", message[62:82])
                    with self.lock:
                        denom = float(tot_buy + tot_sell)
                        if denom > 0:
                            self.obi_depth = (float(tot_buy) - float(tot_sell)) / denom
                    self._record_tick(ltp, float(max(ltq, 1)), bid_p, ask_p, now_ts)
            except Exception as exc:
                with self.lock:
                    self.last_err = f"Packet decode: {exc}"

        def on_error(_ws, error):
            with self.lock:
                self.connected = False
                self.last_err = str(error)[:160]

        def on_close(_ws, _code, _msg):
            with self.lock:
                self.connected = False

        while True:
            with self.lock:
                if not self.running or self.target_key != key:
                    break
            try:
                self.ws = ws_client.WebSocketApp(
                    url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
                )
                self.ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_err = str(exc)[:160]
            time.sleep(5.0)

    def _record_tick(self, ltp, qty, bid, ask, ts):
        if not math.isfinite(ltp) or ltp <= 0:
            return
        with self.lock:
            # Lee-Ready Tick Classification: Quote rule first, then Tick-Test rule
            if bid and ask and bid > 0 and ask >= bid:
                mid = 0.5 * (bid + ask)
                if ltp > mid:
                    d = 1
                elif ltp < mid:
                    d = -1
                else:
                    d = 1 if ltp > self.latest_ltp else -1 if ltp < self.latest_ltp else self.last_dir
            else:
                if self.latest_ltp > 0:
                    d = 1 if ltp > self.latest_ltp else -1 if ltp < self.latest_ltp else self.last_dir
                else:
                    d = 1
            self.latest_ltp = round(float(ltp), 2)
            self.last_dir = d
            self.last_msg_ts = ts
            self.packet_count += 1
            if d > 0:
                self.buy_ticks += 1
                self.buy_vol += qty
            else:
                self.sell_ticks += 1
                self.sell_vol += qty
            self.ticks.append({"ts": ts, "ltp": self.latest_ltp, "dir": d, "qty": qty})

    def snapshot(self):
        with self.lock:
            tot_t = self.buy_ticks + self.sell_ticks
            delta_pct = ((self.buy_ticks - self.sell_ticks) / tot_t * 100.0) if tot_t > 0 else 0.0
            return {
                "connected": self.connected,
                "running": self.running,
                "packet_count": self.packet_count,
                "latest_ltp": self.latest_ltp,
                "buy_ticks": self.buy_ticks,
                "sell_ticks": self.sell_ticks,
                "tick_delta_pct": round(delta_pct, 1),
                "obi_depth": round(self.obi_depth, 3),
                "last_age_sec": round(time.time() - self.last_msg_ts, 1) if self.last_msg_ts else None,
                "last_err": self.last_err,
                "recent_ticks": list(self.ticks)[-30:],
            }


@st.cache_resource(show_spinner=False)
def get_ws_worker():
    return DhanLiveStreamWorker()


def compute_chain_microstructure(near, spot, step):
    """
    Chain-native Order Book / Quote-Location Microstructure (works even without WebSocket).
    Computes where traded LTP sits inside the [Bid, Ask] spread across near-ATM Calls and Puts:
      Quote Location = (LTP - Bid) / (Ask - Bid) in [0, 1].
      > 0.55 means takers are aggressively lifting the Ask; < 0.45 means takers are hitting the Bid.
    """
    atm_band = near[(near["Strike"] >= spot - 3 * step) & (near["Strike"] <= spot + 3 * step)].copy()
    if atm_band.empty:
        atm_band = near.copy()

    def side_aggression(side):
        locs, weights = [], []
        spreads = []
        for _, r in atm_band.iterrows():
            bid, ask, ltp = float(r[f"{side}_Bid"]), float(r[f"{side}_Ask"]), float(r[f"{side}_LTP"])
            vol = max(float(r[f"{side}_Vol"]), 1.0)
            if bid > 0 and ask > bid and ltp > 0:
                loc = min(1.0, max(0.0, (ltp - bid) / (ask - bid)))
                locs.append(loc * vol)
                weights.append(vol)
                spreads.append((ask - bid) / (0.5 * (ask + bid)) * 100.0)
        agg = (sum(locs) / sum(weights)) if weights else 0.5
        med_sp = float(np.median(spreads)) if spreads else 0.0
        return round(agg, 3), round(med_sp, 2)

    ce_agg, ce_sp = side_aggression("CE")
    pe_agg, pe_sp = side_aggression("PE")
    # Net Option-Taker Flow: Calls lifted at Ask (+) vs Puts lifted at Ask (-)
    net_taker = round((ce_agg - pe_agg) * 100.0, 1)
    if net_taker >= 8.0:
        label = "CALL TAKERS LIFTING ASK (Bullish Aggression)"
    elif net_taker <= -8.0:
        label = "PUT TAKERS LIFTING ASK (Bearish Aggression)"
    else:
        label = "BALANCED QUOTE SPREAD"
    return {
        "ce_quote_loc": ce_agg,
        "pe_quote_loc": pe_agg,
        "ce_med_spread_pct": ce_sp,
        "pe_med_spread_pct": pe_sp,
        "net_taker_edge": net_taker,
        "label": label,
    }


# ============================================================
# SQLITE WAL SIGNAL CALIBRATION & MFE/MAE TELEMETRY LEDGER
# ============================================================
def _db_path():
    return os.path.join(tempfile.gettempdir(), "bbg_v11_signal_audit.sqlite")


def init_signal_db():
    conn = sqlite3.connect(_db_path(), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                date_ist TEXT NOT NULL,
                time_ist TEXT NOT NULL,
                idx_name TEXT NOT NULL,
                expiry TEXT NOT NULL,
                source TEXT NOT NULL,
                direction TEXT NOT NULL,
                gex_regime TEXT,
                status_label TEXT,
                confidence INTEGER,
                entry_spot REAL NOT NULL,
                target_spot REAL NOT NULL,
                sl_spot REAL NOT NULL,
                risk_pts REAL NOT NULL,
                high_seen REAL NOT NULL,
                low_seen REAL NOT NULL,
                last_spot REAL NOT NULL,
                mfe_pts REAL DEFAULT 0.0,
                mae_pts REAL DEFAULT 0.0,
                mfe_r REAL DEFAULT 0.0,
                mae_r REAL DEFAULT 0.0,
                realized_r REAL DEFAULT 0.0,
                outcome TEXT DEFAULT 'OPEN',
                closed_ts REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def log_and_reconcile_signals(idx_name, expiry, m, sig, now_ist):
    """
    1) Updates MFE, MAE, and outcome (TARGET_HIT, SL_HIT, EOD_CLOSED) for all OPEN signals on this index.
    2) Logs any new risk-validated AI signal or Dual-Force GAMMA BLAST signal (deduplicated over 30 min).
    """
    init_signal_db()
    spot = float(m["spot"])
    today_str = now_ist.date().isoformat()
    time_str = now_ist.strftime("%H:%M:%S")
    now_ts = now_ist.timestamp()
    after_close = (now_ist.hour * 60 + now_ist.minute) >= (15 * 60 + 30)

    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Reconcile open signals for this index
        open_rows = conn.execute(
            "SELECT * FROM signal_audit WHERE idx_name = ? AND outcome = 'OPEN'", (idx_name,)
        ).fetchall()
        for r in open_rows:
            hi = max(float(r["high_seen"]), spot)
            lo = min(float(r["low_seen"]), spot)
            entry = float(r["entry_spot"])
            target = float(r["target_spot"])
            sl = float(r["sl_spot"])
            risk = max(float(r["risk_pts"]), 1.0)
            is_buy = r["direction"] == "BUY"

            mfe = max(0.0, (hi - entry) if is_buy else (entry - lo))
            mae = max(0.0, (entry - lo) if is_buy else (hi - entry))
            mfe_r = round(mfe / risk, 2)
            mae_r = round(mae / risk, 2)

            outcome = "OPEN"
            realized_r = round(((spot - entry) if is_buy else (entry - spot)) / risk, 2)
            closed_ts = None

            if is_buy:
                if spot >= target:
                    outcome, realized_r, closed_ts = "TARGET_HIT", round(abs(target - entry) / risk, 2), now_ts
                elif spot <= sl:
                    outcome, realized_r, closed_ts = "SL_HIT", -1.0, now_ts
            else:
                if spot <= target:
                    outcome, realized_r, closed_ts = "TARGET_HIT", round(abs(entry - target) / risk, 2), now_ts
                elif spot >= sl:
                    outcome, realized_r, closed_ts = "SL_HIT", -1.0, now_ts

            if outcome == "OPEN" and (r["date_ist"] < today_str or after_close):
                outcome, closed_ts = "EOD_CLOSED", now_ts

            conn.execute("""
                UPDATE signal_audit
                SET high_seen=?, low_seen=?, last_spot=?, mfe_pts=?, mae_pts=?,
                    mfe_r=?, mae_r=?, realized_r=?, outcome=?, closed_ts=?
                WHERE id=?
            """, (hi, lo, spot, round(mfe, 2), round(mae, 2), mfe_r, mae_r, realized_r, outcome, closed_ts, r["id"]))

        # Helper to insert a new signal if not recently logged
        def _maybe_insert(source, direction, target, sl, conf):
            if direction not in ("BUY", "SELL") or target is None or sl is None:
                return
            recent = conn.execute("""
                SELECT id FROM signal_audit
                WHERE idx_name=? AND expiry=? AND source=? AND direction=?
                  AND (outcome='OPEN' OR (? - ts) < 1800)
            """, (idx_name, expiry, source, direction, now_ts)).fetchone()
            if recent is not None:
                return
            risk_pts = max(abs(spot - float(sl)), 1.0)
            conn.execute("""
                INSERT INTO signal_audit (
                    ts, date_ist, time_ist, idx_name, expiry, source, direction,
                    gex_regime, status_label, confidence, entry_spot, target_spot,
                    sl_spot, risk_pts, high_seen, low_seen, last_spot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_ts, today_str, time_str, idx_name, str(expiry), source, direction,
                m.get("gex_regime", ""), m.get("status", ""), int(conf),
                spot, float(target), float(sl), round(risk_pts, 2), spot, spot, spot
            ))

        # 2. Log Risk-Validated AI Council signal if active
        if sig and sig.get("signal") in ("BUY", "SELL"):
            _maybe_insert("AI_VALIDATED", sig["signal"], sig["target"], sig["sl"], sig.get("confidence", 0))

        # 3. Log Dual-Force Engine Gamma Blast if active and structural walls exist
        if m.get("engine_signal") in ("BUY", "SELL"):
            res, sup = m["struct"]["res"], m["struct"]["sup"]
            buf = m["buf"]
            if res and sup:
                if m["engine_signal"] == "BUY":
                    t_lvl, s_lvl = res["level"], sup["level"] - buf
                else:
                    t_lvl, s_lvl = sup["level"], res["level"] + buf
                _maybe_insert("ENGINE_BLAST", m["engine_signal"], t_lvl, s_lvl, m.get("score", 50))

        conn.commit()
    finally:
        conn.close()


def fetch_signal_ledger(idx_name=None):
    init_signal_db()
    conn = sqlite3.connect(_db_path(), timeout=10)
    try:
        if idx_name and idx_name != "ALL":
            df = pd.read_sql_query(
                "SELECT * FROM signal_audit WHERE idx_name=? ORDER BY id DESC LIMIT 200",
                conn, params=(idx_name,)
            )
        else:
            df = pd.read_sql_query("SELECT * FROM signal_audit ORDER BY id DESC LIMIT 200", conn)
        return df
    finally:
        conn.close()


def clear_signal_ledger():
    init_signal_db()
    conn = sqlite3.connect(_db_path(), timeout=10)
    try:
        conn.execute("DELETE FROM signal_audit")
        conn.commit()
    finally:
        conn.close()


# ============================================================
# DATA FETCHERS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])


def enforce_global_api_throttle(min_interval=None):
    """Space option-chain calls by MIN_CHAIN_GAP_SEC across ALL browser sessions on this server (exclusive file lock)."""
    min_interval = MIN_CHAIN_GAP_SEC if min_interval is None else min_interval
    path = os.path.join(tempfile.gettempdir(), "bbg_api_throttle.lock")
    try:
        import fcntl
    except ImportError:
        last = st.session_state.get("_last_chain_call", 0.0)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        st.session_state["_last_chain_call"] = time.time()
        return
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read().strip()
            last = float(raw) if raw else 0.0
            wait = min_interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            f.seek(0)
            f.truncate()
            f.write(str(time.time()))
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _n(x):
    """Numeric coercion for display/analytics. Missing values become 0, but presence is tracked separately."""
    try:
        if x is None or x == "":
            return 0.0
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _pick(obj, *keys):
    """Return the first non-null alias without confusing a real numeric zero with a missing field."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] is not None and obj[key] != "":
            return obj[key]
    lower = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value is not None and value != "":
            return value
    return None


def _fetch_chain_raw(scrip, seg, expiry, token, client_id):
    enforce_global_api_throttle()

    r = requests.post(OPTIONCHAIN_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry},
                      timeout=12)
    check(r)
    data = r.json().get("data", {}) or {}
    spot = float(data.get("last_price") or 0)

    rows = []
    oc = data.get("oc") or data.get("option_chain") or {}
    if not isinstance(oc, dict):
        oc = {}
    for strike_str, item in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        ce = _pick(item, "ce", "CE", "call", "CALL") or {}
        pe = _pick(item, "pe", "PE", "put", "PUT") or {}
        ce_g = _pick(ce, "greeks", "Greeks") or {}
        pe_g = _pick(pe, "greeks", "Greeks") or {}
        ce_oi = _pick(ce, "oi", "open_interest", "openInterest")
        pe_oi = _pick(pe, "oi", "open_interest", "openInterest")
        ce_prev = _pick(ce, "previous_oi", "prev_oi", "previousOi", "prevOi")
        pe_prev = _pick(pe, "previous_oi", "prev_oi", "previousOi", "prevOi")
        ce_iv = _pick(ce, "implied_volatility", "iv", "impliedVolatility")
        pe_iv = _pick(pe, "implied_volatility", "iv", "impliedVolatility")
        rows.append({
            "Strike": strike,
            "CE_OI": _n(ce_oi), "PE_OI": _n(pe_oi),
            "CE_PrevOI": _n(ce_prev), "PE_PrevOI": _n(pe_prev),
            "CE_IV": _n(ce_iv), "PE_IV": _n(pe_iv),
            "CE_Gamma": _n(_pick(ce_g, "gamma")), "PE_Gamma": _n(_pick(pe_g, "gamma")),
            "CE_LTP": _n(_pick(ce, "last_price", "ltp", "lastPrice")),
            "PE_LTP": _n(_pick(pe, "last_price", "ltp", "lastPrice")),
            "CE_Bid": _n(_pick(ce, "top_bid_price", "bid_price", "best_bid")),
            "CE_Ask": _n(_pick(ce, "top_ask_price", "ask_price", "best_ask")),
            "PE_Bid": _n(_pick(pe, "top_bid_price", "bid_price", "best_bid")),
            "PE_Ask": _n(_pick(pe, "top_ask_price", "ask_price", "best_ask")),
            "CE_Vol": _n(_pick(ce, "volume", "traded_volume")), "PE_Vol": _n(_pick(pe, "volume", "traded_volume")),
            "CE_Delta": _n(_pick(ce_g, "delta")), "PE_Delta": _n(_pick(pe_g, "delta")),
            "CE_OI_Present": ce_oi is not None, "PE_OI_Present": pe_oi is not None,
            "CE_PrevOI_Present": ce_prev is not None, "PE_PrevOI_Present": pe_prev is not None,
            "CE_IV_Present": ce_iv is not None, "PE_IV_Present": pe_iv is not None,
            "CE_LTP_Present": _pick(ce, "last_price", "ltp", "lastPrice") is not None,
            "PE_LTP_Present": _pick(pe, "last_price", "ltp", "lastPrice") is not None,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return spot, df
    return spot, df.sort_values("Strike").reset_index(drop=True)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    return _fetch_chain_raw(scrip, seg, expiry, token, client_id)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_prev_close(scrip, seg, token, client_id):
    """Return the latest valid completed daily close before today's IST date."""
    today = datetime.now(IST).date()
    candle_error = "not attempted"
    try:
        candles = fetch_daily_candles(scrip, seg, token, client_id)
        completed = []
        for candle in candles or []:
            try:
                candle_day = date.fromisoformat(str(candle.get("date", ""))[:10])
                close = float(candle.get("c"))
            except (TypeError, ValueError, OverflowError):
                continue
            if candle_day < today and math.isfinite(close) and close > 0:
                completed.append((candle_day, close))
        if completed:
            candle_day, close = max(completed, key=lambda item: item[0])
            return round(close, 2), f"Dhan historical daily candle: {candle_day.isoformat()} close"
        candle_error = "no valid completed daily candle before today's date"
    except Exception as exc:
        candle_error = str(exc)[:180]

    quote_error = "quote not attempted"
    try:
        response = requests.post(
            f"{BASE_URL}/marketfeed/quote",
            headers=auth_headers(token, client_id),
            json={seg: [scrip]}, timeout=10,
        )
        check(response)
        payload = response.json() or {}
        quote = ((payload.get("data") or {}).get(seg) or {}).get(str(scrip)) or {}
        raw_ltp, raw_net = quote.get("last_price"), quote.get("net_change")
        if raw_ltp is None or raw_net is None:
            quote_error = "quote omitted last_price or net_change"
        else:
            ltp, net = float(raw_ltp), float(raw_net)
            candidate = ltp - net
            if math.isfinite(ltp) and ltp > 0 and math.isfinite(net) and math.isfinite(candidate) and candidate > 0:
                return round(candidate, 2), "Dhan quote: LTP - explicitly supplied net_change (fallback)"
            quote_error = "quote values were non-finite or produced a non-positive close"
    except Exception as exc:
        quote_error = str(exc)[:180]
    return None, f"Previous close unavailable; daily candles: {candle_error}; quote fallback: {quote_error}"


# ---------------- LIVE NEWS ----------------
POS_WORDS = ("surge", "surges", "rally", "rallies", "gain", "gains", "jump", "jumps", "rise", "rises", "soar",
             "soars", "rebound", "rebounds", "recover", "recovers", "record high", "upbeat", "strong", "beat",
             "rate cut", "buying", "inflow", "inflows", "optimism", "bullish")
NEG_WORDS = ("fall", "falls", "slump", "plunge", "plunges", "crash", "selloff", "sell-off", "drop", "drops",
             "decline", "declines", "weak", "fear", "fears", "war", "tariff", "tariffs", "hike", "outflow",
             "outflows", "selling", "slide", "slides", "tumble", "tumbles", "loss", "losses", "concern",
             "concerns", "downgrade", "bearish", "worry", "worries")


def _strip_source(title, source):
    if source and title.endswith(" - " + source):
        return title[: -(len(source) + 3)]
    return title


@st.cache_data(ttl=300, show_spinner=False)
def fetch_news(idx_name, extra_feeds=()):
    """Pull recent market headlines from Google News RSS (+ optional extra feeds). Returns (items, status)."""
    queries = ['Nifty OR Sensex OR "Dalal Street"', "India stocks FII DII", 'RBI OR "US Fed" OR "crude oil" OR rupee']
    if "BANK" in idx_name.upper():
        queries.insert(0, '"Bank Nifty" OR "banking stocks"')
    feeds = [(f"GoogleNews[{q[:16]}]",
              f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=en-IN&gl=IN&ceid=IN:en")
             for q in queries]
    feeds += [(f"Extra[{u[:30]}]", u) for u in extra_feeds]

    items, status, seen = [], [], set()
    for name, url in feeds:
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            n = 0
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                src_el = it.find("source")
                source = (src_el.text or "").strip() if src_el is not None and src_el.text else "feed"
                title = _strip_source(title, source)
                key = re.sub(r"\W+", "", title.lower())[:70]
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                except Exception:
                    ts = 0.0
                link = (it.findtext("link") or "").strip()
                items.append({"title": title, "source": source, "link": link, "ts": ts})
                n += 1
            status.append(f"{name}: OK ({n})")
        except Exception as e:
            status.append(f"{name}: FAIL ({str(e)[:80]})")
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items, status


def recent_news(items, max_age_h=NEWS_MAX_AGE_H, top_n=NEWS_TOP_N):
    now = time.time()
    fresh = [i for i in items if i["ts"] and (now - i["ts"]) <= max_age_h * 3600]
    if len(fresh) < 3:
        fresh = [i for i in items if i["ts"] and (now - i["ts"]) <= 72 * 3600]
    out = []
    for i in fresh[:top_n]:
        d = dict(i)
        d["age_min"] = int((now - i["ts"]) / 60)
        out.append(d)
    return out


def age_label(mins):
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def headline_tone(items):
    pos = neg = 0
    for i in items:
        t = i["title"].lower()
        pos += sum(1 for w in POS_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
        neg += sum(1 for w in NEG_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    return round((pos - neg) / (pos + neg) * 100) if (pos + neg) else 0


def news_for_ai(items, manual_notes):
    lines = [f"- [{age_label(i['age_min'])}] {i['source']}: {i['title']}" for i in items]
    text = "\n".join(lines) if lines else "NO HEADLINES FETCHED"
    if manual_notes and manual_notes.strip():
        text += f"\n- [USER NOTE] {manual_notes.strip()}"
    return text


def news_hash(items):
    if not items:
        return ""
    return hashlib.md5("|".join(i["title"] for i in items[:5]).encode()).hexdigest()


def parse_dte(expiry_str):
    """Returns calendar days to expiry, allowing 0 on expiry day (0 DTE)."""
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
        return max(0, (exp_date - datetime.now(IST).date()).days)
    except ValueError:
        return 0


# ============================================================
# INSTITUTIONAL SURFACE, DEALER GREEKS & ANALYSIS ENGINE
# ============================================================
def years_to_expiry(expiry_str, now=None):
    now = now or datetime.now(IST)
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 1.0 / 365.0
    close_t = datetime.strptime("15:30", "%H:%M").time()
    if TIME_BASIS["mode"] == "calendar":
        exp_dt = datetime.combine(exp_date, close_t, tzinfo=IST)
        return max(600.0, (exp_dt - now).total_seconds()) / (365.0 * 86400.0)
    sess, mins, today = 375.0, 0.0, now.date()
    if today <= exp_date and today.weekday() < 5:
        close_dt = datetime.combine(today, close_t, tzinfo=IST)
        mins += min(sess, max(0.0, (close_dt - now).total_seconds() / 60.0))
    cur = today
    while cur < exp_date:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            mins += sess
    return max(10.0, mins) / (252.0 * sess)


def _mid_price(bid, ask, ltp):
    if bid > 0 and ask > 0 and ask >= bid:
        return 0.5 * (bid + ask)
    return ltp if ltp > 0 else 0.0


def compute_surface_and_exposures(df, spot, t_years, atm_iv, step):
    """
    Computes:
    1) Put-Call Parity Synthetic Future (F_syn) & Live Basis (F_syn - Spot).
    2) 25-Delta Risk Reversal (RR25 = IV_25d_CE - IV_25d_PE) & 25-Delta Butterfly (BF25).
    3) Signed Net Dealer GEX, Vanna Exposure (VEX), Charm Exposure (CEX), and interpolated Zero-Gamma Flip strike.
    """
    out = df.copy()
    t_safe = max(t_years, 1e-5)
    sqrt_t = math.sqrt(t_safe)
    disc_inv = math.exp(RISK_FREE * t_safe)

    # 1. Synthetic Future via Put-Call Parity near ATM
    near_atm = out[(out["Strike"] >= spot - 4 * step) & (out["Strike"] <= spot + 4 * step)].copy()
    syn_estimates = []
    for _, r in near_atm.iterrows():
        c_mid = _mid_price(r["CE_Bid"], r["CE_Ask"], r["CE_LTP"])
        p_mid = _mid_price(r["PE_Bid"], r["PE_Ask"], r["PE_LTP"])
        if c_mid > 1.0 and p_mid > 1.0:
            diff = abs(c_mid - p_mid)
            f_k = float(r["Strike"]) + disc_inv * (c_mid - p_mid)
            if abs(f_k - spot) <= 0.03 * spot:
                syn_estimates.append((diff, f_k))
    if syn_estimates:
        syn_estimates.sort(key=lambda x: x[0])
        top_k = [x[1] for x in syn_estimates[:min(3, len(syn_estimates))]]
        syn_fut = float(sum(top_k) / len(top_k))
    else:
        syn_fut = float(spot)
    basis = syn_fut - spot

    # 2. 25-Delta Risk Reversal & Butterfly
    ce_valid = out[(out["CE_IV"] > 0) & (out["Strike"] >= spot - 2 * step) & (out["Strike"] <= spot + 12 * step)].copy()
    pe_valid = out[(out["PE_IV"] > 0) & (out["Strike"] <= spot + 2 * step) & (out["Strike"] >= spot - 12 * step)].copy()
    iv_25c, iv_25p = atm_iv, atm_iv
    if not ce_valid.empty:
        ce_d = ce_valid["CE_Delta"].abs()
        if (ce_d > 0).any():
            idx_25c = (ce_d - 0.25).abs().idxmin()
            iv_25c = float(ce_valid.loc[idx_25c, "CE_IV"])
    if not pe_valid.empty:
        pe_d = pe_valid["PE_Delta"].abs()
        if (pe_d > 0).any():
            idx_25p = (pe_d - 0.25).abs().idxmin()
            iv_25p = float(pe_valid.loc[idx_25p, "PE_IV"])
    rr_25 = iv_25c - iv_25p
    bf_25 = 0.5 * (iv_25c + iv_25p) - atm_iv

    # 3. Signed Net GEX, Vanna Exposure (VEX), and Charm Exposure (CEX)
    out["CE_GEX"] = out["CE_Gamma"].abs() * out["CE_OI"] * (spot ** 2) * 0.01
    out["PE_GEX"] = -out["PE_Gamma"].abs() * out["PE_OI"] * (spot ** 2) * 0.01
    out["Net_GEX"] = out["CE_GEX"] + out["PE_GEX"]

    vex_list, cex_list = [], []
    for _, row in out.iterrows():
        k = float(row["Strike"])
        if k <= 0:
            vex_list.append(0.0)
            cex_list.append(0.0)
            continue
        iv_c = (row["CE_IV"] if row["CE_IV"] > 0 else atm_iv) / 100.0
        iv_p = (row["PE_IV"] if row["PE_IV"] > 0 else atm_iv) / 100.0

        def _vanna_charm(sigma):
            sigma = max(sigma, 0.03)
            d1 = (math.log(syn_fut / k) + 0.5 * (sigma ** 2) * t_safe) / (sigma * sqrt_t)
            d2 = d1 - sigma * sqrt_t
            n_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
            vanna = -n_d1 * d2 / sigma
            charm = n_d1 * (d2 * sigma * sqrt_t) / (2.0 * t_safe * sigma * sqrt_t)
            return vanna, charm

        v_c, c_c = _vanna_charm(iv_c)
        v_p, c_p = _vanna_charm(iv_p)
        vex_list.append((v_c * row["CE_OI"] - v_p * row["PE_OI"]) * 0.01)
        cex_list.append((c_c * row["CE_OI"] - c_p * row["PE_OI"]) / 365.0)

    out["Net_VEX"] = vex_list
    out["Net_CEX"] = cex_list

    # Interpolate Zero-Gamma (Gamma Flip) strike near spot
    near_gex = out[(out["Strike"] >= spot - 8 * step) & (out["Strike"] <= spot + 8 * step)].sort_values("Strike")
    gamma_flip = None
    if len(near_gex) >= 2:
        g_vals = near_gex["Net_GEX"].values
        k_vals = near_gex["Strike"].values
        best_dist = float("inf")
        for i in range(len(g_vals) - 1):
            if g_vals[i] * g_vals[i + 1] < 0:
                w = abs(g_vals[i]) / (abs(g_vals[i]) + abs(g_vals[i + 1]))
                cross_k = float(k_vals[i] + w * (k_vals[i + 1] - k_vals[i]))
                if abs(cross_k - spot) < best_dist:
                    best_dist = abs(cross_k - spot)
                    gamma_flip = round(cross_k, 1)

    net_gex_near = float(near_gex["Net_GEX"].sum()) if not near_gex.empty else float(out["Net_GEX"].sum())
    gex_regime = "POSITIVE GAMMA (Mean-Reverting)" if net_gex_near >= 0 else "NEGATIVE GAMMA (Momentum / Breakout)"

    return {
        "df": out,
        "syn_fut": round(syn_fut, 2),
        "basis": round(basis, 2),
        "rr_25": round(rr_25, 2),
        "bf_25": round(bf_25, 2),
        "iv_25c": round(iv_25c, 2),
        "iv_25p": round(iv_25p, 2),
        "net_gex": net_gex_near,
        "net_vex": float(near_gex["Net_VEX"].sum()) if not near_gex.empty else 0.0,
        "net_cex": float(near_gex["Net_CEX"].sum()) if not near_gex.empty else 0.0,
        "gamma_flip": gamma_flip,
        "gex_regime": gex_regime,
    }


def interpret_oi_battle(ce_chg, pe_chg, ce_tot, pe_tot):
    """Scale-invariant OI battle using percentage of chain OI (threshold: 0.5% of side OI)."""
    ce_th = max(2000.0, 0.005 * ce_tot)
    pe_th = max(2000.0, 0.005 * pe_tot)
    if ce_chg > ce_th and pe_chg <= 0:
        return "CE Build (Bearish)"
    if pe_chg > pe_th and ce_chg <= 0:
        return "PE Build (Bullish)"
    if ce_chg < -ce_th and pe_chg >= 0:
        return "CE Unwind (Bullish)"
    if pe_chg < -pe_th and ce_chg >= 0:
        return "PE Unwind (Bearish)"
    if ce_chg > 0 and pe_chg > 0:
        return "Straddle Build"
    return "Liquidation"


def calc_max_pain(df):
    strikes = df["Strike"].values
    ce_oi, pe_oi = df["CE_OI"].values, df["PE_OI"].values
    pain = [(ce_oi * np.maximum(0, k - strikes)).sum() + (pe_oi * np.maximum(0, strikes - k)).sum()
            for k in strikes]
    return float(strikes[int(np.argmin(pain))])


def compute_bias(engine, spot, prev_close, pcr, sd, basis, rr_25):
    """Scale-invariant bias score (0..100) using % of near-spot OI, daily sigma, and futures basis/skew."""
    score = 50
    ce_pct = engine["ce_net_flow"] / engine["ce_oi_near"] * 100 if engine["ce_oi_near"] > 0 else 0.0
    pe_pct = engine["pe_net_flow"] / engine["pe_oi_near"] * 100 if engine["pe_oi_near"] > 0 else 0.0

    if ce_pct < -2.0 and pe_pct > 1.5:
        score += 25
    elif ce_pct < -1.0 and pe_pct > 0.75:
        score += 15
    elif pe_pct < -2.0 and ce_pct > 1.5:
        score -= 25
    elif pe_pct < -1.0 and ce_pct > 0.75:
        score -= 15

    if prev_close and prev_close > 0 and sd > 0:
        move_sigma = (spot - prev_close) / sd
        if move_sigma > 0.12:
            score += 10
        elif move_sigma < -0.12:
            score -= 10

    if pcr > 1.2:
        score += 8
    elif pcr < 0.8:
        score -= 8

    if basis > 0.001 * spot and rr_25 > -0.5:
        score += 5
    elif basis < -0.001 * spot and rr_25 < -1.5:
        score -= 5

    score = max(0, min(100, score))
    label = "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRAL"
    return score, label


def engine_sums(near):
    out = {k: 0.0 for k in ("ce_exits", "pe_exits", "ce_build", "pe_build",
                            "ce_exit_pct", "pe_exit_pct", "ce_build_pct", "pe_build_pct",
                            "ce_net_flow", "pe_net_flow", "bull_force", "bear_force", "force_ratio",
                            "ce_oi_near", "pe_oi_near")}
    if near is None or near.empty:
        return out

    ce_tot = float(near["CE_OI"].sum())
    pe_tot = float(near["PE_OI"].sum())
    ce_noise = ce_tot * NOISE_PCT / 100.0
    pe_noise = pe_tot * NOISE_PCT / 100.0

    ce_chg = near["CE_OI_Chg"]
    pe_chg = near["PE_OI_Chg"]
    out["ce_exits"] = float(ce_chg[ce_chg < -ce_noise].sum())
    out["pe_exits"] = float(pe_chg[pe_chg < -pe_noise].sum())
    out["ce_build"] = float(ce_chg[ce_chg > ce_noise].sum())
    out["pe_build"] = float(pe_chg[pe_chg > pe_noise].sum())
    out["ce_oi_near"], out["pe_oi_near"] = ce_tot, pe_tot

    pct = lambda q, tot: abs(q) / tot * 100.0 if tot > 0 else 0.0
    out["ce_exit_pct"] = pct(out["ce_exits"], ce_tot)
    out["pe_exit_pct"] = pct(out["pe_exits"], pe_tot)
    out["ce_build_pct"] = pct(out["ce_build"], ce_tot)
    out["pe_build_pct"] = pct(out["pe_build"], pe_tot)

    out["ce_net_flow"] = out["ce_build"] + out["ce_exits"]
    out["pe_net_flow"] = out["pe_build"] + out["pe_exits"]

    out["bull_force"] = out["pe_build_pct"] + out["ce_exit_pct"]
    out["bear_force"] = out["ce_build_pct"] + out["pe_exit_pct"]
    hi, lo = max(out["bull_force"], out["bear_force"]), min(out["bull_force"], out["bear_force"])
    out["force_ratio"] = (hi / lo) if lo > 0 else (999.0 if hi > 0 else 1.0)
    return out


def detect_gamma_blast(near):
    e = engine_sums(near)
    if near is None or near.empty:
        return "BALANCED ACCUMULATION", "WAIT", "No near-spot strikes available."

    base_bull = e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] >= MIN_FLOW_PCT
    base_bear = e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] >= MIN_FLOW_PCT
    bull_cap = e["ce_net_flow"] < 0
    bear_cap = e["pe_net_flow"] < 0
    bull_dom = e["bull_force"] >= DOMINANCE * e["bear_force"]
    bear_dom = e["bear_force"] >= DOMINANCE * e["bull_force"]
    pe_net_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    ce_net_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0

    if base_bull and bull_cap and bull_dom:
        return ("BULLISH GAMMA BLAST", "BUY",
                f"Call writers net unwinding ({ce_net_pct:+.1f}%) + put writers building ({e['pe_build_pct']:.1f}%); "
                f"bull force {e['bull_force']:.1f} vs bear {e['bear_force']:.1f}.")
    if base_bear and bear_cap and bear_dom:
        return ("BEARISH GAMMA BLAST", "SELL",
                f"Put writers net unwinding ({pe_net_pct:+.1f}%) + call writers building ({e['ce_build_pct']:.1f}%); "
                f"bear force {e['bear_force']:.1f} vs bull {e['bull_force']:.1f}.")
    if base_bull and base_bear:
        return ("CONFLICTED", "WAIT",
                f"Opposing flows both active (bull force {e['bull_force']:.1f} vs bear {e['bear_force']:.1f}). Stand aside.")
    if base_bear:
        if bear_dom:
            return ("RESISTANCE BUILD (bearish lean)", "WAIT",
                    f"Heavy call writing ({e['ce_build_pct']:.1f}% of near OI) but put writers are still NET adding "
                    f"({pe_net_pct:+.1f}%), so there is no capitulation yet. Not a blast.")
        return ("CONFLICTED", "WAIT", "Put exits and call builds present, but no clear winner. Stand aside.")
    if base_bull:
        if bull_dom:
            return ("SUPPORT BUILD (bullish lean)", "WAIT",
                    f"Heavy put writing ({e['pe_build_pct']:.1f}% of near OI) but call writers are still NET adding "
                    f"({ce_net_pct:+.1f}%), so there is no capitulation yet. Not a blast.")
        return ("CONFLICTED", "WAIT", "Call exits and put builds present, but no clear winner. Stand aside.")

    weak = MIN_FLOW_PCT / 2.0
    if e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] < weak:
        return "IV SPIKE BULL TRAP", "WAIT", "Isolated call short covering without put support. Trap risk."
    if e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] < weak:
        return "IV SPIKE BEAR TRAP", "WAIT", "Isolated put short covering without call support. Trap risk."
    return "BALANCED ACCUMULATION", "WAIT", "Market in range compression. Awaiting trigger."


def find_structure(df, spot, step, exp_move, call_wall, put_wall):
    reach = max(exp_move * 1.5, step * 4)
    res_pool = df[(df["Strike"] >= spot + step) & (df["Strike"] <= spot + reach) & (df["CE_OI"] > 0)]
    sup_pool = df[(df["Strike"] <= spot - step) & (df["Strike"] >= spot - reach) & (df["PE_OI"] > 0)]

    def pick(pool, col, chg_col, fallback, ok):
        if not pool.empty:
            strong = pool[pool[col] >= 0.8 * pool[col].max()].copy()
            strong["d"] = (strong["Strike"] - spot).abs()
            r = strong.loc[strong["d"].idxmin()]
            return {"level": float(r["Strike"]), "oi": float(r[col]), "chg": float(r[chg_col]), "src": "nearest"}
        if ok(fallback):
            r = df[df["Strike"] == fallback].iloc[0]
            return {"level": float(fallback), "oi": float(r[col]), "chg": float(r[chg_col]), "src": "global wall"}
        return None

    res = pick(res_pool, "CE_OI", "CE_OI_Chg", call_wall, lambda x: x > spot)
    sup = pick(sup_pool, "PE_OI", "PE_OI_Chg", put_wall, lambda x: x < spot)
    return {"res": res, "sup": sup}


def compute_metrics(df, spot, prev_close, expiry_str, step):
    df = df.copy()
    # Mask uninitialized PrevOI == 0 rows when OI > 0 and Vol < OI
    df["CE_Prev_Valid"] = (df["CE_OI"] == 0) | (df["CE_PrevOI"] > 0) | (df["CE_Vol"] >= df["CE_OI"])
    df["PE_Prev_Valid"] = (df["PE_OI"] == 0) | (df["PE_PrevOI"] > 0) | (df["PE_Vol"] >= df["PE_OI"])
    df["CE_OI_Chg"] = np.where(df["CE_Prev_Valid"], df["CE_OI"] - df["CE_PrevOI"], 0.0)
    df["PE_OI_Chg"] = np.where(df["PE_Prev_Valid"], df["PE_OI"] - df["PE_PrevOI"], 0.0)

    ce_tot_chain, pe_tot_chain = float(df["CE_OI"].sum()), float(df["PE_OI"].sum())
    pcr = pe_tot_chain / ce_tot_chain if ce_tot_chain > 0 else 1.0
    dce, dpe = float(df["CE_OI_Chg"].sum()), float(df["PE_OI_Chg"].sum())

    atm_idx = (df["Strike"] - spot).abs().idxmin()
    ce_iv, pe_iv = float(df.loc[atm_idx, "CE_IV"]), float(df.loc[atm_idx, "PE_IV"])
    ivs = [x for x in (ce_iv, pe_iv) if x > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else 12.0
    iv_gap = abs(ce_iv - pe_iv) if (ce_iv > 0 and pe_iv > 0) else 0.0
    dte = parse_dte(expiry_str)
    t_years = years_to_expiry(expiry_str)
    exp_move = spot * (atm_iv / 100) * math.sqrt(t_years)

    surf = compute_surface_and_exposures(df, spot, t_years, atm_iv, step)
    df = surf["df"]

    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    gamma_strike = float(df.loc[df["total_gamma"].idxmax(), "Strike"]) if df["total_gamma"].sum() > 0 else spot
    call_wall = float(df.loc[df["CE_OI"].idxmax(), "Strike"])
    put_wall = float(df.loc[df["PE_OI"].idxmax(), "Strike"])
    max_pain = calc_max_pain(df)

    window = max(300, step * 6)
    near = df[(df["Strike"] >= spot - window) & (df["Strike"] <= spot + window)].copy()
    engine = engine_sums(near)
    micro = compute_chain_microstructure(near, spot, step)

    buf = step_buffer(step)
    ce_trap, pe_trap = None, None
    if not near.empty:
        ce_trap_th = max(2500.0, (TRAP_MIN_OI_PCT / 100.0) * engine["ce_oi_near"])
        pe_trap_th = max(2500.0, (TRAP_MIN_OI_PCT / 100.0) * engine["pe_oi_near"])
        i = near["CE_OI_Chg"].idxmax()
        if near.loc[i, "CE_OI_Chg"] >= ce_trap_th:
            ce_trap = float(near.loc[i, "Strike"]) + buf
        j = near["PE_OI_Chg"].idxmax()
        if near.loc[j, "PE_OI_Chg"] >= pe_trap_th:
            pe_trap = float(near.loc[j, "Strike"]) - buf

    days_basis = 365.0 if TIME_BASIS["mode"] == "calendar" else 252.0
    sd = spot * (atm_iv / 100.0) * math.sqrt(1.0 / days_basis)
    score, bias = compute_bias(engine, spot, prev_close, pcr, sd, surf["basis"], surf["rr_25"])
    status, signal, desc = detect_gamma_blast(near)
    struct = find_structure(df, spot, step, exp_move, call_wall, put_wall)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "buf": buf, "pcr": pcr,
        "dce": dce, "dpe": dpe, "battle": interpret_oi_battle(dce, dpe, ce_tot_chain, pe_tot_chain),
        "atm_iv": atm_iv, "dte": dte, "exp_move": round(exp_move, 2),
        "t_years": t_years, "days_left": t_years * days_basis,
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "engine": engine, "struct": struct, "micro": micro,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
        "syn_fut": surf["syn_fut"], "basis": surf["basis"],
        "rr_25": surf["rr_25"], "bf_25": surf["bf_25"],
        "iv_25c": surf["iv_25c"], "iv_25p": surf["iv_25p"],
        "net_gex": surf["net_gex"], "net_vex": surf["net_vex"], "net_cex": surf["net_cex"],
        "gamma_flip": surf["gamma_flip"], "gex_regime": surf["gex_regime"],
    }


# ============================================================
# 3-MIN OI VELOCITY
# ============================================================
ZERO_VEL = {"ce_builds": 0.0, "ce_unwinds": 0.0, "pe_builds": 0.0, "pe_unwinds": 0.0,
            "ce_unw_pct": 0.0, "pe_unw_pct": 0.0}


def update_oi_velocity(m, key):
    now = datetime.now(IST)
    snap = st.session_state.get("oi_snap")
    current = {r["Strike"]: (r["CE_OI"], r["PE_OI"]) for _, r in m["near"].iterrows()}

    if snap is None or snap["key"] != key:
        st.session_state.oi_snap = {"key": key, "ts": now, "map": current}
        st.session_state.velocity = dict(ZERO_VEL)
        return st.session_state.velocity

    if (now - snap["ts"]).total_seconds() < VELOCITY_WINDOW_SEC:
        return st.session_state.get("velocity", dict(ZERO_VEL))

    vel = dict(ZERO_VEL)
    for stk, (c_ce, c_pe) in current.items():
        if stk not in snap["map"]:
            continue
        p_ce, p_pe = snap["map"][stk]
        d_ce, d_pe = c_ce - p_ce, c_pe - p_pe
        if d_ce > 0:
            vel["ce_builds"] += d_ce
        elif d_ce < 0:
            vel["ce_unwinds"] += abs(d_ce)
        if d_pe > 0:
            vel["pe_builds"] += d_pe
        elif d_pe < 0:
            vel["pe_unwinds"] += abs(d_pe)

    ce_near = m["engine"]["ce_oi_near"]
    pe_near = m["engine"]["pe_oi_near"]
    vel["ce_unw_pct"] = (vel["ce_unwinds"] / ce_near * 100.0) if ce_near > 0 else 0.0
    vel["pe_unw_pct"] = (vel["pe_unwinds"] / pe_near * 100.0) if pe_near > 0 else 0.0

    st.session_state.oi_snap = {"key": key, "ts": now, "map": current}
    st.session_state.velocity = vel
    return vel


# ============================================================
# STATE-CHANGE GATING
# ============================================================
def evaluate_market_state_change(m, vel, nhash=""):
    last = st.session_state.get("last_ai_state")
    if last is None:
        return True, "INITIAL_SCAN"

    step_thresh = max(15.0, m["step"] * 0.5)
    spot_diff = m["spot"] - last["spot"]
    if abs(spot_diff) >= step_thresh:
        return True, f"SPOT_MOVED ({spot_diff:+.1f} pts)"
    if abs(m["pcr"] - last["pcr"]) >= 0.05:
        return True, "PCR_SHIFT"
    if m["call_wall"] != last["call_wall"]:
        return True, "CALL_WALL_SHIFT"
    if m["put_wall"] != last["put_wall"]:
        return True, "PUT_WALL_SHIFT"
    if m["gamma_strike"] != last["gamma_strike"]:
        return True, "GAMMA_ZONE_SHIFT"
    if m["gex_regime"] != last.get("gex_regime"):
        return True, "GAMMA_REGIME_FLIP"
    if m["status"] != last["status"]:
        return True, "ENGINE_STATUS_CHANGE"
    trap_now = (m["ce_trap"] is not None) or (m["pe_trap"] is not None)
    if trap_now and not last.get("trap_active", False):
        return True, "TRAP_DETECTED"
    if vel.get("ce_unw_pct", 0) >= VEL_TRIGGER_PCT or vel.get("pe_unw_pct", 0) >= VEL_TRIGGER_PCT:
        return True, "HIGH_UNWIND_VELOCITY"
    if m.get("exh_flag", "") != last.get("exh_flag", "") and "CONFIRMED" in m.get("exh_flag", ""):
        return True, "EXHAUSTION_CONFIRMED"
    t = st.session_state.get("last_ai_time")
    gap_ok = t is None or (datetime.now(IST) - t).total_seconds() >= NEWS_MIN_GAP_SEC
    if nhash and nhash != last.get("news_hash", "") and gap_ok:
        return True, "NEWS_CHANGED"
    return False, "STATE_UNCHANGED"


# ============================================================
# GROQ MULTI-AGENT COUNCIL
# ============================================================
def run_council(m, vel, news, api_key, model):
    if Groq is None:
        return {"error": "groq package not installed (pip install groq)."}
    if not api_key:
        return {"error": "No GROQ API key provided in sidebar."}

    eg = m["engine"]
    mc = m["micro"]
    sres, ssup = m["struct"]["res"], m["struct"]["sup"]
    struct_txt = (
        f"nearest OI resistance {sres['level']:.0f} (CE OI {sres['oi']:,.0f}, chg {sres['chg']:+,.0f})"
        if sres else "no nearby OI resistance") + " | " + (
        f"nearest OI support {ssup['level']:.0f} (PE OI {ssup['oi']:,.0f}, chg {ssup['chg']:+,.0f})"
        if ssup else "no nearby OI support")
    ex = m.get("exh") or {}
    if ex.get("ready"):
        exh_txt = (f"buyer exhaustion {ex['buyer']['score']}/100 ({ex['buyer']['status']}); "
                   f"seller exhaustion {ex['seller']['score']}/100 ({ex['seller']['status']}); window {ex['age']:.0f} min")
    else:
        exh_txt = ex.get("why", "not available")
    strike_log = []
    for _, r in m["near"].iterrows():
        strike_log.append(
            f"K={int(r['Strike'])}: CE_OI={int(r['CE_OI']):,} (chg {int(r['CE_OI_Chg']):+,}), "
            f"PE_OI={int(r['PE_OI']):,} (chg {int(r['PE_OI_Chg']):+,}), Net_GEX={r['Net_GEX']:+,.0f}"
        )
    chain_summary = "\n".join(strike_log[:25])
    gflip_txt = f"{m['gamma_flip']:,.1f}" if m["gamma_flip"] else "none in near window"

    prompt = f"""You are an institutional council of 5 options-market agents. Analyze and answer in JSON only.

MARKET & DEALER POSITIONING DATA:
- Spot: {m['spot']:.2f} | Synthetic Future (Put-Call Parity): {m['syn_fut']:.2f} (Basis {m['basis']:+.2f} pts)
- PCR: {m['pcr']:.3f} | Bias: {m['bias']} (score {m['score']}/100) | DTE: {m['dte']} ({m['days_left']:.2f}d remaining)
- Dealer Gamma Regime: {m['gex_regime']} | Net GEX (near spot): {m['net_gex']:+,.0f} | Gamma Flip Level: {gflip_txt}
- Higher-Order Dealer Flows: Net Vanna (VEX) {m['net_vex']:+,.1f} | Net Charm (CEX) {m['net_cex']:+,.1f}
- Vol Surface: ATM IV {m['atm_iv']:.1f}% | 25d Risk Reversal (CE-PE): {m['rr_25']:+.2f}% | 25d Butterfly: {m['bf_25']:+.2f}%
- Quote Microstructure: CE Quote Location {mc['ce_quote_loc']:.2f} vs PE {mc['pe_quote_loc']:.2f} (Net Taker Edge {mc['net_taker_edge']:+.1f} pts -> {mc['label']})
- Engine status: {m['status']} | OI battle: {m['battle']}
- Dual-Force flow (near-spot strikes only, % = share of that side's near-spot OI):
  CE exits {eg['ce_exits']:,.0f} ({eg['ce_exit_pct']:.1f}%) | CE builds {eg['ce_build']:,.0f} ({eg['ce_build_pct']:.1f}%) | CE net flow {eg['ce_net_flow']:+,.0f}
  PE exits {eg['pe_exits']:,.0f} ({eg['pe_exit_pct']:.1f}%) | PE builds {eg['pe_build']:,.0f} ({eg['pe_build_pct']:.1f}%) | PE net flow {eg['pe_net_flow']:+,.0f}
  Bull force {eg['bull_force']:.1f} vs Bear force {eg['bear_force']:.1f} (ratio {eg['force_ratio']:.2f}; a winner needs >= {DOMINANCE})
- OI structure around spot: {struct_txt}
- Definitions: Bull force = PE builds% + CE exits%; Bear force = CE builds% + PE exits%. Put OI BUILDING is bullish support, call OI BUILDING is bearish resistance. Do not call put builds bearish.
- Exhaustion monitor: {exh_txt}
- Whole-chain net OI change: CE {m['dce']:+,.0f} | PE {m['dpe']:+,.0f}
- Expected Move: +/-{m['exp_move']} pts [{m['lower_1sigma']} - {m['upper_1sigma']}]
- Call Wall: {m['call_wall']:.0f} | Put Wall: {m['put_wall']:.0f} | Max Pain: {m['max_pain']:.0f} | Peak Gamma strike: {m['gamma_strike']:.0f}
- Step-scaled traps (±{m['buf']}pt): CE resistance={m['ce_trap']} | PE support={m['pe_trap']}
- 3-Min Flow: CE build={vel['ce_builds']:,.0f}, CE unwind={vel['ce_unwinds']:,.0f} ({vel.get('ce_unw_pct', 0):.1f}%) | PE build={vel['pe_builds']:,.0f}, PE unwind={vel['pe_unwinds']:,.0f} ({vel.get('pe_unw_pct', 0):.1f}%)
- Strikes near spot:
{chain_summary}
- LIVE HEADLINES (latest first, fetched just now):
{news}

AGENTS:
1. Price Action Agent: spot vs Gamma Flip, Synthetic Basis, and structural walls
2. Order Flow Agent: OI changes, quote-spread aggression, capitulation vs builds, and step-scaled traps
3. Volatility & Surface Agent: GEX regime (mean-reverting vs breakout), 25d skew (RR25/BF25), Vanna/Charm pin risk
4. Sentiment Agent: judge ONLY from the LIVE HEADLINES list; name the headline(s) driving the view. If the list says NO HEADLINES FETCHED, say that instead of claiming "no news impact"
5. Risk Officer: final approval; reject if evidence is conflicting or engine status is CONFLICTED; specify exact spot invalidation level

Return ONLY this JSON:
{{"price_action_agent":"1 sentence","order_flow_agent":"1 sentence","volatility_agent":"1 sentence",
"news_agent":"1 sentence","news_score":-100 to 100,"final_approval":"synthesis",
"invalidation_level":"spot level or condition that invalidates the view","signal":"BUY or SELL or WAIT","confidence":0-100}}"""

    client = Groq(api_key=api_key)
    candidates = [model] + [x for x in GROQ_MODELS if x != model]
    last_err = ""
    for mdl in candidates:
        try:
            resp = client.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2500,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"},
            )
            txt = (resp.choices[0].message.content or "").strip()
            txt = txt.replace("```json", "").replace("```", "").strip()
            out = json.loads(txt)
            out["_model"] = mdl
            return out
        except Exception as e:
            last_err = f"{mdl}: {e}"
            if "model_not_found" in str(e) or "404" in str(e):
                continue
            break
    return {"error": f"Groq call failed: {last_err}"}


# ============================================================
# SIGNAL VALIDATION
# ============================================================
def chain_checks(m):
    """Independent option-chain, surface & microstructure reads. v = +1 bullish, -1 bearish, 0 neutral."""
    e = m["engine"]
    mc = m["micro"]
    checks = []

    def add(name, v, detail):
        checks.append({"name": name, "v": v, "detail": detail})

    st_ = m["status"]
    add("Dual-Force engine", 1 if "BULLISH GAMMA" in st_ else -1 if "BEARISH GAMMA" in st_ else 0, st_)

    ce_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0
    pe_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    gap = pe_pct - ce_pct
    add("Near-spot net OI flow", 1 if gap >= FLOW_EDGE_PCT else -1 if gap <= -FLOW_EDGE_PCT else 0,
        f"PE {pe_pct:+.1f}% vs CE {ce_pct:+.1f}% of near-spot OI")

    total = e["ce_oi_near"] + e["pe_oi_near"]
    oi_imbalance = (e["pe_oi_near"] - e["ce_oi_near"]) / total if total > 0 else 0.0
    add("Near-spot OI balance", 1 if oi_imbalance >= 0.10 else -1 if oi_imbalance <= -0.10 else 0,
        f"{oi_imbalance:+.3f} (PE-heavy is +; positioning context only)")

    add("PCR (whole chain)", 1 if m["pcr"] > 1.2 else -1 if m["pcr"] < 0.8 else 0, f"{m['pcr']:.2f}")
    add("Bias score", 1 if m["score"] > 55 else -1 if m["score"] < 45 else 0, f"{m['score']}/100 {m['bias']}")

    basis_thresh = 0.0008 * m["spot"]
    surf_v = 1 if (m["basis"] >= basis_thresh and m["rr_25"] >= -0.8) else -1 if (m["basis"] <= -basis_thresh and m["rr_25"] <= -1.0) else 0
    add("Synthetic Basis & 25Δ Skew", surf_v,
        f"F_syn {m['syn_fut']:,.1f} (basis {m['basis']:+.1f}) | 25Δ RR {m['rr_25']:+.2f}% | BF {m['bf_25']:+.2f}%")

    taker_v = 1 if mc["net_taker_edge"] >= 8.0 else -1 if mc["net_taker_edge"] <= -8.0 else 0
    add("Quote-Spread Aggression", taker_v,
        f"CE loc {mc['ce_quote_loc']:.2f} vs PE loc {mc['pe_quote_loc']:.2f} ({mc['label']})")

    mp_gap = m["spot"] - m["max_pain"]
    add("Spot vs max pain", -1 if mp_gap > m["step"] else 1 if mp_gap < -m["step"] else 0,
        f"spot {m['spot']:,.0f} vs max pain {m['max_pain']:,.0f} ({mp_gap:+,.0f})")
    return checks


def build_signal(m, ai):
    ai_sig = str(ai.get("signal", "WAIT")).upper()
    if ai_sig not in ("BUY", "SELL", "WAIT"):
        ai_sig = "WAIT"
    try:
        conf = int(float(ai.get("confidence", 0)))
    except (TypeError, ValueError):
        conf = 0

    direction = {"BUY": 1, "SELL": -1}.get(ai_sig, 0)
    checks = chain_checks(m)
    lean = sum(c["v"] for c in checks)
    net = lean * direction
    entry = m["spot"]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    buf = m["buf"]
    notes = []
    sig, target, sl, rr = ai_sig, None, None, None

    if sig != "WAIT":
        if net < MIN_CONFIRM:
            notes.append(f"{sig} rejected: option-chain agreement {net:+d} is below the required +{MIN_CONFIRM}.")
            sig = "WAIT"

    exh = m.get("exh") or {}
    if sig != "WAIT" and exh.get("ready"):
        mine = exh["buyer"] if sig == "BUY" else exh["seller"]
        if mine["flag"] == "CONFIRMED":
            notes.append(f"{sig} rejected: {'buyer' if sig == 'BUY' else 'seller'} exhaustion confirmed "
                         f"({mine['score']}/100), the move you would join is running out of fuel.")
            sig = "WAIT"
        elif mine["flag"] == "WATCH":
            notes.append(f"Caution: {'buyer' if sig == 'BUY' else 'seller'} exhaustion building ({mine['score']}/100).")

    if sig != "WAIT":
        if res is None or sup is None:
            notes.append(f"{sig} rejected: no usable OI wall on one side of spot.")
            sig = "WAIT"
        elif sig == "BUY":
            target, sl = res["level"], sup["level"] - buf
        else:
            target, sl = sup["level"], res["level"] + buf

    if sig != "WAIT":
        risk = abs(entry - sl)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else None
        if rr is None or rr < MIN_RR:
            rr_txt = f"1:{rr:.2f}" if rr else "n/a"
            notes.append(f"{sig} rejected: R:R {rr_txt} from the nearest OI walls "
                         f"(target {target:,.0f}, SL {sl:,.0f}) is below 1:{MIN_RR:.1f}.")
            sig, target, sl, rr = "WAIT", None, None, None

    return {"signal": sig, "ai_signal": ai_sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": " ".join(notes), "checks": checks, "net": net, "lean": lean,
            "direction": direction, "invalidation": ai.get("invalidation_level", "")}


# ============================================================
# DAY MOVEMENT LEVELS (sigma bands, pivots, ATR) + OI confluence
# ============================================================
def _candle_date(ts):
    today = datetime.now(IST).date()
    d = datetime.fromtimestamp(float(ts), IST).date()
    if d < today - timedelta(days=400):
        d = datetime.fromtimestamp(float(ts) + 315532800, IST).date()
    return d


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_candles(scrip, seg, token, client_id):
    today = datetime.now(IST).date()
    last_err = None
    for to_date in (today + timedelta(days=1), today):
        try:
            response = requests.post(
                f"{BASE_URL}/charts/historical",
                headers=auth_headers(token, client_id),
                json={
                    "securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                    "expiryCode": 0, "oi": False,
                    "fromDate": (today - timedelta(days=45)).isoformat(),
                    "toDate": to_date.isoformat(),
                }, timeout=12,
            )
            check(response)
            payload = response.json() or {}
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            closes = data.get("close") or []
            timestamps = data.get("timestamp") or []
            opens, highs, lows = data.get("open") or [], data.get("high") or [], data.get("low") or []
            if not closes or not timestamps:
                raise RuntimeError(f"empty/malformed candle response; keys={list(data.keys())[:12]}")
            count = min(len(closes), len(timestamps), len(opens), len(highs), len(lows))
            candles = []
            for i in range(count):
                try:
                    day = _candle_date(timestamps[i]).isoformat()
                    o, h, lo, c = map(float, (opens[i], highs[i], lows[i], closes[i]))
                    if not all(math.isfinite(v) for v in (o, h, lo, c)) or min(o, h, lo, c) <= 0:
                        continue
                    if h < max(o, lo, c) or lo > min(o, h, c):
                        continue
                    candles.append({"date": day, "o": o, "h": h, "l": lo, "c": c})
                except (TypeError, ValueError, OverflowError, IndexError, OSError):
                    continue
            if not candles:
                raise RuntimeError(f"no valid OHLC candles after validation; lengths={len(timestamps)}/{len(opens)}/{len(highs)}/{len(lows)}/{len(closes)}")
            by_day = {item["date"]: item for item in candles}
            return [by_day[day] for day in sorted(by_day)]
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Dhan daily candles failed: {last_err}")


def compute_day_levels(candles, spot, sd, m, prev_close_fallback, today_ohlc=None):
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()
    after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    synthetic = False
    if after_close and now_ist.weekday() < 5 and today_ohlc and not any(c["date"] == today for c in candles):
        candles = list(candles) + [{"date": today, **today_ohlc}]
        synthetic = True
    done = [c for c in candles if c["date"] < today or (c["date"] == today and after_close)]
    live = next((c for c in candles if c["date"] == today and not after_close), None)

    ref = done[-1] if done else None
    levels = []

    if ref is None and (prev_close_fallback is None or not math.isfinite(float(prev_close_fallback)) or float(prev_close_fallback) <= 0):
        return {"available": False, "table": pd.DataFrame(columns=["Level", "Price", "Dist pts", "Dist σ", "OI confluence"]),
                "ref_close": None, "basis": "Unavailable: no verified prior close or completed daily candle",
                "atr": None, "sd": sd, "tol": max(m["step"] * 0.5, 0.15 * sd), "band1": None, "band2": None,
                "live": live, "moved_sigma": None}

    def add(name, price, kind):
        levels.append((name, float(price), kind))

    if ref:
        c, h, l = ref["c"], ref["h"], ref["l"]
        basis = f"session {ref['date']} (H {h:,.0f} / L {l:,.0f} / C {c:,.0f})" + (" [OHLC from quote]" if synthetic and ref["date"] == today else "")
    else:
        c, h, l = prev_close_fallback, None, None
        basis = f"previous close {c:,.0f} (no candle data, pivots unavailable)"

    for k in (2, 1):
        add(f"Close +{k}σ", c + k * sd, "σ")
    add("Close (ref)", c, "ref")
    for k in (1, 2):
        add(f"Close -{k}σ", c - k * sd, "σ")

    atr = None
    if h is not None:
        P = (h + l + c) / 3
        for nm, v in (("R3", h + 2 * (P - l)), ("R2", P + (h - l)), ("R1", 2 * P - l), ("Pivot", P),
                      ("S1", 2 * P - h), ("S2", P - (h - l)), ("S3", l - 2 * (h - P))):
            add(nm, v, "pivot")
        trs = []
        for i in range(1, len(done)):
            pc = done[i - 1]["c"]
            trs.append(max(done[i]["h"] - done[i]["l"], abs(done[i]["h"] - pc), abs(done[i]["l"] - pc)))
        if len(trs) >= 5:
            atr = sum(trs[-14:]) / len(trs[-14:])
            add("Close +ATR", c + atr, "atr")
            add("Close -ATR", c - atr, "atr")

    if live:
        add("Today open", live["o"], "today")
        add("Open +1σ", live["o"] + sd, "today")
        add("Open -1σ", live["o"] - sd, "today")
        add("Today high", live["h"], "today")
        add("Today low", live["l"], "today")

    oi_refs = {"Call wall": m["call_wall"], "Put wall": m["put_wall"], "Max pain": m["max_pain"],
               "Gamma strike": m["gamma_strike"]}
    if m.get("gamma_flip"):
        oi_refs["Gamma flip"] = m["gamma_flip"]
    if m["struct"]["res"]:
        oi_refs["Nearest OI resistance"] = m["struct"]["res"]["level"]
    if m["struct"]["sup"]:
        oi_refs["Nearest OI support"] = m["struct"]["sup"]["level"]
    tol = max(m["step"] * 0.5, 0.15 * sd)

    rows = []
    for name, price, kind in levels:
        hits = [f"{n} {v:,.0f}" for n, v in oi_refs.items() if abs(v - price) <= tol]
        rows.append({"Level": name, "Price": price, "Dist pts": price - spot, "Dist σ": (price - spot) / sd,
                     "OI confluence": ("★ " + ", ".join(hits)) if hits else ""})
    rows.append({"Level": "◄ SPOT", "Price": spot, "Dist pts": 0.0, "Dist σ": 0.0, "OI confluence": ""})
    tbl = pd.DataFrame(rows).sort_values("Price", ascending=False).reset_index(drop=True)
    return {"available": True, "table": tbl, "ref_close": c, "basis": basis, "atr": atr, "sd": sd, "tol": tol,
            "band1": (c - sd, c + sd), "band2": (c - 2 * sd, c + 2 * sd), "live": live,
            "moved_sigma": (spot - c) / sd if sd else 0.0}


# ============================================================
# SESSION HISTORY + EXHAUSTION MONITOR
# ============================================================
def daily_sigma(m):
    days = 365.0 if TIME_BASIS["mode"] == "calendar" else 252.0
    return m["spot"] * m["atm_iv"] / 100.0 * math.sqrt(1.0 / days)


def hist_path(key):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key)
    return os.path.join(tempfile.gettempdir(), f"bbg_hist_{safe}.json")


def load_hist(key):
    try:
        with open(hist_path(key)) as f:
            return json.load(f)
    except Exception:
        return []


def save_hist(key, hist):
    try:
        with open(hist_path(key), "w") as f:
            json.dump(hist, f)
    except Exception:
        pass


def build_snapshot(m):
    df, spot = m["df"], m["spot"]
    sub = df[(df["Strike"] >= spot - OI_MAP_SPAN) & (df["Strike"] <= spot + OI_MAP_SPAN)]
    oi_map = {str(int(k)): [float(c), float(p)] for k, c, p in zip(sub["Strike"], sub["CE_OI"], sub["PE_OI"])}
    e = m["engine"]
    ce_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0
    pe_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    return {"ts": time.time(), "spot": spot, "syn_fut": m["syn_fut"], "basis": m["basis"],
            "iv": m["atm_iv"], "pcr": m["pcr"], "edge": pe_pct - ce_pct,
            "net_gex": m["net_gex"], "rr_25": m["rr_25"],
            "max_pain": m["max_pain"], "dte": m["dte"], "status": m["status"],
            "res": res["level"] if res else None, "sup": sup["level"] if sup else None, "oi": oi_map}


def record_snapshot(m, key):
    """Date-stamped session key so midnight rollover cleanly starts a fresh day."""
    dated_key = f"{datetime.now(IST).date().isoformat()}_{key}"
    if st.session_state.get("hist_key") != dated_key:
        st.session_state["hist_key"] = dated_key
        st.session_state["hist"] = load_hist(dated_key)
    hist = st.session_state["hist"]
    if hist and time.time() - hist[-1]["ts"] < HIST_MIN_GAP_SEC:
        return hist
    hist.append(build_snapshot(m))
    del hist[:-HIST_MAX]
    save_hist(dated_key, hist)
    return hist


def _nearest(hist, ts):
    return min(hist, key=lambda h: abs(h["ts"] - ts))


def lb_flows(then, now, spot, window):
    out = {"ce_build": 0.0, "ce_unw": 0.0, "pe_build": 0.0, "pe_unw": 0.0, "ce_base": 0.0, "pe_base": 0.0}
    for k, (c1, p1) in now["oi"].items():
        if abs(float(k) - spot) > window:
            continue
        out["ce_base"] += c1
        out["pe_base"] += p1
        if k in then["oi"]:
            c0, p0 = then["oi"][k]
            dc, dp = c1 - c0, p1 - p0
            out["ce_build" if dc > 0 else "ce_unw"] += abs(dc)
            out["pe_build" if dp > 0 else "pe_unw"] += abs(dp)
    return out


def exhaustion(hist, m, prev_close, lb_min):
    sd = daily_sigma(m)
    out = {"ready": False, "n": len(hist), "lb_min": lb_min, "sd": sd, "why": ""}
    if len(hist) < 3:
        out["why"] = f"Collecting history ({len(hist)} snapshot(s)). Needs at least 3 refreshes."
        return out
    now = hist[-1]
    older = hist[:-1]
    ref = _nearest(older, now["ts"] - lb_min * 60)
    age = (now["ts"] - ref["ts"]) / 60
    if age < max(5.0, 0.5 * lb_min):
        out["why"] = f"Collecting history: have {age:.0f} min, need about {max(5.0, 0.5 * lb_min):.0f}+ min."
        return out
    mid = _nearest(older, now["ts"] - age * 30)
    out["ready"] = True

    spot = now["spot"]
    in_win = [h for h in hist if h["ts"] >= ref["ts"]]
    lb_high, lb_low = max(h["spot"] for h in in_win), min(h["spot"] for h in in_win)
    h_high, h_low = max(h["spot"] for h in hist), min(h["spot"] for h in hist)
    pc = prev_close if prev_close and prev_close > 0 else spot
    ext = {1: max(spot - h_low, spot - pc, 0) / sd, -1: max(h_high - spot, pc - spot, 0) / sd}

    iv_peak = max(h["iv"] for h in in_win)
    iv_off_peak = (1 - now["iv"] / iv_peak) * 100 if iv_peak > 0 else 0.0
    iv_chg = (now["iv"] / ref["iv"] - 1) * 100 if ref["iv"] > 0 else 0.0
    pcr_d = now["pcr"] - ref["pcr"]
    edge_d = now["edge"] - ref["edge"]
    fl = lb_flows(ref, now, spot, max(300, m["step"] * 6))
    pct = lambda q, b: q / b * 100 if b > 0 else 0.0
    ce_b, pe_b = pct(fl["ce_build"], fl["ce_base"]), pct(fl["pe_build"], fl["pe_base"])

    def wall_oi_chg(level, idx):
        if level is None:
            return None
        k = str(int(level))
        if k in now["oi"] and k in ref["oi"] and ref["oi"][k][idx] > 0:
            return (now["oi"][k][idx] / ref["oi"][k][idx] - 1) * 100
        return None

    def side(sgn):
        sig = []
        name = "call" if sgn > 0 else "put"

        level = now["res"] if sgn > 0 else now["sup"]
        chg = wall_oi_chg(level, 0 if sgn > 0 else 1)
        if level is None:
            sig.append(("Wall absorption", 0.0, "no nearby OI wall"))
        else:
            gap = abs(level - spot) / sd
            if gap <= 0.35 and chg is not None and chg >= 1:
                sig.append(("Wall absorption", 1.0, f"spot {gap:.2f}σ from {level:,.0f} wall; its {name} OI {chg:+.1f}% over {age:.0f}m"))
            elif gap <= 0.35:
                sig.append(("Wall absorption", 0.5, f"spot {gap:.2f}σ from {level:,.0f} wall; OI not growing"))
            else:
                sig.append(("Wall absorption", 0.0, f"{gap:.2f}σ away from the {level:,.0f} wall"))

        extreme = (lb_high - spot if sgn > 0 else spot - lb_low) / sd
        if extreme <= 0.3 and iv_off_peak >= 3:
            sig.append(("IV rollover at extreme", 1.0, f"IV {iv_off_peak:.1f}% off its window peak while price is at the extreme"))
        elif extreme <= 0.3 and iv_off_peak >= 1.5:
            sig.append(("IV rollover at extreme", 0.5, f"IV {iv_off_peak:.1f}% off peak"))
        else:
            sig.append(("IV rollover at extreme", 0.0, f"IV {iv_chg:+.1f}% over window, {iv_off_peak:.1f}% off peak"))

        ed = -sgn * edge_d
        sig.append(("Flow edge flipping", 1.0 if ed >= 3 else 0.5 if ed >= 1.5 else 0.0,
                    f"PE-vs-CE net flow edge {edge_d:+.1f} pts over window"))

        pd_ = -sgn * pcr_d
        sig.append(("PCR turning", 1.0 if pd_ >= 0.03 else 0.5 if pd_ >= 0.015 else 0.0, f"PCR {pcr_d:+.3f} over window"))

        mine, other = (ce_b, pe_b) if sgn > 0 else (pe_b, ce_b)
        mq, oq = (fl["ce_build"], fl["pe_build"]) if sgn > 0 else (fl["pe_build"], fl["ce_build"])
        if mine >= 1 and mq >= 1.5 * oq:
            sig.append((f"Fresh {name} writing", 1.0, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite (near spot, {age:.0f}m)"))
        elif mq > oq and mine >= 0.5:
            sig.append((f"Fresh {name} writing", 0.5, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite"))
        else:
            sig.append((f"Fresh {name} writing", 0.0, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite"))

        mp = sgn * (spot - now["max_pain"]) / sd
        if now["dte"] > 2:
            sig.append(("Max pain pull", 0.0, f"{now['dte']}d to expiry, pull is weak"))
        else:
            sig.append(("Max pain pull", 1.0 if mp >= 0.5 else 0.5 if mp >= 0.3 else 0.0,
                        f"spot {mp:+.2f}σ beyond max pain {now['max_pain']:,.0f}"))

        first = sgn * (mid["spot"] - ref["spot"]) / sd
        second = sgn * (spot - mid["spot"]) / sd
        if mid is ref or first < 0.15:
            sig.append(("Momentum stall", 0.0, f"no clear first-leg move ({first:+.2f}σ)"))
        elif second <= 0:
            sig.append(("Momentum stall", 1.0, f"first leg {first:+.2f}σ, then {second:+.2f}σ (stalled/reversed)"))
        elif second < 0.4 * first:
            sig.append(("Momentum stall", 0.5, f"first leg {first:+.2f}σ, then only {second:+.2f}σ"))
        else:
            sig.append(("Momentum stall", 0.0, f"first leg {first:+.2f}σ, then {second:+.2f}σ (still running)"))

        score = sum(x[1] for x in sig) / len(sig) * 100
        ctx = ext[sgn]
        turned = ((lb_high - spot) if sgn > 0 else (spot - lb_low)) / sd >= 0.1
        if ctx < EXH_CONTEXT_SIGMA:
            status, flag = f"NO {'UP' if sgn > 0 else 'DOWN'}TREND TO EXHAUST (extension {ctx:.2f}σ)", ""
        elif score >= EXH_ALERT and turned:
            status, flag = "CONFIRMED: signals aligned and price has turned", "CONFIRMED"
        elif score >= EXH_ALERT:
            status, flag = "WATCH: exhaustion building, price has NOT turned yet", "WATCH"
        elif score >= EXH_WATCH:
            status, flag = "EARLY SIGNS", "EARLY"
        else:
            status, flag = "TREND INTACT", ""
        return {"score": round(score), "signals": sig, "status": status, "flag": flag,
                "ext": ctx, "turned": turned}

    out["buyer"], out["seller"] = side(1), side(-1)
    out.update({"age": age, "lb_high": lb_high, "lb_low": lb_low, "spot_chg_sigma": (spot - ref["spot"]) / sd})
    return out


def exh_flag(exh):
    if not exh or not exh.get("ready"):
        return ""
    return f"B:{exh['buyer']['flag']}|S:{exh['seller']['flag']}"


def chain_health(df, spot):
    """Report field coverage and uninitialized PrevOI=0 anomalies near spot."""
    if df is None or df.empty:
        return {"grade": "FAIL", "messages": ["Option-chain table is empty."], "coverage": {}, "atm_rows": 0}
    near = df[(df["Strike"] >= spot - 600) & (df["Strike"] <= spot + 600)]
    if near.empty:
        near = df.iloc[(df["Strike"] - spot).abs().argsort()[:min(12, len(df))]]
    coverage = {}
    for field in ("CE_OI_Present", "PE_OI_Present", "CE_PrevOI_Present", "PE_PrevOI_Present",
                  "CE_IV_Present", "PE_IV_Present", "CE_LTP_Present", "PE_LTP_Present"):
        coverage[field] = float(near[field].mean()) if field in near else 0.0
    messages = []
    if len(df) < 5:
        messages.append(f"Only {len(df)} strikes returned.")
    for side in ("CE", "PE"):
        if coverage.get(f"{side}_OI_Present", 0) < 0.8:
            messages.append(f"{side} OI field coverage is only {coverage.get(f'{side}_OI_Present', 0):.0%} near spot.")
        if coverage.get(f"{side}_PrevOI_Present", 0) < 0.8:
            messages.append(f"{side} previous-OI coverage is only {coverage.get(f'{side}_PrevOI_Present', 0):.0%}; OI-change interpretations are limited.")
        live = near[near[f"{side}_OI"] > 0]
        if len(live) >= 3:
            uninit_frac = ((live[f"{side}_PrevOI"] == 0) & (live[f"{side}_Vol"] < live[f"{side}_OI"])).mean()
            if uninit_frac >= 0.20:
                messages.append(f"{uninit_frac:.0%} of near-spot {side} strikes had PrevOI=0 with Vol < OI (masked to prevent phantom builds).")
    nonzero_oi = int(((near["CE_OI"] > 0) | (near["PE_OI"] > 0)).sum())
    nonzero_ltp = int(((near["CE_LTP"] > 0) | (near["PE_LTP"] > 0)).sum())
    nonzero_iv = int(((near["CE_IV"] > 0) | (near["PE_IV"] > 0)).sum())
    if nonzero_oi == 0:
        messages.append("No non-zero OI in the near-spot window; do not trust OI-derived signals.")
    if nonzero_ltp == 0 and nonzero_iv == 0:
        messages.append("No non-zero premium or IV in the near-spot window; chain may be stale, closed, or malformed.")
    grade = "FAIL" if (nonzero_oi == 0 and nonzero_ltp == 0 and nonzero_iv == 0) else "WARN" if messages else "PASS"
    return {"grade": grade, "messages": messages, "coverage": coverage, "atm_rows": len(near),
            "nonzero_oi_rows": nonzero_oi, "nonzero_ltp_rows": nonzero_ltp, "nonzero_iv_rows": nonzero_iv}


def data_quality(m):
    near = m["near"]
    msgs = []
    for side in ("CE", "PE"):
        live = near[near[f"{side}_OI"] > 0]
        if len(live) >= 3:
            frac = (live[f"{side}_PrevOI"] == 0).mean()
            if frac >= 0.25:
                msgs.append(f"{frac:.0%} of near-spot {side} strikes had previous OI = 0 (unverified builds were masked)")
    return "; ".join(msgs)


# ============================================================
# PREMIUM POTENTIAL SCANNER (Scenario Repricing)
# ============================================================
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    disc = math.exp(-RISK_FREE * T)
    if is_call:
        return S * _ncdf(d1) - K * disc * _ncdf(d2)
    return K * disc * _ncdf(-d2) - S * _ncdf(-d1)


def bs_delta(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma * sigma) * T) / sq
    return _ncdf(d1) if is_call else _ncdf(d1) - 1.0


def reprice(ltp, S0, S1, K, T0, T1, iv0, iv1, is_call):
    base = bs_price(S0, K, T0, iv0, is_call)
    if base < 1e-6:
        return None
    return ltp * bs_price(S1, K, T1, iv1, is_call) / base


def premium_scanner(m, horizon_days=0.25, iv_shock=15.0, skew=True):
    df, S0, step = m["df"], m["spot"], m["step"]
    atm_iv = m["atm_iv"]
    sd = daily_sigma(m)
    cal = TIME_BASIS["mode"] == "calendar"
    basis_days = 365.0 if cal else 252.0
    min_t = 10.0 / (basis_days * (1440.0 if cal else 375.0))
    T0 = max(m.get("t_years", max(m["dte"], 1) / 365.0), min_t)
    T1 = max(T0 - horizon_days / basis_days, min_t)
    span = max(2.5 * sd, 8 * step)
    cand = df[(df["Strike"] >= S0 - span) & (df["Strike"] <= S0 + span)]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    crush, spike = 1.0 - iv_shock / 100.0, 1.0 + iv_shock / 100.0

    rows = []
    for _, r in cand.iterrows():
        K = float(r["Strike"])
        for side in ("CE", "PE"):
            is_call = side == "CE"
            sign = 1.0 if is_call else -1.0
            ltp = float(r.get(f"{side}_LTP", 0) or 0)
            if ltp < MIN_PREMIUM:
                continue
            iv0 = float(r.get(f"{side}_IV", 0) or 0) or atm_iv
            mult = lambda S1, iv1=iv0: reprice(ltp, S0, S1, K, T0, T1, iv0, iv1, is_call)
            Sfav = lambda k: S0 + sign * k * sd
            coef = SKEW_COEF if skew else 0.0
            iv_at = lambda k: iv0 * max(0.2, 1.0 + (-sign) * coef * (iv_shock / 100.0) * k)
            m05, m1, m15 = mult(Sfav(0.5), iv_at(0.5)), mult(Sfav(1.0), iv_at(1.0)), mult(Sfav(1.5), iv_at(1.5))
            m_crush = mult(Sfav(1.0), iv0 * crush)
            m_spike = mult(Sfav(1.0), iv0 * spike)
            m_wrong = mult(S0 - sign * 0.5 * sd, iv_at(-0.5))
            if None in (m05, m1, m15, m_crush, m_spike, m_wrong):
                continue
            wall = (res if is_call else sup)
            m_wall = mult(wall["level"], iv_at(abs(wall["level"] - S0) / sd)) if wall else None
            x = lambda v: (v / ltp) if v is not None else None

            need2 = None
            lo, hi = 0.0, 4.0
            if (mult(Sfav(hi), iv_at(hi)) or 0) >= 2 * ltp:
                for _i in range(40):
                    mid = (lo + hi) / 2
                    if (mult(Sfav(mid), iv_at(mid)) or 0) >= 2 * ltp:
                        hi = mid
                    else:
                        lo = mid
                need2 = hi

            bid, ask = float(r.get(f"{side}_Bid", 0) or 0), float(r.get(f"{side}_Ask", 0) or 0)
            spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask > 0 else float("nan")
            d_now = abs(float(r.get(f"{side}_Delta", 0) or 0)) or abs(bs_delta(S0, K, T0, iv0, is_call))
            d_1s = abs(bs_delta(Sfav(1.0), K, T1, iv_at(1.0), is_call))
            money = (K - S0) if is_call else (S0 - K)
            rows.append({
                "Side": side, "Strike": K,
                "Money": f"OTM {money:.0f}" if money > step * 0.4 else ("ITM " + f"{-money:.0f}" if money < -step * 0.4 else "ATM"),
                "LTP": ltp, "Delta": d_now, "Delta@1σ": d_1s, "Spread%": spread,
                "Vol": float(r.get(f"{side}_Vol", 0) or 0),
                "OI": float(r[f"{side}_OI"]), "OIchg": float(r[f"{side}_OI_Chg"]),
                "x0.5σ": x(m05), "x1σ": x(m1), "x1.5σ": x(m15), "xWall": x(m_wall),
                "x1σ crush": x(m_crush), "x1σ spike": x(m_spike), "xWrong": x(m_wrong),
                "Needs2x(σ)": need2,
            })
    out = pd.DataFrame(rows)
    meta = {"sd": sd, "T0d": T0 * basis_days, "T1d": T1 * basis_days, "crush": iv_shock, "skew": coef}
    if out.empty:
        return out, meta

    out["vol_pct"] = out["Vol"].rank(pct=True)
    fuel = out["OIchg"].clip(lower=0)
    out["fuel_pct"] = fuel / fuel.max() if fuel.max() > 0 else 0.0
    reward = 0.5 * (out["x1σ"] - 1) + 0.5 * (out["x1σ crush"] - 1)
    loss = (1 - out["xWrong"]).clip(lower=0, upper=1)
    liq = out["Spread%"].apply(lambda v: 0.5 if pd.isna(v) else 1.0 if v <= 3 else 0.7 if v <= 6 else 0.4 if v <= 12 else 0.15)
    prob = (2 * out["Delta"]).clip(upper=1.0) ** 0.5
    activity = 0.5 + 0.25 * out["vol_pct"] + 0.25 * out["fuel_pct"]
    out["Score"] = (reward.clip(lower=0) / (0.25 + loss)) * liq * prob * activity
    out = out.sort_values("Score", ascending=False).reset_index(drop=True)
    return out, meta


def fmt_scanner(df):
    d = df.copy()
    for c in ("x0.5σ", "x1σ", "x1.5σ", "xWall", "x1σ crush", "x1σ spike", "xWrong"):
        d[c] = d[c].map(lambda v: "-" if v is None or pd.isna(v) else f"{v:.2f}x")
    d["Strike"] = d["Strike"].map(lambda v: f"{v:,.0f}")
    d["LTP"] = d["LTP"].map(lambda v: f"{v:,.1f}")
    d["Δ"] = d.apply(lambda r: f"{r['Delta']:.2f}→{r['Delta@1σ']:.2f}", axis=1)
    d["Spread%"] = d["Spread%"].map(lambda v: "-" if pd.isna(v) else f"{v:.1f}")
    d["Vol"] = d["Vol"].map(lambda v: f"{v:,.0f}")
    d["OIchg"] = d["OIchg"].map(lambda v: f"{v:+,.0f}")
    d["Needs2x(σ)"] = d["Needs2x(σ)"].map(lambda v: ">4" if v is None or pd.isna(v) else f"{v:.2f}")
    d["Score"] = d["Score"].map(lambda v: f"{v:.2f}")
    cols = ["Side", "Strike", "Money", "LTP", "Δ", "Spread%", "Vol", "OIchg", "x0.5σ", "x1σ", "x1.5σ",
            "xWall", "x1σ crush", "x1σ spike", "xWrong", "Needs2x(σ)", "Score"]
    return d[cols]


def market_open_now(now):
    return now.weekday() < 5 and 9 * 60 + 15 <= now.hour * 60 + now.minute < 15 * 60 + 30


def show_df(df, **kw):
    try:
        st.dataframe(df, width="stretch", **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, **kw)


@st.cache_data(ttl=120, show_spinner=False)
def fetch_today_ohlc(scrip, seg, token, client_id):
    try:
        r = requests.post(f"{BASE_URL}/marketfeed/ohlc", headers=auth_headers(token, client_id),
                          json={seg: [scrip]}, timeout=10)
        check(r)
        q = (r.json().get("data") or {}).get(seg, {}).get(str(scrip)) or {}
        o = q.get("ohlc") or {}
        vals = {k: _n(o.get(k2)) for k, k2 in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
        if all(v > 0 for v in vals.values()):
            return vals
    except Exception:
        pass
    return None


def esc(x):
    return html.escape(str(x if x is not None else "-"))


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL CONFIG v11.0")
    dhan_token = clean(st.text_input("DHAN ACCESS TOKEN", type="password", value=DEFAULT_DHAN_TOKEN,
                                     help="The long JWT (starts with 'eyJ'). NOT the API key/secret."))
    client_id = clean(st.text_input("DHAN CLIENT ID", value=DEFAULT_CLIENT_ID))
    groq_key = st.text_input("GROQ API KEY", type="password", value=DEFAULT_GROQ_KEY,
                             help="Paste your Groq API key here to activate the AI Council.").strip()

    groq_model = st.selectbox("GROQ MODEL", GROQ_MODELS, index=0)

    if not dhan_token or not client_id:
        st.title("VIPUL BLOOMBERG TERMINAL v11.0")
        st.warning("Connect Dhan Data API to load the terminal. Enter the Dhan access token and client ID in the sidebar.")
        st.markdown("**Setup checklist**\n\n1. Use the Dhan **access token/JWT**, not API key/secret.\n2. Confirm the token matches the client ID.\n3. Add `DHAN_ACCESS_TOKEN`, `DHAN_CLIENT_ID`, and optionally `GROQ_API_KEY` to Streamlit Secrets for deployment.\n4. Redeploy after replacing `app.py` with the complete file.")
        st.stop()

    with st.expander("🔧 DIAGNOSTICS"):
        st.write(f"Token length: **{len(dhan_token)}** | starts: `{dhan_token[:3]}…`")
        st.write(f"Client ID: `{client_id}`")
        st.write(f"Token pre-filled from secrets/env: **{bool(DEFAULT_DHAN_TOKEN)}**")
        st.write(f"`websocket-client` installed: **{ws_client is not None}**")
        if not dhan_token.startswith("eyJ"):
            st.warning("Token does not start with 'eyJ' - this may be the API key, not the access token.")
        if st.button("Test token (/v2/profile)"):
            try:
                pr = requests.get(PROFILE_URL,
                                  headers={"access-token": dhan_token, "client-id": client_id},
                                  timeout=10)
                st.code(f"{pr.status_code}\n{pr.text[:600]}")
            except Exception as e:
                st.error(e)

    idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]

    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token, client_id)
    except Exception as e:
        expiries = []
        st.error(f"Could not load expiries: {e}")
    if not expiries:
        st.warning("No expiries available. Use DIAGNOSTICS → Test token.")
        st.stop()

    expiry = st.selectbox("EXPIRY DATE", expiries)
    auto_prev = st.toggle("AUTO PREV CLOSE", value=True,
                          help="Fetch yesterday's close from Dhan. Turn off to type it manually.")
    auto_val, auto_src = (None, "")
    if auto_prev:
        auto_val, auto_src = fetch_prev_close(info["scrip"], info["seg"], dhan_token, client_id)
    if auto_prev and auto_val:
        prev_close = float(auto_val)
        st.caption(f"PREV CLOSE = {prev_close:,.2f}  ({auto_src})")
    else:
        if auto_prev:
            st.warning(f"Auto prev close unavailable: {auto_src}. Enter it manually.")
        prev_close = st.number_input("PREV CLOSE (0 = unknown)", min_value=0.0, value=0.0, step=float(info["step"]),
                                     key=f"prev_{idx_name}",
                                     help="Yesterday's close. 0 means unknown: the day-change terms are skipped.")

    enable_ws = st.toggle("LIVE DHAN v2 WEBSOCKET", value=True,
                          help="Background thread streaming live Dhan v2 binary ticks for Lee-Ready tick delta.")
    live_news = st.toggle("LIVE NEWS FEED", value=True,
                          help="Fetch fresh market headlines (Google News RSS) for the Sentiment agent.")
    news = st.text_area("EXTRA NEWS / MACRO NOTES", placeholder="Optional: add your own notes on top of the live feed",
                        height=80)
    horizon_label = st.selectbox("SCENARIO HORIZON (premium scanner)",
                                 ["Intraday (0.25d)", "1 day", "2 days"], index=0)
    horizon_days = {"Intraday (0.25d)": 0.25, "1 day": 1.0, "2 days": 2.0}[horizon_label]
    iv_shock = st.slider("IV CRUSH / SPIKE SHOCK (%)", 5, 40, 15, step=5,
                         help="Relative change applied to IV in the crush / spike scenarios.")
    use_skew = st.toggle("SPOT-VOL LINK IN SCENARIOS", value=True, key="use_skew",
                         help="Scenario IV falls when spot rises and rises when spot falls (index leverage effect), "
                              f"by {SKEW_COEF} x the IV shock per 1 sigma of spot move.")
    _basis = st.selectbox("VOL TIME BASIS", ["Calendar (days/365)", "Trading (252 d x 375 min)"], index=0, key="time_basis",
                          help="Use Calendar unless your IV source is annualised on trading time.")
    TIME_BASIS["mode"] = "calendar" if _basis.startswith("Calendar") else "trading"
    exh_lookback = st.slider("EXHAUSTION LOOKBACK (min)", 6, 45, 15, step=3,
                             help="History window used by the exhaustion monitor. With a 3-min refresh, 15 min = 5 snapshots.")
    force_run = st.button("FORCE AI RUN")

    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="bbg")

# ============================================================
# START / UPDATE WEBSOCKET WORKER & FETCH CHAIN
# ============================================================
ws_worker = get_ws_worker()
if enable_ws:
    ws_worker.start_or_update(dhan_token, client_id, info["scrip"], info["seg"])
else:
    ws_worker.stop()
ws_snap = ws_worker.snapshot()

try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

if df.empty or spot == 0:
    st.warning("Option chain returned no data (market closed or invalid expiry).")
    st.stop()

# If WebSocket has a fresh tick (<15s old) within 1% of chain spot, display the live tick alongside chain spot
if ws_snap["connected"] and ws_snap["latest_ltp"] > 0 and (ws_snap["last_age_sec"] or 999) < 15:
    if abs(ws_snap["latest_ltp"] - spot) / spot < 0.01:
        spot = float(ws_snap["latest_ltp"])

m = compute_metrics(df, spot, prev_close, expiry, info["step"])
health = chain_health(df, spot)
vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
hist = record_snapshot(m, f"{idx_name}|{expiry}")
m["exh"] = exhaustion(hist, m, prev_close, exh_lookback)
m["exh_flag"] = exh_flag(m["exh"])

# ---- live news ----
news_items, news_status = ([], ["LIVE NEWS: off"])
if live_news:
    try:
        raw_items, news_status = fetch_news(idx_name, NEWS_EXTRA_FEEDS)
        news_items = recent_news(raw_items)
    except Exception as e:
        news_status = [f"fetch_news crashed: {e}"]
news_text = news_for_ai(news_items, news)
nhash = news_hash(news_items)

# ---- AI gating + cache ----
changed, reason = evaluate_market_state_change(m, vel, nhash)
_now_ai = datetime.now(IST)
_last_ai_time = st.session_state.get("last_ai_time")
_ai_age = (_now_ai - _last_ai_time).total_seconds() if isinstance(_last_ai_time, datetime) else float("inf")
_ai_stale = _ai_age >= AI_VERDICT_MAX_AGE_SEC
if _ai_stale and not changed:
    reason = "VERDICT_EXPIRED"
_last_try = st.session_state.get("last_ai_attempt")
_retry_ok = _last_try is None or (_now_ai - _last_try).total_seconds() >= AI_RETRY_SEC
if force_run or ((changed or _ai_stale) and _retry_ok):
    st.session_state["last_ai_attempt"] = datetime.now(IST)
    ai = run_council(m, vel, news_text, groq_key, groq_model)
    if "error" not in ai:
        st.session_state.last_ai_verdict = ai
        st.session_state.last_ai_time = datetime.now(IST)
        st.session_state.last_ai_state = {
            "spot": m["spot"], "pcr": m["pcr"], "call_wall": m["call_wall"],
            "put_wall": m["put_wall"], "gamma_strike": m["gamma_strike"], "status": m["status"],
            "gex_regime": m["gex_regime"],
            "trap_active": bool(m["ce_trap"] or m["pe_trap"]),
            "news_hash": nhash,
            "exh_flag": m.get("exh_flag", ""),
        }
        gate_label = f"FRESH ({'FORCED' if force_run else reason}) | {ai.get('_model', groq_model)}"
    else:
        err = ai["error"]
        cached = st.session_state.get("last_ai_verdict")
        ai = cached if cached and not _ai_stale else {"error": err}
        gate_label = f"AI ERROR: {err}" + (" (showing fresh cached verdict)" if cached and not _ai_stale else " (no fresh verdict available)")
else:
    cached = st.session_state.get("last_ai_verdict")
    t = st.session_state.get("last_ai_time")
    if cached and not _ai_stale:
        ai = cached
        gate_label = f"CACHED {t.strftime('%H:%M:%S') if t else ''} | age {_ai_age:.0f}s"
    else:
        ai = {"error": "AI verdict expired; waiting for a fresh successful Groq response."}
        gate_label = f"STALE / NO CURRENT VERDICT | last success {t.strftime('%H:%M:%S') if t else 'never'}"

# ---- risk-validated signal + premium scanner + SQLite MFE/MAE reconciliation ----
now_ist = datetime.now(IST)
ai_ok = not ("error" in ai and "signal" not in ai)
if health["grade"] == "FAIL":
    ai = {"error": "Data-integrity lock: option-chain fields are unusable; no directional AI signal will be shown."}
    ai_ok = False
sig = build_signal(m, ai) if ai_ok else None
scan, smeta = premium_scanner(m, horizon_days, iv_shock, skew=use_skew)

try:
    log_and_reconcile_signals(idx_name, expiry, m, sig, now_ist)
except Exception as _db_err:
    pass

# ============================================================
# DASHBOARD HEADER
# ============================================================
st.markdown(
    f"## {idx_name} // SPOT {spot:,.2f}"
    + (f"  ({spot - prev_close:+,.2f})" if prev_close > 0 else "")
    + f"  |  SYN FUT {m['syn_fut']:,.2f} ({m['basis']:+.1f})"
)
_mkt = "OPEN" if market_open_now(now_ist) else "CLOSED (spot, OI and premiums shown are the last close, not live)"
_ws_badge = f"WS LIVE ({ws_snap['packet_count']} pkts)" if ws_snap["connected"] else "WS STANDBY (Chain Microstructure Active)"
st.caption(f"{now_ist.strftime('%a %d %b %Y %H:%M:%S')} IST | market {_mkt} | DTE: {m['dte']} ({m['days_left']:.2f}d) | {_ws_badge}")

_health_color = {"PASS": "#00e676", "WARN": "#ffab00", "FAIL": "#ff1744"}.get(health["grade"], "#90a4ae")
st.markdown(f"<div class='bbg-panel' style='border-left:4px solid {_health_color};'><b style='color:{_health_color};'>DATA HEALTH: {health['grade']}</b> &nbsp;|&nbsp; strikes {len(df)} &nbsp;|&nbsp; near-spot rows checked {health['atm_rows']} &nbsp;|&nbsp; OI rows non-zero {health.get('nonzero_oi_rows', 0)} &nbsp;|&nbsp; premium rows non-zero {health.get('nonzero_ltp_rows', 0)} &nbsp;|&nbsp; IV rows non-zero {health.get('nonzero_iv_rows', 0)}</div>", unsafe_allow_html=True)
if health["messages"]:
    for _msg in health["messages"]:
        st.warning(_msg)
if health["grade"] == "FAIL":
    st.error("DATA-INTEGRITY LOCK: directional interpretation is disabled because the chain lacks usable near-spot data.")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "📊 Dashboard & Engine",
    "📈 GEX, Vanna & Skew Charts",
    "📐 Day Levels",
    "🧭 Exhaustion",
    "🎯 Premium Scanner",
    "🤖 AI Council & News",
    "🏆 Signal Audit (MFE/MAE)",
    "📋 Option Chain & Stream",
])

with tab1:
    status = m["status"]
    if "BULLISH GAMMA BLAST" in status:
        color, flash = "#00e676", "flash-green"
    elif "BEARISH GAMMA BLAST" in status:
        color, flash = "#ff1744", "flash-red"
    elif "TRAP" in status or "CONFLICTED" in status or "BUILD" in status:
        color, flash = "#ffab00", "flash-amber"
    else:
        color, flash = "#90a4ae", ""

    st.markdown(f"""
    <div class="bbg-panel {flash}" style="border:2px solid {color};">
      <div class="bbg-title">Dual-Force Engine &nbsp;|&nbsp; {esc(m['gex_regime'])}</div>
      <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
      <div class="bbg-desc">{m['desc']}</div>
    </div>""", unsafe_allow_html=True)

    if prev_close > 0 and abs(prev_close - spot) / spot > 0.05:
        st.warning(f"PREV CLOSE ({prev_close:,.0f}) is more than 5% away from spot ({spot:,.0f}). "
                   "It is probably stale or wrong, and it skews the BIAS SCORE.")

    # ---- DEALER POSITIONING & VOL SURFACE STRIP ----
    st.caption("INSTITUTIONAL DEALER GREEKS & VOLATILITY SURFACE (Put-Call Parity Forward + Signed GEX/VEX/CEX)")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("GAMMA FLIP (ZERO-GEX)", f"{m['gamma_flip']:,.0f}" if m["gamma_flip"] else "—",
              f"{m['spot'] - m['gamma_flip']:+,.0f} vs spot" if m["gamma_flip"] else "One-sided GEX", delta_color="off")
    g2.metric("SYNTHETIC FUTURE", f"{m['syn_fut']:,.1f}", f"Basis {m['basis']:+.1f} pts", delta_color="off")
    g3.metric("25Δ RISK REVERSAL", f"{m['rr_25']:+.2f}%", f"25Δ CE {m['iv_25c']:.1f}% vs PE {m['iv_25p']:.1f}%", delta_color="off")
    g4.metric("25Δ BUTTERFLY (TAIL)", f"{m['bf_25']:+.2f}%", f"Net VEX {m['net_vex']:+,.0f} | CEX {m['net_cex']:+,.0f}", delta_color="off")

    # ---- ORDER-BOOK & QUOTE-LOCATION MICROSTRUCTURE STRIP ----
    mc = m["micro"]
    st.caption("ORDER-BOOK & QUOTE-SPREAD MICROSTRUCTURE (Near-ATM Bid/Ask Aggression + Lee-Ready Tick Stream)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CE QUOTE LOCATION", f"{mc['ce_quote_loc']*100:.0f}%", f"Med spread {mc['ce_med_spread_pct']:.2f}%", delta_color="off")
    m2.metric("PE QUOTE LOCATION", f"{mc['pe_quote_loc']*100:.0f}%", f"Med spread {mc['pe_med_spread_pct']:.2f}%", delta_color="off")
    m3.metric("NET TAKER EDGE", f"{mc['net_taker_edge']:+.1f} pts", mc["label"], delta_color="off")
    if ws_snap["connected"] and (ws_snap["buy_ticks"] + ws_snap["sell_ticks"]) > 0:
        m4.metric("LEE-READY TICK DELTA", f"{ws_snap['tick_delta_pct']:+.1f}%",
                  f"Buy {ws_snap['buy_ticks']} / Sell {ws_snap['sell_ticks']} ticks", delta_color="off")
    else:
        m4.metric("LEE-READY STREAM", "STANDBY", f"Net GEX {m['net_gex']:+,.0f}", delta_color="off")

    # ---- ENGINE DEBUG ROW ----
    es = m["engine"]
    st.caption(f"DUAL-FORCE ENGINE INPUTS (near-spot strikes, % of that side's OI | each leg needs >= {MIN_FLOW_PCT:.1f}%)")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("CE EXITS", f"{es['ce_exit_pct']:.1f}%", f"{es['ce_exits']:+,.0f} qty", delta_color="off")
    d2.metric("PE BUILDS", f"{es['pe_build_pct']:.1f}%", f"{es['pe_build']:+,.0f} qty", delta_color="off")
    d3.metric("PE EXITS", f"{es['pe_exit_pct']:.1f}%", f"{es['pe_exit_pct']:.1f}% ({es['pe_exits']:+,.0f})", delta_color="off")
    d4.metric("CE BUILDS", f"{es['ce_build_pct']:.1f}%", f"{es['ce_build']:+,.0f} qty", delta_color="off")
    f1, f2, f3 = st.columns(3)
    f1.metric("BULL FORCE (PE build + CE exit)", f"{es['bull_force']:.1f}")
    f2.metric("BEAR FORCE (CE build + PE exit)", f"{es['bear_force']:.1f}")
    f3.metric("FORCE RATIO", f"{min(es['force_ratio'], 99):.2f}x", f"winner needs >= {DOMINANCE}x", delta_color="off")
    _zero = [n for n, k in (("CE EXITS", "ce_exit_pct"), ("PE BUILDS", "pe_build_pct"),
                            ("PE EXITS", "pe_exit_pct"), ("CE BUILDS", "ce_build_pct")) if es[k] == 0]
    if _zero:
        st.caption(f"{', '.join(_zero)} = 0.0% means no near-spot strike moved that way by more than {NOISE_PCT}% of that side's OI.")
    _dq = data_quality(m)
    if _dq:
        st.warning("Data quality: " + _dq + ".")

    if m["iv_gap"] > 3:
        st.caption(f"⚠ ATM IV mismatch: CE {m['atm_iv_ce']:.1f}% vs PE {m['atm_iv_pe']:.1f}% (implied basis {m['basis']:+.1f} pts). "
                   f"Expected move uses the average ({m['atm_iv']:.1f}%).")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
    c2.metric("PCR", f"{m['pcr']:.2f}")
    c3.metric("EXPECTED MOVE (±)", f"{m['exp_move']:,.0f}", f"IV {m['atm_iv']:.1f}% | {m['days_left']:.1f}d")
    c4.metric("OI BATTLE", m["battle"])

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("CALL WALL", f"{m['call_wall']:,.0f}")
    c6.metric("PUT WALL", f"{m['put_wall']:,.0f}")
    c7.metric("MAX PAIN", f"{m['max_pain']:,.0f}")
    c8.metric("GAMMA STRIKE", f"{m['gamma_strike']:,.0f}", f"{m['gamma_strike'] - spot:+,.0f} vs spot")

    c9, c10, c11, c12 = st.columns(4)
    c9.metric(f"CE TRAP (+{m['buf']}pt)", f"{m['ce_trap']:,.0f}" if m["ce_trap"] else "—")
    c10.metric(f"PE TRAP (-{m['buf']}pt)", f"{m['pe_trap']:,.0f}" if m["pe_trap"] else "—")
    c11.metric("NET CE OI Δ", f"{m['dce']:+,.0f}")
    c12.metric("NET PE OI Δ", f"{m['dpe']:+,.0f}")

    st.markdown("#### 3-MIN OI VELOCITY (near spot)")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("CE BUILDS", f"{vel['ce_builds']:,.0f}")
    v2.metric("CE UNWINDS", f"{vel['ce_unwinds']:,.0f}", f"{vel.get('ce_unw_pct', 0):.2f}% of near OI", delta_color="off")
    v3.metric("PE BUILDS", f"{vel['pe_builds']:,.0f}")
    v4.metric("PE UNWINDS", f"{vel['pe_unwinds']:,.0f}", f"{vel.get('pe_unw_pct', 0):.2f}% of near OI", delta_color="off")
    if sum(vel[k] for k in ("ce_builds", "ce_unwinds", "pe_builds", "pe_unwinds")) == 0:
        st.caption("Velocity needs two snapshots ~3 min apart. It will populate after the next refresh.")


with tab2:
    st.markdown("#### 📈 DEALER POSITIONING (GEX / VANNA / CHARM) & VOLATILITY SKEW")
    chart_span = st.select_slider(
        "STRIKE WINDOW AROUND SPOT",
        options=[6, 10, 14, 20],
        value=10,
        format_func=lambda x: f"±{x} strikes (±{x * m['step']} pts)",
    )
    cdf = m["df"][
        (m["df"]["Strike"] >= spot - chart_span * m["step"])
        & (m["df"]["Strike"] <= spot + chart_span * m["step"])
    ].copy()
    if cdf.empty:
        st.info("No strikes in selected window.")
    else:
        cdf["Strike_Label"] = cdf["Strike"].map(lambda k: f"{int(k)}")
        gcol1, gcol2 = st.columns(2)
        with gcol1:
            st.markdown("##### 1. Signed Net Dealer GEX per Strike")
            st.caption("Positive (+) = Call gamma > Put gamma (dealers dampen moves / pin). Negative (−) = Put gamma dominates (dealers amplify moves).")
            st.bar_chart(cdf.set_index("Strike")[["Net_GEX"]])
        with gcol2:
            st.markdown("##### 2. Call GEX vs Put GEX Split")
            st.caption(f"Gamma Flip Level: {f'{m[\"gamma_flip\"]:,.1f}' if m['gamma_flip'] else 'None in window'} | Peak Gamma Strike: {m['gamma_strike']:,.0f}")
            st.bar_chart(cdf.set_index("Strike")[["CE_GEX", "PE_GEX"]])

        vcol1, vcol2 = st.columns(2)
        with vcol1:
            st.markdown("##### 3. Higher-Order Dealer Flows: Net Vanna (VEX) & Charm (CEX)")
            st.caption("Vanna measures dealer delta hedging when IV shifts; Charm measures dealer delta unwind as time decays toward 15:30 IST.")
            st.bar_chart(cdf.set_index("Strike")[["Net_VEX", "Net_CEX"]])
        with vcol2:
            st.markdown("##### 4. Implied Volatility Smile / Skew Surface (CE_IV vs PE_IV)")
            st.caption(f"25Δ Risk Reversal: {m['rr_25']:+.2f}% | 25Δ Butterfly: {m['bf_25']:+.2f}% | Synthetic Basis: {m['basis']:+.1f} pts")
            iv_df = cdf[(cdf["CE_IV"] > 0) | (cdf["PE_IV"] > 0)].set_index("Strike")[["CE_IV", "PE_IV"]]
            st.line_chart(iv_df)

        st.markdown("##### 5. Strike-by-Strike OI Changes (Intraday Builds vs Unwinds)")
        st.bar_chart(cdf.set_index("Strike")[["CE_OI_Chg", "PE_OI_Chg"]])


with tab3:
    st.markdown("#### 📐 DAY MOVEMENT LEVELS")
    try:
        candles = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id)
        candle_err = ""
    except Exception as e:
        candles, candle_err = [], str(e)
    _after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    _today_ohlc = fetch_today_ohlc(info["scrip"], info["seg"], dhan_token, client_id) if _after_close else None
    dl = compute_day_levels(candles, m["spot"], daily_sigma(m), m, prev_close, _today_ohlc)
    if candle_err:
        st.warning(f"Daily candles unavailable ({candle_err[:160]}). Showing sigma bands around the sidebar PREV CLOSE only.")
    if not dl.get("available", True):
        st.warning(dl["basis"] + ". Day-change levels are disabled until a valid reference close is available.")
    else:
        st.caption(f"Basis: {dl['basis']} | 1σ daily = Spot × ATM IV × √(1/365) ≈ {dl['sd']:,.0f} pts"
                   + (f" | ATR(14) ≈ {dl['atr']:,.0f} pts" if dl["atr"] else ""))
        lc1, lc2, lc3 = st.columns(3)
        lc1.metric("1σ RANGE (68%)", f"{dl['band1'][0]:,.0f} – {dl['band1'][1]:,.0f}")
        lc2.metric("2σ RANGE (95%)", f"{dl['band2'][0]:,.0f} – {dl['band2'][1]:,.0f}")
        lc3.metric("MOVE SO FAR", f"{dl['moved_sigma']:+.2f}σ", f"{m['spot'] - dl['ref_close']:+,.0f} pts vs ref close", delta_color="off")
        show = dl["table"].copy()
        show["Price"] = show["Price"].map(lambda v: f"{v:,.0f}")
        show["Dist pts"] = show["Dist pts"].map(lambda v: f"{v:+,.0f}")
        show["Dist σ"] = show["Dist σ"].map(lambda v: f"{v:+.2f}")
        show_df(show, hide_index=True)
        st.caption(f"★ = level within ±{dl['tol']:,.0f} pts of an OI level (wall, max pain, gamma strike, gamma flip).")


with tab4:
    st.markdown("#### 🧭 EXHAUSTION MONITOR (is the trend running out of fuel?)")
    ex = m["exh"]
    if not ex["ready"]:
        st.info(ex["why"] + " History is saved per day and survives a browser refresh, but only builds while this page is open.")
    else:
        st.caption(f"Window {ex['age']:.0f} min ({ex['n']} snapshots stored today) | spot {ex['spot_chg_sigma']:+.2f}σ over window | "
                   f"window high {ex['lb_high']:,.0f} / low {ex['lb_low']:,.0f} | 1σ daily ≈ {ex['sd']:,.0f} pts")
        ecol1, ecol2 = st.columns(2)
        for col, key, title in ((ecol1, "buyer", "BUYER EXHAUSTION (uptrend fading)"),
                                (ecol2, "seller", "SELLER EXHAUSTION (downtrend fading)")):
            side_ = ex[key]
            scol = "#ff1744" if side_["flag"] == "CONFIRMED" else "#ffab00" if side_["flag"] in ("WATCH", "EARLY") else "#90a4ae"
            rows = "".join(
                f'<div class="agent">{"🔴" if v >= 1 else "🟠" if v >= 0.5 else "⚪"} <b>{esc(n)}:</b> {esc(d)}</div>'
                for n, v, d in side_["signals"])
            col.markdown(f"""<div class="bbg-panel" style="border:2px solid {scol};">
              <div class="bbg-title">{title}</div>
              <div class="bbg-big" style="color:{scol};">{side_['score']}/100</div>
              <div class="bbg-desc" style="color:{scol};">{esc(side_['status'])}</div>
              {rows}</div>""", unsafe_allow_html=True)
        st.caption(f"Exhaustion is a warning, not a reversal call. CONFIRMED needs score >= {EXH_ALERT} AND a price turn of "
                   "at least 0.1σ off the window extreme. A BUY/SELL is blocked when its own side is CONFIRMED.")
    with st.expander("SESSION HISTORY (spot / basis / IV / PCR / GEX)"):
        if len(hist) >= 2:
            hdf = pd.DataFrame([{"Time": datetime.fromtimestamp(h["ts"], IST).strftime("%H:%M:%S"),
                                 "Spot": h["spot"], "Basis": round(h.get("basis", 0.0), 1),
                                 "ATM IV": round(h["iv"], 2), "25Δ RR": round(h.get("rr_25", 0.0), 2),
                                 "PCR": round(h["pcr"], 3), "Flow edge": round(h["edge"], 2),
                                 "Status": h["status"]} for h in hist])
            st.line_chart(hdf.set_index("Time")[["Spot"]])
            st.line_chart(hdf.set_index("Time")[["ATM IV"]])
            show_df(hdf.iloc[::-1], hide_index=True)
        else:
            st.write("Waiting for more snapshots.")


with tab5:
    st.markdown("#### 🎯 PREMIUM POTENTIAL SCANNER")
    lean_now = sum(c["v"] for c in chain_checks(m))
    fav = "CE" if lean_now > 0 else "PE" if lean_now < 0 else None
    if "BULLISH GAMMA" in m["status"]:
        fav = "CE"
    elif "BEARISH GAMMA" in m["status"]:
        fav = "PE"
    if scan.empty:
        st.info("No priced options found (needs last_price from the option chain; market may be closed or premiums < "
                f"{MIN_PREMIUM:.0f}).")
    else:
        fav_txt = {"CE": "CALLS (bullish side)", "PE": "PUTS (bearish side)"}.get(fav, "NO CLEAR SIDE - both shown, treat as low edge")
        st.caption(f"Chain lean {lean_now:+d} | status {m['status']} → favoured: {fav_txt}. "
                   f"1σ daily move ≈ {smeta['sd']:,.0f} pts | horizon {horizon_label} (time left {smeta['T0d']:.1f}d → {smeta['T1d']:.1f}d) | "
                   f"IV shock ±{smeta['crush']:.0f}%" + (f" | spot-vol link ±{smeta['skew'] * smeta['crush']:.1f}% IV per σ." if smeta["skew"] else "."))
        top = scan[scan["Side"] == fav].head(5) if fav else scan.head(6)
        other = scan[scan["Side"] != fav].head(3) if fav else None
        if top.empty:
            st.info("No priced option on the favoured side right now; showing the best-ranked contracts overall.")
            top = scan.head(6)
        show_df(fmt_scanner(top), hide_index=True)
        if other is not None and not other.empty:
            with st.expander("Counter-trend side (against the chain lean)"):
                show_df(fmt_scanner(other), hide_index=True)

        b = top.iloc[0]
        sgn = 1 if b["Side"] == "CE" else -1
        s1 = m["spot"] + sgn * smeta["sd"]
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Top pick walk-through: {m['spot']:,.0f} spot, {b['Strike']:,.0f} {b['Side']} ({b['Money']}) at ₹{b['LTP']:,.1f}</div>
          <div class="agent">If spot moves 1σ in favour to ≈ <b>{s1:,.0f}</b>: premium ≈ <b>{b['x1σ']:.2f}x</b> (₹{b['LTP']*b['x1σ']:,.1f}); delta rises {b['Delta']:.2f} → {b['Delta@1σ']:.2f}, which is the gamma effect (OTM turning into ITM/ATM).</div>
          <div class="agent">With IV crush −{smeta['crush']:.0f}% on that same move: <b>{b['x1σ crush']:.2f}x</b> (₹{b['LTP']*b['x1σ crush']:,.1f}). With IV spike +{smeta['crush']:.0f}%: <b>{b['x1σ spike']:.2f}x</b> (₹{b['LTP']*b['x1σ spike']:,.1f}).</div>
          <div class="agent">If it goes the wrong way by 0.5σ: <b>{b['xWrong']:.2f}x</b> (₹{b['LTP']*b['xWrong']:,.1f}). Spot move needed just to double the premium: <b>{('>4' if pd.isna(b['Needs2x(σ)']) else f"{b['Needs2x(σ)']:.2f}")}σ</b>.</div>
        </div>""", unsafe_allow_html=True)


with tab6:
    st.markdown("#### LIVE MARKET NEWS")
    if news_items:
        tone = headline_tone(news_items)
        tcol = "#00e676" if tone > 15 else "#ff1744" if tone < -15 else "#90a4ae"
        rows = []
        for i in news_items:
            link = i["link"] if i["link"].startswith(("http://", "https://")) else ""
            t = esc(i["title"])
            t = f'<a href="{html.escape(link, quote=True)}" target="_blank" style="color:#ddd;">{t}</a>' if link else t
            rows.append(f'<div class="agent"><b>{esc(age_label(i["age_min"]))}</b> · {esc(i["source"])} — {t}</div>')
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Headline tone (keyword backstop): <span style="color:{tcol};">{tone:+d}</span></div>
          {''.join(rows)}</div>""", unsafe_allow_html=True)
    else:
        st.warning("No headlines available. " + " | ".join(news_status))
    with st.expander("NEWS FEED STATUS"):
        for line in news_status:
            st.write(line)

    st.markdown("#### AI COUNCIL")
    st.caption(f"Gate: {gate_label}")
    if "error" in ai and "signal" not in ai:
        st.warning(ai["error"])
    else:
        scolor = {"BUY": "#00e676", "SELL": "#ff1744"}.get(sig["signal"], "#90a4ae")
        lv = ""
        if sig["target"] is not None:
            rr_txt = f"1:{sig['rr']:.1f}" if sig["rr"] else "n/a"
            lv = (f"Entry {sig['entry']:,.0f} &nbsp;|&nbsp; Target {sig['target']:,.0f} &nbsp;|&nbsp; "
                  f"SL {sig['sl']:,.0f} &nbsp;|&nbsp; R:R {rr_txt}")
        inv_txt = f"<div class='agent'><b>Invalidation Condition:</b> {esc(sig['invalidation'])}</div>" if sig.get("invalidation") else ""
        ai_note = f" (AI said {sig['ai_signal']})" if sig["ai_signal"] != sig["signal"] else ""
        st.markdown(f"""
        <div class="bbg-panel" style="border:2px solid {scolor};">
          <div class="bbg-title">Risk-Validated Signal</div>
          <div class="bbg-big" style="color:{scolor};">{sig['signal']}{esc(ai_note)} &nbsp;|&nbsp; Confidence {sig['confidence']}%</div>
          <div class="bbg-desc">{lv}</div>
          {inv_txt}
          <div class="bbg-desc" style="color:#ffab00;">{esc(sig['note']) if sig['note'] else ''}</div>
        </div>""", unsafe_allow_html=True)

        d = sig["direction"]
        def icon(v):
            if d == 0:
                return "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
            return "✅" if v * d > 0 else "❌" if v * d < 0 else "➖"
        crow = "".join(f'<div class="agent">{icon(c["v"])} <b>{esc(c["name"])}:</b> {esc(c["detail"])}</div>'
                       for c in sig["checks"])
        lean_txt = "BULLISH" if sig["lean"] > 0 else "BEARISH" if sig["lean"] < 0 else "NEUTRAL"
        sres, ssup = m["struct"]["res"], m["struct"]["sup"]
        lvl_txt = (f"Nearest OI resistance {sres['level']:,.0f} ({sres['oi']/1e5:,.1f}L, chg {sres['chg']/1e5:+,.1f}L)" if sres else "No nearby resistance") \
            + " &nbsp;|&nbsp; " + \
            (f"Nearest OI support {ssup['level']:,.0f} ({ssup['oi']/1e5:,.1f}L, chg {ssup['chg']/1e5:+,.1f}L)" if ssup else "No nearby support") \
            + f" &nbsp;|&nbsp; Global walls {m['call_wall']:,.0f} / {m['put_wall']:,.0f}"
        need = f"AI call agreement {sig['net']:+d} (needs +{MIN_CONFIRM})" if d else "no directional call to confirm"
        st.markdown(f"""
        <div class="bbg-panel">
          <div class="bbg-title">Option-chain & Surface cross-check &nbsp;|&nbsp; chain lean {lean_txt} ({sig['lean']:+d}) &nbsp;|&nbsp; {need}</div>
          {crow}
          <div class="agent" style="color:#888;">{lvl_txt}</div>
        </div>""", unsafe_allow_html=True)

        try:
            ns = int(float(ai.get("news_score", 0)))
        except (TypeError, ValueError):
            ns = None
        ns_txt = f" (score {ns:+d})" if ns is not None else ""
        st.markdown(f"""
        <div class="bbg-panel">
          <div class="agent"><b>Price Action:</b> {esc(ai.get('price_action_agent'))}</div>
          <div class="agent"><b>Order Flow:</b> {esc(ai.get('order_flow_agent'))}</div>
          <div class="agent"><b>Volatility & Surface:</b> {esc(ai.get('volatility_agent'))}</div>
          <div class="agent"><b>Sentiment{ns_txt}:</b> {esc(ai.get('news_agent'))}</div>
          <div class="agent"><b>Risk Officer:</b> {esc(ai.get('final_approval'))}</div>
        </div>""", unsafe_allow_html=True)


with tab7:
    st.markdown("#### 🏆 AUTOMATED SIGNAL AUDIT & MFE/MAE TELEMETRY (SQLite WAL)")
    st.caption(
        "Automatically logs risk-validated AI Council signals and Dual-Force Gamma Blasts, then tracks subsequent spot movement "
        "to grade Maximum Favorable Excursion (MFE), Maximum Adverse Excursion (MAE), and regime-split Win Rates. (Calibration telemetry only — no orders placed.)"
    )
    audit_filter = st.radio("FILTER BY INDEX", [idx_name, "ALL"], horizontal=True)
    ldf = fetch_signal_ledger(audit_filter)
    if ldf.empty:
        st.info("No directional BUY/SELL signals have triggered yet in this SQLite ledger. Signals will appear here automatically as they fire.")
    else:
        closed = ldf[ldf["outcome"].isin(["TARGET_HIT", "SL_HIT", "EOD_CLOSED"])]
        wins = (closed["outcome"] == "TARGET_HIT").sum()
        losses = (closed["outcome"] == "SL_HIT").sum()
        decided = wins + losses
        win_rate = (wins / decided * 100.0) if decided > 0 else 0.0
        avg_mfe_r = float(ldf["mfe_r"].mean()) if not ldf.empty else 0.0
        avg_mae_r = float(ldf["mae_r"].mean()) if not ldf.empty else 0.0
        expectancy_r = float(closed["realized_r"].mean()) if not closed.empty else 0.0

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("TOTAL SIGNALS LOGGED", f"{len(ldf)}", f"Open: {(ldf['outcome'] == 'OPEN').sum()}", delta_color="off")
        a2.metric("DECIDED WIN RATE", f"{win_rate:.1f}%", f"{wins} Target / {losses} SL", delta_color="off")
        a3.metric("AVG MFE / MAE (R)", f"+{avg_mfe_r:.2f}R / -{avg_mae_r:.2f}R", "Excursion ratio", delta_color="off")
        a4.metric("REALIZED EXPECTANCY", f"{expectancy_r:+.2f}R", f"across {len(closed)} closed signals", delta_color="off")

        # Regime breakdown
        pos_df = ldf[ldf["gex_regime"].str.contains("POSITIVE", na=False)]
        neg_df = ldf[ldf["gex_regime"].str.contains("NEGATIVE", na=False)]
        st.caption(
            f"Regime Split — Positive Gamma Signals: {len(pos_df)} (Avg MFE +{pos_df['mfe_r'].mean() if not pos_df.empty else 0:.2f}R) | "
            f"Negative Gamma Signals: {len(neg_df)} (Avg MFE +{neg_df['mfe_r'].mean() if not neg_df.empty else 0:.2f}R)"
        )

        disp_cols = [
            "date_ist", "time_ist", "idx_name", "expiry", "source", "direction",
            "gex_regime", "entry_spot", "target_spot", "sl_spot", "last_spot",
            "mfe_pts", "mae_pts", "mfe_r", "mae_r", "realized_r", "outcome"
        ]
        show_df(ldf[[c for c in disp_cols if c in ldf.columns]], hide_index=True)

        col_dl, col_clr = st.columns([1, 1])
        col_dl.download_button(
            "⬇ Export Signal Audit CSV",
            data=ldf.to_csv(index=False).encode("utf-8"),
            file_name=f"bbg_signal_audit_{datetime.now(IST).date().isoformat()}.csv",
            mime="text/csv",
        )
        if col_clr.button("🗑 Reset Signal Audit Ledger"):
            clear_signal_ledger()
            st.rerun()


with tab8:
    st.caption(f"1σ range to expiry: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now(IST).strftime('%H:%M:%S')} IST")
    _all_chain = m["df"].copy()
    _atm_window = st.selectbox("STRIKES AROUND SPOT", ["±300 points", "±600 points", "±1000 points", "All strikes"], index=1)
    _radius = {"±300 points": 300, "±600 points": 600, "±1000 points": 1000}.get(_atm_window)
    if _radius is not None:
        _shown = _all_chain[(_all_chain["Strike"] >= spot - _radius) & (_all_chain["Strike"] <= spot + _radius)].copy()
    else:
        _shown = _all_chain.copy()
    _shown["Distance from Spot"] = (_shown["Strike"] - spot).round(2)
    _shown["Net_GEX"] = _shown["Net_GEX"].round(0)
    _shown["Net_VEX"] = _shown["Net_VEX"].round(1)
    _shown["Net_CEX"] = _shown["Net_CEX"].round(1)
    _cols = [c for c in [
        "Strike", "Distance from Spot", "Net_GEX", "Net_VEX", "Net_CEX",
        "CE_OI", "CE_PrevOI", "CE_OI_Chg", "CE_IV", "CE_Gamma", "CE_Delta", "CE_Vol", "CE_Bid", "CE_Ask", "CE_LTP",
        "PE_LTP", "PE_Bid", "PE_Ask", "PE_Vol", "PE_Delta", "PE_Gamma", "PE_IV", "PE_OI_Chg", "PE_PrevOI", "PE_OI"
    ] if c in _shown.columns]
    show_df(_shown[_cols].sort_values("Strike", key=lambda col: (col - spot).abs()), hide_index=True)

    with st.expander("📡 LIVE DHAN v2 WEBSOCKET & MICROSTRUCTURE TELEMETRY"):
        st.json({
            "ws_connected": ws_snap["connected"],
            "ws_running": ws_snap["running"],
            "packets_decoded": ws_snap["packet_count"],
            "latest_ws_ltp": ws_snap["latest_ltp"],
            "buy_initiated_ticks": ws_snap["buy_ticks"],
            "sell_initiated_ticks": ws_snap["sell_ticks"],
            "lee_ready_tick_delta_pct": ws_snap["tick_delta_pct"],
            "last_packet_age_sec": ws_snap["last_age_sec"],
            "ws_last_error": ws_snap["last_err"],
            "chain_microstructure": m["micro"],
        })
        if ws_snap["recent_ticks"]:
            tdf = pd.DataFrame(ws_snap["recent_ticks"])
            tdf["Time"] = tdf["ts"].map(lambda x: datetime.fromtimestamp(x, IST).strftime("%H:%M:%S"))
            tdf["Side"] = tdf["dir"].map(lambda d: "BUY_INIT" if d > 0 else "SELL_INIT")
            show_df(tdf[["Time", "ltp", "Side", "qty"]].iloc[::-1], hide_index=True)

    with st.expander("FIELD COVERAGE / DATA DIAGNOSTICS"):
        st.json({"health_grade": health["grade"], "coverage": health["coverage"], "messages": health["messages"], "payload_strikes": len(_all_chain), "displayed_strikes": len(_shown)})
        st.caption("A numeric 0 can be a legitimate market value. Coverage is measured separately from values; uninitialized PrevOI=0 rows are masked when Vol < OI.")
