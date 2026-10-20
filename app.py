"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.3 UPGRADED EDITION
- All P0/P1/P2 accuracy fixes integrated
- Adaptive strategies engine added
- Regime-aware dynamic thresholds
- Confluence scoring system
- IV-adjusted position sizing
- Session state isolation
- Auto-save scorecard with hash integrity
No order execution features: this app places no orders.
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

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import plotly.graph_objects as go
except ImportError:
    go = None

st.set_page_config(page_title="Vipul Bloomberg Terminal v13.3 Upgraded", layout="wide", initial_sidebar_state="expanded")

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
# CONFIGURATION & GLOBALS (SESSION-ISOLATED)
# ============================================================
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
PROFILE_URL = f"{BASE_URL}/profile"

YEAR_DAYS = 365

# SESSION STATE INITIALIZATION (CRITICAL - replaces module globals)
for key in ["time_basis", "skew_coef", "min_confirm", "dominance", "flow_edge_pct", 
            "min_rr", "sl_buffer", "noise_pct", "exh_context_sigma", "exh_alert",
            "min_flow_pct", "surface_check"]:
    if key not in st.session_state:
        if key == "time_basis": st.session_state[key] = "calendar"
        elif key == "skew_coef": st.session_state[key] = 0.5
        elif key == "min_confirm": st.session_state[key] = 2
        elif key == "dominance": st.session_state[key] = 1.5
        elif key == "flow_edge_pct": st.session_state[key] = 2.0
        elif key == "min_rr": st.session_state[key] = 1.0
        elif key == "sl_buffer": st.session_state[key] = 35
        elif key == "noise_pct": st.session_state[key] = 0.5
        elif key == "exh_context_sigma": st.session_state[key] = 0.4
        elif key == "exh_alert": st.session_state[key] = 60
        elif key == "min_flow_pct": st.session_state[key] = 2.0
        elif key == "surface_check": st.session_state[key] = False

TIME_BASIS = st.session_state["time_basis"]
SKEW_COEF = st.session_state["skew_coef"]
MIN_CONFIRM = st.session_state["min_confirm"]
DOMINANCE = st.session_state["dominance"]
FLOW_EDGE_PCT = st.session_state["flow_edge_pct"]
MIN_RR = st.session_state["min_rr"]
SL_BUFFER = st.session_state["sl_buffer"]
NOISE_PCT = st.session_state["noise_pct"]
EXH_CONTEXT_SIGMA = st.session_state["exh_context_sigma"]
EXH_ALERT = st.session_state["exh_alert"]
MIN_FLOW_PCT = st.session_state["min_flow_pct"]
USE_SURFACE_CHECK = st.session_state["surface_check"]

IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = os.getenv("BBG_DATA_DIR") or tempfile.gettempdir()
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = tempfile.gettempdir()

HIST_MIN_GAP_SEC = 60
HIST_MAX = 400
OI_MAP_SPAN = 800
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
SURF_CHECK_NAME = "Synthetic basis vs fair carry"
SURFACE_KEYS = ("syn_fut", "fair_fwd", "carry", "basis", "excess_basis", "surface_ok", "iv_25c", "iv_25p", "rr_25", "bf_25",
                "net_gex", "gross_gex", "gex_regime", "gex_regime_label", "regime_stable", "regime_by_window", "crossover",
                "zero_gamma", "total_gex_at_spot", "net_vex", "net_cex")
REGIME_LABEL = {
    "POSITIVE": "POSITIVE GAMMA (if dealers are long calls / short puts: mean-reverting)",
    "NEGATIVE": "NEGATIVE GAMMA (if dealers are long calls / short puts: momentum / breakout)",
    "NEUTRAL": "NEUTRAL GAMMA (net exposure near zero)",
}


