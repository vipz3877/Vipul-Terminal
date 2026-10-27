"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.5 (REVIEWED EDITION)
Built on v13.4. Changes in this version:
 - AI Council & News tab now renders headlines and each agent's verdict as readable text (no raw JSON dump)
 - DATA GATE: AI runs, snapshots and signals are paused when the market is closed / holiday / IV is unusable
 - Risk Officer veto (final_approval) and minimum AI confidence are now real gates
 - Agreement score is counted per FAMILY (flow / positioning / volatility) so correlated checks do not stack
 - New price-confirmed writer check (OI change + premium change), not just OI direction
 - Scale-invariant thresholds (% of near-spot OI, sigma units) replace hard-coded contract counts
 - Near-spot window scales with index level (no more fixed 300 points)
 - Daily sigma always uses trading-day basis (252)
 - Intraday 1-min candle high/low used for target/stop touches and MFE/MAE
 - Scorecard multi-session merge now merges MFE/MAE/horizon marks instead of last-writer-wins
 - Kelly sizing uses a Wilson lower bound on win rate, counts TIMEOUT as non-win, MIN_RR raised to 1.5
 - Exhaustion veto is optional (off by default) until it is validated on your own scorecard
 - Headline tone needs >= 4 keyword hits before it shows a number
 - Telegram: validated signals + outcomes by default (rejected optional), correct resolve time, alert state pruned
 - NET GEX shown in Rs crore, cross-index view cached 5 min
 - NSE holiday list via secret NSE_HOLIDAYS (comma separated YYYY-MM-DD)
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v13.5", layout="wide", initial_sidebar_state="expanded")

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
# CONFIG & GLOBAL INITIALIZATIONS
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

# NSE trading holidays are NOT hard-coded (they change every year). Put them in the secret/env
# NSE_HOLIDAYS as comma-separated ISO dates, e.g. "2026-11-08,2026-11-09,2026-12-25".
NSE_HOLIDAYS = {x.strip() for x in get_secret("NSE_HOLIDAYS", "").split(",") if x.strip()}

GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

BASE_URL = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{BASE_URL}/optionchain"
EXPIRY_URL = f"{BASE_URL}/optionchain/expirylist"
PROFILE_URL = f"{BASE_URL}/profile"

YEAR_DAYS = 365


class _SessionFlag:
    """Per-session stand-in for a module-level dict (module globals are shared by every user)."""

    def __init__(self, name, default):
        self._name, self._default = name, default

    def _store(self):
        return st.session_state.setdefault("_bbg_flags", {})

    def __getitem__(self, key):
        return self._store().get(self._name, self._default)

    def __setitem__(self, key, value):
        self._store()[self._name] = value


TIME_BASIS = _SessionFlag("time_basis", "calendar")

# Tunable parameters live in st.session_state (widget keys "p_<name>"), so they are per-session
# and take effect immediately. Reading them through P() avoids stale module-level copies.
PARAM_DEFAULTS = {
    "noise_pct": 0.5, "dominance": 1.5, "min_confirm": 2,
    "flow_conc_on": False, "flow_conc_min": 50,
    "max_risk": 2.0, "capital": 100000, "move_alert_pct": 2.0,
    "min_conf": 50, "exh_veto": False, "alert_rejected": False,
}


def P(name):
    return st.session_state.get("p_" + name, PARAM_DEFAULTS[name])


SKEW_COEF = 0.5
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0
MIN_RR = 1.5
MIN_FLOW_PCT = 2.0
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
NEWS_MIN_TONE_HITS = 4
NEWS_EXTRA_FEEDS = tuple(u.strip() for u in get_secret("NEWS_EXTRA_FEEDS", "").split(",") if u.strip())

# Scale-invariant thresholds (percent of the relevant OI base, not contract counts)
BATTLE_PCT = 0.25          # whole-chain OI change that counts as material for the "OI battle" label (% of side OI)
BIAS_STRONG = (1.5, 1.0)   # (unwind %, build %) strong
BIAS_MILD = (0.8, 0.5)     # (unwind %, build %) mild
BIAS_MOVE_SIGMA = 0.2      # spot vs prev close, in daily sigmas
JOBBER_ABSORB_PCT = 0.5    # ladder unwind % that counts as absorption
JOBBER_OTHER_PCT = 0.2     # opposite-side unwind must be milder than this %
TRAP_PCT = 2.0             # single-strike OI build as % of near-spot side OI
VEL_PCT = 1.0              # 3-min unwind velocity as % of near-spot side OI
WRITE_EDGE = 0.25          # difference in "writing share" between puts and calls to count as a vote

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
USE_SURFACE_CHECK = _SessionFlag("surface_check", False)
SURFACE_KEYS = ("syn_fut", "fair_fwd", "carry", "basis", "excess_basis", "surface_ok", "iv_25c", "iv_25p", "rr_25", "bf_25",
                "net_gex", "gross_gex", "gex_regime", "gex_regime_label", "regime_stable", "regime_by_window", "crossover",
                "zero_gamma", "total_gex_at_spot", "net_vex", "net_cex")
REGIME_LABEL = {
    "POSITIVE": "POSITIVE GAMMA (ASSUMING dealers long calls / short puts: mean-reverting)",
    "NEGATIVE": "NEGATIVE GAMMA (ASSUMING dealers long calls / short puts: momentum / breakout)",
    "NEUTRAL": "NEUTRAL GAMMA (net exposure near zero)",
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
# MARKET CALENDAR HELPERS
# ============================================================
def is_trading_day(now):
    return now.weekday() < 5 and now.date().isoformat() not in NSE_HOLIDAYS


def market_open_now(now):
    return is_trading_day(now) and 9 * 60 + 15 <= now.hour * 60 + now.minute < 15 * 60 + 30


def _mins(now):
    return now.hour * 60 + now.minute


def near_window(spot, step):
    """Half-width (points) of the 'near spot' strike window. Scales with index level and strike step."""
    return max(step * 6, round(spot * 0.013 / step) * step)


def fmt_cr(x):
    """Format a rupee exposure in crore."""
    return f"{x / 1e7:+,.2f} Cr"


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
    path = os.path.join(DATA_DIR, "bbg_api_throttle.lock")
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
            "CE_PrevLTP": _n(ce.get("previous_close_price")), "PE_PrevLTP": _n(pe.get("previous_close_price")),
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
    """Returns (tone, hits). Tone is only meaningful when hits >= NEWS_MIN_TONE_HITS (crude keyword count)."""
    pos = neg = 0
    for i in items:
        t = i["title"].lower()
        pos += sum(1 for w in POS_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
        neg += sum(1 for w in NEG_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    hits = pos + neg
    return (round((pos - neg) / hits * 100) if hits else 0), hits


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
    if today <= exp_date and is_trading_day(now):
        close_dt = datetime.combine(today, close_t, tzinfo=IST)
        mins += min(sess, max(0.0, (close_dt - now).total_seconds() / 60.0))
    cur = today
    while cur < exp_date:
        cur += timedelta(days=1)
        if cur.weekday() < 5 and cur.isoformat() not in NSE_HOLIDAYS:
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


def interpret_oi_battle(ce_chg, pe_chg, ce_tot, pe_tot):
    """Thresholds are % of each side's whole-chain OI, so the label means the same on every index."""
    ce_p = ce_chg / ce_tot * 100.0 if ce_tot > 0 else 0.0
    pe_p = pe_chg / pe_tot * 100.0 if pe_tot > 0 else 0.0
    t = BATTLE_PCT
    if ce_p > t and pe_p <= 0:
        return "CE Build (Bearish)"
    if pe_p > t and ce_p <= 0:
        return "PE Build (Bullish)"
    if ce_p < -t and pe_p >= 0:
        return "CE Unwind (Bullish)"
    if pe_p < -t and ce_p >= 0:
        return "PE Unwind (Bearish)"
    if ce_p > 0 and pe_p > 0:
        return "Straddle Build"
    return "Liquidation"


def calc_max_pain(df):
    strikes = df["Strike"].values
    ce_oi, pe_oi = df["CE_OI"].values, df["PE_OI"].values
    pain = [(ce_oi * np.maximum(0, k - strikes)).sum() + (pe_oi * np.maximum(0, strikes - k)).sum()
            for k in strikes]
    return float(strikes[int(np.argmin(pain))])


def daily_sigma_from(spot, iv_pct):
    """One TRADING-day 1-sigma move. Always /sqrt(252): day levels, exhaustion and the scanner all think in trading days."""
    return spot * iv_pct / 100.0 * math.sqrt(1.0 / 252.0)


def daily_sigma(m):
    return daily_sigma_from(m["spot"], m["atm_iv"])


def compute_bias(dce, dpe, ce_tot, pe_tot, spot, prev_close, pcr, basis, rr_25, sd):
    score = 50
    dce_p = dce / ce_tot * 100.0 if ce_tot > 0 else 0.0
    dpe_p = dpe / pe_tot * 100.0 if pe_tot > 0 else 0.0
    (su, sb), (mu, mb) = BIAS_STRONG, BIAS_MILD
    if dce_p < -su and dpe_p > sb:
        score += 25
    elif dce_p < -mu and dpe_p > mb:
        score += 15
    elif dpe_p < -su and dce_p > sb:
        score -= 25
    elif dpe_p < -mu and dce_p > mb:
        score -= 15

    if prev_close and prev_close > 0 and sd > 0:
        z = (spot - prev_close) / sd
        if z > BIAS_MOVE_SIGMA:
            score += 10
        elif z < -BIAS_MOVE_SIGMA:
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


def _write_share(chg, px, noise):
    """Of the OI that was BUILT (chg above noise) at strikes with a known premium change, what share was
    WRITING (premium fell vs previous close) rather than BUYING (premium rose)? Returns (share, known)."""
    sel = (chg > noise) & px.notna()
    tot = float(chg[sel].sum())
    if tot <= 0:
        return 0.5, False
    return float(chg[sel & (px < 0)].sum()) / tot, True


def engine_sums(near):
    out = {k: 0.0 for k in ("ce_exits", "pe_exits", "ce_build", "pe_build",
                            "ce_exit_pct", "pe_exit_pct", "ce_build_pct", "pe_build_pct",
                            "ce_net_flow", "pe_net_flow", "bull_force", "bear_force", "force_ratio",
                            "ce_oi_near", "pe_oi_near")}
    out["ce_write_share"], out["pe_write_share"], out["wr_known"] = 0.5, 0.5, False
    if near is None or near.empty:
        return out

    noise_pct = P("noise_pct")
    ce_tot = float(near["CE_OI"].sum())
    pe_tot = float(near["PE_OI"].sum())
    ce_noise = ce_tot * noise_pct / 100.0
    pe_noise = pe_tot * noise_pct / 100.0

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

    if "CE_PxChg" in near.columns and "PE_PxChg" in near.columns:
        cs, ck = _write_share(ce_chg, near["CE_PxChg"], ce_noise)
        ps, pk = _write_share(pe_chg, near["PE_PxChg"], pe_noise)
        out["ce_write_share"], out["pe_write_share"], out["wr_known"] = cs, ps, bool(ck and pk)
    return out


def detect_gamma_blast(near):
    e = engine_sums(near)
    if near is None or near.empty:
        return "BALANCED ACCUMULATION", "WAIT", "No near-spot strikes available."

    dom = P("dominance")
    base_bull = e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] >= MIN_FLOW_PCT
    base_bear = e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] >= MIN_FLOW_PCT
    bull_cap = e["ce_net_flow"] < 0
    bear_cap = e["pe_net_flow"] < 0
    bull_dom = e["bull_force"] >= dom * e["bear_force"]
    bear_dom = e["bear_force"] >= dom * e["bull_force"]
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

    ce_unw = ladder[ladder["CE_OI_Chg"] < 0]["CE_OI_Chg"].sum()
    pe_unw = ladder[ladder["PE_OI_Chg"] < 0]["PE_OI_Chg"].sum()
    ce_unw_pct = ce_unw / total_ce * 100.0 if total_ce > 0 else 0.0
    pe_unw_pct = pe_unw / total_pe * 100.0 if total_pe > 0 else 0.0

    if ce_unw_pct < -JOBBER_ABSORB_PCT and pe_unw_pct > -JOBBER_OTHER_PCT:
        micro_turn_signal = "ABSORPTION BULLISH (Shorts Trapped)"
    elif pe_unw_pct < -JOBBER_ABSORB_PCT and ce_unw_pct > -JOBBER_OTHER_PCT:
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


def flow_concentration(df, near):
    """Share (0-100) of the chain's gross OI change that sits in the near-spot strikes.
    This measures where the activity is, NOT who is trading (it is not FII/DII data)."""
    whole = float(df["CE_OI_Chg"].abs().sum() + df["PE_OI_Chg"].abs().sum())
    if whole <= 0 or near is None or near.empty:
        return 0.0
    nr = float(near["CE_OI_Chg"].abs().sum() + near["PE_OI_Chg"].abs().sum())
    return round(nr / whole * 100.0, 1)


def compute_metrics(df, spot, prev_close, expiry_str, step):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]
    # premium change vs previous close (NaN when the previous close or today's LTP is missing)
    for s in ("CE", "PE"):
        ok = (df[f"{s}_PrevLTP"] > 0) & (df[f"{s}_LTP"] > 0)
        df[f"{s}_PxChg"] = np.where(ok, df[f"{s}_LTP"] - df[f"{s}_PrevLTP"], np.nan)

    ce_tot_all, pe_tot_all = float(df["CE_OI"].sum()), float(df["PE_OI"].sum())
    pcr = pe_tot_all / ce_tot_all if ce_tot_all > 0 else 1.0
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

    window = near_window(spot, step)
    near = df[(df["Strike"] >= spot - window) & (df["Strike"] <= spot + window)].copy()

    ce_trap, pe_trap = None, None
    trap_buf = SL_BUFFER * step / 50.0
    if not near.empty:
        near_ce, near_pe = float(near["CE_OI"].sum()), float(near["PE_OI"].sum())
        i = near["CE_OI_Chg"].idxmax()
        if near_ce > 0 and near.loc[i, "CE_OI_Chg"] / near_ce * 100.0 >= TRAP_PCT:
            ce_trap = float(near.loc[i, "Strike"]) + trap_buf
        j = near["PE_OI_Chg"].idxmax()
        if near_pe > 0 and near.loc[j, "PE_OI_Chg"] / near_pe * 100.0 >= TRAP_PCT:
            pe_trap = float(near.loc[j, "Strike"]) - trap_buf

    sd = daily_sigma_from(spot, atm_iv)
    score, bias = compute_bias(dce, dpe, ce_tot_all, pe_tot_all, spot, prev_close, pcr,
                               surf["excess_basis"], surf["rr_25"], sd)
    status, signal, desc = detect_gamma_blast(near)
    engine = engine_sums(near)
    struct = find_structure(df, spot, step, exp_move, call_wall, put_wall)
    jobber = calculate_jobber_microstructure(df, spot)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "pcr": pcr,
        "dce": dce, "dpe": dpe, "battle": interpret_oi_battle(dce, dpe, ce_tot_all, pe_tot_all),
        "atm_iv": atm_iv, "dte": dte, "exp_move": round(exp_move, 2),
        "t_years": t_years, "days_left": t_years * (365.0 if TIME_BASIS["mode"] == "calendar" else 252.0),
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "jobber": jobber, "engine": engine, "struct": struct,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
        "flow_conc": flow_concentration(df, near), "prev_close": prev_close,
        **{k: surf[k] for k in SURFACE_KEYS},
    }


