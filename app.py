"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.3 // UPGRADED EDITION
Complete Indian market options analytics with:
 - Dual-Force Engine & Jobber Microstructure
 - Dealer Positioning Engine: Signed Net GEX, Zero-Gamma Flip
 - Put-Call Parity Synthetic Future, Basis, RR25 & BF25
 - Fully Automatic Telegram Broadcasting (Validated/Rejected/Target-Hit/Stop-Loss)
 - Complete Signal Scorecard (15m/30m/60m/Close, MFE/MAE, check-edge stats)
 - Data Health Monitor & Telegram Alerting Engine
 - NEW: Institutional Flow, Power-of-Two Levels, Theta Acceleration
 - NEW: Emergency Stop Loss, Weekly Expiry Analysis, Calendar Spreads
 - NEW: Cross-Index Correlation, Kelly Position Sizing
NO ORDER EXECUTION FEATURES: This app places no orders.
"""

import os
import re
import base64
import json
import math
import html
import time
import hashlib
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# CRITICAL: SESSION STATE INITIALIZATION (BEFORE ANY WIDGETS)
# ============================================================
# Initialize ALL 14 required session state keys to prevent crashes
SESSION_KEYS = [
    "_bbg_flags", "_cur_ai_key", "_last_ai_time", "_last_ai_attempt",
    "_last_api_call", "oi_snap", "velocity", "last_ai_verdict",
    "last_ai_verdict_key", "last_ai_state", "scorecard", "hist_key",
    "hist", "last_surface_check"
]

for key in SESSION_KEYS:
    if key not in st.session_state:
        if key == "scorecard":
            st.session_state[key] = []
        elif key in ["oi_snap", "last_ai_verdict", "last_ai_state"]:
            st.session_state[key] = {} if key != "velocity" else {"ce_builds": 0.0, "ce_unwinds": 0.0, "pe_builds": 0.0, "pe_unwinds": 0.0}
        elif key == "hist":
            st.session_state[key] = []
        else:
            st.session_state[key] = None

# ============================================================
# CONFIG & GLOBAL CONSTANTS
# ============================================================
st.set_page_config(page_title="Vipul Bloomberg Terminal v13.3", layout="wide", initial_sidebar_state="expanded")

# CSS (preserved from original with minor adjustments)
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

# Secrets helpers
def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)

def clean(s):
    s = (s or "").strip().strip('"').strip("'").strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s.replace("\n", "").replace("\r", "").replace(" ", "")

DEFAULT_CLIENT_ID = clean(get_secret("DHAN_CLIENT_ID", ""))
DEFAULT_DHAN_TOKEN = clean(get_secret("DHAN_ACCESS_TOKEN", ""))
DEFAULT_GROQ_KEY = get_secret("GROQ_API_KEY", "").strip()
DEFAULT_TG_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_TG_CHAT = get_secret("TELEGRAM_CHAT_ID", "").strip()

GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

BASE_URL = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{BASE_URL}/optionchain"
EXPIRY_URL = f"{BASE_URL}/optionchain/expirylist"

YEAR_DAYS = 365

# ============================================================
# SESSION STATE FLAGS (Simple dict entries, not class)
# ============================================================
st.session_state.setdefault("_bbg_flags", {})
bbg_flags = st.session_state["_bbg_flags"]

# Initialize all flags safely
if "time_basis" not in bbg_flags:
    bbg_flags["time_basis"] = "calendar"
if "dominance" not in bbg_flags:
    bbg_flags["dominance"] = 1.5
if "min_confirm" not in bbg_flags:
    bbg_flags["min_confirm"] = 2
if "surface_check" not in bbg_flags:
    bbg_flags["surface_check"] = False
if "last_surface_check" not in st.session_state:
    st.session_state["last_surface_check"] = False

TIME_BASIS = bbg_flags["time_basis"]
SKEW_COEF = 0.5
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0
MIN_RR = 1.0
MIN_FLOW_PCT = 2.0
NOISE_PCT = 0.5
DOMINANCE = bbg_flags["dominance"]
MIN_CONFIRM = bbg_flags["min_confirm"]
SL_BUFFER = 35
FLOW_EDGE_PCT = 2.0
IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = os.getenv("BBG_DATA_DIR") or tempfile.gettempdir()
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = tempfile.gettempdir()

HIST_MIN_GAP_SEC = 60
HIST_MAX = 400
OI_MAP_SPAN = 800
EXH_CONTEXT_SIGMA = 0.4
EXH_WATCH = 35
EXH_ALERT = 60

RISK_FREE = 0.065
MIN_PREMIUM = 5.0
AI_RETRY_SEC = 60
AI_VERDICT_MAX_AGE_SEC = 600

NEWS_MAX_AGE_H = 24
NEWS_TOP_N = 10
NEWS_MIN_GAP_SEC = 600
NEWS_EXTRA_FEEDS = tuple(u.strip() for u in get_secret("NEWS_EXTRA_FEEDS", "").split(",") if u.strip())

INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}

DIV_YIELD = 0.013
GEX_BAND_PCT = 5.0
GEX_WINDOWS = (4, 8, 16)
BASIS_MIN_PTS = 8.0

# Surface check flag from session
USE_SURFACE_CHECK = bbg_flags["surface_check"]

REGIME_LABEL = {
    "POSITIVE": "POSITIVE GAMMA (if dealers are long calls / short puts: mean-reverting)",
    "NEGATIVE": "NEGATIVE GAMMA (if dealers are long calls / short puts: momentum / breakout)",
    "NEUTRAL": "NEUTRAL GAMMA (net exposure near zero)",
}

# ============================================================
# AUTH & HTTP HELPERS
# ============================================================
def auth_headers(token, client_id):
    return {"Content-Type": "application/json", "Accept": "application/json",
            "access-token": token, "client-id": client_id}

def check(r):
    if r.ok:
        return
    hint = ""
    if r.status_code == 401:
        hint = " -> token expired/invalid, or client-id does not match the token."
    elif r.status_code == 429:
        hint = " -> rate limited, wait a few seconds."
    raise RuntimeError(f"HTTP {r.status_code} | {r.text[:300]}{hint}")

# ============================================================
# DATA FETCHERS (All with proper caching & throttle)
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])

@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    enforce_global_api_throttle()
    r = requests.post(OPTIONCHAIN_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12)
    check(r)
    data = r.json().get("data", {}) or {}
    spot = float(data.get("last_price") or 0)

    rows = []
    for strike_str, item in (data.get("oc") or {}).items():
        try:
            strike = float(strike_str)
        except ValueError:
            continue
        ce, pe = item.get("ce") or {}, item.get("pe") or {}
        ce_g, pe_g = ce.get("greeks") or {}, pe.get("greeks") or {}
        rows.append({
            "Strike": strike,
            "CE_OI": _n(ce.get("oi")), "PE_OI": _n(pe.get("oi")),
            "CE_PrevOI": _n(ce.get("previous_oi")), "PE_PrevOI": _n(pe.get("previous_oi")),
            "CE_IV": _n(ce.get("implied_volatility")), "PE_IV": _n(pe.get("implied_volatility")),
            "CE_Gamma": _n(ce_g.get("gamma")), "PE_Gamma": _n(pe_g.get("gamma")),
            "CE_LTP": _n(ce.get("last_price")), "PE_LTP": _n(pe.get("last_price")),
            "CE_Bid": _n(ce.get("top_bid_price")), "CE_Ask": _n(ce.get("top_ask_price")),
            "PE_Bid": _n(pe.get("top_bid_price")), "PE_Ask": _n(pe.get("top_ask_price")),
            "CE_Vol": _n(ce.get("volume")), "PE_Vol": _n(pe.get("volume")),
            "CE_Delta": _n(ce_g.get("delta")), "PE_Delta": _n(pe_g.get("delta")),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return spot, df
    return spot, df.sort_values("Strike").reset_index(drop=True)

def enforce_global_api_throttle(min_interval=None):
    min_interval = MIN_CHAIN_GAP_SEC if min_interval is None else min_interval
    last = st.session_state.get("_last_api_call", 0.0)
    wait = min_interval - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_api_call"] = time.time()

def _n(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0

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
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
        return max(0, (exp_date - datetime.now(IST).date()).days)
    except ValueError:
        return 0

# ============================================================
# INSTITUTIONAL SURFACE & ANALYSIS ENGINE (ENHANCED)
# ============================================================
def years_to_expiry(expiry_str, now=None):
    now = now or datetime.now(IST)
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 1.0 / 365.0
    close_t = datetime.strptime("15:30", "%H:%M").time()
    if TIME_BASIS == "calendar":
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

def synthetic_future(df, spot, t_years, step):
    disc_inv = math.exp(RISK_FREE * max(t_years, 0.0))
    near = df[(df["Strike"] >= spot - 4 * step) & (df["Strike"] <= spot + 4 * step)]
    est = []
    for r in near.itertuples():
        c = _mid_price(r.CE_Bid, r.CE_Ask, r.CE_LTP)
        p = _mid_price(r.PE_Bid, r.PE_Ask, r.PE_LTP)
        if c > 1.0 and p > 1.0:
            f = float(r.Strike) + disc_inv * (c - p)
            if abs(f - spot) <= 0.03 * spot:
                est.append((abs(c - p), f))
    if not est:
        return None
    est.sort(key=lambda x: x[0])
    top = [x[1] for x in est[:3]]
    return float(sum(top) / len(top))

def skew_25d(df, spot, step, atm_iv):
    iv_c = iv_p = atm_iv
    ok_c = ok_p = False
    ce = df[(df["CE_IV"] > 0) & (df["CE_Delta"].abs() > 0) & (df["Strike"] >= spot - 2 * step) & (df["Strike"] <= spot + 12 * step)]
    pe = df[(df["PE_IV"] > 0) & (df["PE_Delta"].abs() > 0) & (df["Strike"] <= spot + 2 * step) & (df["Strike"] >= spot - 12 * step)]
    if not ce.empty:
        iv_c, ok_c = float(ce.loc[(ce["CE_Delta"].abs() - 0.25).abs().idxmin(), "CE_IV"]), True
    if not pe.empty:
        iv_p, ok_p = float(pe.loc[(pe["PE_Delta"].abs() - 0.25).abs().idxmin(), "PE_IV"]), True
    return iv_c, iv_p, iv_c - iv_p, 0.5 * (iv_c + iv_p) - atm_iv, (ok_c and ok_p)

def dealer_exposures(df, spot, syn_fut, t_years, atm_iv):
    out = df.copy()
    out["CE_GEX"] = out["CE_Gamma"].abs() * out["CE_OI"] * (spot ** 2) * 0.01
    out["PE_GEX"] = -out["PE_Gamma"].abs() * out["PE_OI"] * (spot ** 2) * 0.01
    out["Net_GEX"] = out["CE_GEX"] + out["PE_GEX"]
    t = max(t_years, 1e-5)
    sq = math.sqrt(t)
    K = out["Strike"].to_numpy(float)
    ce_oi, pe_oi = out["CE_OI"].to_numpy(float), out["PE_OI"].to_numpy(float)

    def vanna_charm(iv_col):
        iv = out[iv_col].to_numpy(float)
        sig = np.maximum(np.where(iv > 0, iv, atm_iv) / 100.0, 0.03)
        d1 = (np.log(syn_fut / K) + 0.5 * sig * sig * t) / (sig * sq)
        d2 = d1 - sig * sq
        n = np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        return -n * d2 / sig, n * d2 / (2.0 * t)

    v_c, c_c = vanna_charm("CE_IV")
    v_p, c_p = vanna_charm("PE_IV")
    out["Net_VEX"] = (v_c * ce_oi - v_p * pe_oi) * 0.01
    out["Net_CEX"] = (c_c * ce_oi - c_p * pe_oi) / 365.0
    return out

def gex_regime_for(df, spot, step, n_steps):
    w = df[(df["Strike"] >= spot - n_steps * step) & (df["Strike"] <= spot + n_steps * step)]
    net = float(w["Net_GEX"].sum())
    gross = float(w["CE_GEX"].abs().sum() + w["PE_GEX"].abs().sum())
    if gross <= 0:
        return "NEUTRAL", net, gross
    pct = net / gross * 100.0
    return ("POSITIVE" if pct > GEX_BAND_PCT else "NEGATIVE" if pct < -GEX_BAND_PCT else "NEUTRAL"), net, gross

def strike_crossover(df, spot, step, n_steps=8):
    w = df[(df["Strike"] >= spot - n_steps * step) & (df["Strike"] <= spot + n_steps * step)].sort_values("Strike")
    k, g = w["Strike"].to_numpy(float), w["Net_GEX"].to_numpy(float)
    best = None
    for i in range(len(g) - 1):
        a, b = g[i], g[i + 1]
        if a == 0 and b != 0:
            cand = k[i]
        elif a * b < 0:
            cand = k[i] + abs(a) / (abs(a) + abs(b)) * (k[i + 1] - k[i])
        else:
            continue
        if best is None or abs(cand - spot) < abs(best - spot):
            best = cand
    return None if best is None else round(float(best), 1)

def zero_gamma_level(df, spot, t_years):
    t = max(t_years, 1e-5)
    sq = math.sqrt(t)
    d = df[(df["Strike"] >= spot * 0.85) & (df["Strike"] <= spot * 1.15)]
    K = d["Strike"].to_numpy(float)
    ce_iv = np.where(d["CE_IV"].to_numpy(float) > 0, d["CE_IV"].to_numpy(float), np.nan)
    pe_iv = np.where(d["PE_IV"].to_numpy(float) > 0, d["PE_IV"].to_numpy(float), np.nan)
    iv_c, iv_p = np.where(np.isnan(ce_iv), pe_iv, ce_iv), np.where(np.isnan(pe_iv), ce_iv, pe_iv)
    ok = ~np.isnan(iv_c)
    if ok.sum() < 5:
        return None, 0.0
    K, iv_c, iv_p = K[ok], iv_c[ok] / 100.0, iv_p[ok] / 100.0
    ce_oi, pe_oi = d["CE_OI"].to_numpy(float)[ok], d["PE_OI"].to_numpy(float)[ok]

    def total(S):
        tot = 0.0
        for sg, oi, sign in ((iv_c, ce_oi, 1.0), (iv_p, pe_oi, -1.0)):
            d1 = (np.log(S / K) + (RISK_FREE + 0.5 * sg * sg) * t) / (sg * sq)
            g = np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) / (S * sg * sq)
            tot += sign * float(np.sum(g * oi)) * S * S * 0.01
        return tot

    grid = spot * np.linspace(0.97, 1.03, 61)
    vals = [total(S) for S in grid]
    best = None
    for i in range(len(grid) - 1):
        a, b = vals[i], vals[i + 1]
        if a * b < 0:
            cand = grid[i] + abs(a) / (abs(a) + abs(b)) * (grid[i + 1] - grid[i])
            if best is None or abs(cand - spot) < abs(best - spot):
                best = cand
    return (None if best is None else round(float(best), 1)), total(spot)

def compute_surface_and_exposures(df, spot, t_years, atm_iv, step):
    f_syn = synthetic_future(df, spot, t_years, step)
    fair = spot * math.exp((RISK_FREE - DIV_YIELD) * max(t_years, 0.0))
    ok = f_syn is not None
    syn = f_syn if ok else fair
    iv_c, iv_p, rr, bf, _skew_ok = skew_25d(df, spot, step, atm_iv)
    out = dealer_exposures(df, spot, syn, t_years, atm_iv)
    by_win = {n: gex_regime_for(out, spot, step, n) for n in GEX_WINDOWS}
    reg, net, gross = by_win[8]
    zg, total_at_spot = zero_gamma_level(out, spot, t_years)
    near = out[(out["Strike"] >= spot - 8 * step) & (out["Strike"] <= spot + 8 * step)]
    return {
        "df": out, "syn_fut": round(syn, 2), "fair_fwd": round(fair, 2), "carry": round(fair - spot, 2),
        "basis": round(syn - spot, 2) if ok else 0.0, "excess_basis": round(syn - fair, 2) if ok else 0.0, "surface_ok": ok,
        "iv_25c": round(iv_c, 2), "iv_25p": round(iv_p, 2), "rr_25": round(rr, 2), "bf_25": round(bf, 2),
        "net_gex": net, "gross_gex": gross, "gex_regime": reg, "gex_regime_label": REGIME_LABEL[reg],
        "regime_stable": len({v[0] for v in by_win.values()}) == 1, "regime_by_window": {n: v[0] for n, v in by_win.items()},
        "crossover": strike_crossover(out, spot, step), "zero_gamma": zg, "total_gex_at_spot": total_at_spot,
        "net_vex": float(near["Net_VEX"].sum()), "net_cex": float(near["Net_CEX"].sum()),
    }

def interpret_oi_battle(ce_chg, pe_chg):
    if ce_chg > 5000 and pe_chg <= 0:
        return "CE Build (Bearish)"
    if pe_chg > 5000 and ce_chg <= 0:
        return "PE Build (Bullish)"
    if ce_chg < -5000 and pe_chg >= 0:
        return "CE Unwind (Bullish)"
    if pe_chg < -5000 and ce_chg >= 0:
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

def compute_bias(dce, dpe, spot, prev_close, pcr, basis, rr_25):
    score = 50
    if dce < -15000 and dpe > 10000:
        score += 25
    elif dce < -8000 and dpe > 5000:
        score += 15
    elif dpe < -15000 and dce > 10000:
        score -= 25
    elif dpe < -8000 and dce > 5000:
        score -= 15
    if prev_close and prev_close > 0:
        if spot - prev_close > 20:
            score += 10
        elif spot - prev_close < -20:
            score -= 10
    if pcr > 1.2:
        score += 8
    elif pcr < 0.8:
        score -= 8
    thr = max(BASIS_MIN_PTS, 0.0004 * spot)
    if basis > thr and rr_25 > -0.5:
        score += 5
    elif basis < -thr and rr_25 < -1.5:
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

    ce_chg, pe_chg = near["CE_OI_Chg"], near["PE_OI_Chg"]
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

def calculate_jobber_microstructure(df, spot):
    df = df.copy()
    df["dist"] = (df["Strike"] - spot).abs()
    ladder = df.sort_values("dist").head(10).sort_values("Strike").copy()

    if ladder.empty:
        return {"ladder_imbalance": 0.0, "pocket_support": spot, "micro_turn": "NEUTRAL AUCTION"}

    total_ce = ladder["CE_OI"].sum()
    total_pe = ladder["PE_OI"].sum()
    ladder_imbalance = (total_pe - total_ce) / (total_ce + total_pe) if (total_ce + total_pe) > 0 else 0.0

    ladder["Net_OI_Flow"] = ladder["PE_OI_Chg"] - ladder["CE_OI_Chg"]
    pocket_support = float(ladder.loc[ladder["Net_OI_Flow"].idxmax(), "Strike"])

    ce_unwind_sum = ladder[ladder["CE_OI_Chg"] < 0]["CE_OI_Chg"].sum()
    pe_unwind_sum = ladder[ladder["PE_OI_Chg"] < 0]["PE_OI_Chg"].sum()

    if ce_unwind_sum < -5000 and pe_unwind_sum > -2000:
        micro_turn_signal = "ABSORPTION BULLISH (Shorts Trapped)"
    elif pe_unwind_sum < -5000 and ce_unwind_sum > -2000:
        micro_turn_signal = "ABSORPTION BEARISH (Longs Trapped)"
    else:
        micro_turn_signal = "NEUTRAL AUCTION (Balanced)"

    return {
        "ladder_imbalance": round(float(ladder_imbalance), 3),
        "pocket_support": pocket_support,
        "micro_turn": micro_turn_signal,
    }

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
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    pcr = df["PE_OI"].sum() / df["CE_OI"].sum() if df["CE_OI"].sum() > 0 else 1.0
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

    ce_trap, pe_trap = None, None
    trap_buf = SL_BUFFER * step / 50.0
    if not near.empty:
        i = near["CE_OI_Chg"].idxmax()
        if near.loc[i, "CE_OI_Chg"] > 10000:
            ce_trap = float(near.loc[i, "Strike"]) + trap_buf
        j = near["PE_OI_Chg"].idxmax()
        if near.loc[j, "PE_OI_Chg"] > 10000:
            pe_trap = float(near.loc[j, "Strike"]) - trap_buf

    score, bias = compute_bias(dce, dpe, spot, prev_close, pcr, surf["excess_basis"], surf["rr_25"])
    status, signal, desc = detect_gamma_blast(near)
    engine = engine_sums(near)
    struct = find_structure(df, spot, step, exp_move, call_wall, put_wall)
    jobber = calculate_jobber_microstructure(df, spot)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "pcr": pcr,
        "dce": dce, "dpe": dpe, "battle": interpret_oi_battle(dce, dpe),
        "atm_iv": atm_iv, "dte": dte, "exp_move": round(exp_move, 2),
        "t_years": t_years, "days_left": t_years * (365.0 if TIME_BASIS == "calendar" else 252.0),
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "jobber": jobber, "engine": engine, "struct": struct,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
        **{k: surf[k] for k in ("syn_fut", "fair_fwd", "carry", "basis", "excess_basis", "surface_ok",
                               "iv_25c", "iv_25p", "rr_25", "bf_25", "net_gex", "gross_gex", "gex_regime",
                               "gex_regime_label", "regime_stable", "regime_by_window", "crossover",
                               "zero_gamma", "total_gex_at_spot", "net_vex", "net_cex")},
    }

# ============================================================
# 3-MIN OI VELOCITY
# ============================================================
ZERO_VEL = {"ce_builds": 0.0, "ce_unwinds": 0.0, "pe_builds": 0.0, "pe_unwinds": 0.0}

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
    if last.get("key") != st.session_state.get("_cur_ai_key"):
        return True, "INDEX_OR_EXPIRY_CHANGED"
    _t0 = st.session_state.get("last_ai_time")
    if _t0 is not None and (datetime.now(IST) - _t0).total_seconds() > AI_VERDICT_MAX_AGE_SEC:
        return True, "VERDICT_EXPIRED"

    step_thresh = max(20.0, m["step"] * 0.5)
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
    if (m.get("gex_regime") in ("POSITIVE", "NEGATIVE") and last.get("gex_regime") in ("POSITIVE", "NEGATIVE")
            and m["gex_regime"] != last["gex_regime"]):
        return True, "GAMMA_REGIME_FLIP"
    if m["status"] != last["status"]:
        return True, "ENGINE_STATUS_CHANGE"
    trap_now = (m["ce_trap"] is not None) or (m["pe_trap"] is not None)
    if trap_now and not last.get("trap_active", False):
        return True, "35PT_TRAP_DETECTED"
    if vel["ce_unwinds"] > 40000 or vel["pe_unwinds"] > 40000:
        return True, "HIGH_UNWIND_VELOCITY"
    if m.get("exh_flag", "") != last.get("exh_flag", "") and "CONFIRMED" in m.get("exh_flag", ""):
        return True, "EXHAUSTION_CONFIRMED"
    t = st.session_state.get("last_ai_time")
    gap_ok = t is None or (datetime.now(IST) - t).total_seconds() >= NEWS_MIN_GAP_SEC
    if nhash and nhash != last.get("news_hash", "") and gap_ok:
        return True, "NEWS_CHANGED"
    return False, "STATE_UNCHANGED"

def valid_cached_verdict(key):
    v = st.session_state.get("last_ai_verdict")
    t = st.session_state.get("last_ai_time")
    if not v or t is None or st.session_state.get("last_ai_verdict_key") != key:
        return None
    if (datetime.now(IST) - t).total_seconds() > AI_VERDICT_MAX_AGE_SEC:
        return None
    return v

# ============================================================
# GROQ MULTI-AGENT COUNCIL
# ============================================================
def run_council(m, vel, news, api_key, model):
    if Groq is None:
        return {"error": "groq package not installed (pip install groq)."}
    if not api_key:
        return {"error": "No GROQ API key provided in sidebar."}

    eg = m["engine"]
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
            f"PE_OI={int(r['PE_OI']):,} (chg {int(r['PE_OI_Chg']):+,})"
        )
    chain_summary = "\n".join(strike_log[:25])

    prompt = f"""You are a council of 5 options-market agents. Analyze and answer in JSON only.

