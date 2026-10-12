"""
Unified: v8.0 Bloomberg Pro Terminal (Multi-Tab Architecture)
Enhancements & Fixes Applied:
 - Tabbed UI Layout: Terminal, Levels/Exhaustion, Scanner, News, and Chain Data.
 - Dynamic R:R derivation using ATR micro-stops (fixes asymmetric wall rejection).
 - NSE trading time accounting (252 days x 375 mins) for Expected Move & Black-Scholes.
 - Cross-tab file-based rate limiter (safeguards Dhan 1 req / 3 sec quota).
 - Asymmetric volatility skew applied to the scenario repricer.
 - Intraday session baseline tracking for true active order flow.
"""

import os
import re
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v8.0 Pro", layout="wide")

# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>
.stApp { background-color:#050505; color:#ff9800; font-family:'Courier New',monospace; }
section[data-testid="stSidebar"] { background-color:#0b0b0b; border-right:1px solid #222; }
h1,h2,h3,h4 { color:#ff9800 !important; font-family:'Courier New',monospace; letter-spacing:1px; }
[data-testid="stMetricValue"] { color:#fff; font-family:'Courier New',monospace; }
[data-testid="stMetricLabel"] { color:#ff9800; font-family:'Courier New',monospace; }
.bbg-panel { border:1px solid #333; background:#0f0f0f; padding:14px; border-radius:4px; margin-bottom:12px; }
.bbg-title { font-size:12px; color:#888; letter-spacing:2px; text-transform:uppercase; }
.bbg-big { font-size:24px; font-weight:800; }
.bbg-desc { font-size:13px; margin-top:6px; color:#ddd; }
.agent { font-size:13px; color:#ddd; margin:4px 0; }
.agent b { color:#ff9800; }
@keyframes flashGreen { 0%,100% {box-shadow:0 0 0 rgba(0,230,118,0);} 50% {box-shadow:0 0 25px rgba(0,230,118,.8);} }
@keyframes flashRed   { 0%,100% {box-shadow:0 0 0 rgba(255,23,68,0);} 50% {box-shadow:0 0 25px rgba(255,23,68,.8);} }
@keyframes flashAmber { 0%,100% {box-shadow:0 0 0 rgba(255,171,0,0);} 50% {box-shadow:0 0 20px rgba(255,171,0,.8);} }
.flash-green { animation: flashGreen 1s infinite; }
.flash-red   { animation: flashRed 1s infinite; }
.flash-amber { animation: flashAmber 1.4s infinite; }
.stTabs [data-baseweb="tab-list"] { gap: 8px; border-bottom: 1px solid #333; }
.stTabs [data-baseweb="tab"] { background-color: #111; color: #888; border-radius: 4px 4px 0 0; padding: 8px 16px; }
.stTabs [aria-selected="true"] { background-color: #1f1f1f !important; color: #ff9800 !important; border-bottom: 2px solid #ff9800; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# CONFIGURATION & CONSTANTS
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

TRADING_DAYS_YEAR = 252.0
MINUTES_PER_DAY = 375.0
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.2
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
        hint = " -> Token expired or Client ID mismatch. Verify JWT format."
    elif r.status_code == 429:
        hint = " -> Rate limit reached. Throttling requests."
    raise RuntimeError(f"HTTP {r.status_code} | {r.text[:300]}{hint}")


def enforce_global_api_throttle(min_interval=MIN_CHAIN_GAP_SEC):
    lock_file = os.path.join(tempfile.gettempdir(), "bbg_api_throttle.lock")
    now = time.time()
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                last_call = float(f.read().strip() or 0)
            elapsed = now - last_call
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        except Exception:
            pass
    try:
        with open(lock_file, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


# ============================================================
# DATA INGESTION & MARKET METRICS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])


def _n(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
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


def track_intraday_baseline(df, key):
    today_str = datetime.now(IST).strftime("%Y%m%d")
    cache_key = f"open_base_{key}_{today_str}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = {
            r["Strike"]: (r["CE_OI"], r["PE_OI"]) for _, r in df.iterrows()
        }
    base = st.session_state[cache_key]
    df["CE_Intra_Chg"] = df.apply(lambda r: r["CE_OI"] - base.get(r["Strike"], (r["CE_PrevOI"], 0))[0], axis=1)
    df["PE_Intra_Chg"] = df.apply(lambda r: r["PE_OI"] - base.get(r["Strike"], (0, r["PE_PrevOI"]))[1], axis=1)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def fetch_prev_close(scrip, seg, token, client_id):
    today = datetime.now(IST).date()
    try:
        candles = fetch_daily_candles(scrip, seg, token, client_id)
        completed = []
        for candle in candles or []:
            try:
                c_day = date.fromisoformat(str(candle.get("date", ""))[:10])
                c_val = float(candle.get("c", 0))
            except (TypeError, ValueError):
                continue
            if c_day < today and math.isfinite(c_val) and c_val > 0:
                completed.append((c_day, c_val))
        if completed:
            c_day, c_val = max(completed, key=lambda item: item[0])
            return round(c_val, 2), f"Dhan candle: {c_day.isoformat()} close"
    except Exception as e:
        candle_error = str(e)
    else:
        candle_error = "No historical candle available"

    try:
        r = requests.post(f"{BASE_URL}/marketfeed/quote", headers=auth_headers(token, client_id),
                          json={seg: [scrip]}, timeout=10)
        check(r)
        q = (r.json().get("data") or {}).get(seg, {}).get(str(scrip)) or {}
        ltp, net = float(q.get("last_price") or 0), float(q.get("net_change") or 0)
        if ltp > 0 and math.isfinite(net):
            return round(ltp - net, 2), "Dhan Quote: LTP - net_change"
    except Exception as e:
        quote_error = str(e)
    else:
        quote_error = "Quote payload missing change fields"
    return None, f"Prev close error: {candle_error} / {quote_error}"


def time_to_expiry_trading_years(expiry_str, now=None):
    now = now or datetime.now(IST)
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 1.0 / (TRADING_DAYS_YEAR * MINUTES_PER_DAY)

    today = now.date()
    cur = today
    workdays = 0
    while cur < exp_date:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            workdays += 1

    market_close = datetime.combine(today, datetime.strptime("15:30", "%H:%M").time(), tzinfo=IST)
    mins_left_today = max(0.0, (market_close - now).total_seconds() / 60.0)
    mins_left_today = min(MINUTES_PER_DAY, mins_left_today)
    total_mins = max(10.0, (workdays * MINUTES_PER_DAY) + mins_left_today)
    return total_mins / (TRADING_DAYS_YEAR * MINUTES_PER_DAY)


# ============================================================
# NEWS & SENTIMENT AGENT ENGINE
# ============================================================
POS_WORDS = ("surge", "rally", "gain", "jump", "rise", "soar", "rebound", "upbeat", "strong", "rate cut", "bullish")
NEG_WORDS = ("fall", "slump", "plunge", "crash", "selloff", "drop", "decline", "weak", "fear", "war", "tariff", "bearish")


def _strip_source(title, source):
    if source and title.endswith(" - " + source):
        return title[: -(len(source) + 3)]
    return title


@st.cache_data(ttl=300, show_spinner=False)
def fetch_news(idx_name, extra_feeds=()):
    queries = ['Nifty OR Sensex OR "Dalal Street"', "India stocks FII DII", 'RBI OR "US Fed" OR crude']
    if "BANK" in idx_name.upper():
        queries.insert(0, '"Bank Nifty" OR "banking stocks"')
    feeds = [(f"GoogleNews[{q[:15]}]",
              f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=en-IN&gl=IN&ceid=IN:en")
             for q in queries]
    feeds += [(f"Extra[{u[:25]}]", u) for u in extra_feeds]

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
                source = (src_el.text or "").strip() if src_el is not None and src_el.text else "News"
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
            status.append(f"{name}: FAIL ({str(e)[:60]})")
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
        text += f"\n- [USER MACRO NOTE] {manual_notes.strip()}"
    return text


def news_hash(items):
    if not items:
        return ""
    return hashlib.md5("|".join(i["title"] for i in items[:5]).encode()).hexdigest()


# ============================================================
# ANALYSIS ENGINES (Dual-Force, Structure, Exhaustion)
# ============================================================
def calc_max_pain(df):
    strikes = df["Strike"].values
    ce_oi, pe_oi = df["CE_OI"].values, df["PE_OI"].values
    pain = [(ce_oi * np.maximum(0, k - strikes)).sum() + (pe_oi * np.maximum(0, strikes - k)).sum()
            for k in strikes]
    return float(strikes[int(np.argmin(pain))])


def compute_bias(dce, dpe, spot, prev_close, pcr):
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

    ce_chg = near["CE_Intra_Chg"] if "CE_Intra_Chg" in near else near["CE_OI_Chg"]
    pe_chg = near["PE_Intra_Chg"] if "PE_Intra_Chg" in near else near["PE_OI_Chg"]

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
    bull_ok = base_bull and e["bull_force"] >= DOMINANCE * e["bear_force"]
    bear_ok = base_bear and e["bear_force"] >= DOMINANCE * e["bull_force"]

    if bull_ok:
        return ("BULLISH GAMMA BLAST", "BUY",
                f"Call short covering ({e['ce_exit_pct']:.1f}%) + Put writing ({e['pe_build_pct']:.1f}%). "
                f"Bull force {e['bull_force']:.1f} vs Bear {e['bear_force']:.1f}.")
    if bear_ok:
        return ("BEARISH GAMMA BLAST", "SELL",
                f"Put unwinding ({e['pe_exit_pct']:.1f}%) + Call writing ({e['ce_build_pct']:.1f}%). "
                f"Bear force {e['bear_force']:.1f} vs Bull {e['bull_force']:.1f}.")
    if base_bull or base_bear:
        return ("CONFLICTED", "WAIT",
                f"Opposing flows active (Bull {e['bull_force']:.1f} vs Bear {e['bear_force']:.1f}). Stand aside.")

    weak = MIN_FLOW_PCT / 2.0
    if e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] < weak:
        return "IV SPIKE BULL TRAP", "WAIT", "Call unwinding without Put support. High trap risk."
    if e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] < weak:
        return "IV SPIKE BEAR TRAP", "WAIT", "Put unwinding without Call support. High trap risk."
    return "BALANCED ACCUMULATION", "WAIT", "Market consolidating within range. Standby."


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

    t_expiry = max(time_to_expiry_trading_years(expiry_str), 1.0 / (TRADING_DAYS_YEAR * MINUTES_PER_DAY))
    exp_move = spot * (atm_iv / 100.0) * math.sqrt(t_expiry)

    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    gamma_strike = float(df.loc[df["total_gamma"].idxmax(), "Strike"]) if df["total_gamma"].sum() > 0 else spot
    call_wall = float(df.loc[df["CE_OI"].idxmax(), "Strike"])
    put_wall = float(df.loc[df["PE_OI"].idxmax(), "Strike"])
    max_pain = calc_max_pain(df)

    window = max(300, step * 6)
    near = df[(df["Strike"] >= spot - window) & (df["Strike"] <= spot + window)].copy()

    ce_trap, pe_trap = None, None
    if not near.empty:
        i = near["CE_OI_Chg"].idxmax()
        if near.loc[i, "CE_OI_Chg"] > 10000:
            ce_trap = float(near.loc[i, "Strike"]) + 35
        j = near["PE_OI_Chg"].idxmax()
        if near.loc[j, "PE_OI_Chg"] > 10000:
            pe_trap = float(near.loc[j, "Strike"]) - 35

    score, bias = compute_bias(dce, dpe, spot, prev_close, pcr)
    status, signal, desc = detect_gamma_blast(near)
    engine = engine_sums(near)
    struct = find_structure(df, spot, step, exp_move, call_wall, put_wall)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "pcr": pcr,
        "dce": dce, "dpe": dpe, "atm_iv": atm_iv, "exp_move": round(exp_move, 2),
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap, "score": score, "bias": bias, "status": status,
        "engine_signal": signal, "desc": desc, "engine": engine, "struct": struct,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
    }


def update_oi_velocity(m, key):
    now = datetime.now()
    snap = st.session_state.get("oi_snap")
    current = {r["Strike"]: (r["CE_OI"], r["PE_OI"]) for _, r in m["near"].iterrows()}
    zero_vel = {"ce_builds": 0.0, "ce_unwinds": 0.0, "pe_builds": 0.0, "pe_unwinds": 0.0}

    if snap is None or snap["key"] != key:
        st.session_state.oi_snap = {"key": key, "ts": now, "map": current}
        st.session_state.velocity = dict(zero_vel)
        return st.session_state.velocity

    if (now - snap["ts"]).total_seconds() < VELOCITY_WINDOW_SEC:
        return st.session_state.get("velocity", dict(zero_vel))

    vel = dict(zero_vel)
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
# EXHAUSTION & SIGNAL VALIDATION GATEWAY
# ============================================================
def daily_sigma(m):
    return m["spot"] * m["atm_iv"] / 100.0 * math.sqrt(1.0 / TRADING_DAYS_YEAR)


def hist_path(key):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key)
    return os.path.join(tempfile.gettempdir(), f"bbg_hist_{date.today().isoformat()}_{safe}.json")


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


def record_snapshot(m, key):
    if st.session_state.get("hist_key") != key:
        st.session_state["hist_key"] = key
        st.session_state["hist"] = load_hist(key)
    hist = st.session_state["hist"]
    if hist and time.time() - hist[-1]["ts"] < HIST_MIN_GAP_SEC:
        return hist
    df, spot = m["df"], m["spot"]
    sub = df[(df["Strike"] >= spot - OI_MAP_SPAN) & (df["Strike"] <= spot + OI_MAP_SPAN)]
    oi_map = {str(int(k)): [float(c), float(p)] for k, c, p in zip(sub["Strike"], sub["CE_OI"], sub["PE_OI"])}
    e = m["engine"]
    ce_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0
    pe_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    res, sup = m["struct"]["res"], m["struct"]["sup"]

    hist.append({
        "ts": time.time(), "spot": spot, "iv": m["atm_iv"], "pcr": m["pcr"], "edge": pe_pct - ce_pct,
        "max_pain": m["max_pain"], "status": m["status"],
        "res": res["level"] if res else None, "sup": sup["level"] if sup else None, "oi": oi_map
    })
    del hist[:-HIST_MAX]
    save_hist(key, hist)
    return hist


def exhaustion(hist, m, prev_close, lb_min):
    sd = daily_sigma(m)
    out = {"ready": False, "n": len(hist), "lb_min": lb_min, "sd": sd, "why": ""}
    if len(hist) < 3:
        out["why"] = f"Building session history ({len(hist)}/3 refreshes minimum)."
        return out
    now = hist[-1]
    older = hist[:-1]
    ref = min(older, key=lambda h: abs(h["ts"] - (now["ts"] - lb_min * 60)))
    age = (now["ts"] - ref["ts"]) / 60
    if age < max(4.0, 0.4 * lb_min):
        out["why"] = f"Gathering history window: {age:.0f}m collected, needs ~{max(5.0, 0.5 * lb_min):.0f}m."
        return out
    mid = min(older, key=lambda h: abs(h["ts"] - (now["ts"] - age * 30)))
    out["ready"] = True
    spot = now["spot"]

    in_win = [h for h in hist if h["ts"] >= ref["ts"]]
    lb_high, lb_low = max(h["spot"] for h in in_win), min(h["spot"] for h in in_win)
    h_high, h_low = max(h["spot"] for h in hist), min(h["spot"] for h in hist)
    pc = prev_close if prev_close and prev_close > 0 else spot
    ext = {1: max(spot - h_low, spot - pc, 0) / sd, -1: max(h_high - spot, pc - spot, 0) / sd}

    iv_peak = max(h["iv"] for h in in_win)
    iv_off_peak = (1 - now["iv"] / iv_peak) * 100 if iv_peak > 0 else 0.0
    pcr_d = now["pcr"] - ref["pcr"]
    edge_d = now["edge"] - ref["edge"]

    def side(sgn):
        sig = []
        name = "call" if sgn > 0 else "put"
        level = now["res"] if sgn > 0 else now["sup"]
        gap = abs(level - spot) / sd if level else 9.0
        sig.append(("Wall absorption", 1.0 if gap <= 0.35 else 0.0, f"{gap:.2f}σ to wall"))

        extreme = (lb_high - spot if sgn > 0 else spot - lb_low) / sd
        sig.append(("IV rollover at extreme", 1.0 if extreme <= 0.3 and iv_off_peak >= 2.5 else 0.0,
                    f"IV -{iv_off_peak:.1f}% off peak"))

        ed = -sgn * edge_d
        sig.append(("Flow edge flip", 1.0 if ed >= 2.5 else 0.5 if ed >= 1.0 else 0.0, f"Flow edge Δ {edge_d:+.1f}"))

        pd_ = -sgn * pcr_d
        sig.append(("PCR turning", 1.0 if pd_ >= 0.03 else 0.5 if pd_ >= 0.015 else 0.0, f"PCR Δ {pcr_d:+.3f}"))

        first = sgn * (mid["spot"] - ref["spot"]) / sd
        second = sgn * (spot - mid["spot"]) / sd
        sig.append(("Momentum stall", 1.0 if (first >= 0.15 and second <= 0.0) else 0.0,
                    f"Leg1 {first:+.2f}σ, Leg2 {second:+.2f}σ"))

        score = sum(x[1] for x in sig) / len(sig) * 100
        ctx = ext[sgn]
        turned = ((lb_high - spot) if sgn > 0 else (spot - lb_low)) / sd >= 0.1
        if ctx < EXH_CONTEXT_SIGMA:
            status, flag = f"NO EXTENSION ({ctx:.2f}σ)", ""
        elif score >= EXH_ALERT and turned:
            status, flag = "CONFIRMED EXHAUSTION", "CONFIRMED"
        elif score >= EXH_ALERT:
            status, flag = "WATCH (EXHAUSTION BUILDING)", "WATCH"
        elif score >= EXH_WATCH:
            status, flag = "EARLY SIGNS", "EARLY"
        else:
            status, flag = "TREND INTACT", ""
        return {"score": round(score), "signals": sig, "status": status, "flag": flag}

    out["buyer"], out["seller"] = side(1), side(-1)
    out.update({"age": age, "lb_high": lb_high, "lb_low": lb_low, "spot_chg_sigma": (spot - ref["spot"]) / sd})
    return out


def chain_checks(m):
    e = m["engine"]
    checks = []
    st_ = m["status"]
    checks.append({"name": "Dual-Force Engine", "v": 1 if "BULLISH" in st_ else -1 if "BEARISH" in st_ else 0, "detail": st_})
    ce_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0
    pe_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    gap = pe_pct - ce_pct
    checks.append({"name": "Net Flow Gap", "v": 1 if gap >= FLOW_EDGE_PCT else -1 if gap <= -FLOW_EDGE_PCT else 0,
                   "detail": f"PE {pe_pct:+.1f}% vs CE {ce_pct:+.1f}%"})
    checks.append({"name": "Whole PCR", "v": 1 if m["pcr"] > 1.2 else -1 if m["pcr"] < 0.8 else 0, "detail": f"{m['pcr']:.2f}"})
    checks.append({"name": "Bias Model", "v": 1 if m["score"] > 55 else -1 if m["score"] < 45 else 0, "detail": f"{m['score']}/100"})
    return checks


def build_signal(m, ai, atr_val=None):
    ai_sig = str(ai.get("signal", "WAIT")).upper()
    if ai_sig not in ("BUY", "SELL", "WAIT"):
        ai_sig = "WAIT"
    try:
        conf = max(0, min(100, int(float(ai.get("confidence", 0)))))
    except Exception:
        conf = 0

    direction = {"BUY": 1, "SELL": -1}.get(ai_sig, 0)
    checks = chain_checks(m)
    lean = sum(c["v"] for c in checks)
    net = lean * direction
    entry = m["spot"]
    step = m["step"]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    atr = atr_val or (step * 0.85)

    notes = []
    sig, target, sl, rr = ai_sig, None, None, None

    if sig != "WAIT" and net < MIN_CONFIRM:
        notes.append(f"{sig} rejected: chain agreement score ({net:+d}) is below +{MIN_CONFIRM}.")
        sig = "WAIT"

    exh = m.get("exh") or {}
    if sig != "WAIT" and exh.get("ready"):
        mine = exh["buyer"] if sig == "BUY" else exh["seller"]
        if mine["flag"] == "CONFIRMED":
            notes.append(f"{sig} rejected: {mine['status']} detected.")
            sig = "WAIT"

    if sig != "WAIT":
        if sig == "BUY":
            target = res["level"] if res and res["level"] > entry else (entry + 2 * step)
            if (target - entry) < (step * 0.5):
                target += step
            sl = entry - max(step * 0.75, 0.65 * atr)
            if sup and (entry - sup["level"]) < (1.5 * step):
                sl = min(sl, sup["level"] - 15)
        else:
            target = sup["level"] if sup and sup["level"] < entry else (entry - 2 * step)
            if (entry - target) < (step * 0.5):
                target -= step
            sl = entry + max(step * 0.75, 0.65 * atr)
            if res and (res["level"] - entry) < (1.5 * step):
                sl = max(sl, res["level"] + 15)

        risk = abs(entry - sl)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else 0
        if rr < MIN_RR:
            notes.append(f"{sig} rejected: R:R 1:{rr:.2f} is below minimum 1:{MIN_RR:.1f}.")
            sig, target, sl, rr = "WAIT", None, None, None

    return {"signal": sig, "ai_signal": ai_sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": " ".join(notes), "checks": checks, "net": net, "lean": lean,
            "direction": direction}


# ============================================================
# DAY MOVEMENT LEVELS & PIVOTS
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
    r = requests.post(f"{BASE_URL}/charts/historical", headers=auth_headers(token, client_id),
                      json={"securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                            "expiryCode": 0, "fromDate": (today - timedelta(days=35)).isoformat(),
                            "toDate": (today + timedelta(days=1)).isoformat()}, timeout=12)
    check(r)
    d = r.json()
    n = len(d.get("close", []))
    if n == 0:
        raise RuntimeError("Empty candle response")
    return [{"date": _candle_date(d["timestamp"][i]).isoformat(), "o": float(d["open"][i]),
             "h": float(d["high"][i]), "l": float(d["low"][i]), "c": float(d["close"][i])}
            for i in range(n)]


def compute_day_levels(candles, spot, sd, m, prev_close_fallback):
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()
    after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 35
    done = [c for c in candles if c["date"] < today or (c["date"] == today and after_close)]
    live = next((c for c in candles if c["date"] == today and not after_close), None)
    ref = done[-1] if done else None
    levels = []

    c, h, l = (ref["c"], ref["h"], ref["l"]) if ref else (prev_close_fallback, None, None)
    for k in (2, 1):
        levels.append((f"Close +{k}σ", c + k * sd, "σ"))
    levels.append(("Close (ref)", c, "ref"))
    for k in (1, 2):
        levels.append((f"Close -{k}σ", c - k * sd, "σ"))

    atr = None
    if h is not None:
        P = (h + l + c) / 3.0
        for nm, v in (("R3", h + 2 * (P - l)), ("R2", P + (h - l)), ("R1", 2 * P - l), ("Pivot", P),
                      ("S1", 2 * P - h), ("S2", P - (h - l)), ("S3", l - 2 * (h - P))):
            levels.append((nm, v, "pivot"))
        trs = []
        for i in range(1, len(done)):
            pc = done[i - 1]["c"]
            trs.append(max(done[i]["h"] - done[i]["l"], abs(done[i]["h"] - pc), abs(done[i]["l"] - pc)))
        if len(trs) >= 5:
            atr = sum(trs[-14:]) / len(trs[-14:])
            levels.append(("Close +ATR", c + atr, "atr"))
            levels.append(("Close -ATR", c - atr, "atr"))

    if live:
        levels.append(("Today Open", live["o"], "today"))
        levels.append(("Today High", live["h"], "today"))
        levels.append(("Today Low", live["l"], "today"))

    oi_refs = {"Call Wall": m["call_wall"], "Put Wall": m["put_wall"], "Max Pain": m["max_pain"], "Gamma Strike": m["gamma_strike"]}
    tol = max(m["step"] * 0.5, 0.15 * sd)
    rows = []
    for name, price, kind in levels:
        hits = [f"{n} {v:,.0f}" for n, v in oi_refs.items() if abs(v - price) <= tol]
        rows.append({"Level": name, "Price": price, "Dist pts": price - spot,
                     "Dist σ": (price - spot) / sd if sd else 0.0,
                     "OI Confluence": ("★ " + ", ".join(hits)) if hits else ""})
    rows.append({"Level": "◄ SPOT", "Price": spot, "Dist pts": 0.0, "Dist σ": 0.0, "OI Confluence": ""})
    tbl = pd.DataFrame(rows).sort_values("Price", ascending=False).reset_index(drop=True)
    return {"table": tbl, "ref_close": c, "atr": atr, "sd": sd,
            "band1": (c - sd, c + sd), "band2": (c - 2 * sd, c + 2 * sd),
            "moved_sigma": (spot - c) / sd if sd else 0.0}


# ============================================================
# PREMIUM POTENTIAL SCANNER (With Vol Skew Shocks)
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
    return S * _ncdf(d1) - K * disc * _ncdf(d2) if is_call else K * disc * _ncdf(-d2) - S * _ncdf(-d1)


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


def premium_scanner(m, horizon_days=0.25, iv_shock=15.0):
    df, S0, step = m["df"], m["spot"], m["step"]
    atm_iv = m["atm_iv"]
    sd = daily_sigma(m)
    min_t = 1.0 / (TRADING_DAYS_YEAR * MINUTES_PER_DAY)
    T0 = max(time_to_expiry_trading_years(m.get("expiry", "")), min_t)
    horizon_t = (horizon_days * MINUTES_PER_DAY) / (TRADING_DAYS_YEAR * MINUTES_PER_DAY)
    T1 = max(T0 - horizon_t, min_t)
    span = max(2.5 * sd, 8 * step)
    cand = df[(df["Strike"] >= S0 - span) & (df["Strike"] <= S0 + span)]
    res, sup = m["struct"]["res"], m["struct"]["sup"]

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

            # Skew-adjusted IV shocks
            if is_call:
                iv_fav = iv0 * (1.0 - (iv_shock * 0.25) / 100.0)
                iv_wrong = iv0 * (1.0 + (iv_shock * 0.4) / 100.0)
            else:
                iv_fav = iv0 * (1.0 + (iv_shock * 0.8) / 100.0)
                iv_wrong = iv0 * (1.0 - (iv_shock * 0.5) / 100.0)

            mult = lambda S1, iv1=iv0: reprice(ltp, S0, S1, K, T0, T1, iv0, iv1, is_call)
            Sfav = lambda k: S0 + sign * k * sd
            m05, m1, m15 = mult(Sfav(0.5)), mult(Sfav(1.0), iv_fav), mult(Sfav(1.5), iv_fav)
            if None in (m05, m1, m15):
                continue
            wall = (res if is_call else sup)
            m_wall = mult(wall["level"]) if wall else None
            m_crush = mult(Sfav(1.0), iv0 * (1.0 - iv_shock / 100.0))
            m_spike = mult(Sfav(1.0), iv0 * (1.0 + iv_shock / 100.0))
            m_wrong = mult(S0 - sign * 0.5 * sd, iv_wrong)
            x = lambda v: (v / ltp) if v is not None else None

            # Spot required to 2x premium
            need2 = None
            lo, hi = 0.0, 4.0
            if (mult(Sfav(hi)) or 0) >= 2 * ltp:
                for _ in range(30):
                    mid = (lo + hi) / 2
                    if (mult(Sfav(mid)) or 0) >= 2 * ltp:
                        hi = mid
                    else:
                        lo = mid
                need2 = hi

            bid, ask = float(r.get(f"{side}_Bid", 0) or 0), float(r.get(f"{side}_Ask", 0) or 0)
            spread = ((ask - bid) / ((ask + bid) / 2) * 100) if (bid > 0 and ask >= bid) else float("nan")
            d_now = abs(float(r.get(f"{side}_Delta", 0) or 0)) or abs(bs_delta(S0, K, T0, iv0, is_call))
            d_1s = abs(bs_delta(Sfav(1.0), K, T1, iv0, is_call))
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
    meta = {"sd": sd, "T0d": T0 * TRADING_DAYS_YEAR, "T1d": T1 * TRADING_DAYS_YEAR, "crush": iv_shock}
    if out.empty:
        return out, meta

    fuel = out["OIchg"].clip(lower=0)
    out["fuel_pct"] = fuel / fuel.max() if fuel.max() > 0 else 0.0
    reward = 0.5 * (out["x1σ"] - 1) + 0.5 * (out["x1σ crush"] - 1)
    loss = (1 - out["xWrong"]).clip(lower=0, upper=1)
    liq = out["Spread%"].apply(lambda v: 0.0 if pd.isna(v) else 1.0 if v <= 3 else 0.7 if v <= 6 else 0.3)
    prob = (2 * out["Delta"].clip(lower=0, upper=0.5)).clip(upper=1.0) ** 0.5
    activity = 0.5 + 0.5 * out["fuel_pct"]
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
    d["Spread%"] = d["Spread%"].map(lambda v: "-" if pd.isna(v) else f"{v:.1f}%")
    d["Vol"] = d["Vol"].map(lambda v: f"{v:,.0f}")
    d["OIchg"] = d["OIchg"].map(lambda v: f"{v:+,.0f}")
    d["Needs2x(σ)"] = d["Needs2x(σ)"].map(lambda v: ">4" if v is None or pd.isna(v) else f"{v:.2f}")
    d["Score"] = d["Score"].map(lambda v: f"{v:.2f}")
    cols = ["Side", "Strike", "Money", "LTP", "Δ", "Spread%", "Vol", "OIchg", "x0.5σ", "x1σ", "x1.5σ",
            "xWall", "x1σ crush", "x1σ spike", "xWrong", "Needs2x(σ)", "Score"]
    return d[cols]


def esc(x):
    return html.escape(str(x if x is not None else "-"))


# ============================================================
# GROQ MULTI-AGENT COUNCIL
# ============================================================
def run_council(m, vel, news, api_key, model):
    if Groq is None:
        return {"error": "Groq client library is not installed."}
    if not api_key:
        return {"error": "Missing Groq API key in sidebar."}

    eg = m["engine"]
    sres, ssup = m["struct"]["res"], m["struct"]["sup"]
    struct_txt = (f"Res {sres['level']:.0f} (CE {sres['oi']:,.0f})" if sres else "No nearby Res") + " | " + \
                 (f"Sup {ssup['level']:.0f} (PE {ssup['oi']:,.0f})" if ssup else "No nearby Sup")
    ex = m.get("exh") or {}
    exh_txt = f"Buyer {ex.get('buyer',{}).get('score',0)}/100, Seller {ex.get('seller',{}).get('score',0)}/100" if ex.get("ready") else "Exhaustion building"

    strike_log = [f"K={int(r['Strike'])}: CE_OI={int(r['CE_OI']):,} (chg {int(r['CE_OI_Chg']):+,}), PE_OI={int(r['PE_OI']):,} (chg {int(r['PE_OI_Chg']):+,})"
                  for _, r in m["near"].iterrows()]
    chain_summary = "\n".join(strike_log[:20])

    prompt = f"""You are a council of 5 derivatives traders. Output valid JSON only.

MARKET STATE:
Spot: {m['spot']:.2f} | PCR: {m['pcr']:.3f} | Bias: {m['bias']} ({m['score']}/100) | Status: {m['status']}
Flows: Bull Force={eg['bull_force']:.1f}, Bear Force={eg['bear_force']:.1f}, Ratio={eg['force_ratio']:.2f}
Structure: {struct_txt}
Exhaustion: {exh_txt}
Expected Move: ±{m['exp_move']} pts | ATM IV: {m['atm_iv']:.1f}%
Walls: Call {m['call_wall']:.0f} | Put {m['put_wall']:.0f} | Max Pain {m['max_pain']:.0f}
Velocity (3m): CE Unwinds={vel['ce_unwinds']:,.0f}, PE Unwinds={vel['pe_unwinds']:,.0f}
Key Strikes:
{chain_summary}
Headlines:
{news}

AGENTS:
1. Price Action: Spot vs structural pivot walls.
2. Order Flow: Near-spot writing vs unwinds.
3. Volatility: IV skew & gamma profile.
4. Sentiment: Tone derived from headlines.
5. Risk Officer: Final trade decision. Must output WAIT if opposing forces conflict.

JSON Response Format:
{{"price_action_agent":"1 line","order_flow_agent":"1 line","volatility_agent":"1 line","news_agent":"1 line","news_score":0,"final_approval":"summary","signal":"BUY or SELL or WAIT","confidence":0}}"""

    client = Groq(api_key=api_key)
    for mdl in [model] + [x for x in GROQ_MODELS if x != model]:
        try:
            resp = client.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2200,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"}
            )
            txt = (resp.choices[0].message.content or "").strip().replace("```json", "").replace("```", "").strip()
            out = json.loads(txt)
            out["_model"] = mdl
            return out
        except Exception as e:
            if "model_not_found" in str(e) or "404" in str(e):
                continue
            return {"error": f"Groq query failed: {e}"}
    return {"error": "Groq models unavailable."}


# ============================================================
# SIDEBAR CONFIGURATION
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL CONFIG")
    dhan_token = clean(st.text_input("DHAN ACCESS TOKEN", type="password", value=DEFAULT_DHAN_TOKEN))
    client_id = clean(st.text_input("DHAN CLIENT ID", value=DEFAULT_CLIENT_ID))
    groq_key = st.text_input("GROQ API KEY", type="password", value=DEFAULT_GROQ_KEY).strip()
    groq_model = st.selectbox("GROQ MODEL", GROQ_MODELS, index=0)

    if not dhan_token or not client_id:
        st.error("Dhan Access Token & Client ID are mandatory.")
        st.stop()

    idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]

    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token, client_id)
    except Exception as e:
        expiries = []
        st.error(f"Failed to fetch expiries: {e}")
    if not expiries:
        st.warning("No active expiries loaded.")
        st.stop()

    expiry = st.selectbox("EXPIRY DATE", expiries)
    auto_prev = st.toggle("AUTO PREV CLOSE", value=True)
    auto_val, auto_src = (None, "")
    if auto_prev:
        auto_val, auto_src = fetch_prev_close(info["scrip"], info["seg"], dhan_token, client_id)
    if auto_prev and auto_val:
        prev_close = float(auto_val)
        st.caption(f"PREV CLOSE: {prev_close:,.2f} ({auto_src})")
    else:
        prev_close = st.number_input("PREV CLOSE", min_value=0.0, value=0.0, step=float(info["step"]))

    live_news = st.toggle("LIVE NEWS FEED", value=True)
    user_news = st.text_area("MACRO NOTES", placeholder="Optional macro/event notes", height=70)
    horizon_label = st.selectbox("SCANNER HORIZON", ["Intraday (0.25d)", "1 Day", "2 Days"], index=0)
    horizon_days = {"Intraday (0.25d)": 0.25, "1 Day": 1.0, "2 Days": 2.0}[horizon_label]
    iv_shock = st.slider("IV SHOCK (%)", 5, 40, 15, step=5)
    exh_lookback = st.slider("EXHAUSTION WINDOW (MIN)", 6, 45, 15, step=3)
    force_run = st.button("FORCE AI RUN")

    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="bbg_refresh")


# ============================================================
# MAIN PIPELINE EXECUTION
# ============================================================
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
except Exception as e:
    st.error(f"Option Chain retrieval failed: {e}")
    st.stop()

if df.empty or spot <= 0:
    st.warning("Option chain empty or spot price not detected.")
    st.stop()

df = track_intraday_baseline(df, f"{idx_name}_{expiry}")
m = compute_metrics(df, spot, prev_close, expiry, info["step"])
m["expiry"] = expiry
vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
hist = record_snapshot(m, f"{idx_name}|{expiry}")
m["exh"] = exhaustion(hist, m, prev_close, exh_lookback)

news_items, news_status = ([], ["NEWS: Disabled"])
if live_news:
    try:
        raw_items, news_status = fetch_news(idx_name, NEWS_EXTRA_FEEDS)
        news_items = recent_news(raw_items)
    except Exception as e:
        news_status = [f"News fetch error: {e}"]
news_text = news_for_ai(news_items, user_news)
nhash = news_hash(news_items)

# AI Decision Execution
last_ai_time = st.session_state.get("last_ai_time")
ai_age_sec = (datetime.now() - last_ai_time).total_seconds() if last_ai_time else None
trigger_ai = force_run or (st.session_state.get("last_ai_verdict") is None) or (ai_age_sec and ai_age_sec > 600)

if trigger_ai:
    ai = run_council(m, vel, news_text, groq_key, groq_model)
    if "error" not in ai:
        st.session_state.last_ai_verdict = ai
        st.session_state.last_ai_time = datetime.now()
else:
    ai = st.session_state.get("last_ai_verdict", {"signal": "WAIT", "confidence": 0})

# Daily Technical Levels & Confluence
try:
    candles = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id)
except Exception:
    candles = []
dl = compute_day_levels(candles, m["spot"], daily_sigma(m), m, prev_close)

# Build Risk-Adjusted Execution Signal
sig = build_signal(m, ai, atr_val=dl.get("atr"))


# ============================================================
# MULTI-TAB INTERFACE
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f} " + (f"({spot - prev_close:+,.2f})" if prev_close else ""))

tab_term, tab_levels, tab_scanner, tab_news, tab_chain = st.tabs([
    "⚡ Terminal & Signals",
    "📐 Levels & Exhaustion",
    "🎯 Options Scanner",
    "📰 News & Sentiment",
    "📊 Option Chain & Diagnostics"
])

# ---------------- TAB 1: TERMINAL & SIGNALS ----------------
with tab_term:
    status = m["status"]
    color, flash = ("#00e676", "flash-green") if "BULLISH" in status else \
                   (("#ff1744", "flash-red") if "BEARISH" in status else \
                   (("#ffab00", "flash-amber") if ("TRAP" in status or "CONFLICTED" in status) else ("#90a4ae", "")))

    st.markdown(f"""
    <div class="bbg-panel {flash}" style="border:2px solid {color};">
      <div class="bbg-title">Dual-Force Engine</div>
      <div class="bbg-big" style="color:{color};">{esc(status)} &nbsp;|&nbsp; {esc(m['engine_signal'])}</div>
      <div class="bbg-desc">{esc(m['desc'])}</div>
    </div>""", unsafe_allow_html=True)

    # Risk-Validated Execution Panel
    scolor = {"BUY": "#00e676", "SELL": "#ff1744"}.get(sig["signal"], "#90a4ae")
    lv_text = f"Entry: <b>{sig['entry']:,.0f}</b> | Target: <b>{sig['target']:,.0f}</b> | Stop-Loss: <b>{sig['sl']:,.0f}</b> | R:R: <b>1:{sig['rr']:.2f}</b>" if sig["target"] else "Setup standby"
    st.markdown(f"""
    <div class="bbg-panel" style="border:2px solid {scolor};">
      <div class="bbg-title">Validated Trade Execution</div>
      <div class="bbg-big" style="color:{scolor};">{sig['signal']} (Confidence {sig['confidence']}%)</div>
      <div class="bbg-desc">{lv_text}</div>
      <div class="bbg-desc" style="color:#ffab00;">{esc(sig['note'])}</div>
    </div>""", unsafe_allow_html=True)

    # Core Stats Metrics
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
    k2.metric("PCR", f"{m['pcr']:.2f}")
    k3.metric("EXPECTED MOVE", f"±{m['exp_move']:,.0f} pts", f"IV {m['atm_iv']:.1f}%")
    k4.metric("MAX PAIN", f"{m['max_pain']:,.0f}")

    # Dual-Force Flow
    es = m["engine"]
    st.caption("DUAL-FORCE ENGINE BREAKDOWN (Near-spot strikes)")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("CE EXITS", f"{es['ce_exit_pct']:.1f}%", f"{es['ce_exits']:+,.0f}")
    d2.metric("PE BUILDS", f"{es['pe_build_pct']:.1f}%", f"{es['pe_build']:+,.0f}")
    d3.metric("PE EXITS", f"{es['pe_exit_pct']:.1f}%", f"{es['pe_exits']:+,.0f}")
    d4.metric("CE BUILDS", f"{es['ce_build_pct']:.1f}%", f"{es['ce_build']:+,.0f}")

    f1, f2, f3 = st.columns(3)
    f1.metric("BULL FORCE", f"{es['bull_force']:.1f}")
    f2.metric("BEAR FORCE", f"{es['bear_force']:.1f}")
    f3.metric("FORCE RATIO", f"{min(es['force_ratio'], 99):.2f}x", f"Threshold: {DOMINANCE}x")

    st.markdown("#### 3-MIN OI VELOCITY")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("CE BUILDS", f"{vel['ce_builds']:,.0f}")
    v2.metric("CE UNWINDS", f"{vel['ce_unwinds']:,.0f}")
    v3.metric("PE BUILDS", f"{vel['pe_builds']:,.0f}")
    v4.metric("PE UNWINDS", f"{vel['pe_unwinds']:,.0f}")

    # AI Council Agent Perspectives
    if "error" not in ai:
        st.markdown("#### AI COUNCIL SYNTHESIS")
        st.markdown(f"""
        <div class="bbg-panel">
          <div class="agent"><b>Price Action:</b> {esc(ai.get('price_action_agent'))}</div>
          <div class="agent"><b>Order Flow:</b> {esc(ai.get('order_flow_agent'))}</div>
          <div class="agent"><b>Volatility:</b> {esc(ai.get('volatility_agent'))}</div>
          <div class="agent"><b>News & Macro:</b> {esc(ai.get('news_agent'))}</div>
          <div class="agent"><b>Risk Approval:</b> {esc(ai.get('final_approval'))}</div>
        </div>""", unsafe_allow_html=True)

# ---------------- TAB 2: LEVELS & EXHAUSTION ----------------
with tab_levels:
    st.markdown("#### 🧭 TREND EXHAUSTION MONITOR")
    ex = m["exh"]
    if not ex["ready"]:
        st.info(ex["why"])
    else:
        st.caption(f"Lookback Window: {ex['age']:.0f}m ({ex['n']} snapshots) | Spot Δ {ex['spot_chg_sigma']:+.2f}σ")
        ecol1, ecol2 = st.columns(2)
        for col, key, title in ((ecol1, "buyer", "BUYER EXHAUSTION (Uptrend Fading)"),
                                (ecol2, "seller", "SELLER EXHAUSTION (Downtrend Fading)")):
            side_ = ex[key]
            scol = "#ff1744" if side_["flag"] == "CONFIRMED" else "#ffab00" if side_["flag"] in ("WATCH", "EARLY") else "#90a4ae"
            rows = "".join(f'<div class="agent">{"🔴" if v >= 1 else "🟠" if v >= 0.5 else "⚪"} <b>{esc(n)}:</b> {esc(d)}</div>'
                           for n, v, d in side_["signals"])
            col.markdown(f"""<div class="bbg-panel" style="border:2px solid {scol};">
              <div class="bbg-title">{title}</div>
              <div class="bbg-big" style="color:{scol};">{side_['score']}/100</div>
              <div class="bbg-desc" style="color:{scol};">{esc(side_['status'])}</div>
              {rows}</div>""", unsafe_allow_html=True)

    st.markdown("#### 📐 DAY MOVEMENT LEVELS & CONFLUENCE")
    lc1, lc2, lc3 = st.columns(3)
    lc1.metric("1σ RANGE (68%)", f"{dl['band1'][0]:,.0f} – {dl['band1'][1]:,.0f}")
    lc2.metric("2σ RANGE (95%)", f"{dl['band2'][0]:,.0f} – {dl['band2'][1]:,.0f}")
    lc3.metric("ATR(14)", f"{dl['atr']:,.1f} pts" if dl["atr"] else "—")

    show = dl["table"].copy()
    show["Price"] = show["Price"].map(lambda v: f"{v:,.0f}")
    show["Dist pts"] = show["Dist pts"].map(lambda v: f"{v:+,.0f}")
    show["Dist σ"] = show["Dist σ"].map(lambda v: f"{v:+.2f}")
    st.dataframe(show, use_container_width=True, hide_index=True)

# ---------------- TAB 3: OPTIONS SCANNER ----------------
with tab_scanner:
    st.markdown("#### 🎯 PREMIUM POTENTIAL SCANNER")
    scan, smeta = premium_scanner(m, horizon_days, iv_shock)
    if scan.empty:
        st.info("No contracts passed minimum liquidity requirements.")
    else:
        lean_now = sum(c["v"] for c in chain_checks(m))
        fav = "CE" if lean_now > 0 else "PE" if lean_now < 0 else None
        st.caption(f"Scenario Horizon: {horizon_label} | Vol Skew Shock: ±{smeta['crush']:.0f}%")
        top = scan[scan["Side"] == fav].head(6) if fav else scan.head(6)
        st.dataframe(fmt_scanner(top), use_container_width=True, hide_index=True)

        b = top.iloc[0]
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Top Option Walk-Through: {b['Strike']:,.0f} {b['Side']} (₹{b['LTP']:,.1f})</div>
          <div class="agent">At +1σ Move ({smeta['sd']:,.0f} pts): <b>{b['x1σ']:.2f}x</b> (₹{b['LTP']*b['x1σ']:,.1f}) | Delta expands {b['Delta']:.2f} → {b['Delta@1σ']:.2f}.</div>
          <div class="agent">With IV Crush (-{smeta['crush']:.0f}%): <b>{b['x1σ crush']:.2f}x</b> | With IV Spike (+{smeta['crush']:.0f}%): <b>{b['x1σ spike']:.2f}x</b>.</div>
          <div class="agent">Adverse -0.5σ Retrace: <b>{b['xWrong']:.2f}x</b> | Distance Required to Double: <b>{b['Needs2x(σ)']}σ</b>.</div>
        </div>""", unsafe_allow_html=True)

# ---------------- TAB 4: NEWS & SENTIMENT ----------------
with tab_news:
    st.markdown("#### 📰 REAL-TIME NEWS & SENTIMENT")
    if news_items:
        tone = headline_tone(news_items)
        tcol = "#00e676" if tone > 15 else "#ff1744" if tone < -15 else "#90a4ae"
        st.metric("HEADLINE SENTIMENT TONE", f"{tone:+d}", delta_color="off")
        for i in news_items:
            link = i["link"] if i["link"].startswith(("http://", "https://")) else ""
            st.markdown(f"""<div class="agent">
              <b>{esc(age_label(i['age_min']))}</b> · {esc(i['source'])} — 
              <a href="{html.escape(link, quote=True)}" target="_blank" style="color:#ddd;">{esc(i['title'])}</a>
            </div>""", unsafe_allow_html=True)
    else:
        st.warning("No recent headlines loaded.")

# ---------------- TAB 5: OPTION CHAIN & DIAGNOSTICS ----------------
with tab_chain:
    st.markdown("#### 📊 FULL OPTION CHAIN")
    st.dataframe(m["df"], use_container_width=True)

    with st.expander("🔧 DHAN CONNECTION DIAGNOSTICS"):
        st.write(f"Access Token: `{dhan_token[:4]}...{dhan_token[-4:]}`")
        st.write(f"Client ID: `{client_id}`")
        if st.button("Run Token Validation Check (/v2/profile)"):
            try:
                pr = requests.get(PROFILE_URL, headers=auth_headers(dhan_token, client_id), timeout=10)
                st.code(f"HTTP Status: {pr.status_code}\nPayload: {pr.text[:400]}")
            except Exception as e:
                st.error(f"Diagnostics request failed: {e}")