# ============================================================
# DATA GATE
# ============================================================
def data_gate(now, m):
    """Is the data live and usable enough to run the AI, store snapshots and validate signals?"""
    if not is_trading_day(now):
        return False, "not a trading day (weekend / holiday list)"
    if not market_open_now(now):
        return False, "market closed"
    real_ivs = [x for x in (m["atm_iv_ce"], m["atm_iv_pe"]) if x > 0]
    if not real_ivs or sum(real_ivs) / len(real_ivs) < 5.0:
        return False, "ATM implied volatility is missing or below 5% (stale / off-hours greeks)"
    if m["iv_gap"] > 5.0:
        return False, f"ATM call/put IV gap {m['iv_gap']:.1f} is too wide (bad quotes)"
    return True, "live"


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
    # NOTE: the assumed dealer-gamma regime flip is intentionally NOT an AI trigger any more
    # (its sign convention is an assumption for NSE and it flickers near zero).
    if m["status"] != last["status"]:
        return True, "ENGINE_STATUS_CHANGE"
    trap_now = (m["ce_trap"] is not None) or (m["pe_trap"] is not None)
    if trap_now and not last.get("trap_active", False):
        return True, "35PT_TRAP_DETECTED"
    e = m["engine"]
    if (e["ce_oi_near"] > 0 and vel["ce_unwinds"] / e["ce_oi_near"] * 100.0 > VEL_PCT) or \
            (e["pe_oi_near"] > 0 and vel["pe_unwinds"] / e["pe_oi_near"] * 100.0 > VEL_PCT):
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
    dom = P("dominance")
    wr_txt = (f"share of near-spot OI builds that were WRITING (premium fell vs prev close): "
              f"calls {eg['ce_write_share']:.0%}, puts {eg['pe_write_share']:.0%}") if eg["wr_known"] else \
        "premium-confirmed writing share: not available"

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
  Bull force {eg['bull_force']:.1f} vs Bear force {eg['bear_force']:.1f} (ratio {eg['force_ratio']:.2f}; a winner needs >= {dom})
- {wr_txt}
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
5. Risk Officer: final approval; reject if evidence is conflicting or engine status is CONFLICTED. Begin final_approval with the single word APPROVE or REJECT, then a short reason.