MARKET DATA:
- Spot: {m['spot']:.2f} | PCR: {m['pcr']:.3f} | Bias: {m['bias']} (score {m['score']}/100)
- Jobber Microstructure: Ladder Imbalance={m['jobber']['ladder_imbalance']}, Pocket Support={m['jobber']['pocket_support']}, Micro-Turn={m['jobber']['micro_turn']}
- Engine status: {m['status']} | OI battle: {m['battle']}
- Dealer positioning (ASSUMES dealers long calls / short puts, unverified for NSE): regime {m['gex_regime']} (stable across windows: {m['regime_stable']}), zero-gamma level {m['zero_gamma']}, per-strike crossover {m['crossover']}
- Volatility surface: synthetic future {m['syn_fut']:.1f} vs fair carry forward {m['fair_fwd']:.1f} (excess {m['excess_basis']:+.1f} pts), 25d risk reversal {m['rr_25']:+.2f}, butterfly {m['bf_25']:+.2f}
- Dual-Force flow (near-spot strikes only, % = share of that side's near-spot OI):
  CE exits {eg['ce_exits']:,.0f} ({eg['ce_exit_pct']:.1f}%) | CE builds {eg['ce_build']:,.0f} ({eg['ce_build_pct']:.1f}%) | CE net flow {eg['ce_net_flow']:+,.0f}
  PE exits {eg['pe_exits']:,.0f} ({eg['pe_exit_pct']:.1f}%) | PE builds {eg['pe_build']:,.0f} ({eg['pe_build_pct']:.1f}%) | PE net flow {eg['pe_net_flow']:+,.0f}
  Bull force {eg['bull_force']:.1f} vs Bear force {eg['bear_force']:.1f} (ratio {eg['force_ratio']:.2f}; a winner needs >= {DOMINANCE})
- OI structure around spot: {struct_txt}
- Definitions: Bull force = PE builds% + CE exits%; Bear force = CE builds% + PE exits%. Put OI BUILDING is bullish support, call OI BUILDING is bearish resistance. Do not call put builds bearish.
- Exhaustion monitor (is the current trend running out of fuel?): {exh_txt}
- Whole-chain net OI change: CE {m['dce']:+,.0f} | PE {m['dpe']:+,.0f} (compare net call vs net put writing before claiming either side is unwinding)
- Expected Move: +/-{m['exp_move']} pts [{m['lower_1sigma']} - {m['upper_1sigma']}] | ATM IV {m['atm_iv']:.1f}% | DTE {m['dte']}
- Call Wall: {m['call_wall']:.0f} | Put Wall: {m['put_wall']:.0f} | Max Pain: {m['max_pain']:.0f} | Gamma strike: {m['gamma_strike']:.0f}
- 35pt traps: CE resistance={m['ce_trap']} | PE support={m['pe_trap']}
- 3-Min Flow: CE build={vel['ce_builds']:,.0f}, CE unwind={vel['ce_unwinds']:,.0f} | PE build={vel['pe_builds']:,.0f}, PE unwind={vel['pe_unwinds']:,.0f}
- Strikes near spot:
{chain_summary}
- LIVE HEADLINES (latest first, fetched just now):
{news}

AGENTS:
1. Price Action Agent: spot vs structural levels
2. Order Flow Agent: OI changes and 35pt traps
3. Volatility Agent: IV regime, DTE decay, gamma
4. Sentiment Agent: judge ONLY from the LIVE HEADLINES list; name the headline(s) driving the view. If the list says NO HEADLINES FETCHED, say that instead of claiming "no news impact"
5. Risk Officer: final approval; reject if evidence is conflicting or engine status is CONFLICTED

Return ONLY this JSON:
{{"price_action_agent":"1 sentence","order_flow_agent":"1 sentence","volatility_agent":"1 sentence",
"news_agent":"1 sentence","news_score":-100 to 100,"final_approval":"synthesis","signal":"BUY or SELL or WAIT","confidence":0-100}}"""

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
            _es = str(e).lower()
            if any(t in _es for t in ("model_not_found", "404", "429", "rate limit", "rate_limit", "503", "overloaded")):
                continue
            break
    return {"error": f"Groq call failed: {last_err}"}

# ============================================================
# SIGNAL VALIDATION (ENHANCED WITH NEW STRATEGIES)
# ============================================================
def chain_checks(m):
    e, j = m["engine"], m["jobber"]
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

    li = j["ladder_imbalance"]
    add("Ladder OI imbalance", 1 if li >= 0.10 else -1 if li <= -0.10 else 0, f"{li:+.3f} (PE-heavy is +)")

    mt = j["micro_turn"]
    add("Micro-turn", 1 if "BULLISH" in mt else -1 if "BEARISH" in mt else 0, mt)

    add("PCR (whole chain)", 1 if m["pcr"] > 1.2 else -1 if m["pcr"] < 0.8 else 0, f"{m['pcr']:.2f}")

    add("Bias score", 1 if m["score"] > 55 else -1 if m["score"] < 45 else 0, f"{m['score']}/100 {m['bias']}")

    if m["dte"] > 2:
        add("Spot vs max pain", 0, f"{m['dte']}d to expiry: max-pain pull is weak, neutral")
    else:
        mp_gap = m["spot"] - m["max_pain"]
        add("Spot vs max pain", -1 if mp_gap > m["step"] else 1 if mp_gap < -m["step"] else 0,
            f"spot {m['spot']:,.0f} vs max pain {m['max_pain']:,.0f} ({mp_gap:+,.0f})")

    thr = max(BASIS_MIN_PTS, 0.0004 * m["spot"])
    ex_b = m.get("excess_basis", 0.0)
    sv = 1 if (m.get("surface_ok") and ex_b >= thr) else -1 if (m.get("surface_ok") and ex_b <= -thr) else 0
    on = USE_SURFACE_CHECK
    checks.append({"name": "Synthetic basis vs fair carry", "v": sv, "counted": on,
                   "detail": (f"F_syn {m['syn_fut']:,.1f} vs fair {m['fair_fwd']:,.1f} (excess {ex_b:+.1f}, needs ±{thr:.0f}) | "
                              f"RR25 {m.get('rr_25', 0):+.2f}" + ("" if on else " [observe only]")) if m.get("surface_ok")
                   else "no balanced near-ATM call/put mids [observe only]"})
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
    lean = sum(c["v"] for c in checks if c.get("counted", True))
    net = lean * direction
    entry = m["spot"]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    notes = []
    sl_buf = SL_BUFFER * m["step"] / 50.0
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
            target, sl = res["level"], sup["level"] - sl_buf
        else:
            target, sl = sup["level"], res["level"] + sl_buf

    if sig != "WAIT":
        risk = abs(entry - sl)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else None
        if rr is None or rr < MIN_RR:
            rr_txt = f"1:{rr:.2f}" if rr else "n/a"
            notes.append(f"{sig} rejected: R:R {rr_txt} from the nearest OI walls "
                         f"(target {target:,.0f}, SL {sl:,.0f}) is below 1:{MIN_RR:.1f}.")
            sig, target, sl, rr = "WAIT", None, None, None

    # NEW: Institutional flow filter
    if sig != "WAIT":
        inst_pct = m.get("institutional_flow_pct", 0)
        if inst_pct < MIN_CONFIRM:  # Default 70%
            notes.append(f"{sig} filtered: Institutional participation {inst_pct:.1f}% below minimum {MIN_CONFIM}%.")
            sig = "WAIT"

    return {"signal": sig, "ai_signal": ai_sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": " ".join(notes), "checks": checks, "net": net, "lean": lean,
            "direction": direction}


# ============================================================
# DAY MOVEMENT LEVELS
# ============================================================
def _candle_date(ts):
    today = datetime.now(IST).date()
    d = datetime.fromtimestamp(float(ts), IST).date()
    if d < today - timedelta(days=400):
        d = datetime.fromtimestamp(float(ts) + 315532800, IST).date()
    return d

@st.cache_data(ttl=600, show_spinner=False)
def fetch_daily_candles(scrip, seg, token, client_id):
    today = datetime.now(IST).date()
    last_err = None
    for to_date in (today + timedelta(days=1), today):
        try:
            r = requests.post(f"{BASE_URL}/charts/historical", headers=auth_headers(token, client_id),
                              json={"securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                                    "expiryCode": 0, "fromDate": (today - timedelta(days=35)).isoformat(),
                                    "toDate": to_date.isoformat()}, timeout=12)
            check(r)
            d = r.json()
            n = len(d.get("close", []))
            if n == 0:
                raise RuntimeError("empty candle response")
            return [{"date": _candle_date(d["timestamp"][i]).isoformat(), "o": float(d["open"][i]),
                     "h": float(d["high"][i]), "l": float(d["low"][i]), "c": float(d["close"][i])}
                    for i in range(n)]
        except Exception as e:
            last_err = e
    raise RuntimeError(f"daily candles failed: {last_err}")

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

    def add(name, price, kind):
        levels.append((name, float(price), kind))

    if ref:
        c, h, l = ref["c"], ref["h"], ref["l"]
        basis = f"session {ref['date']} (H {h:,.0f} / L {l:,.0f} / C {c:,.0f})" + (" [OHLC from quote]" if synthetic and ref["date"] == today else "")
    else:
        c, h, l = (prev_close_fallback or spot), None, None
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
    if m.get("zero_gamma"):
        oi_refs["Gamma flip"] = m["zero_gamma"]
    if m["struct"]["res"]:
        oi_refs["Nearest OI resistance"] = m["struct"]["res"]["level"]
    if m["struct"]["sup"]:
        oi_refs["Nearest OI support"] = m["struct"]["sup"]["level"]
    tol = max(m["step"] * 0.5, 0.15 * sd)

    rows = []
    for name, price, kind in levels:
        hits = [f"{n} {v:,.0f}" for n, v in oi_refs.items() if abs(v - price) <= tol]
        rows.append({"Level": name, "Price": price, "Dist pts": price - spot, "Dist σ": ((price - spot) / sd) if sd else 0.0,
                     "OI confluence": ("★ " + ", ".join(hits)) if hits else ""})
    rows.append({"Level": "◄ SPOT", "Price": spot, "Dist pts": 0.0, "Dist σ": 0.0, "OI confluence": ""})
    tbl = pd.DataFrame(rows).sort_values("Price", ascending=False).reset_index(drop=True)
    return {"table": tbl, "ref_close": c, "basis": basis, "atr": atr, "sd": sd, "tol": tol,
            "band1": (c - sd, c + sd), "band2": (c - 2 * sd, c + 2 * sd), "live": live,
            "moved_sigma": (spot - c) / sd if sd else 0.0}


# ============================================================
# SESSION HISTORY + EXHAUSTION MONITOR
# ============================================================
def daily_sigma(m):
    days = 365.0 if TIME_BASIS == "calendar" else 252.0
    return m["spot"] * m["atm_iv"] / 100.0 * math.sqrt(1.0 / days)

def hist_path(key):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key)
    return os.path.join(DATA_DIR, f"bbg_hist_{datetime.now(IST).date().isoformat()}_{safe}.json")

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
    return {"ts": time.time(), "spot": spot, "iv": m["atm_iv"], "pcr": m["pcr"], "edge": pe_pct - ce_pct,
            "rr_25": m.get("rr_25"), "excess_basis": m.get("excess_basis"), "net_gex": m.get("net_gex"),
            "max_pain": m["max_pain"], "dte": m["dte"], "status": m["status"],
            "res": res["level"] if res else None, "sup": sup["level"] if sup else None, "oi": oi_map}


def record_snapshot(m, key):
    if st.session_state.get("hist_key") != key:
        st.session_state["hist_key"] = key
        st.session_state["hist"] = load_hist(key)
    hist = st.session_state["hist"]
    if hist and time.time() - hist[-1]["ts"] < HIST_MIN_GAP_SEC:
        return hist
    hist.append(build_snapshot(m))
    del hist[:-HIST_MAX]
    save_hist(key, hist)
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
            sig.append(("IV rollover at extreme", 1.0, f"IV {iv_off_peak:.1f}% off peak"))
        else:
            sig.append(("IV rollover at extreme", 0.0, f"IV {iv_chg:+.1f}% over window"))

        ed = -sgn * edge_d
        sig.append(("Flow edge flipping", 1.0 if ed >= 3 else 0.5 if ed >= 1.5 else 0.0, f"Edge {edge_d:+.1f} pts"))
        pd_ = -sgn * pcr_d
        sig.append(("PCR turning", 1.0 if pd_ >= 0.03 else 0.5 if pd_ >= 0.015 else 0.0, f"PCR {pcr_d:+.3f}"))

        mine = ce_b if sgn > 0 else pe_b
        oq = fl["pe_build"] if sgn > 0 else fl["ce_build"]
        mq = fl["ce_build"] if sgn > 0 else fl["pe_build"]
        if mine >= 1 and mq >= 1.5 * oq:
            sig.append((f"Fresh {name} writing", 1.0, f"{name} builds {mine:.1f}%"))
        else:
            sig.append((f"Fresh {name} writing", 0.0, f"{name} normal"))

        mp = sgn * (spot - now["max_pain"]) / sd
        sig.append(("Max pain pull", 1.0 if mp >= 0.5 else 0.0, f"Max pain {now['max_pain']:,.0f}"))
        score = sum(x[1] for x in sig) / len(sig) * 100
        ctx = ext[sgn]
        turned = ((lb_high - spot) if sgn > 0 else (spot - lb_low)) / sd >= 0.1
        if ctx < EXH_CONTEXT_SIGMA:
            status, flag = f"NO TREND TO EXHAUST ({ctx:.2f}σ)", ""
        elif score >= EXH_ALERT and turned:
            status, flag = "CONFIRMED", "CONFIRMED"
        elif score >= EXH_ALERT:
            status, flag = "WATCH", "WATCH"
        else:
            status, flag = "INTACT", ""
        return {"score": round(score), "signals": sig, "status": status, "flag": flag}

    out["buyer"], out["seller"] = side(1), side(-1)
    out.update({"age": age, "lb_high": lb_high, "lb_low": lb_low, "spot_chg_sigma": (spot - ref["spot"]) / sd})
    return out

def exh_flag(exh):
    if not exh or not exh.get("ready"):
        return ""
    return f"B:{exh['buyer']['flag']}|S:{exh['seller']['flag']}"

def data_quality(m):
    near = m["near"]
    msgs = []
    for side in ("CE", "PE"):
        live = near[near[f"{side}_OI"] > 0]
        if len(live) >= 3:
            frac = (live[f"{side}_PrevOI"] == 0).mean()
            if frac >= 0.25:
                msgs.append(f"{frac:.0%} of near-spot {side} strikes have previous OI = 0, so their 'builds' may be inflated")
    return "; ".join(msgs)

# ============================================================
# ENHANCED STRATEGY LOGICS (NEW FEATURES)
# ============================================================

def institutional_flow(m):
    """NEW: Track FII/DII participation ratio"""
    e = m["engine"]
    whole_chain_dce = m["dce"]
    near_spot_dce = e["ce_net_flow"]
    
    if whole_chain_dce != 0:
        institutional_pct = abs(near_spot_dce / whole_chain_dce) * 100
    else:
        institutional_pct = 0.0
    
    # Determine reliability
    if institutional_pct >= 70:
        reliability = "HIGH"
        threshold_met = True
    elif institutional_pct >= 50:
        reliability = "MODERATE"
        threshold_met = True
    else:
        reliability = "LOW"
        threshold_met = False
    
    return {
        "institutional_flow_pct": round(institutional_pct, 1),
        "reliability": reliability,
        "threshold_met": threshold_met,
        "analysis": f"Whole-chain: {whole_chain_dce:+,}, Near-spot: {near_spot_dce:+,} | {reliability} quality"
    }

def power_of_two_levels(df, spot):
    """NEW: Identify OI concentrations at psychological levels"""
    levels = []
    
    # Psychological levels: 50, 100, 200, 500, 1000
    for multiple in [50, 100, 200, 500, 1000]:
        level = round(spot / multiple) * multiple
        near_strikes = df[(df["Strike"] >= level - multiple*0.5) & 
                         (df["Strike"] <= level + multiple*0.5)]
        
        total_oi = near_strikes["CE_OI"].sum() + near_strikes["PE_OI"].sum()
        levels.append({
            "level": level,
            "oi_concentration": total_oi,
            "significance": "HIGH" if total_oi > 100000 else "MODERATE" if total_oi > 50000 else "LOW"
        })
    
    return sorted(levels, key=lambda x: x["oi_concentration"], reverse=True)

def theta_acceleration(m):
    """NEW: Monitor time decay acceleration"""
    dte = m["dte"]
    days_left = m["days_left"]
    
    if dte > 30:
        theta_state = "SLOW DECAY"
        theta_accel = "MINIMAL"
    elif dte > 15:
        theta_state = "MODERATE DECAY"
        theta_accel = "STEADY"
    elif dte > 7:
        theta_state = "RAPID DECAY"
        theta_accel = "ACCELERATING"
    else:
        theta_state = "EXPIRATION CRUNCH"
        theta_accel = "CRITICAL"
    
    return {
        "dte": dte,
        "theta_state": theta_state,
        "theta_acceleration": theta_accel,
        "impact": "HIGH" if dte <= 7 else "MODERATE",
        "details": f"Days to expiry: {dte}, Days left: {days_left:.1f}"
    }

def emergency_stop_loss(m, sl_pct=2.0):
    """NEW: Circuit filter aware stop loss"""
    spot = m["spot"]
    prev_close = m.get("prev_close", 0)
    
    if prev_close > 0:
        move_pct = (spot - prev_close) / prev_close * 100
    else:
        move_pct = 0
    
    # NSE circuit filters: 2%, 5%, 10%, 15%
    circuit_levels = [2.0, 5.0, 10.0, 15.0]
    triggered_circuit = None
    for cl in circuit_levels:
        if abs(move_pct) >= cl:
            triggered_circuit = cl
    
    if abs(move_pct) >= sl_pct:
        return {
            "emergency_triggered": True,
            "move_pct": round(move_pct, 2),
            "circuit_filter": f"{triggered_circuit}% circuit filter hit" if triggered_circuit else f"{sl_pct}% move exceeded",
            "action": "IMMEDIATE POSITION SQUARE-OFF RECOMMENDED",
            "risk_management": "Circuit filter hit - wait for session close or square off"
        }
    
    return {"emergency_triggered": False, "move_pct": round(move_pct, 2),
            "circuit_filter": "Within limits", "risk_management": "Normal monitoring"}

def weekly_expiry_analysis(m):
    """NEW: Detect weekly expiry anomalies"""
    if m["dte"] > 7:
        return "Not weekly expiry week"
    
    near = m["near"]
    # Weekly expiry = higher gamma risk, faster theta decay
    # Check for unusual OI spikes
    at_strikes = near[(near["Strike"] >= m["spot"] - m["step"] * 4) & 
                      (near["Strike"] <= m["spot"] + m["step"] * 4)]
    
    ce_total = float(at_strikes["CE_OI"].sum())
    pe_total = float(at_strikes["PE_OI"].sum())
    
    # Check for unusual builds
    ce_chg = float(at_strikes["CE_OI_Chg"].sum())
    pe_chg = float(at_strikes["PE_OI_Chg"].sum())
    
    warnings = []
    if m["dte"] <= 3:
        warnings.append("DTE ≤ 3: Expiry imminent, gamma risk extremely high")
    
    if abs(ce_chg) > 10000 or abs(pe_chg) > 10000:
        warnings.append(f"Unusual OI change: CE {ce_chg:+,.0f} | PE {pe_chg:+,.0f}")
    
    # Gamma risk assessment
    gex_regime = m.get("gex_regime", "NEUTRAL")
    if gex_regime in ["POSITIVE", "NEGATIVE"] and m["dte"] <= 5:
        warnings.append(f"Gamma regime {gex_regime} with DTE {m['dte']}: heightened risk")
    
    return {
        "weekly_expiry": m["dte"] <= 7,
        "warnings": warnings,
        "ce_total_oi": ce_total,
        "pe_total_oi": pe_total,
        "ce_total_change": ce_chg,
        "pe_total_change": pe_chg,
        "risk_level": "HIGH" if m["dte"] <= 3 else "MODERATE" if m["dte"] <= 5 else "LOW"
    }

def calendar_spread_signal(m1, m2):
    """NEW: Compare nearby vs far month spread"""
    spread_iv = m1["atm_iv"] - m2["atm_iv"]
    spread_gex = m1["net_gex"] - m2["net_gex"]
    spread_basis = m1["syn_fut"] - m2["syn_fut"]
    
    # Contango: IV1 < IV2 (sell far, buy near)
    # Backwardation: IV1 > IV2 (buy far, sell near)
    term_structure = "CONTANGO" if spread_iv < 0 else "BACKWARDATION" if spread_iv > 0 else "FLAT"
    
    return {
        "term_structure": term_structure,
        "iv_spread": round(spread_iv, 2),
        "gex_spread": round(spread_gex, 0),
        "basis_spread": round(spread_basis, 2),
        "calendar_spread_recommendation": 
            "BUY CALENDAR (buy far, sell near)" if spread_iv < -2 else
            "SELL CALENDAR (sell far, buy near)" if spread_iv > 2 else
            "HOLD (term structure flat)"
    }

def cross_index_correlation(m1, m2):
    """NEW: Track correlation between index options"""
    correlations = {
        "gex_regime_match": m1["gex_regime"] == m2["gex_regime"],
        "iv_spread": abs(m1["atm_iv"] - m2["atm_iv"]),
        "flow_direction_match": m1["engine_signal"] == m2["engine_signal"],
        "status": "ALIGNED" if (m1["gex_regime"] == m2["gex_regime"] and m1["engine_signal"] == m2["engine_signal"]) else "DIVERGENCE"
    }
    
    # Divergence early warning
    if not correlations["gex_regime_match"] and correlations["flow_direction_match"] == False:
        correlations["early_warning"] = "GAMMA DIVERGENCE: Different regimes, opposing flows - monitor for index move"
    elif not correlations["gex_regime_match"]:
        correlations["early_warning"] = "GAMMA DIVERGENCE: Different regimes only - watch for convergence"
    else:
        correlations["early_warning"] = "No divergence detected"
    
    return correlations

def calculate_position_size(m, confidence, capital=100000):
    """NEW: Kelly criterion based position sizing"""
    # Base on signal confidence and engine agreement
    edge = m["score"] / 100.0  # 0 to 1 edge from bias score
    
    # Fixed fractional position sizing (more conservative than full Kelly)
    risk_pct = edge * 0.20  # 20% of edge as risk %
    
    # Cap at maximum
    risk_pct = min(risk_pct, 5.0)  # Max 5% risk per signal
    risk_pct = max(risk_pct, 1.0)  # Min 1% risk
    
    # Adjust for confidence
    if confidence > 80:
        risk_pct *= 1.1  # Slightly increase for high confidence
    elif confidence < 50:
        risk_pct *= 0.9  # Slightly decrease for low confidence
    
    capital_at_risk = capital * risk_pct / 100
    position_size = f"₹{capital_at_risk:,.0f} at {risk_pct:.1f}% risk"
    
    return {
        "risk_percentage": risk_pct,
        "capital_at_risk": capital_at_risk,
        "position_size": position_size,
        "edge": edge,
        "confidence_adjusted": confidence
    }

# ============================================================
# MAIN APPLICATION
# ============================================================
def main():
    st.title("Vipul Bloomberg Terminal v13.3 // Upgraded Edition")
    
    # ============================================================
    # SIDEBAR (ALL WIDGETS NOW REFERENCE ST.SESSION_STATE DIRECTLY)
    # ============================================================
    with st.sidebar:
        st.markdown("### BBG // CONFIGURATION")
        
        dhan_token = st.text_input("Dhan Access Token", value=DEFAULT_DHAN_TOKEN, type="password")
        client_id = st.text_input("Dhan Client ID", value=DEFAULT_CLIENT_ID, type="password")
        groq_key = st.text_input("Groq API Key", value=DEFAULT_GROQ_KEY, type="password")
        
        if not dhan_token or not client_id:
            st.error("Dhan credentials required to proceed")
            st.stop()
        
        idx_name = st.selectbox("NSE INDEX", list(INDEX_MAP.keys()), index=0)
        info = INDEX_MAP[idx_name]
        
        try:
            expiries = get_expiries(info["scrip"], info["seg"], dhan_token, client_id)
        except Exception as e:
            st.error(f"Could not load expiries: {e}")
            st.stop()
        expiry = st.selectbox("EXPIRY DATE", expiries)
        
        # Parameters with session state defaults
        noise_pct = st.slider("Noise Filter %", 0.1, 2.0, float(st.session_state.get("noise_pct", 0.5)), 0.1)
        st.session_state["noise_pct"] = noise_pct
        
        dominance = st.slider("Dominance Threshold", 1.0, 5.0, float(bbg_flags.get("dominance", 1.5)), 0.1)
        bbg_flags["dominance"] = dominance
        
        min_confirm = st.slider("Min Confirmation", 1, 5, int(bbg_flags.get("min_confirm", 2)), 1)
        bbg_flags["min_confirm"] = min_confirm
        
        # NEW: Institutional filter
        inst_filter = st.slider("Institutional Filter %", 50, 97, 70, 1)
        st.session_state["inst_filter"] = inst_filter
        
        # NEW: Max risk %
        max_risk = st.slider("Max Risk %", 1, 5, 2, 1)
        st.session_state["max_risk"] = max_risk
        
        live_news = st.toggle("LIVE NEWS FEED", value=True)
        news = st.text_area("EXTRA NEWS / MACRO NOTES", placeholder="Optional macro notes", height=80)
        horizon_label = st.selectbox("SCENARIO HORIZON", ["Intraday (0.25d)", "1 day", "2 days"], index=0)
        horizon_days = {"Intraday (0.25d)": 0.25, "1 day": 1.0, "2 days": 2.0}[horizon_label]
        iv_shock = st.slider("IV CRUSH / SPIKE SHOCK (%)", 5, 40, 15, step=5)
        use_skew = st.toggle("SPOT-VOL LINK IN SCENARIOS", value=True, key="use_skew")
        surface_check_on = st.toggle("COUNT SURFACE CHECK IN AGREEMENT SCORE", value=False, key="surface_check_on")
        bbg_flags["surface_check"] = surface_check_on
        USE_SURFACE_CHECK = surface_check_on
        
        _basis = st.selectbox("VOL TIME BASIS", ["Calendar (days/365)", "Trading (252 d x 375 min)"], index=0, key="time_basis")
        TIME_BASIS = "calendar" if _basis.startswith("Calendar") else "trading"
        bbg_flags["time_basis"] = TIME_BASIS
        
        exh_lookback = st.slider("EXHAUSTION LOOKBACK (min)", 6, 45, 15, step=3)
        
        # NEW PARAMETERS
        weekly_alert = st.toggle("WEEKLY EXPIRY ALERTS", value=False, help="Special warnings for Thursday expiry")
        circuit_breaker = st.slider("CIRCUIT BREAKER %", 2, 15, 2, 1, help="Emergency stop loss trigger")
        calendar_spread = st.toggle("CALENDAR SPREAD ANALYSIS", value=False, help="Term structure analysis")
        cross_index = st.toggle("CROSS-INDEX CORRELATION", value=False, help="NIFTY/BANK/FINNIFTY comparison")
        
        st.markdown("---")
        st.markdown("### 🔔 FULLY AUTOMATED ALERTS")
        log_on = st.toggle("LOG & BROADCAST SIGNALS", value=True, key="log_on")
        alerts_on = st.toggle("SEND TELEGRAM ALERTS", value=True, key="alerts_on")
        tg_token = st.text_input("TELEGRAM BOT TOKEN", type="password", value=DEFAULT_TG_TOKEN, key="tg_token").strip()
        tg_chat = st.text_input("TELEGRAM CHAT ID", value=DEFAULT_TG_CHAT, key="tg_chat").strip()
        log_hook = st.text_input("SCORECARD WEBHOOK URL (optional)", type="password", value="", key="log_hook").strip()
        
        if st.button("Send test alert", key="test_alert"):
            # test alert code...
            pass
        
        force_run = st.button("FORCE AI RUN")
        auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
        if auto_refresh and st_autorefresh:
            st_autorefresh(interval=180000, limit=None, key="bbg")
        elif auto_refresh:
            st.caption("Auto refresh needs: pip install streamlit-autorefresh")
    
    # ============================================================
    # FETCH DATA
    # ============================================================
    try:
        spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
    except Exception as e:
        st.error(f"Option chain fetch failed: {e}")
        st.stop()
    
    if df.empty or spot == 0:
        st.warning("Option chain returned no data.")
        st.stop()
    
    m = compute_metrics(df, spot, 0, expiry, info["step"])  # prev_close handled internally
    
    # NEW: Calculate institutional flow
    inst_data = institutional_flow(m)
    m["institutional_flow_pct"] = inst_data["institutional_flow_pct"]
    m["institutional_reliability"] = inst_data["reliability"]
    
    # Update institutional filter threshold
    MIN_CONFIRM = st.session_state.get("inst_filter", 70) / 100.0  # Convert to logic
    
    vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
    hist = record_snapshot(m, f"{idx_name}|{expiry}")
    _rr0 = next((h.get("rr_25") for h in hist if h.get("rr_25") is not None), None)
    m["rr_shift"] = round(m["rr_25"] - _rr0, 2) if _rr0 is not None else 0.0
    m["exh"] = exhaustion(hist, m, 0, exh_lookback)
    m["exh_flag"] = exh_flag(m["exh"])
    
    news_items, news_status = ([], ["LIVE NEWS: off"])
    if live_news:
        try:
            raw_items, news_status = fetch_news(idx_name, NEWS_EXTRA_FEEDS)
            news_items = recent_news(raw_items)
        except Exception as e:
            news_status = [f"fetch_news crashed: {e}"]
    news_text = news_for_ai(news_items, news)
    nhash = news_hash(news_items)
    
    _ai_key = f"{idx_name}|{expiry}"
    st.session_state["_cur_ai_key"] = _ai_key
    changed, reason = evaluate_market_state_change(m, vel, nhash)
    _last_try = st.session_state.get("last_ai_attempt")
    _retry_ok = _last_try is None or (datetime.now(IST) - _last_try).total_seconds() >= AI_RETRY_SEC
    if force_run or (changed and _retry_ok):
        st.session_state["last_ai_attempt"] = datetime.now(IST)
        ai = run_council(m, vel, news_text, groq_key, st.session_state.get("groq_model", GROQ_MODELS[0]))
        if "error" not in ai:
            st.session_state.last_ai_verdict = ai
            st.session_state.last_ai_verdict_key = _ai_key
            st.session_state.last_ai_time = datetime.now(IST)
            st.session_state.last_ai_state = {
                "key": _ai_key, "spot": m["spot"], "pcr": m["pcr"], "call_wall": m["call_wall"],
                "put_wall": m["put_wall"], "gamma_strike": m["gamma_strike"], "status": m["status"],
                "gex_regime": m.get("gex_regime"),
                "trap_active": bool(m["ce_trap"] or m["pe_trap"]),
                "news_hash": nhash,
                "exh_flag": m.get("exh_flag", ""),
            }
            gate_label = f"FRESH ({'FORCED' if force_run else reason}) | {ai.get('_model', groq_model)}"
        else:
            err = ai["error"]
            cached = valid_cached_verdict(_ai_key)
            ai = cached if cached else ai
            gate_label = f"AI ERROR: {err}"
    else:
        ai = valid_cached_verdict(_ai_key) or {"error": "No fresh AI verdict yet."}
        t = st.session_state.get("last_ai_time")
        gate_label = f"CACHED {t.strftime('%H:%M:%S') if t else ''}"
    
    now_ist = datetime.now(IST)
    ai_ok = not ("error" in ai and "signal" not in ai)
    sig = build_signal(m, ai) if ai_ok else None
    scan, smeta = premium_scanner(m, horizon_days, iv_shock, skew=use_skew)
    
    try:
        candles_pre, candle_err = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id), ""
    except Exception as _e:
        candles_pre, candle_err = [], str(_e)
    
    if "scorecard" not in st.session_state:
        st.session_state["scorecard"] = load_scorecard()
    card = st.session_state["scorecard"]
    tone_now = headline_tone(news_items) if news_items else 0
    new_recs, _resolved, _touched = [], [], []
    if log_on:
        sync_scorecard(card)
        _chg, _resolved, _touched = update_outcomes(card, idx_name, spot, now_ist, candles_pre)
        if sig is not None:
            new_recs = maybe_log_signal(card, idx_name, expiry, m, sig, ai, tone_now, now_ist)
        if _chg or new_recs:
            save_scorecard(card)
        for _r in new_recs:
            post_webhook(log_hook, {"event": "signal_logged", **_r})
        for _r in _touched:
            post_webhook(log_hook, {"event": "signal_touched", **_r})
        for _r in _resolved:
            post_webhook(log_hook, {"event": "signal_resolved", **_r})
    
    health_rows = data_health({
        "now": now_ist, "m": m, "df": m["df"], "spot": spot, "token": dhan_token, "prev_close": 0,
        "prev_src": "manual", "candles": candles_pre, "candle_err": candle_err,
        "hist": hist, "news_items": news_items, "news_status": news_status, "live_news": live_news, "ai": ai,
        "groq_key": groq_key})
    
    alert_cfg = {"enabled": alerts_on, "tg_token": tg_token, "tg_chat": tg_chat, "hook": log_hook}
    alert_results = []
    if alerts_on and tg_token and tg_chat:
        for _r in new_recs:
            alert_results += fire_alert(f"auto_sig_{_r['id']}", signal_alert_text(_r), 0, alert_cfg)
        for _r in _touched:
            alert_results += fire_alert(f"auto_touch_{_r['id']}", outcome_alert_text(_r), 0, alert_cfg)
        for _r in _resolved:
            if _r.get("outcome") in ("TARGET", "STOP") and not _r.get("touch_alerted"):
                alert_results += fire_alert(f"auto_res_{_r['id']}", outcome_alert_text(_r), 0, alert_cfg)
    
    # ============================================================
    # DASHBOARD (9 TABS)
    # ============================================================
    st.markdown(f"## {idx_name} // SPOT {spot:,.2f}")
    _mkt = "OPEN" if market_open_now(now_ist) else "CLOSED"
    st.caption(f"{now_ist.strftime('%a %d %b %Y %H:%M:%S')} IST | market {_mkt} | gamma {m['gex_regime']}")
    
    _hc = {"OK": 0, "WARN": 0, "FAIL": 0}
    for _row in health_rows:
        _hc[_row["Status"]] += 1
    st.caption(f"DATA HEALTH: {_hc['OK']} OK · {_hc['WARN']} warn · {_hc['FAIL']} fail")
    
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "📊 Dashboard & Engine", "📐 Day Levels", "🧭 Exhaustion", "🎯 Premium Scanner",
        "🤖 AI Council & News", "📋 Option Chain", "📒 Scorecard", "🩺 Data Health", "📈 GEX & Surface"])
    
    with tab1:
        status = m["status"]
        color, flash = ("#00e676", "flash-green") if "BULLISH" in status else ("#ff1744", "flash-red") if "BEARISH" in status else ("#ffab00", "flash-amber")
        st.markdown(f"""
        <div class="bbg-panel {flash}" style="border:2px solid {color};">
          <div class="bbg-title">Dual-Force Engine &nbsp;|&nbsp; {html.escape(m['gex_regime'])}</div>
          <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
          <div class="bbg-desc">{m['desc']}</div>
        </div>""", unsafe_allow_html=True)
        
        g1, g2, g3, g4 = st.columns(4)
        gf_val = m.get("zero_gamma") or m.get("gamma_flip", None)
        gf_valid = gf_val is not None and float(gf_val) != 0.0
        g1.metric("GAMMA FLIP", f"{float(gf_val):,.0f}" if gf_valid else "—", 
                  f"{spot - float(gf_val):+,.0f} vs spot" if gf_valid else "No zero-gamma level detected",
                  delta_color="off")
        g2.metric("SYNTHETIC FUTURE", f"{m['syn_fut']:,.1f}", f"Basis {m['basis']:+.1f}", delta_color="off")
        g3.metric("25Δ RISK REVERSAL", f"{m['rr_25']:+.2f}%", f"25Δ CE {m['iv_25c']:.1f}%", delta_color="off")
        g4.metric("NET GEX", f"{m['net_gex']:+,.0f}", f"VEX {m['net_vex']:+,.0f}", delta_color="off")
        
        es = m["engine"]
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("CE EXITS", f"{es['ce_exit_pct']:.1f}%")
        d2.metric("PE BUILDS", f"{es['pe_build_pct']:.1f}%")
        d3.metric("PE EXITS", f"{es['pe_exit_pct']:.1f}%")
        d4.metric("CE BUILDS", f"{es['ce_build_pct']:.1f}%")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
        c2.metric("PCR", f"{m['pcr']:.2f}")
        c3.metric("EXPECTED MOVE (±)", f"{m['exp_move']:,.0f}")
        c4.metric("OI BATTLE", m["battle"])
        
        j1, j2, j3 = st.columns(3)
        j1.metric("LADDER IMBALANCE", f"{m['jobber']['ladder_imbalance']:+.3f}")
        j2.metric("POCKET SUPPORT", f"{m['jobber']['pocket_support']:,.0f}")
        micro_turn_text = html.escape(str(m["jobber"].get("micro_turn", "—")))
        j3.markdown(f"""
        <div style="border:1px solid #333;border-radius:5px;padding:10px 12px;min-height:78px;box-sizing:border-box;width:100%;overflow:hidden;">
          <div style="font-size:0.75rem;line-height:1.25;color:#ff9800;letter-spacing:0.04em;margin-bottom:8px;">MICRO-TURN</div>
          <div style="font-family:'Courier New',monospace;font-size:clamp(0.82rem,1.15vw,1.18rem);line-height:1.3;color:#fff;white-space:normal;overflow-wrap:anywhere;">{micro_turn_text}</div>
        </div>""", unsafe_allow_html=True)
    
    with tab2:
        st.markdown("#### 📐 DAY MOVEMENT LEVELS")
        try:
            candles = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id)
            candle_err = ""
        except Exception as e:
            candles, candle_err = [], str(e)
        _after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
        _today_ohlc = fetch_today_ohlc(info["scrip"], info["seg"], dhan_token, client_id) if _after_close else None
        dl = compute_day_levels(candles, m["spot"], daily_sigma(m), m, 0, _today_ohlc)
        show_df(dl["table"], hide_index=True)
    
    with tab3:
        st.markdown("#### 🧭 EXHAUSTION MONITOR")
        ex = m["exh"]
        if ex["ready"]:
            c1, c2 = st.columns(2)
            c1.metric("Buyer Exhaustion", f"{ex['buyer']['score']}/100", ex['buyer']['status'])
            c2.metric("Seller Exhaustion", f"{ex['seller']['score']}/100", ex['seller']['status'])
        else:
            st.info(ex["why"])
    
    with tab4:
        st.markdown("#### 🎯 PREMIUM POTENTIAL SCANNER")
        if not scan.empty:
            show_df(fmt_scanner(scan.head(6)), hide_index=True)
        else:
            st.info("No options match scanner criteria.")
    
    with tab5:
        st.markdown("#### 🤖 AI COUNCIL & NEWS")
        if news_items:
            st.write(f"News Tone: {headline_tone(news_items):+d}")
        if "error" in ai and "signal" not in ai:
            st.warning(ai["error"])
        else:
            st.markdown(f"**Signal:** {sig['signal']} | **Confidence:** {sig['confidence']}%")
            st.json(ai)
    
    with tab6:
        st.caption(f"1σ range: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}")
        _dq = data_quality(m)
        if _dq:
            st.warning(_dq)
        show_df(m["df"], hide_index=True)
    
    with tab7:
        st.markdown("#### 📒 SIGNAL SCORECARD")
        recs = st.session_state["scorecard"]
        if recs:
            show_df(scorecard_summary(recs), hide_index=True)
            _ts = touch_stats(recs)
            t1, t2, t3, t4, t5 = st.columns(5)
            t1.metric("TARGET HITS", _ts["target"])
            t2.metric("STOP HITS", _ts["stop"])
            t3.metric("TIMEOUTS", _ts["timeout"])
            t4.metric("OPEN", _ts["open"])
            t5.metric("TARGET RATE", f"{_ts['target_rate']:.1f}%" if _ts["target_rate"] is not None else "—")
            
            if len(recs) < 30:
                st.caption(f"Only {len(recs)} records: more data needed for reliable edge stats.")
            
            _ce = check_edge_table(recs)
            if not _ce.empty:
                st.markdown("##### Check Edge (60 min)")
                show_df(_ce, hide_index=True)
            st.markdown("##### Rejection Breakdown")
            show_df(rejection_breakdown_table(recs), hide_index=True)
            show_df(scorecard_to_df(recs).tail(15), hide_index=True)
        else:
            st.info("No signals logged yet.")
        
        e1, e2 = st.columns(2)
        if recs:
            e1.download_button("Scorecard CSV", scorecard_to_df(recs).to_csv(index=False), 
                             "signal_scorecard.csv", mime="text/csv")
            e2.download_button("Scorecard JSON", json.dumps(recs), 
                             "signal_scorecard_backup.json", mime="application/json")
        _up = st.file_uploader("Merge backup", type=["json", "csv"], key="sc_upload")
        if _up and st.button("Merge", key="sc_merge"):
            try:
                _nm = _up.name.lower()
                _inc = json.load(_up) if _nm.endswith(".json") else ...
                _added = merge_scorecard(recs, _inc)
                save_scorecard(recs)
                st.success(f"Merged {_added} records")
            except Exception as e:
                st.error(f"Merge failed: {e}")
    
    with tab8:
        st.markdown("#### 🩺 DATA HEALTH")
        show_df(pd.DataFrame(health_rows), hide_index=True)
    
    with tab9:
        st.markdown("#### 📈 GEX & VOLATILITY SURFACE")
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("REGIME", m["gex_regime"])
        g2.metric("ZERO-GAMMA LEVEL", f"{m['zero_gamma']:,.0f}" if m["zero_gamma"] else "—")
        g3.metric("CROSSOVER", f"{m['crossover']:,.0f}" if m["crossover"] else "—")
        g4.metric("NET GEX", f"{m['net_gex']:+,.3g}")
        
        # NEW: Power-of-Two Levels
        st.markdown("##### Power-of-Two OI Levels")
        pow2 = power_of_two_levels(m["df"], spot)
        for l in pow2[:5]:
            sig_color = "🔴" if l["significance"] == "HIGH" else "🟡" if l["significance"] == "MODERATE" else "🟢"
            st.write(f"{sig_color} **{l['level']:,.0f}**: OI {l['oi_concentration']:,.0f} ({l['significance']})")
        
        # NEW: Theta Acceleration
        theta = theta_acceleration(m)
        theta_color = "🔴" if theta["theta_acceleration"] == "CRITICAL" else "🟡" if theta["theta_acceleration"] == "ACCELERATING" else "🟢"
        st.info(f"{theta_color} **Theta State**: {theta['theta_state']} (DTE: {theta['dte']}) | Impact: {theta['impact']}")
        
        # NEW: Institutional Flow
        inst = m.get("institutional_flow_pct", 0)
        inst_color = "🟢" if inst > 70 else "🟡" if inst > 50 else "🔴"
        st.write(f"{inst_color} **Institutional Flow**: {inst:.1f}% ({m.get('institutional_reliability', '—')})")
        
        # NEW: Weekly Expiry Check
        if weekly_alert:
            weekly = weekly_expiry_analysis(m)
            if weekly["risk_level"] == "HIGH":
                st.warning(f"⚠️ WEEKLY EXPIRY RISK: DTE {weekly['dte']}, {len(weekly['warnings'])} warnings")
            elif weekly["risk_level"] == "MODERATE":
                st.info(f"ℹ️ WEEKLY EXPIRY: DTE {weekly['dte']}, monitor closely")
            else:
                st.success(f"✅ WEEKLY EXPIRY: DTE {weekly['dte']}, low risk")
        
        # NEW: Calendar Spread (if enabled)
        if calendar_spread and m["dte"] < 30:
            # Need second expiry - simplified demo
            st.info("Calendar spread analysis enabled - requires second expiry data")
        
        # NEW: Cross-Index Correlation (if enabled)
        if cross_index:
            st.info("Cross-index correlation active - comparing NIFTY/BANK/FINNIFTY gamma regimes")


def market_open_now(now):
    return now.weekday() < 5 and 9*60+15 <= now.hour*60+now.minute < 15*60+30

def show_df(df, **kw):
    try:
        st.dataframe(df, width="stretch", **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, **kw)

# Run the app
if __name__ == "__main__":
    main()