# ============================================================
# ROBUST PARSING & UTILS
# ============================================================
def _n(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_json_parse(txt: str) -> dict:
    """P0 CRITICAL: Robust JSON parsing with multiple fallbacks."""
    try:
        return json.loads(txt.strip())
    except json.JSONDecodeError:
        pass
    
    match = re.search(r'```(?:json)?\n(.*?)```', txt, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', txt.strip())
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
            
    return {"error": "LLM_JSON_PARSE_FAILED", "signal": "WAIT", "confidence": 0, "raw_preview": txt[:500]}


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


_last_api_call = 0
def api_throttle(min_interval=3.0):
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_api_call = time.time()


# ============================================================
# DATA FETCHERS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    api_throttle()
    r = requests.post(EXPIRY_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    api_throttle()
    r = requests.post(OPTIONCHAIN_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry},
                      timeout=12)
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


@st.cache_data(ttl=600, show_spinner=False)
def fetch_prev_close(scrip, seg, token, client_id):
    err = ""
    try:
        candles = fetch_daily_candles(scrip, seg, token, client_id)
        today = datetime.now(IST).date().isoformat()
        prior = [c for c in candles if c["date"] < today]
        if prior:
            return round(prior[-1]["c"], 2), f"daily candle close {prior[-1]['date']}"
        err = "no completed candle before today"
    except Exception as e:
        err = str(e)[:100]
    try:
        r = requests.post(f"{BASE_URL}/marketfeed/quote", headers=auth_headers(token, client_id),
                          json={seg: [scrip]}, timeout=10)
        check(r)
        q = (r.json().get("data") or {}).get(seg, {}).get(str(scrip)) or {}
        ltp, net = _n(q.get("last_price")), _n(q.get("net_change"))
        if ltp > 0 and net != 0:
            return round(ltp - net, 2), "quote: LTP - net_change"
        return None, f"candles: {err}; quote had no net_change"
    except Exception as e:
        return None, f"candles: {err}; quote: {str(e)[:80]}"


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
# INSTITUTIONAL SURFACE & ANALYSIS ENGINE
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
        **{k: surf[k] for k in SURFACE_KEYS},
    }


# ============================================================
# ADAPTIVE STRATEGIES & CONFLUENCE ENGINES (P1/P2 SAFE)
# ============================================================
def select_adaptive_strategy(m) -> dict:
    regime = m["gex_regime"]
    iv_pct = m["atm_iv"]
    dte = m["dte"]
    ex = m.get("exh") or {}
    exhaustion_status = ex.get("buyer", {}).get("status") if ex.get("ready") else "COLLECTING"
    flow_gap = abs(m["engine"]["pe_net_flow"] - m["engine"]["ce_net_flow"])
    
    strategy = {
        "name": "SIDELINES",
        "action": "WAIT",
        "position_size_pct": 0,
        "expected_holding": "UNKNOWN",
        "rationale": []
    }
    
    if regime == "NEGATIVE" and exhaustion_status != "CONFIRMED" and flow_gap > 15000:
        strategy.update({
            "name": "MOMENTUM BREAKOUT",
            "action": "BUY",
            "position_size_pct": 15,
            "expected_holding": "1-3 days",
            "rationale": [
                "Negative gamma = dealer short gamma = momentum favoring",
                "Strong OI flow confirms direction",
                "No exhaustion = trend intact"
            ]
        })
    elif regime == "POSITIVE" and exhaustion_status == "CONFIRMED":
        strategy.update({
            "name": "MEAN REVERSION PLAY",
            "action": "SELL",
            "position_size_pct": 10,
            "expected_holding": "Same day to expiry",
            "rationale": [
                "Positive gamma = dealer long gamma = mean reversion",
                "Exhaustion confirmed = trend spent",
                "Sell premium decaying to zero"
            ]
        })
    elif iv_pct > 20 and dte < 21:
        strategy.update({
            "name": "THETA DECAY ACCELERATION",
            "action": "SELL OTM",
            "position_size_pct": 12,
            "expected_holding": "Until 50% premium decay or 7 DTE",
            "rationale": [
                "Rich premium = favorable for sellers",
                "Time decay accelerates near expiry",
                "Limit risk with strict stops"
            ]
        })
    elif iv_pct < 15 and dte > 45:
        strategy.update({
            "name": "IV REVERSION LONG",
            "action": "BUY OTM",
            "position_size_pct": 8,
            "expected_holding": "Until IV expands or 50% move",
            "rationale": [
                "Cheap premium = favorable for buyers",
                "IV expansion potential",
                "Far expiry = room for thesis to play out"
            ]
        })
    else:
        strategy.update({
            "name": "SIDELINES",
            "action": "WAIT",
            "position_size_pct": 0,
            "expected_holding": "Until conditions change",
            "rationale": [
                "No optimal strategy meets criteria",
                "Wait for favorable regime/IV setup"
            ]
        })
    
    return strategy


def confluence_score(m, ai_signal: str) -> dict:
    components = {
        "gamma_regime": 0, "oi_flow": 0, "technical_levels": 0,
        "sentiment": 0, "risk_reward": 0
    }
    
    if m["gex_regime"] == "NEGATIVE" and ai_signal == "BUY":
        components["gamma_regime"] = 20
    elif m["gex_regime"] == "POSITIVE" and ai_signal == "SELL":
        components["gamma_regime"] = 20
    elif m["gex_regime"] == "NEUTRAL":
        components["gamma_regime"] = 10
    
    flow_gap = m["engine"]["pe_net_flow"] - m["engine"]["ce_net_flow"]
    if abs(flow_gap) > 20000:
        components["oi_flow"] = 20
    elif abs(flow_gap) > 10000:
        components["oi_flow"] = 10
    
    if m["struct"]["res"] and m["struct"]["sup"]:
        components["technical_levels"] = 15
    if m["zero_gamma"]:
        components["technical_levels"] += 5
        
    tone = m.get("headline_tone", 0)
    if ai_signal == "BUY" and tone > 0:
        components["sentiment"] = 10
    elif ai_signal == "SELL" and tone < 0:
        components["sentiment"] = 10
        
    total = sum(components.values())
    confidence = min(100, total)
    grade = "A+" if total >= 80 else "A" if total >= 65 else "B" if total >= 50 else "C"
    recommendation = "HIGH CONFIDENCE" if total >= 80 else "MODERATE" if total >= 50 else "LOW - Stand Aside"
    
    return {
        "total": total, "components": components, "grade": grade,
        "confidence": confidence, "recommendation": recommendation
    }


def iv_adjusted_position_size(iv_pct: float, dte: int = 30, account_risk: float = 1.0, max_position: float = 20.0) -> float:
    if iv_pct >= 25:
        factor = 0.6
    elif iv_pct >= 15:
        factor = 0.8
    elif iv_pct >= 10:
        factor = 1.0
    else:
        factor = 1.2
        
    dte_factor = min(1.0, dte / 30.0)
    final_size = max(1.0, min(max_position, account_risk * factor * dte_factor))
    return round(final_size, 1)


def render_regime_disclaimer():
    st.markdown("""
    <div style="
        border-left: 4px solid #ff9800;
        background: #2a0a0a;
        padding: 12px 16px;
        margin: 12px 0;
        border-radius: 4px;
        color: #ff9800;
        font-family: 'Courier New', monospace;
        font-size: 0.85rem;
    ">
        ⚠️ <b>DEALER POSITIONING DISCLAIMER:</b> Gamma exposure (GEX/zero-gamma) calculations assume 
        dealers are <b>long calls / short puts</b> (typical for NSE index options). This assumption 
        <b>is not confirmed on NSE</b> and should be treated as <b>probabilistic guidance</b>, not fact. 
        Actual dealer positioning may differ—use as one input among many, not as definitive signal.
    </div>
    """, unsafe_allow_html=True)


# ============================================================
# MAIN APPLICATION
# ============================================================
def main():
    render_regime_disclaimer()
    
    with st.sidebar:
        st.markdown("### BBG // TERMINAL v13.3 UPGRADED")
        dhan_token = clean(get_secret("DHAN_ACCESS_TOKEN", DEFAULT_DHAN_TOKEN))
        client_id = clean(get_secret("DHAN_CLIENT_ID", DEFAULT_CLIENT_ID))
        groq_key = get_secret("GROQ_API_KEY", DEFAULT_GROQ_KEY).strip()

        groq_model = st.selectbox("GROQ MODEL", GROQ_MODELS, index=0)

        if not dhan_token or not client_id:
            st.error("DHAN access token and client ID required.")
            st.stop()

        idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
        info = INDEX_MAP[idx_name]

        try:
            expiries = get_expiries(info["scrip"], info["seg"], dhan_token, client_id)
        except Exception as e:
            expiries = []
            st.error(f"Could not load expiries: {e}")
        if not expiries:
            st.warning("No expiries available.")
            st.stop()

        expiry = st.selectbox("EXPIRY DATE", expiries)
        auto_prev = st.toggle("AUTO PREV CLOSE", value=True)
        auto_val, auto_src = (None, "")
        if auto_prev:
            auto_val, auto_src = fetch_prev_close(info["scrip"], info["seg"], dhan_token, client_id)
        if auto_prev and auto_val:
            prev_close = float(auto_val)
            st.caption(f"PREV CLOSE = {prev_close:,.2f}  ({auto_src})")
        else:
            prev_close = st.number_input("PREV CLOSE", min_value=0.0, value=0.0, step=float(info["step"]))

        st.sidebar.markdown("---")
        st.sidebar.markdown("**Adaptive Engine Parameters**")
        MIN_CONFIRM = st.sidebar.slider("Min signal confirmation", 1, 5, int(st.session_state["min_confirm"]))
        DOMINANCE = st.sidebar.slider("Gamma dominance ratio", 1.0, 3.0, float(st.session_state["dominance"]))
        FLOW_EDGE_PCT = st.sidebar.slider("Near-spot flow edge %", 0.5, 5.0, float(st.session_state["flow_edge_pct"]))
        MIN_RR = st.sidebar.slider("Minimum risk:reward", 0.5, 3.0, float(st.session_state["min_rr"]))
        SL_BUFFER = st.sidebar.slider("Stop-loss buffer (pts)", 10, 100, int(st.session_state["sl_buffer"]))
        MIN_FLOW_PCT = st.sidebar.slider("Min flow percentage", 0.5, 5.0, float(st.session_state["min_flow_pct"]))
        
        # Fixed float type casting for Noise Floor slider
        NOISE_PCT = st.sidebar.slider("Noise floor %", 0.1, 2.0, float(st.session_state["noise_pct"]), 0.1)

        st.session_state["min_confirm"] = MIN_CONFIRM
        st.session_state["dominance"] = DOMINANCE
        st.session_state["flow_edge_pct"] = FLOW_EDGE_PCT
        st.session_state["min_rr"] = MIN_RR
        st.session_state["sl_buffer"] = SL_BUFFER
        st.session_state["min_flow_pct"] = MIN_FLOW_PCT
        st.session_state["noise_pct"] = NOISE_PCT

        force_run = st.button("FORCE AI RUN")
        auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
        if auto_refresh and st_autorefresh:
            st_autorefresh(interval=180000, limit=None, key="bbg")

    try:
        spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
    except Exception as e:
        st.error(f"Option chain fetch failed: {e}")
        st.stop()

    if df.empty or spot == 0:
        st.warning("Option chain returned no data.")
        st.stop()

    m = compute_metrics(df, spot, prev_close, expiry, info["step"])
    
    # Run adaptive and confluence engines (fully safe against missing optional keys)
    adaptive_strat = select_adaptive_strategy(m)
    confluence = confluence_score(m, adaptive_strat["action"])
    pos_size = iv_adjusted_position_size(m["atm_iv"], m["dte"])

    st.markdown(f"## {idx_name} // SPOT {spot:,.2f}" + (f"  ({spot - prev_close:+,.2f})" if prev_close > 0 else ""))
    
    # Display Adaptive Strategy & Confluence Metrics on Dashboard
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ADAPTIVE STRATEGY", adaptive_strat["name"], adaptive_strat["action"])
    c2.metric("CONFLUENCE GRADE", f"{confluence['grade']} ({confluence['total']}/100)", confluence["recommendation"])
    c3.metric("IV-ADJUSTED SIZE", f"{pos_size}%", f"ATM IV: {m['atm_iv']:.1f}%")
    c4.metric("REGIME SETUP", m["gex_regime"])


if __name__ == "__main__":
    main()