Return ONLY this JSON:
{{"price_action_agent":"1 sentence","order_flow_agent":"1 sentence","volatility_agent":"1 sentence",
"news_agent":"1 sentence","news_score":-100 to 100,"final_approval":"APPROVE or REJECT: short reason","signal":"BUY or SELL or WAIT","confidence":0-100}}"""

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
# SIGNAL VALIDATION
# ============================================================
FAMILIES = ("flow", "positioning", "volatility")


def chain_checks(m):
    """Every check carries a FAMILY. Checks derived from the same data cannot stack votes:
    the agreement score counts one vote per family (see build_signal)."""
    e, j = m["engine"], m["jobber"]
    checks = []

    def add(name, v, detail, fam):
        checks.append({"name": name, "v": v, "detail": detail, "family": fam})

    st_ = m["status"]
    add("Dual-Force engine", 1 if "BULLISH GAMMA" in st_ else -1 if "BEARISH GAMMA" in st_ else 0, st_, "flow")

    ce_pct = e["ce_net_flow"] / e["ce_oi_near"] * 100 if e["ce_oi_near"] > 0 else 0.0
    pe_pct = e["pe_net_flow"] / e["pe_oi_near"] * 100 if e["pe_oi_near"] > 0 else 0.0
    gap = pe_pct - ce_pct
    add("Near-spot net OI flow", 1 if gap >= FLOW_EDGE_PCT else -1 if gap <= -FLOW_EDGE_PCT else 0,
        f"PE {pe_pct:+.1f}% vs CE {ce_pct:+.1f}% of near-spot OI", "flow")

    mt = j["micro_turn"]
    add("Micro-turn", 1 if "BULLISH" in mt else -1 if "BEARISH" in mt else 0, mt, "flow")

    if e["wr_known"]:
        wgap = e["pe_write_share"] - e["ce_write_share"]
        add("Premium-confirmed writing", 1 if wgap >= WRITE_EDGE else -1 if wgap <= -WRITE_EDGE else 0,
            f"puts written {e['pe_write_share']:.0%} vs calls written {e['ce_write_share']:.0%} of new OI "
            f"(write = OI up and premium down vs prev close; confounded by the index's own move)", "flow")
    else:
        add("Premium-confirmed writing", 0, "no previous-close premiums available", "flow")

    li = j["ladder_imbalance"]
    add("Ladder OI imbalance", 1 if li >= 0.10 else -1 if li <= -0.10 else 0, f"{li:+.3f} (PE-heavy is +)", "positioning")
    add("PCR (whole chain)", 1 if m["pcr"] > 1.2 else -1 if m["pcr"] < 0.8 else 0, f"{m['pcr']:.2f}", "positioning")
    add("Bias score", 1 if m["score"] > 55 else -1 if m["score"] < 45 else 0, f"{m['score']}/100 {m['bias']}", "positioning")

    if m["dte"] > 2:
        add("Spot vs max pain", 0, f"{m['dte']}d to expiry: max-pain pull is weak, neutral", "volatility")
    else:
        mp_gap = m["spot"] - m["max_pain"]
        add("Spot vs max pain", -1 if mp_gap > m["step"] else 1 if mp_gap < -m["step"] else 0,
            f"spot {m['spot']:,.0f} vs max pain {m['max_pain']:,.0f} ({mp_gap:+,.0f})", "volatility")

    thr = max(BASIS_MIN_PTS, 0.0004 * m["spot"])
    ex_b = m.get("excess_basis", 0.0)
    sv = 1 if (m.get("surface_ok") and ex_b >= thr) else -1 if (m.get("surface_ok") and ex_b <= -thr) else 0
    on = USE_SURFACE_CHECK["on"]
    checks.append({"name": SURF_CHECK_NAME, "v": sv, "counted": on, "family": "volatility",
                   "detail": (f"F_syn {m['syn_fut']:,.1f} vs fair {m['fair_fwd']:,.1f} (excess {ex_b:+.1f}, needs ±{thr:.0f}) | "
                              f"RR25 {m.get('rr_25', 0):+.2f}" + ("" if on else " [observe only]")) if m.get("surface_ok")
                   else "no balanced near-ATM call/put mids [observe only]"})
    return checks


VETO_RE = re.compile(r"\b(REJECT|REJECTED|VETO|VETOED|DENY|DENIED|DECLINE|DECLINED|DISAPPROVE|DISAPPROVED|NOT APPROVED)\b")


def build_signal(m, ai, data_ok=True, gate_reason=""):
    ai_sig = str(ai.get("signal", "WAIT")).upper()
    if ai_sig not in ("BUY", "SELL", "WAIT"):
        ai_sig = "WAIT"
    try:
        conf = int(float(ai.get("confidence", 0)))
    except (TypeError, ValueError):
        conf = 0

    min_confirm = int(P("min_confirm"))
    min_conf = int(P("min_conf"))
    direction = {"BUY": 1, "SELL": -1}.get(ai_sig, 0)
    checks = chain_checks(m)

    # one vote per family: sign of the summed counted votes inside the family
    fam_sum = {f: 0 for f in FAMILIES}
    for c in checks:
        if c.get("counted", True):
            fam_sum[c["family"]] += c["v"]
    families = {f: (1 if s > 0 else -1 if s < 0 else 0) for f, s in fam_sum.items()}
    lean = sum(families.values())
    net = lean * direction

    entry = m["spot"]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    notes = []
    sl_buf = SL_BUFFER * m["step"] / 50.0
    sig, target, sl, rr = ai_sig, None, None, None

    if sig != "WAIT" and not data_ok:
        notes.append(f"{sig} rejected: data gate closed ({gate_reason}).")
        sig = "WAIT"

    if sig != "WAIT":
        fa = str(ai.get("final_approval", "")).upper()
        if VETO_RE.search(fa):
            notes.append(f"{sig} rejected: AI risk officer veto ({str(ai.get('final_approval', ''))[:80]}).")
            sig = "WAIT"

    if sig != "WAIT" and conf < min_conf:
        notes.append(f"{sig} rejected: AI confidence {conf}% is below the required {min_conf}%.")
        sig = "WAIT"

    if sig != "WAIT" and net < min_confirm:
        notes.append(f"{sig} rejected: option-chain agreement {net:+d} (one vote per family) is below the required +{min_confirm}.")
        sig = "WAIT"

    if sig != "WAIT" and P("flow_conc_on"):
        fc, fc_min = m.get("flow_conc", 0.0), P("flow_conc_min")
        if fc < fc_min:
            notes.append(f"{sig} rejected: near-spot flow concentration {fc:.0f}% is below the required {fc_min}%.")
            sig = "WAIT"

    exh = m.get("exh") or {}
    if sig != "WAIT" and exh.get("ready"):
        mine = exh["buyer"] if sig == "BUY" else exh["seller"]
        who = "buyer" if sig == "BUY" else "seller"
        if mine["flag"] == "CONFIRMED" and P("exh_veto"):
            notes.append(f"{sig} rejected: {who} exhaustion confirmed ({mine['score']}/100), the move you would join is running out of fuel.")
            sig = "WAIT"
        elif mine["flag"] in ("CONFIRMED", "WATCH"):
            notes.append(f"Caution: {who} exhaustion {mine['flag'].lower()} ({mine['score']}/100)"
                         + ("" if P("exh_veto") else " [advisory only: exhaustion veto is off]") + ".")

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

    return {"signal": sig, "ai_signal": ai_sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": " ".join(notes), "checks": checks, "net": net, "lean": lean,
            "families": families, "direction": direction}


# ============================================================
# DAY MOVEMENT LEVELS
# ============================================================
def _epoch(ts):
    """Dhan timestamps may be unix or offset to 1980; normalise to unix seconds."""
    ts = float(ts)
    if ts < time.time() - 400 * 86400:
        ts += 315532800
    return ts


def _candle_date(ts):
    return datetime.fromtimestamp(_epoch(ts), IST).date()


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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_intraday_candles(scrip, seg, token, client_id):
    """Today's 1-minute index candles (used so target/stop touches and MFE/MAE see intrabar wicks)."""
    today = datetime.now(IST).date().isoformat()
    r = requests.post(f"{BASE_URL}/charts/intraday", headers=auth_headers(token, client_id),
                      json={"securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                            "interval": "1", "fromDate": f"{today} 09:15:00", "toDate": f"{today} 15:30:00"},
                      timeout=12)
    check(r)
    d = r.json()
    n = len(d.get("close", []))
    if n == 0:
        raise RuntimeError("empty intraday response")
    return [{"ts": _epoch(d["timestamp"][i]), "h": float(d["high"][i]), "l": float(d["low"][i]),
             "c": float(d["close"][i])} for i in range(n)]


def compute_day_levels(candles, spot, sd, m, prev_close_fallback, today_ohlc=None):
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()
    after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    synthetic = False
    if after_close and is_trading_day(now_ist) and today_ohlc and not any(c["date"] == today for c in candles):
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
        P_ = (h + l + c) / 3
        for nm, v in (("R3", h + 2 * (P_ - l)), ("R2", P_ + (h - l)), ("R1", 2 * P_ - l), ("Pivot", P_),
                      ("S1", 2 * P_ - h), ("S2", P_ - (h - l)), ("S3", l - 2 * (h - P_))):
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


def record_snapshot(m, key, store=True):
    """Load today's history for this key; append a new snapshot only when `store` (data gate open)."""
    if st.session_state.get("hist_key") != key:
        st.session_state["hist_key"] = key
        st.session_state["hist"] = load_hist(key)
    hist = st.session_state["hist"]
    if not store:
        return hist
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
        out["why"] = f"Collecting history ({len(hist)} snapshot(s)). Needs at least 3 refreshes while the data gate is open."
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
    fl = lb_flows(ref, now, spot, near_window(spot, m["step"]))
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
# PREMIUM SCANNER
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
    # NOTE: the horizon is measured in model days on the chosen vol basis; overnight/weekend gaps are not modelled.
    df, S0, step = m["df"], m["spot"], m["step"]
    atm_iv = m["atm_iv"]
    cal = TIME_BASIS["mode"] == "calendar"
    basis_days = 365.0 if cal else 252.0
    min_t = 10.0 / (basis_days * (1440.0 if cal else 375.0))
    T0 = max(m.get("t_years", m["dte"] / 365.0), min_t)
    T1 = max(T0 - horizon_days / basis_days, min_t)
    sd = min(daily_sigma(m), S0 * atm_iv / 100.0 * math.sqrt(T0))
    span = max(2.5 * sd, 8 * step)
    cand = df[(df["Strike"] >= S0 - span) & (df["Strike"] <= S0 + span)]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    crush, spike = 1.0 - iv_shock / 100.0, 1.0 + iv_shock / 100.0
    coef = SKEW_COEF if skew else 0.0

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
            iv_at = lambda k: iv0 * max(0.2, 1.0 + (-sign) * coef * (iv_shock / 100.0) * k)
            m05, m1, m15 = mult(Sfav(0.5), iv_at(0.5)), mult(Sfav(1.0), iv_at(1.0)), mult(Sfav(1.5), iv_at(1.5))
            if None in (m05, m1, m15):
                continue
            wall = (res if is_call else sup)
            m_wall = mult(wall["level"], iv_at(abs(wall["level"] - S0) / sd)) if wall else None
            m_crush = mult(Sfav(1.0), iv0 * crush)
            m_spike = mult(Sfav(1.0), iv0 * spike)
            m_wrong = mult(S0 - sign * 0.5 * sd, iv_at(-0.5))
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


# ============================================================
# EXTRA ANALYTICS
# ============================================================
def round_number_levels(df, spot, step, top_n=5):
    """Strikes that are round numbers (multiples of 500, or 1000 for wide-step indices), ranked by total OI."""
    base = 1000 if step >= 100 else 500
    span = spot * 0.05
    d = df[(df["Strike"] >= spot - span) & (df["Strike"] <= spot + span)]
    d = d[(d["Strike"] % base) == 0]
    if d.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "Level": d["Strike"], "CE OI": d["CE_OI"], "PE OI": d["PE_OI"],
        "Total OI": d["CE_OI"] + d["PE_OI"], "Dist pts": d["Strike"] - spot,
    })
    return out.sort_values("Total OI", ascending=False).head(top_n).reset_index(drop=True)


def theta_profile(m):
    """Expected one-day decay of the ATM straddle, using the sqrt(T) scaling of ATM option value."""
    basis_days = 365.0 if TIME_BASIS["mode"] == "calendar" else 252.0
    T = max(m["t_years"], 1e-6)
    day = 1.0 / basis_days
    if T <= day:
        frac = 100.0
    else:
        frac = (1.0 - math.sqrt((T - day) / T)) * 100.0
    df, spot = m["df"], m["spot"]
    a = df.iloc[int((df["Strike"] - spot).abs().values.argmin())]
    straddle = float(a["CE_LTP"] + a["PE_LTP"])
    label = ("EXTREME" if frac >= 25 else "HIGH" if frac >= 12 else "MODERATE" if frac >= 5 else "LOW")
    return {"decay_pct": round(frac, 1), "label": label, "straddle": straddle,
            "decay_pts": straddle * frac / 100.0, "days_left": T * basis_days}


def large_move_check(spot, prev_close, thresh):
    if not prev_close or prev_close <= 0:
        return None
    pct = (spot - prev_close) / prev_close * 100.0
    return {"pct": round(pct, 2), "triggered": abs(pct) >= thresh}


def weekly_expiry_analysis(m):
    out = {"active": m["dte"] <= 7, "dte": m["dte"], "warnings": [], "risk_level": "NONE"}
    if not out["active"]:
        return out
    near = m["near"]
    tot = float(near["CE_OI"].sum() + near["PE_OI"].sum())
    chg = float(near["CE_OI_Chg"].abs().sum() + near["PE_OI_Chg"].abs().sum())
    if m["dte"] <= 1:
        out["warnings"].append("Expiry today/tomorrow: gamma and spread risk are at their highest.")
    elif m["dte"] <= 3:
        out["warnings"].append("DTE ≤ 3: gamma risk is elevated.")
    if tot > 0 and chg / tot > 0.03:
        out["warnings"].append(f"Heavy near-spot OI churn: {chg / tot * 100:.1f}% of near-spot OI changed.")
    if m.get("gex_regime") in ("POSITIVE", "NEGATIVE") and m["dte"] <= 5:
        out["warnings"].append(f"{m['gex_regime']} (assumed) gamma regime this close to expiry: moves can accelerate.")
    out["risk_level"] = "HIGH" if m["dte"] <= 1 or len(out["warnings"]) >= 3 else "MODERATE" if m["dte"] <= 3 else "LOW"
    return out


def next_expiry(expiries, expiry):
    try:
        i = list(expiries).index(expiry)
        return expiries[i + 1] if i + 1 < len(expiries) else None
    except ValueError:
        return None


