"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v12.1 UNIFIED MASTER EDITION
Analysis terminal combining:
 - Dual-Force Engine & Jobber Microstructure (net-capitulation aware, scale-invariant)
 - Dealer Positioning Engine: Signed Net GEX, interpolated Zero-Gamma Flip, Net Vanna & Charm
 - Put-Call Parity Synthetic Future (F_syn), Live Basis, 25-Delta Risk Reversal (RR25) & Butterfly (BF25)
 - Complete Signal Scorecard (horizons 15m/30m/60m/Close, MFE/MAE, check-edge stats, JSON backup/merge)
 - Comprehensive Data Health Monitor (JWT token expiry, feed freshness, quotes, candles, AI)
 - Full Telegram & Webhook Alerting Engine with Cooldowns and Scrubbed Safety
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v12.1 Unified", layout="wide", initial_sidebar_state="expanded")

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
TIME_BASIS = {"mode": "calendar"}
SKEW_COEF = 0.5
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0
MIN_RR = 1.0
MIN_FLOW_PCT = 2.0
NOISE_PCT = 0.5
DOMINANCE = 1.5
MIN_CONFIRM = 2
SL_BUFFER = 35
FLOW_EDGE_PCT = 2.0
IST = timezone(timedelta(hours=5, minutes=30))

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
# DATA FETCHERS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])


def enforce_global_api_throttle(min_interval=None):
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
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_chain_raw(scrip, seg, expiry, token, client_id):
    enforce_global_api_throttle()
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


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    return _fetch_chain_raw(scrip, seg, expiry, token, client_id)


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
    out = df.copy()
    t_safe = max(t_years, 1e-5)
    sqrt_t = math.sqrt(t_safe)
    disc_inv = math.exp(RISK_FREE * t_safe)

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
        "df": out, "syn_fut": round(syn_fut, 2), "basis": round(basis, 2),
        "rr_25": round(rr_25, 2), "bf_25": round(bf_25, 2),
        "iv_25c": round(iv_25c, 2), "iv_25p": round(iv_25p, 2),
        "net_gex": net_gex_near, "net_vex": float(near_gex["Net_VEX"].sum()) if not near_gex.empty else 0.0,
        "net_cex": float(near_gex["Net_CEX"].sum()) if not near_gex.empty else 0.0,
        "gamma_flip": gamma_flip, "gex_regime": gex_regime,
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

    weak = MIN_FLOW_PCT / 2.