def calendar_view(m1, info, expiries, expiry, token, client_id, prev_close):
    nxt = next_expiry(expiries, expiry)
    if not nxt:
        return None, "No later expiry available."
    spot2, df2 = fetch_option_chain(info["scrip"], info["seg"], nxt, token, client_id)
    if df2.empty or spot2 <= 0:
        return None, "Later expiry returned no data."
    m2 = compute_metrics(df2, spot2, prev_close, nxt, info["step"])
    iv1, iv2, T1, T2 = m1["atm_iv"], m2["atm_iv"], m1["t_years"], m2["t_years"]
    fwd = None
    if T2 > T1:
        var = ((iv2 / 100) ** 2 * T2 - (iv1 / 100) ** 2 * T1) / (T2 - T1)
        fwd = math.sqrt(var) * 100 if var > 0 else None
    spread = iv1 - iv2
    return {"front": str(expiry)[:10], "back": str(nxt)[:10], "dte1": m1["dte"], "dte2": m2["dte"],
            "iv1": iv1, "iv2": iv2, "spread": spread, "fwd_iv": fwd,
            "structure": "BACKWARDATION (front IV above back: near-term stress or event priced)" if spread > 0.5
            else "CONTANGO (normal: back IV above front)" if spread < -0.5 else "FLAT",
            "gex1": m1["gex_regime"], "gex2": m2["gex_regime"], "pcr1": m1["pcr"], "pcr2": m2["pcr"]}, ""


def cross_index_view(token, client_id):
    rows = []
    for name, inf in INDEX_MAP.items():
        try:
            exps = get_expiries(inf["scrip"], inf["seg"], token, client_id)
            if not exps:
                rows.append({"Index": name, "Note": "no expiries"})
                continue
            sp, d = fetch_option_chain(inf["scrip"], inf["seg"], exps[0], token, client_id)
            if d.empty or sp <= 0:
                rows.append({"Index": name, "Note": "no chain data"})
                continue
            pc, _src = fetch_prev_close(inf["scrip"], inf["seg"], token, client_id)
            mm = compute_metrics(d, sp, pc or 0.0, exps[0], inf["step"])
            rows.append({"Index": name, "Expiry": str(exps[0])[:10], "Spot": round(sp, 1),
                         "Chg %": round((sp - pc) / pc * 100, 2) if pc else None,
                         "ATM IV": round(mm["atm_iv"], 1), "PCR": round(mm["pcr"], 2),
                         "Gamma (assumed)": mm["gex_regime"], "Bias": mm["bias"], "Engine": mm["status"], "Note": ""})
        except Exception as e:
            rows.append({"Index": name, "Note": str(e)[:80]})
    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def cross_index_cached(token, client_id):
    return cross_index_view(token, client_id)


MIN_SAMPLE_FOR_VERDICT = 30


def wilson_lower(wins, n, z=1.28):
    """Lower confidence bound (about 90% one-sided) of a win rate."""
    if n <= 0:
        return 0.0
    p = wins / n
    den = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    adj = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - adj) / den)


def kelly_sizing(sig, card, capital, max_risk_pct):
    """Risk budget for a validated signal. Uses quarter-Kelly on the Wilson LOWER bound of the win rate
    (TIMEOUT counts as a non-win) only when enough resolved signals exist; otherwise a flat conservative budget.
    TARGET/STOP here are index-level touches, not option P&L."""
    rr = sig.get("rr")
    if not rr or rr <= 0:
        return None
    done = [r for r in card if r.get("kind") == "VALIDATED" and r.get("outcome") in ("TARGET", "STOP", "TIMEOUT")]
    n = len(done)
    if n >= MIN_SAMPLE_FOR_VERDICT:
        wins = sum(1 for r in done if r["outcome"] == "TARGET")
        p_raw = wins / n
        p = wilson_lower(wins, n)
        f = p - (1 - p) / rr
        if f <= 0:
            return {"method": f"Kelly on {n} resolved signals (raw win rate {p_raw:.0%}, conservative {p:.0%}, R:R 1:{rr:.2f})",
                    "risk_pct": 0.0, "capital_at_risk": 0.0,
                    "note": "Kelly fraction is zero or negative at the conservative win rate: no demonstrated edge, size is 0.", "n": n, "p": p}
        risk_pct = min(0.25 * f * 100.0, max_risk_pct)
        method = (f"Quarter-Kelly on {n} resolved signals (raw win rate {p_raw:.0%}, Wilson lower bound {p:.0%}, "
                  f"timeouts count as non-wins, R:R 1:{rr:.2f}), capped at max risk")
        p_out = p
    else:
        risk_pct = max_risk_pct / 2.0
        method = f"Flat budget = half of max risk ({n} resolved signals; need {MIN_SAMPLE_FOR_VERDICT}+ for Kelly)"
        p_out = None
    return {"method": method, "risk_pct": risk_pct, "capital_at_risk": capital * risk_pct / 100.0,
            "note": "", "n": n, "p": p_out}


# ============================================================
# SCORECARD, ALERTS & DATA HEALTH
# ============================================================
SCORECARD_FILE = os.path.join(DATA_DIR, "bbg_scorecard_v13_2.json")
ALERT_FILE = os.path.join(DATA_DIR, "bbg_alerts_sent_v13_2.json")
SIGNAL_DEDUP_MIN = 30
OUTCOME_HORIZONS = (15, 30, 60)
HORIZON_TOL_MIN = 10
ALERT_STATE_KEEP_SEC = 3 * 86400


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        pass


def token_expiry(token):
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = data.get("exp")
        return datetime.fromtimestamp(float(exp), IST) if exp else None
    except Exception:
        return None


def _scrub(text, *secrets):
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def send_alert(text, tg_token="", tg_chat="", webhook=""):
    out = []
    if tg_token and tg_chat:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                              json={"chat_id": tg_chat, "text": text.replace("**", ""), "disable_web_page_preview": True}, timeout=8)
            out.append(("telegram", bool(r.ok), "" if r.ok else f"HTTP {r.status_code}"))
        except Exception as e:
            out.append(("telegram", False, _scrub(e, tg_token)[:90]))
    if webhook:
        try:
            r = requests.post(webhook, json={"text": text, "content": text}, timeout=8)
            out.append(("webhook", bool(r.ok), "" if r.ok else f"HTTP {r.status_code}"))
        except Exception as e:
            out.append(("webhook", False, _scrub(e, webhook)[:90]))
    return out


def fire_alert(key, text, cooldown_min, cfg):
    if not cfg.get("enabled") or not (cfg.get("tg_token") and cfg.get("tg_chat") or cfg.get("hook")):
        return []
    now_ts = time.time()
    state = _load_json(ALERT_FILE, {})
    # prune old keys so the file does not grow forever
    state = {k: v for k, v in state.items() if now_ts - float(v) < ALERT_STATE_KEEP_SEC}
    if now_ts - float(state.get(key, 0)) < cooldown_min * 60:
        return []
    res = send_alert(text, cfg.get("tg_token", ""), cfg.get("tg_chat", ""), cfg.get("hook", ""))
    if any(ok for _, ok, _ in res):
        state[key] = now_ts
        _save_json(ALERT_FILE, state)
    return res


def signal_alert_text(r):
    tgt = f"{r['target']:,.0f}" if r.get("target") else "-"
    sl = f"{r['sl']:,.0f}" if r.get("sl") else "-"
    rr = f"1:{r['rr']:.2f}" if r.get("rr") else "-"
    kind_label = "✅ VALIDATED SIGNAL" if r['kind'] == "VALIDATED" else "❌ REJECTED SIGNAL"
    reason_txt = f"\nReason: {r['reason']}" if r.get("reason") else ""
    return (f"📣 **{kind_label}**\n{r['index']} | **{r['signal']}** @ {r['entry']:,.0f}\n"
            f"Target {tgt} | Stop {sl} | R:R {rr}\n"
            f"AI Confidence {r['confidence']}% | Chain Agreement {r['net']:+d}{reason_txt}\n"
            f"{r['time']} IST. Analysis only, not trading advice.")


def outcome_alert_text(r):
    return (f"🏁 **SIGNAL RESOLVED: {r['outcome']}**\n"
            f"{r['index']} | {r['signal']} entered @ {r['entry']:,.0f}\n"
            f"MFE: +{r['mfe']} pts | MAE: -{r['mae']} pts\n"
            f"Result: {r['outcome']} at {r.get('resolved_time', r['time'])} IST.")


def post_webhook(url, payload):
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception:
        pass


def load_scorecard():
    recs = _load_json(SCORECARD_FILE, [])
    return recs if isinstance(recs, list) else []


def _merge_rec(a, b):
    """Combine two copies of the same record written by different sessions without losing progress."""
    if b.get("resolved") and not a.get("resolved"):
        base, other = dict(b), a
    elif a.get("resolved") and not b.get("resolved"):
        base, other = dict(a), b
    elif (a.get("samples") or 0) >= (b.get("samples") or 0):
        base, other = dict(a), b
    else:
        base, other = dict(b), a
    for k in ("mfe", "mae", "max_gap_min", "samples"):
        base[k] = max(base.get(k) or 0, other.get(k) or 0)
    for k in ("s15", "s30", "s60", "sclose", "first_touch"):
        if base.get(k) is None and other.get(k) is not None:
            base[k] = other[k]
    base["touch_alerted"] = bool(a.get("touch_alerted") or b.get("touch_alerted"))
    if base.get("resolved") and base.get("first_touch") in ("TARGET", "STOP") and base.get("outcome") in ("TIMEOUT", "RESOLVED", "OPEN"):
        base["outcome"] = base["first_touch"]
    return base


def sync_scorecard(card):
    """Pull in records written by other sessions/tabs and MERGE progress (MFE/MAE, horizon marks, touches)."""
    try:
        disk = load_scorecard()
    except Exception:
        return
    idx = {r.get("id"): i for i, r in enumerate(card)}
    for r in disk:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        i = idx.get(r["id"])
        if i is None:
            card.append(r)
            idx[r["id"]] = len(card) - 1
        else:
            card[i] = _merge_rec(card[i], r)
    card.sort(key=lambda x: x.get("ts", 0))


def save_scorecard(recs):
    sync_scorecard(recs)
    _save_json(SCORECARD_FILE, recs)


def _dir(sig):
    return 1 if sig == "BUY" else -1


def classify_rejection(note):
    n = (note or "").lower()
    if "rejected" in n:
        n = n.rsplit("rejected", 1)[-1]
    if not n.strip():
        return "None (validated)"
    if "data gate" in n:
        return "Data gate closed"
    if "veto" in n:
        return "AI risk officer veto"
    if "ai confidence" in n:
        return "Low AI confidence"
    if "concentration" in n:
        return "Low flow concentration"
    if "agreement" in n:
        return "Agreement below minimum"
    if "r:r" in n:
        return "Poor R:R"
    if "exhaustion" in n:
        return "Trend exhaustion"
    if "wall" in n:
        return "No usable OI wall"
    return "Other"


def rejection_category(r):
    return r.get("rejection_category") or classify_rejection(r.get("reason"))


def make_record(kind, idx_name, expiry, m, sig, ai, tone, now):
    direction = sig["signal"] if kind == "VALIDATED" else sig["ai_signal"]
    ex = m.get("exh") or {}
    ready = bool(ex.get("ready"))
    own = (ex["buyer"] if direction == "BUY" else ex["seller"]) if ready else None
    note = sig["note"] if kind == "REJECTED" else ""
    return {
        "id": hashlib.md5(f"{now.timestamp()}{idx_name}{kind}{direction}".encode()).hexdigest()[:8],
        "kind": kind, "ts": now.timestamp(), "date": now.date().isoformat(), "time": now.strftime("%H:%M:%S"),
        "index": idx_name, "expiry": str(expiry)[:10], "signal": direction, "entry": round(float(m["spot"]), 2),
        "target": sig["target"] if kind == "VALIDATED" else None, "sl": sig["sl"] if kind == "VALIDATED" else None,
        "rr": sig["rr"] if kind == "VALIDATED" else None, "confidence": sig["confidence"], "net": sig["net"],
        "lean": sig["lean"], "checks": {c["name"]: c["v"] for c in sig["checks"]},
        "status": m["status"], "bias": m["score"], "pcr": round(float(m["pcr"]), 3), "atm_iv": round(float(m["atm_iv"]), 2),
        "sd": round(daily_sigma(m), 1), "flow_conc": m.get("flow_conc"),
        "gex_regime": m.get("gex_regime"), "regime_stable": m.get("regime_stable"), "net_gex": m.get("net_gex"),
        "zero_gamma_dist_sigma": (round((m["zero_gamma"] - m["spot"]) / daily_sigma(m), 2) if m.get("zero_gamma") else None),
        "excess_basis": m.get("excess_basis"), "rr_25": m.get("rr_25"), "rr_shift": m.get("rr_shift"), "bf_25": m.get("bf_25"),
        "rejection_category": classify_rejection(note) if kind == "REJECTED" else "None (validated)",
        "exh_buyer": ex["buyer"]["score"] if ready else None, "exh_seller": ex["seller"]["score"] if ready else None,
        "exh_own_flag": own["flag"] if own else None, "tone": tone, "news_score": ai.get("news_score"),
        "reason": note,
        "s15": None, "s30": None, "s60": None, "sclose": None, "first_touch": None, "mfe": 0.0, "mae": 0.0,
        "samples": 0, "last_sample_ts": None, "max_gap_min": 0.0, "resolved": False, "outcome": "OPEN",
        "touch_alerted": False,
    }


def maybe_log_signal(recs, idx_name, expiry, m, sig, ai, tone, now):
    if not market_open_now(now):
        return []

    def recent(kind, direction):
        return any(r["index"] == idx_name and r["kind"] == kind and r["signal"] == direction
                   and now.timestamp() - r["ts"] < SIGNAL_DEDUP_MIN * 60 for r in recs)

    new = []
    if sig["signal"] in ("BUY", "SELL"):
        if not recent("VALIDATED", sig["signal"]):
            new.append(make_record("VALIDATED", idx_name, expiry, m, sig, ai, tone, now))
    elif sig["ai_signal"] in ("BUY", "SELL"):
        if not recent("REJECTED", sig["ai_signal"]):
            new.append(make_record("REJECTED", idx_name, expiry, m, sig, ai, tone, now))
    recs.extend(new)
    return new


def update_outcomes(recs, idx_name, spot, now, candles, intraday=None):
    """Update open records of the CURRENT index. 1-minute candle highs/lows (when available) drive
    MFE/MAE and target/stop touches; if target and stop are inside the same 1-minute bar the STOP is assumed
    first (conservative). Spot samples are used as a fallback and for the 15/30/60 min marks."""
    changed, resolved_now, touched_now = False, [], []
    today = now.date().isoformat()
    live = market_open_now(now)
    closes = {c["date"]: c["c"] for c in (candles or [])}
    bars = sorted(intraday or [], key=lambda b: b["ts"])
    stamp = now.strftime("%H:%M:%S")
    for r in recs:
        if r["resolved"] or r["index"] != idx_name:
            continue
        d = _dir(r["signal"])
        el = (now.timestamp() - r["ts"]) / 60.0
        same_day = r["date"] == today
        has_levels = r["target"] is not None and r["sl"] is not None
        if same_day:
            later = [b for b in bars if b["ts"] >= r["ts"]]
            for b in later:
                best = (b["h"] - r["entry"]) if d > 0 else (r["entry"] - b["l"])
                worst = (r["entry"] - b["l"]) if d > 0 else (b["h"] - r["entry"])
                r["mfe"] = round(max(r["mfe"], best), 2)
                r["mae"] = round(max(r["mae"], worst), 2)
                if r["first_touch"] is None and has_levels:
                    hit_t = (b["h"] >= r["target"]) if d > 0 else (b["l"] <= r["target"])
                    hit_s = (b["l"] <= r["sl"]) if d > 0 else (b["h"] >= r["sl"])
                    if hit_s:
                        r["first_touch"] = "STOP"
                    elif hit_t:
                        r["first_touch"] = "TARGET"
            if later:
                changed = True
            if live:
                fav = d * (spot - r["entry"])
                if r["last_sample_ts"]:
                    r["max_gap_min"] = max(r["max_gap_min"], (now.timestamp() - r["last_sample_ts"]) / 60.0)
                r["last_sample_ts"] = now.timestamp()
                r["samples"] += 1
                r["mfe"] = round(max(r["mfe"], fav), 2)
                r["mae"] = round(max(r["mae"], -fav), 2)
                if r["first_touch"] is None and has_levels:
                    if (d > 0 and spot >= r["target"]) or (d < 0 and spot <= r["target"]):
                        r["first_touch"] = "TARGET"
                    elif (d > 0 and spot <= r["sl"]) or (d < 0 and spot >= r["sl"]):
                        r["first_touch"] = "STOP"
                for h in OUTCOME_HORIZONS:
                    if r[f"s{h}"] is None and h <= el <= h + HORIZON_TOL_MIN:
                        r[f"s{h}"] = round(spot, 2)
                changed = True
            if r["first_touch"] in ("TARGET", "STOP") and not r.get("touch_alerted"):
                r["touch_alerted"] = True
                touched_now.append({**r, "outcome": r["first_touch"], "time": stamp, "resolved_time": stamp})
                changed = True
        past_close = (r["date"] < today) or (same_day and not live and is_trading_day(now) and _mins(now) >= 15 * 60 + 30)
        if past_close:
            if r["date"] in closes:
                r["sclose"] = round(float(closes[r["date"]]), 2)
            elif same_day:
                r["sclose"] = round(spot, 2)
            r["resolved"] = True
            if r["first_touch"] in ("TARGET", "STOP"):
                r["outcome"] = r["first_touch"]
            elif r["target"] is not None:
                r["outcome"] = "TIMEOUT"
            else:
                r["outcome"] = "RESOLVED"
            changed = True
            resolved_now.append({**r, "resolved_time": stamp})
    return changed, resolved_now, touched_now


def fav_points(r, key):
    v = r.get(key)
    return None if v is None else _dir(r["signal"]) * (v - r["entry"])


def scorecard_summary(recs):
    rows = []
    labels = {"s15": "15 min", "s30": "30 min", "s60": "60 min", "sclose": "Close"}
    for kind in ("VALIDATED", "REJECTED"):
        rs = [r for r in recs if r["kind"] == kind]
        for key, lab in labels.items():
            vals = [(fav_points(r, key), r["sd"]) for r in rs if r.get(key) is not None]
            n = len(vals)
            rows.append({"Type": kind, "Horizon": lab, "n": n,
                         "Hit % (moved the signal's way)": round(sum(1 for v, _ in vals if v > 0) / n * 100, 1) if n else None,
                         "Avg move (pts)": round(sum(v for v, _ in vals) / n, 1) if n else None,
                         "Avg move (σ)": round(sum(v / sd for v, sd in vals if sd) / n, 2) if n else None})
    return pd.DataFrame(rows)


def rejection_breakdown_table(recs):
    rejs = [r for r in recs if r["kind"] == "REJECTED"]
    rows = []
    for cat in sorted({rejection_category(r) for r in rejs}):
        sub = [r for r in rejs if rejection_category(r) == cat]
        v60 = [fav_points(r, "s60") for r in sub if r.get("s60") is not None]
        vcl = [fav_points(r, "sclose") for r in sub if r.get("sclose") is not None]
        rows.append({"Rejection reason": cat, "Logged": len(sub), "With 60-min mark": len(v60),
                     "Hit % (60 min)": round(sum(1 for v in v60 if v > 0) / len(v60) * 100, 1) if v60 else None,
                     "Avg move 60 min (pts)": round(sum(v60) / len(v60), 1) if v60 else None,
                     "Hit % (close)": round(sum(1 for v in vcl if v > 0) / len(vcl) * 100, 1) if vcl else None,
                     "Avg move to close (pts)": round(sum(vcl) / len(vcl), 1) if vcl else None})
    return pd.DataFrame(rows)


def touch_stats(recs):
    v = [r for r in recs if r["kind"] == "VALIDATED" and r["target"] is not None]
    n_t = sum(1 for r in v if r["outcome"] == "TARGET")
    n_s = sum(1 for r in v if r["outcome"] == "STOP")
    n_o = sum(1 for r in v if r["outcome"] == "TIMEOUT")
    n_open = sum(1 for r in v if not r["resolved"])
    done = n_t + n_s
    return {"target": n_t, "stop": n_s, "timeout": n_o, "open": n_open,
            "target_rate": round(n_t / done * 100, 1) if done else None}


def check_edge_table(recs, horizon="s60"):
    names = sorted({k for r in recs for k in r.get("checks", {})})
    rows = []
    for nm in names:
        ag, dg = [], []
        for r in recs:
            fav = fav_points(r, horizon)
            if fav is None:
                continue
            v = r.get("checks", {}).get(nm, 0) * _dir(r["signal"])
            (ag if v > 0 else dg if v < 0 else []).append(fav > 0)
        ha = round(sum(ag) / len(ag) * 100, 1) if ag else None
        hd = round(sum(dg) / len(dg) * 100, 1) if dg else None
        rows.append({"Check": nm, "n agreed": len(ag), "Hit % when agreed": ha, "n disagreed": len(dg),
                     "Hit % when disagreed": hd, "Edge (pts)": round(ha - hd, 1) if (ha is not None and hd is not None) else None})
    return pd.DataFrame(rows)


def group_table(recs, keyfn, horizon="s60"):
    buckets = {}
    for r in recs:
        fav = fav_points(r, horizon)
        if fav is None:
            continue
        buckets.setdefault(keyfn(r), []).append((fav, r["sd"]))
    rows = [{"Group": k, "n": len(v), "Hit %": round(sum(1 for f, _ in v if f > 0) / len(v) * 100, 1),
             "Avg move (σ)": round(sum(f / sd for f, sd in v if sd) / len(v), 2)} for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))]
    return pd.DataFrame(rows)


def cumulative_curve(recs):
    vals = []
    for r in sorted([x for x in recs if x["kind"] == "VALIDATED"], key=lambda x: x["ts"]):
        f = fav_points(r, "s60")
        f = f if f is not None else fav_points(r, "sclose")
        if f is not None and r["sd"]:
            vals.append(f / r["sd"])
    return pd.Series(vals).cumsum() if vals else pd.Series(dtype=float)


def scorecard_to_df(recs):
    rows = []
    for r in recs:
        row = {k: r.get(k) for k in ("id", "kind", "date", "time", "index", "expiry", "signal", "entry", "target", "sl", "rr",
                                    "confidence", "net", "lean", "status", "bias", "pcr", "atm_iv", "sd", "flow_conc", "exh_buyer",
                                    "exh_seller", "exh_own_flag", "tone", "news_score", "reason", "rejection_category", "gex_regime", "regime_stable", "zero_gamma_dist_sigma", "excess_basis", "rr_25", "rr_shift", "bf_25", "s15", "s30", "s60",
                                    "sclose", "first_touch", "mfe", "mae", "samples", "max_gap_min", "resolved", "outcome", "touch_alerted")}
        for nm, v in (r.get("checks") or {}).items():
            row[f"check:{nm}"] = v
        for key in ("s15", "s30", "s60", "sclose"):
            row[f"move_{key}"] = fav_points(r, key)
        rows.append(row)
    return pd.DataFrame(rows)


def _py(v):
    """Convert pandas/numpy scalars to plain JSON-safe Python values (NaN -> None)."""
    if v is None or isinstance(v, (str, list, dict, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    return v.item() if hasattr(v, "item") else v


def records_from_df(df):
    """Rebuild scorecard records from a CSV/XLSX export made by scorecard_to_df."""
    recs = []
    defaults = {"resolved": False, "outcome": "OPEN", "mfe": 0.0, "mae": 0.0, "samples": 0, "max_gap_min": 0.0,
                "last_sample_ts": None, "first_touch": None, "sd": 0.0, "target": None, "sl": None, "touch_alerted": False}
    for row in df.to_dict("records"):
        r = {k: _py(v) for k, v in row.items()}
        checks = {k[6:]: int(v) for k, v in r.items() if k.startswith("check:") and v is not None}
        r = {k: v for k, v in r.items() if not k.startswith("check:") and not k.startswith("move_")}
        if not r.get("id"):
            continue
        r["id"] = str(r["id"])
        r["checks"] = checks
        try:
            r["ts"] = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp()
        except Exception:
            continue
        for k, dv in defaults.items():
            if r.get(k) is None:
                r[k] = dv
        r["resolved"] = str(r["resolved"]).lower() in ("true", "1")
        r["touch_alerted"] = str(r["touch_alerted"]).lower() in ("true", "1")
        recs.append(r)
    return recs


def merge_scorecard(existing, incoming):
    have = {r["id"] for r in existing}
    added = [r for r in incoming if isinstance(r, dict) and r.get("id") and r["id"] not in have]
    existing.extend(added)
    existing.sort(key=lambda r: r["ts"])
    return len(added)


# ---------------- data health ----------------
def data_health(c):
    rows = []

    def add(name, status, detail):
        rows.append({"Check": name, "Status": status, "Detail": detail})

    now, m, df, spot = c["now"], c["m"], c["df"], c["spot"]
    live = market_open_now(now)

    add("Data gate", "OK" if c["data_ok"] else "WARN",
        "open: AI, snapshots and signals active" if c["data_ok"] else f"closed: {c['gate_reason']}. AI, snapshots and signals are paused")

    tok = c["token"]
    exp = token_expiry(tok)
    if not tok.startswith("eyJ"):
        add("Dhan token", "FAIL", "Does not look like a JWT access token")
    elif exp is None:
        add("Dhan token", "WARN", "Looks like a JWT but expiry could not be read")
    else:
        left = (exp - now).total_seconds() / 3600.0
        if left <= 0:
            add("Dhan token", "FAIL", f"Expired {-left:.1f} h ago")
        elif left < 2:
            add("Dhan token", "WARN", f"Expires in {left:.1f} h")
        else:
            add("Dhan token", "OK", f"Expires {exp:%d %b %H:%M} IST ({left:.0f} h left)")

    n_strikes = len(df)
    add("Option chain", "OK" if n_strikes >= 20 and spot > 0 else "FAIL", f"{n_strikes} strikes, spot {spot:,.2f}")

    w = near_window(spot, m["step"])
    near = df[(df["Strike"] >= spot - w) & (df["Strike"] <= spot + w)]
    if len(near):
        ltp_ok = ((near["CE_LTP"] > 0) & (near["PE_LTP"] > 0)).mean()
        add("Premiums (LTP)", "OK" if ltp_ok >= 0.8 else "WARN", f"{ltp_ok:.0%} of near-spot strikes have call/put LTP")
        pxk = (near["CE_PxChg"].notna() & near["PE_PxChg"].notna()).mean()
        add("Previous-close premiums", "OK" if pxk >= 0.6 else "WARN", f"{pxk:.0%} of near-spot strikes have prev-close premium (needed for writing check)")

    iv_ok = 5 <= m["atm_iv"] <= 60 and m["iv_gap"] <= 3
    add("ATM implied volatility", "OK" if iv_ok else "WARN", f"{m['atm_iv']:.1f}% (gap {m['iv_gap']:.1f})")

    if m.get("surface_ok"):
        add("Synthetic future", "OK", f"F_syn {m['syn_fut']:,.1f}, excess carry {m['excess_basis']:+.1f} pts")
    else:
        add("Synthetic future", "WARN", "No balanced near-ATM call/put mids")

    add("Previous close", "OK" if c["prev_close"] and c["prev_close"] > 0 else "WARN",
        f"{c['prev_close']:,.2f} ({c.get('prev_src', '')})")
    add("Daily candles", "OK" if c.get("candles") else "WARN",
        f"{len(c.get('candles') or [])} loaded" + (f" | {c['candle_err'][:80]}" if c.get("candle_err") else ""))
    add("Intraday 1-min candles", "OK" if c.get("intraday") else "WARN",
        f"{len(c.get('intraday') or [])} bars" + (f" | {c['intraday_err']}" if c.get("intraday_err") else " (needed for exact target/stop touches)"))
    add("Snapshot history", "OK", f"{len(c.get('hist') or [])} snapshot(s) stored today")
    _ai = c.get("ai") or {}
    if "error" in _ai and "signal" not in _ai:
        add("AI Council", "WARN" if not c["data_ok"] else "FAIL", str(_ai["error"])[:120])
    else:
        add("AI Council", "OK", f"Active ({_ai.get('_model', '')})")
    add("Holiday list", "OK" if NSE_HOLIDAYS else "WARN",
        f"{len(NSE_HOLIDAYS)} date(s) loaded" if NSE_HOLIDAYS else "NSE_HOLIDAYS secret is empty: exchange holidays are not detected")
    add("Clock", "OK", f"{now:%a %d %b %H:%M:%S} IST, market {'OPEN' if live else 'CLOSED'}")
    return rows


def esc(x):
    return html.escape(str(x if x is not None else "-"))


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL v13.5")
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
        prev_close = st.number_input("PREV CLOSE (0 = unknown)", min_value=0.0, value=0.0, step=float(info["step"]), key=f"prev_{idx_name}")

    st.markdown("##### Engine tuning")
    st.slider("NOISE FILTER %", 0.1, 2.0, PARAM_DEFAULTS["noise_pct"], 0.1, key="p_noise_pct",
              help="OI changes smaller than this % of near-spot OI are ignored.")
    st.slider("DOMINANCE THRESHOLD", 1.0, 5.0, PARAM_DEFAULTS["dominance"], 0.1, key="p_dominance",
              help="Winning side's force must be at least this multiple of the other side.")
    st.slider("MIN FAMILY AGREEMENT (1-3)", 1, 3, PARAM_DEFAULTS["min_confirm"], 1, key="p_min_confirm",
              help="Checks are grouped into 3 families (flow / positioning / volatility), one vote each. "
                   "A BUY/SELL needs this many net agreeing families.")
    st.slider("MIN AI CONFIDENCE %", 0, 90, PARAM_DEFAULTS["min_conf"], 5, key="p_min_conf",
              help="Signals with lower AI confidence are rejected.")
    st.toggle("REQUIRE NEAR-SPOT FLOW CONCENTRATION", value=PARAM_DEFAULTS["flow_conc_on"], key="p_flow_conc_on",
              help="Reject signals when too little of the chain's OI activity is near spot. This is not FII/DII data.")
    st.slider("MIN FLOW CONCENTRATION %", 10, 90, PARAM_DEFAULTS["flow_conc_min"], 5, key="p_flow_conc_min")
    st.toggle("EXHAUSTION CAN VETO SIGNALS", value=PARAM_DEFAULTS["exh_veto"], key="p_exh_veto",
              help="Off by default: the exhaustion score is an untested heuristic. Check it on your scorecard first.")

    st.markdown("##### Risk")
    st.number_input("CAPITAL (₹)", min_value=1000, value=PARAM_DEFAULTS["capital"], step=10000, key="p_capital")
    st.slider("MAX RISK PER SIGNAL %", 0.5, 5.0, PARAM_DEFAULTS["max_risk"], 0.5, key="p_max_risk")
    st.slider("LARGE-MOVE ALERT % (vs prev close)", 0.5, 5.0, PARAM_DEFAULTS["move_alert_pct"], 0.5, key="p_move_alert_pct")

    st.markdown("##### Analysis")
    live_news = st.toggle("LIVE NEWS FEED", value=True)
    news = st.text_area("EXTRA NEWS / MACRO NOTES", placeholder="Optional macro notes", height=80)
    horizon_label = st.selectbox("SCENARIO HORIZON", ["Intraday (0.25d)", "1 day", "2 days"], index=0)
    horizon_days = {"Intraday (0.25d)": 0.25, "1 day": 1.0, "2 days": 2.0}[horizon_label]
    iv_shock = st.slider("IV CRUSH / SPIKE SHOCK (%)", 5, 40, 15, step=5)
    use_skew = st.toggle("SPOT-VOL LINK IN SCENARIOS", value=True, key="use_skew")
    surface_check_on = st.toggle("COUNT SURFACE CHECK IN AGREEMENT SCORE", value=False, key="surface_check_on")
    USE_SURFACE_CHECK["on"] = surface_check_on
    _basis = st.selectbox("VOL TIME BASIS", ["Calendar (days/365)", "Trading (252 d x 375 min)"], index=0, key="time_basis")
    TIME_BASIS["mode"] = "calendar" if _basis.startswith("Calendar") else "trading"
    exh_lookback = st.slider("EXHAUSTION LOOKBACK (min)", 6, 45, 15, step=3)
    weekly_alert = st.toggle("WEEKLY-EXPIRY RISK PANEL", value=True, help="Shows extra warnings when days-to-expiry is 7 or less.")
    calendar_on = st.toggle("CALENDAR TERM-STRUCTURE (next expiry)", value=False, help="Fetches one extra option chain.")
    cross_on = st.toggle("CROSS-INDEX COMPARISON", value=False, help="Fetches the nearest-expiry chain of every index (cached 5 min).")

    st.markdown("---")
    st.markdown("### 🔔 FULLY AUTOMATED ALERTS")
    log_on = st.toggle("LOG & BROADCAST SIGNALS", value=True, key="log_on", help="Automatically logs signals and sends alerts to Telegram.")
    alerts_on = st.toggle("SEND TELEGRAM ALERTS", value=True, key="alerts_on")
    st.toggle("ALSO ALERT ON REJECTED SIGNALS", value=PARAM_DEFAULTS["alert_rejected"], key="p_alert_rejected",
              help="Off by default to avoid noise: only validated signals and outcomes are sent.")
    tg_token = st.text_input("TELEGRAM BOT TOKEN", type="password", value=get_secret("TELEGRAM_BOT_TOKEN", DEFAULT_TG_TOKEN), key="tg_token").strip()
    tg_chat = st.text_input("TELEGRAM CHAT ID", value=get_secret("TELEGRAM_CHAT_ID", DEFAULT_TG_CHAT), key="tg_chat").strip()
    log_hook = st.text_input("SCORECARD WEBHOOK URL (optional)", type="password", value=get_secret("LOG_WEBHOOK_URL", ""), key="log_hook").strip()

    if st.button("Send test alert", key="test_alert"):
        _res = send_alert("BBG terminal test alert. Telegram connected successfully!", tg_token, tg_chat)
        if not _res:
            st.warning("Enter a Telegram bot token and chat ID first.")
        for _c, _ok, _d in _res:
            (st.success if _ok else st.error)(f"{_c}: {'sent' if _ok else 'failed ' + _d}")

    force_run = st.button("FORCE AI RUN")
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="bbg")
    elif auto_refresh:
        st.caption("Auto refresh needs: pip install streamlit-autorefresh")


# ============================================================
# FETCH + ANALYZE
# ============================================================
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

if df.empty or spot == 0:
    st.warning("Option chain returned no data.")
    st.stop()

m = compute_metrics(df, spot, prev_close, expiry, info["step"])
now_ist = datetime.now(IST)
data_ok, gate_reason = data_gate(now_ist, m)

vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
hist = record_snapshot(m, f"{idx_name}|{expiry}", store=data_ok)
_rr0 = next((h.get("rr_25") for h in hist if h.get("rr_25") is not None), None)
m["rr_shift"] = round(m["rr_25"] - _rr0, 2) if _rr0 is not None else 0.0
m["exh"] = exhaustion(hist, m, prev_close, exh_lookback)
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
run_ai = force_run or (data_ok and changed and _retry_ok)
if run_ai:
    st.session_state["last_ai_attempt"] = datetime.now(IST)
    ai = run_council(m, vel, news_text, groq_key, groq_model)
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
    ai = valid_cached_verdict(_ai_key) or {
        "error": ("AI paused: data gate closed (" + gate_reason + "). Use FORCE AI RUN to analyse anyway; signals stay WAIT."
                  if not data_ok else "No fresh AI verdict (none yet, expired, or different index/expiry).")}
    t = st.session_state.get("last_ai_time")
    gate_label = (f"PAUSED ({gate_reason})" if not data_ok else f"CACHED {t.strftime('%H:%M:%S') if t else ''}")

ai_ok = not ("error" in ai and "signal" not in ai)
sig = build_signal(m, ai, data_ok, gate_reason) if ai_ok else None
scan, smeta = premium_scanner(m, horizon_days, iv_shock, skew=use_skew)

try:
    candles_pre, candle_err = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id), ""
except Exception as _e:
    candles_pre, candle_err = [], str(_e)

intraday, intraday_err = [], ""
if log_on and is_trading_day(now_ist):
    try:
        intraday = fetch_intraday_candles(info["scrip"], info["seg"], dhan_token, client_id)
    except Exception as _e:
        intraday_err = str(_e)[:100]

if "scorecard" not in st.session_state:
    st.session_state["scorecard"] = load_scorecard()
card = st.session_state["scorecard"]
_tone_val, _tone_hits = headline_tone(news_items) if news_items else (0, 0)
tone_now = _tone_val if _tone_hits >= NEWS_MIN_TONE_HITS else None
new_recs, _resolved, _touched = [], [], []
if log_on:
    sync_scorecard(card)
    _chg, _resolved, _touched = update_outcomes(card, idx_name, spot, now_ist, candles_pre, intraday)
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
    "now": now_ist, "m": m, "df": m["df"], "spot": spot, "token": dhan_token, "prev_close": prev_close,
    "prev_src": auto_src if (auto_prev and auto_val) else "manual entry", "candles": candles_pre, "candle_err": candle_err,
    "intraday": intraday, "intraday_err": intraday_err, "data_ok": data_ok, "gate_reason": gate_reason,
    "hist": hist, "news_items": news_items, "news_status": news_status, "live_news": live_news, "ai": ai,
    "groq_key": groq_key})

alert_cfg = {"enabled": alerts_on, "tg_token": tg_token, "tg_chat": tg_chat, "hook": log_hook}
alert_results = []
move = large_move_check(spot, prev_close, P("move_alert_pct"))
if alerts_on and tg_token and tg_chat:
    for _r in new_recs:
        if _r["kind"] == "VALIDATED" or P("alert_rejected"):
            alert_results += fire_alert(f"auto_sig_{_r['id']}", signal_alert_text(_r), 24 * 60, alert_cfg)
    for _r in _touched:
        alert_results += fire_alert(f"auto_touch_{_r['id']}", outcome_alert_text(_r), 24 * 60, alert_cfg)
    for _r in _resolved:
        if _r.get("outcome") in ("TARGET", "STOP") and not _r.get("touch_alerted"):
            alert_results += fire_alert(f"auto_res_{_r['id']}", outcome_alert_text(_r), 24 * 60, alert_cfg)
    if move and move["triggered"] and market_open_now(now_ist):
        alert_results += fire_alert(
            f"bigmove_{idx_name}_{now_ist.date().isoformat()}_{int(abs(move['pct']))}",
            f"⚠️ LARGE MOVE: {idx_name} is {move['pct']:+.2f}% vs previous close "
            f"({prev_close:,.0f} → {spot:,.0f}). Review any open positions. Analysis only, not trading advice.",
            0, alert_cfg)

# ============================================================
# DASHBOARD HEADER
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f}" + (f"  ({spot - prev_close:+,.2f})" if prev_close > 0 else ""))

_mkt = "OPEN" if market_open_now(now_ist) else "CLOSED"
st.caption(f"{now_ist.strftime('%a %d %b %Y %H:%M:%S')} IST | market {_mkt} | dealer gamma (assumed) {m['gex_regime']} | AI: {gate_label}")

if not data_ok:
    st.warning(f"DATA GATE CLOSED: {gate_reason}. Figures below may be stale or off-hours; the AI council, history snapshots "
               "and signal validation are paused (signals stay WAIT).")

if move and move["triggered"]:
    st.warning(f"Large move: {idx_name} is {move['pct']:+.2f}% vs previous close (alert threshold ±{P('move_alert_pct')}%). "
               "Consider reviewing open positions.")

_hc = {"OK": 0, "WARN": 0, "FAIL": 0}
for _row in health_rows:
    _hc[_row["Status"]] += 1
st.caption(f"DATA HEALTH: {_hc['OK']} OK · {_hc['WARN']} warn · {_hc['FAIL']} fail")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📊 Dashboard & Engine", "📐 Day Levels", "🧭 Exhaustion", "🎯 Premium Scanner",
    "🤖 AI Council & News", "📋 Option Chain", "📒 Scorecard", "🩺 Data Health", "📈 GEX & Skew"])

with tab1:
    status = m["status"]
    color, flash = ("#00e676", "flash-green") if "BULLISH" in status else ("#ff1744", "flash-red") if "BEARISH" in status else ("#ffab00", "flash-amber")

    st.markdown(f"""
    <div class="bbg-panel {flash}" style="border:2px solid {color};">
      <div class="bbg-title">Dual-Force Engine &nbsp;|&nbsp; dealer gamma (assumed): {esc(m['gex_regime'])}</div>
      <div class="bbg-big" style="color:{color};">{esc(status)} &nbsp;|&nbsp; {esc(m['engine_signal'])}</div>
      <div class="bbg-desc">{esc(m['desc'])}</div>
    </div>""", unsafe_allow_html=True)

    g1, g2, g3, g4 = st.columns(4)
    gamma_flip_value = m.get("gamma_flip", m.get("zero_gamma"))
    gamma_flip_valid = gamma_flip_value is not None and pd.notna(gamma_flip_value) and float(gamma_flip_value) != 0.0
    g1.metric(
        "GAMMA FLIP",
        f"{float(gamma_flip_value):,.0f}" if gamma_flip_valid else "—",
        f"{m['spot'] - float(gamma_flip_value):+,.0f} vs spot" if gamma_flip_valid else "No zero-gamma level detected",
        delta_color="off",
    )
    g2.metric("SYNTHETIC FUTURE", f"{m['syn_fut']:,.1f}", f"Basis {m['basis']:+.1f}", delta_color="off")
    g3.metric("25Δ RISK REVERSAL", f"{m['rr_25']:+.2f}%", f"25Δ CE {m['iv_25c']:.1f}%", delta_color="off")
    g4.metric("NET GEX (assumed, ₹ Cr/1%)", fmt_cr(m["net_gex"]), f"VEX {m['net_vex']:+,.3g}", delta_color="off")

    es = m["engine"]
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("CE EXITS", f"{es['ce_exit_pct']:.1f}%")
    d2.metric("PE BUILDS", f"{es['pe_build_pct']:.1f}%")
    d3.metric("PE EXITS", f"{es['pe_exit_pct']:.1f}%")
    d4.metric("CE BUILDS", f"{es['ce_build_pct']:.1f}%")

    w1, w2, w3 = st.columns(3)
    w1.metric("PUT BUILDS THAT WERE WRITING", f"{es['pe_write_share']:.0%}" if es["wr_known"] else "n/a")
    w2.metric("CALL BUILDS THAT WERE WRITING", f"{es['ce_write_share']:.0%}" if es["wr_known"] else "n/a")
    w3.metric("DATA GATE", "OPEN" if data_ok else "CLOSED", gate_reason if not data_ok else None, delta_color="off")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
    c2.metric("PCR", f"{m['pcr']:.2f}")
    c3.metric("EXPECTED MOVE (±)", f"{m['exp_move']:,.0f}")
    c4.metric("OI BATTLE", m["battle"])

    j1, j2, j3 = st.columns(3)
    j1.metric("LADDER IMBALANCE", f"{m['jobber']['ladder_imbalance']:+.3f}")
    j2.metric("POCKET SUPPORT", f"{m['jobber']['pocket_support']:,.0f}")
    micro_turn_text = html.escape(str(m["jobber"].get("micro_turn", "—")))
    j3.markdown(
        f"""<div style="border:1px solid #333;border-radius:5px;padding:10px 12px;min-height:78px;box-sizing:border-box;width:100%;overflow:hidden;">
          <div style="font-size:0.75rem;line-height:1.25;color:#ff9800;letter-spacing:0.04em;margin-bottom:8px;">MICRO-TURN</div>
          <div style="font-family:'Courier New',monospace;font-size:clamp(0.82rem,1.15vw,1.18rem);line-height:1.3;color:#fff;white-space:normal;overflow-wrap:anywhere;word-break:normal;">{micro_turn_text}</div>
        </div>""",
        unsafe_allow_html=True,
    )


with tab2:
    st.markdown("#### 📐 DAY MOVEMENT LEVELS")
    candles, candle_err2 = candles_pre, candle_err
    _after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    _today_ohlc = fetch_today_ohlc(info["scrip"], info["seg"], dhan_token, client_id) if _after_close else None
    dl = compute_day_levels(candles, m["spot"], daily_sigma(m), m, prev_close, _today_ohlc)
    st.caption(dl["basis"] + f" | σ = one trading-day move at ATM IV ({daily_sigma(m):,.0f} pts)")
    show_df(dl["table"], hide_index=True)


with tab3:
    st.markdown("#### 🧭 EXHAUSTION MONITOR")
    ex = m["exh"]
    if ex["ready"]:
        c1, c2 = st.columns(2)
        c1.metric("Buyer Exhaustion", f"{ex['buyer']['score']}/100", ex['buyer']['status'])
        c2.metric("Seller Exhaustion", f"{ex['seller']['score']}/100", ex['seller']['status'])
        st.caption("Heuristic score (equal-weighted binary signals). It only vetoes signals if you turn on "
                   "'EXHAUSTION CAN VETO SIGNALS' in the sidebar; validate it on your scorecard first.")
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

    # ---------- headlines ----------
    st.markdown("##### 📰 Live headlines")
    if not live_news:
        st.info("LIVE NEWS FEED toggle is off in the sidebar.")
    elif news_items:
        st.caption(
            (f"Keyword tone {_tone_val:+d} from {_tone_hits} keyword hits (crude word count, display only). "
             if _tone_hits >= NEWS_MIN_TONE_HITS else
             f"Keyword tone n/a: only {_tone_hits} keyword hit(s), too few to score. ")
            + f"{len(news_items)} headline(s) sent to the AI.")
        for it in news_items:
            title = it["title"].replace("[", "(").replace("]", ")")
            link = it["link"] if it["link"].startswith("http") else ""
            line = f"[{title}]({link})" if link else title
            st.markdown(f"- `{age_label(it['age_min'])}` **{it['source']}** — {line}")
    else:
        st.warning("No fresh headlines were returned (see feed status below).")
    with st.expander("Feed status / exact news text sent to AI"):
        for s in news_status:
            st.write(s)
        st.code(news_text)

    # ---------- council ----------
    st.markdown("##### 🧠 Council verdict")
    if "error" in ai and "signal" not in ai:
        st.warning(ai["error"])
    else:
        st.markdown(f"**Signal:** {sig['signal']} | **AI said:** {sig['ai_signal']} | **Confidence:** {sig['confidence']}%")
        if sig["note"]:
            st.caption(sig["note"])
        for label, key in (("Price action", "price_action_agent"), ("Order flow", "order_flow_agent"),
                           ("Volatility", "volatility_agent"), ("News", "news_agent"),
                           ("Risk officer", "final_approval")):
            st.markdown(f"**{label}:** {ai.get(key, '-')}")
        st.caption(f"AI news score: {ai.get('news_score', '-')}  |  model: {ai.get('_model', '-')}")
        fam = sig["families"]
        st.caption("Family votes (one per family): " + " | ".join(f"{k} {v:+d}" for k, v in fam.items())
                   + f" → net {sig['net']:+d} (needs +{int(P('min_confirm'))})")
        with st.expander("Option-chain agreement checks"):
            show_df(pd.DataFrame([{"Family": c["family"], "Check": c["name"], "Vote": c["v"],
                                   "Counted": c.get("counted", True), "Detail": c["detail"]} for c in sig["checks"]]),
                    hide_index=True)
        if sig["signal"] in ("BUY", "SELL"):
            _ks = kelly_sizing(sig, card, P("capital"), P("max_risk"))
            if _ks:
                k1, k2, k3 = st.columns(3)
                k1.metric("RISK BUDGET", f"{_ks['risk_pct']:.2f}%")
                k2.metric("CAPITAL AT RISK", f"₹{_ks['capital_at_risk']:,.0f}")
                k3.metric("TARGET / STOP", f"{sig['target']:,.0f} / {sig['sl']:,.0f}")
                st.caption(_ks["method"] + (f". {_ks['note']}" if _ks["note"] else "") +
                           ". This is the amount you would lose if the index hits the stop; convert it to lots using your option's own stop.")
        with st.expander("Raw AI JSON"):
            st.json(ai)


with tab6:
    st.caption(f"1σ range to expiry: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now(IST).strftime('%H:%M:%S')} IST")
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
        if len(recs) < MIN_SAMPLE_FOR_VERDICT:
            st.caption(f"Only {len(recs)} record(s): need about {MIN_SAMPLE_FOR_VERDICT}+ before trusting any edge stat.")
        st.caption("Outcomes of other indices only update while that index is selected, and only while this app is open "
                   "in a browser tab (Streamlit does not run in the background).")
        _ce = check_edge_table(recs)
        if not _ce.empty:
            st.markdown("##### Check Edge (60 min)")
            show_df(_ce, hide_index=True)
        _ex_rows = [r for r in recs if r.get("exh_own_flag") is not None]
        if _ex_rows:
            st.markdown("##### Exhaustion flag vs 60-min outcome (validate before enabling the veto)")
            show_df(group_table(_ex_rows, lambda r: r.get("exh_own_flag") or "none"), hide_index=True)
        st.markdown("##### Rejection Reason Breakdown")
        show_df(rejection_breakdown_table(recs), hide_index=True)
        st.markdown("##### Recent Records")
        show_df(scorecard_to_df(recs).tail(15), hide_index=True)
    else:
        st.info("No scorecard records yet.")

    e1, e2 = st.columns(2)
    if recs:
        e1.download_button("📄 Scorecard CSV", scorecard_to_df(recs).to_csv(index=False), file_name="signal_scorecard.csv", mime="text/csv")
        e2.download_button("💾 Scorecard backup (JSON)", json.dumps(recs), file_name="signal_scorecard_backup.json", mime="application/json")
    _up = st.file_uploader("Merge scorecard backup or earlier export", type=["json", "csv", "xlsx"], key="sc_upload")
    if _up is not None and st.button("♻ MERGE BACKUP", key="sc_merge"):
        try:
            _nm = _up.name.lower()
            _inc = json.load(_up) if _nm.endswith(".json") else records_from_df(pd.read_csv(_up) if _nm.endswith(".csv") else pd.read_excel(_up))
            _added = merge_scorecard(recs, _inc)
            save_scorecard(recs)
            st.success(f"Merged {_added} new record(s).")
        except Exception as e:
            st.error(f"Could not merge: {e}")


with tab8:
    st.markdown("#### 🩺 DATA HEALTH")
    show_df(pd.DataFrame(health_rows), hide_index=True)


with tab9:
    st.markdown("#### 📈 DEALER POSITIONING & VOLATILITY SURFACE")
    st.caption("Dealer signs assume dealers are long calls / short puts. That is unverified for NSE, so treat this as a model view, not fact.")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("REGIME (assumed)", m["gex_regime"])
    g2.metric("ZERO-GAMMA LEVEL", f"{m['zero_gamma']:,.0f}" if m["zero_gamma"] else "none")
    g3.metric("CROSSOVER", f"{m['crossover']:,.0f}" if m["crossover"] else "—")
    g4.metric("NET GEX (₹ Cr per 1% move)", fmt_cr(m["net_gex"]))

    st.markdown("##### Round-number OI levels")
    _rn = round_number_levels(m["df"], spot, info["step"])
    if _rn.empty:
        st.caption("No round-number strikes in range.")
    else:
        show_df(_rn, hide_index=True)

    st.markdown("##### Theta decay profile")
    _th = theta_profile(m)
    t1, t2, t3 = st.columns(3)
    t1.metric("EXPECTED 1-DAY STRADDLE DECAY", f"{_th['decay_pct']:.1f}%", _th["label"], delta_color="off")
    t2.metric("ATM STRADDLE", f"{_th['straddle']:,.1f}")
    t3.metric("≈ DECAY PER DAY (pts)", f"{_th['decay_pts']:,.1f}")
    st.caption("Uses the square-root-of-time scaling of ATM option value; a model estimate, not a live quote.")

    st.markdown("##### Near-spot flow concentration")
    st.metric("SHARE OF CHAIN OI ACTIVITY NEAR SPOT", f"{m['flow_conc']:.1f}%")
    st.caption("Gross OI change near spot divided by gross OI change across the whole chain. "
               "It shows where activity is concentrated, not whether the participants are institutions.")

    if weekly_alert:
        st.markdown("##### Expiry risk")
        _wk = weekly_expiry_analysis(m)
        if not _wk["active"]:
            st.caption(f"{_wk['dte']} days to expiry: no near-expiry warnings.")
        else:
            _fn = st.error if _wk["risk_level"] == "HIGH" else st.warning if _wk["risk_level"] == "MODERATE" else st.info
            _fn(f"Expiry risk {_wk['risk_level']} (DTE {_wk['dte']})" + ("".join(f"\n- {w}" for w in _wk["warnings"])))

    if calendar_on:
        st.markdown("##### Calendar term-structure")
        try:
            _cv, _cerr = calendar_view(m, info, expiries, expiry, dhan_token, client_id, prev_close)
        except Exception as e:
            _cv, _cerr = None, str(e)[:120]
        if _cv is None:
            st.info(_cerr)
        else:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(f"ATM IV {_cv['front']}", f"{_cv['iv1']:.1f}%")
            k2.metric(f"ATM IV {_cv['back']}", f"{_cv['iv2']:.1f}%")
            k3.metric("FRONT − BACK", f"{_cv['spread']:+.2f}")
            k4.metric("FORWARD IV BETWEEN", f"{_cv['fwd_iv']:.1f}%" if _cv["fwd_iv"] else "n/a")
            st.caption(f"{_cv['structure']} | gamma {_cv['gex1']} vs {_cv['gex2']} (assumed) | PCR {_cv['pcr1']:.2f} vs {_cv['pcr2']:.2f}. "
                       "Descriptive only; it is not a trade recommendation.")

    if cross_on:
        st.markdown("##### Cross-index comparison (nearest expiry of each index, cached 5 min)")
        _cx = cross_index_cached(dhan_token, client_id)
        show_df(_cx, hide_index=True)
        if "Bias" in _cx.columns:
            _b = _cx["Bias"].dropna()
            if len(_b) >= 2:
                st.caption("Biases aligned across indices." if _b.nunique() == 1 else "Biases diverge across indices.")
