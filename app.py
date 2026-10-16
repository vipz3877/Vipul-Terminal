"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.0
Analysis terminal: Dual-Force engine + Jobber microstructure + option-chain cross-check + exhaustion monitor + live news
+ day levels + premium scanner + AI council + signal scorecard + data health + alerts
+ DEALER POSITIONING & SURFACE: signed GEX, aggregate zero-gamma level, synthetic future vs fair carry, 25-delta skew, vanna/charm.

v13.0 merges the dealer-positioning engine from the "v12.1" script into the full v9.0 terminal and fixes what the review found:
 - no credentials in code (secrets / sidebar only); no features dropped (exports, merge, health checks, alerts, panels are all kept)
 - basis is compared with the fair carry (it is mostly cost of carry); the surface check is OBSERVE-ONLY until the scorecard shows edge
 - gamma regime has a neutral band and a window-stability test; a true aggregate zero-gamma level is added next to the per-strike crossover
 - scorecard logs the regime / surface values with every signal, breaks rejections down by reason, and can import an exported CSV/XLSX
No order execution: this app places no orders. Analysis only, not trading advice.
"""

import os
import re
import base64
import json
import math
import html
import time
import hashlib
import io
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v13.0", layout="wide")

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
# OpenAI open-weight models hosted on Groq (llama-3.3-70b-versatile is no longer used)
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

BASE_URL = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{BASE_URL}/optionchain"
EXPIRY_URL = f"{BASE_URL}/optionchain/expirylist"
PROFILE_URL = f"{BASE_URL}/profile"

YEAR_DAYS = 365
TIME_BASIS = {"mode": "calendar"}   # "calendar": T = time/365 (how most IV vendors quote); "trading": 252 days x 375 min
SKEW_COEF = 0.5                      # IV change per 1 sigma of spot move = SKEW_COEF x IV shock (spot down -> IV up)
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0   # Dhan option chain limit: ~1 request / 3 sec
MIN_RR = 1.0              # minimum reward:risk to accept a BUY/SELL
MIN_FLOW_PCT = 2.0        # a side's OI change must be >= this % of its near-spot total OI to count
NOISE_PCT = 0.5           # ignore single strikes whose change is < this % of that side's near-spot OI
DOMINANCE = 1.5           # winning force must be >= this multiple of the opposing force
MIN_CONFIRM = 2           # option-chain checks must net-agree with a BUY/SELL by at least this much
SL_BUFFER = 35            # points beyond the OI wall for the stop-loss
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
RISK_FREE = 0.065         # annual rate used in the Black-Scholes repricer
MIN_PREMIUM = 5.0         # ignore options trading below this premium (points)

AI_RETRY_SEC = 60             # minimum gap between AI calls after a failure

# ---- live news ----
NEWS_MAX_AGE_H = 24       # headlines older than this are ignored
NEWS_TOP_N = 10           # headlines sent to the AI / shown on screen
NEWS_MIN_GAP_SEC = 600    # a news change can re-trigger the AI at most this often
# Optional extra RSS feeds, comma-separated, e.g. in secrets: NEWS_EXTRA_FEEDS = "https://.../rss.xml,https://..."
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
# DATA
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
    except ImportError:        # e.g. Windows: fall back to a per-session throttle
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
    enforce_global_api_throttle()      # Dhan: about 1 option-chain request per 3 s

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
    """Previous close = close of the last COMPLETED daily candle before today (IST). Falls back to LTP - net_change.
    ohlc.close is deliberately NOT used: during/after the session it equals today's last price."""
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
    if len(fresh) < 3:   # quiet period / weekend: widen to 72h so the panel is not empty
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
    """Crude keyword tone, -100..+100. A sanity backstop next to the AI's reading, not a signal on its own."""
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
        return max(1, (exp_date - datetime.now(IST).date()).days)
    except ValueError:
        return 1


# ============================================================
# ANALYSIS & JOBBER MICROSTRUCTURE ENGINE
# ============================================================
def years_to_expiry(expiry_str, now=None):
    """
    Time to expiry (15:30 IST on the expiry date) in years, continuous so the decay is right on expiry day.
    calendar: seconds / (365 x 86400), the convention most IV vendors use.
    trading : remaining session minutes / (252 x 375). Use it only if your IV source is annualised on trading time.
    """
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

    if prev_close and prev_close > 0:           # unknown previous close: skip the day-change term
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
    """
    Dual-Force inputs, computed on NEAR-SPOT strikes only and expressed relative to OI.
    Quantities are summed over strikes whose change exceeds NOISE_PCT of that side's near-spot OI.
    Percentages are |quantity| / that side's total near-spot OI * 100.
    """
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

    # Net flow per side (positive = that side is adding OI)
    out["ce_net_flow"] = out["ce_build"] + out["ce_exits"]
    out["pe_net_flow"] = out["pe_build"] + out["pe_exits"]

    # Bullish force = puts building + calls leaving; bearish force = calls building + puts leaving
    out["bull_force"] = out["pe_build_pct"] + out["ce_exit_pct"]
    out["bear_force"] = out["ce_build_pct"] + out["pe_exit_pct"]
    hi, lo = max(out["bull_force"], out["bear_force"]), min(out["bull_force"], out["bear_force"])
    out["force_ratio"] = (hi / lo) if lo > 0 else (999.0 if hi > 0 else 1.0)
    return out


def detect_gamma_blast(near):
    """
    A 'blast' needs the exiting side to be NET unwinding (capitulating), not merely to show some gross exits while
    still adding more OI than it removes. One-sided heavy writing without capitulation is labelled a BUILD instead.
    """
    e = engine_sums(near)
    if near is None or near.empty:
        return "BALANCED ACCUMULATION", "WAIT", "No near-spot strikes available."

    base_bull = e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] >= MIN_FLOW_PCT
    base_bear = e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] >= MIN_FLOW_PCT
    bull_cap = e["ce_net_flow"] < 0          # call writers net unwinding
    bear_cap = e["pe_net_flow"] < 0          # put writers net unwinding
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
    """Nearest meaningful OI walls around spot: strongest CE strike above, strongest PE strike below,
    each at least one strike away and within 1.5x the expected move (ties within 20% go to the closer strike).
    Falls back to the global walls."""
    reach = max(exp_move * 1.5, step * 4)
    res_pool = df[(df["Strike"] >= spot + step) & (df["Strike"] <= spot + reach) & (df["CE_OI"] > 0)]
    sup_pool = df[(df["Strike"] <= spot - step) & (df["Strike"] >= spot - reach) & (df["PE_OI"] > 0)]

    def pick(pool, col, chg_col, fallback, ok):
        if not pool.empty:
            # among strikes with OI >= 80% of the strongest in range, take the one closest to spot
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
    atm_iv = sum(ivs) / len(ivs) if ivs else 12.0          # average CE/PE IV (they should match by put-call parity)
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
    jobber = calculate_jobber_microstructure(df, spot)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "pcr": pcr,
        "dce": dce, "dpe": dpe, "battle": interpret_oi_battle(dce, dpe),
        "atm_iv": atm_iv, "dte": dte, "exp_move": round(exp_move, 2),
        "t_years": t_years, "days_left": t_years * (365.0 if TIME_BASIS["mode"] == "calendar" else 252.0),
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "jobber": jobber, "engine": engine, "struct": struct,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
        **{k: surf[k] for k in SURFACE_KEYS},
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
        d_ce, d_pe = c_ce - p_ce, c_pe - p_pe          # FIXED (was c_ce - p_pe)
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
- Surface: synthetic future {m['syn_fut']:.1f} vs fair {m['fair_fwd']:.1f} (excess {m['excess_basis']:+.1f} pts, mostly noise below {BASIS_MIN_PTS:.0f}), 25d RR {m['rr_25']:+.2f} ({m.get('rr_shift', 0.0):+.2f} vs open), 25d BF {m['bf_25']:+.2f}
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
    # Try the selected model first, then the other OpenAI model as fallback
    candidates = [model] + [x for x in GROQ_MODELS if x != model]
    last_err = ""
    for mdl in candidates:
        try:
            resp = client.chat.completions.create(
                model=mdl,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2500,  # gpt-oss spends tokens on reasoning, so keep headroom
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
                continue  # try next model
            break
    return {"error": f"Groq call failed: {last_err}"}


# ============================================================
# SIGNAL VALIDATION
# ============================================================
def chain_checks(m):
    """Independent option-chain reads. v = +1 bullish, -1 bearish, 0 neutral."""
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

    if m["dte"] > 2:        # max-pain pull is weak far from expiry (it read bearish all day on a +2.2 sigma up-day)
        add("Spot vs max pain", 0, f"{m['dte']}d to expiry: max pain ignored")
    else:
        mp_gap = m["spot"] - m["max_pain"]
        add("Spot vs max pain", -1 if mp_gap > m["step"] else 1 if mp_gap < -m["step"] else 0,
            f"spot {m['spot']:,.0f} vs max pain {m['max_pain']:,.0f} ({mp_gap:+,.0f})")

    exb = m.get("excess_basis", 0.0)
    thr = max(BASIS_MIN_PTS, 0.0004 * m["spot"])
    v = 1 if (m.get("surface_ok") and exb >= thr) else -1 if (m.get("surface_ok") and exb <= -thr) else 0
    checks.append({"name": SURF_CHECK_NAME, "v": v, "counted": USE_SURFACE_CHECK["on"],
                   "detail": f"F_syn {m.get('syn_fut', 0):,.1f} vs fair {m.get('fair_fwd', 0):,.1f} (excess {exb:+.1f}, needs ±{thr:.0f}) | RR25 {m.get('rr_25', 0):+.2f}"
                             + ("" if USE_SURFACE_CHECK["on"] else " [observe only]")})
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
    lean = sum(c["v"] for c in checks if c.get("counted", True))   # + bullish / - bearish (observe-only checks excluded)
    net = lean * direction                                   # agreement with the AI's call
    entry = m["spot"]
    res, sup = m["struct"]["res"], m["struct"]["sup"]
    notes = []
    sig, target, sl, rr = ai_sig, None, None, None

    if sig != "WAIT":
        # 1) Does the option chain back the call?
        if net < MIN_CONFIRM:
            notes.append(f"{sig} rejected: option-chain agreement {net:+d} is below the required +{MIN_CONFIRM}.")
            sig = "WAIT"

    exh = m.get("exh") or {}
    if sig != "WAIT" and exh.get("ready"):
        # 1b) Is the trade joining a trend that is already exhausted?
        mine = exh["buyer"] if sig == "BUY" else exh["seller"]
        if mine["flag"] == "CONFIRMED":
            notes.append(f"{sig} rejected: {'buyer' if sig == 'BUY' else 'seller'} exhaustion confirmed "
                         f"({mine['score']}/100), the move you would join is running out of fuel.")
            sig = "WAIT"
        elif mine["flag"] == "WATCH":
            notes.append(f"Caution: {'buyer' if sig == 'BUY' else 'seller'} exhaustion building ({mine['score']}/100).")

    if sig != "WAIT":
        # 2) Levels from the nearest OI walls
        if res is None or sup is None:
            notes.append(f"{sig} rejected: no usable OI wall on one side of spot.")
            sig = "WAIT"
        elif sig == "BUY":
            target, sl = res["level"], sup["level"] - SL_BUFFER
        else:
            target, sl = sup["level"], res["level"] + SL_BUFFER

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
            "direction": direction}


# ============================================================
# DAY MOVEMENT LEVELS (sigma bands, pivots, ATR) + OI confluence
# ============================================================
def _candle_date(ts):
    """Dhan v2 returns epoch seconds; guard against the older 1980-based epoch."""
    today = datetime.now(IST).date()
    d = datetime.fromtimestamp(float(ts), IST).date()
    if d < today - timedelta(days=400):
        d = datetime.fromtimestamp(float(ts) + 315532800, IST).date()
    return d


@st.cache_data(ttl=600, show_spinner=False)
def fetch_daily_candles(scrip, seg, token, client_id):
    """Daily OHLC for the index (last ~35 days) from POST /v2/charts/historical. Returns list of dicts."""
    today = datetime.now(IST).date()
    last_err = None
    for to_date in (today + timedelta(days=1), today):      # toDate may be exclusive; fall back if rejected
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
    """Sigma bands around the reference close, classic pivots, ATR(14), today's open bands, plus OI confluence."""
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()
    after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    synthetic = False
    if after_close and now_ist.weekday() < 5 and today_ohlc and not any(c["date"] == today for c in candles):
        candles = list(candles) + [{"date": today, **today_ohlc}]      # daily candle for today is usually not published yet
        synthetic = True
    done = [c for c in candles if c["date"] < today or (c["date"] == today and after_close)]
    live = next((c for c in candles if c["date"] == today and not after_close), None)

    ref = done[-1] if done else None
    levels = []                      # (name, price, kind)

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

    # OI reference levels for confluence
    oi_refs = {"Call wall": m["call_wall"], "Put wall": m["put_wall"], "Max pain": m["max_pain"],
               "Gamma strike": m["gamma_strike"]}
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
    return {"table": tbl, "ref_close": c, "basis": basis, "atr": atr, "sd": sd, "tol": tol,
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
    return os.path.join(tempfile.gettempdir(), f"bbg_hist_{datetime.now(IST).date().isoformat()}_{safe}.json")


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
    """Append one snapshot per refresh (>= HIST_MIN_GAP_SEC apart); survives browser refresh via a temp file."""
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
    """OI added / removed on the SAME strikes between two snapshots, near the current spot."""
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
    """
    Scores buyer exhaustion (uptrend running out of fuel) and seller exhaustion (downtrend running out of fuel)
    from 7 signals each, using snapshots from the last `lb_min` minutes. Needs history; cannot predict a reversal.
    """
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
        # sgn=+1: buyer exhaustion (uptrend). sgn=-1: seller exhaustion (downtrend).
        sig = []
        name = "call" if sgn > 0 else "put"

        # 1 wall absorption
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

        # 2 IV rolls over while price sits at its extreme
        extreme = (lb_high - spot if sgn > 0 else spot - lb_low) / sd
        if extreme <= 0.3 and iv_off_peak >= 3:
            sig.append(("IV rollover at extreme", 1.0, f"IV {iv_off_peak:.1f}% off its window peak while price is at the extreme"))
        elif extreme <= 0.3 and iv_off_peak >= 1.5:
            sig.append(("IV rollover at extreme", 0.5, f"IV {iv_off_peak:.1f}% off peak"))
        else:
            sig.append(("IV rollover at extreme", 0.0, f"IV {iv_chg:+.1f}% over window, {iv_off_peak:.1f}% off peak"))

        # 3 net OI flow edge flips against the trend
        ed = -sgn * edge_d   # buyer exhaustion wants edge falling; seller exhaustion wants edge rising
        sig.append(("Flow edge flipping", 1.0 if ed >= 3 else 0.5 if ed >= 1.5 else 0.0,
                    f"PE-vs-CE net flow edge {edge_d:+.1f} pts over window"))

        # 4 PCR turning against the trend
        pd_ = -sgn * pcr_d
        sig.append(("PCR turning", 1.0 if pd_ >= 0.03 else 0.5 if pd_ >= 0.015 else 0.0, f"PCR {pcr_d:+.3f} over window"))

        # 5 fresh writing leaning the other way near spot
        mine, other = (ce_b, pe_b) if sgn > 0 else (pe_b, ce_b)
        mq, oq = (fl["ce_build"], fl["pe_build"]) if sgn > 0 else (fl["pe_build"], fl["ce_build"])
        if mine >= 1 and mq >= 1.5 * oq:
            sig.append((f"Fresh {name} writing", 1.0, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite (near spot, {age:.0f}m)"))
        elif mq > oq and mine >= 0.5:
            sig.append((f"Fresh {name} writing", 0.5, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite"))
        else:
            sig.append((f"Fresh {name} writing", 0.0, f"{name} builds {mine:.1f}% vs {other:.1f}% opposite"))

        # 6 max pain pull, only meaningful close to expiry
        mp = sgn * (spot - now["max_pain"]) / sd
        if now["dte"] > 2:
            sig.append(("Max pain pull", 0.0, f"{now['dte']}d to expiry, pull is weak"))
        else:
            sig.append(("Max pain pull", 1.0 if mp >= 0.5 else 0.5 if mp >= 0.3 else 0.0,
                        f"spot {mp:+.2f}σ beyond max pain {now['max_pain']:,.0f}"))

        # 7 momentum stall: first half of window moved with the trend, second half did not
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
# PREMIUM POTENTIAL SCANNER (scenario repricing, not a forecast)
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
    """Premium after a scenario, anchored to today's traded price (ratio of two model prices)."""
    base = bs_price(S0, K, T0, iv0, is_call)
    if base < 1e-6:
        return None
    return ltp * bs_price(S1, K, T1, iv1, is_call) / base


def premium_scanner(m, horizon_days=0.25, iv_shock=15.0, skew=True):
    """
    For each near-spot CE/PE: reprice under (a) favourable spot moves of 0.5/1/1.5 daily-sigma, (b) a move to the
    nearest OI wall, (c) 1-sigma move with IV crush / IV spike, (d) a wrong-way 0.5-sigma move.
    Ranks by a heuristic potential score. Returns (DataFrame, meta).
    """
    df, S0, step = m["df"], m["spot"], m["step"]
    atm_iv = m["atm_iv"]
    cal = TIME_BASIS["mode"] == "calendar"
    basis_days = 365.0 if cal else 252.0
    min_t = 10.0 / (basis_days * (1440.0 if cal else 375.0))        # 10 minutes
    T0 = max(m.get("t_years", m["dte"] / 365.0), min_t)
    T1 = max(T0 - horizon_days / basis_days, min_t)
    # scenario sigma: one daily move, but never more than the option's remaining life allows (expiry day)
    sd = min(daily_sigma(m), S0 * atm_iv / 100.0 * math.sqrt(T0))
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
            # spot-vol link: IV falls when spot rises and rises when spot falls (index leverage effect), scaled by move size
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

            # spot needed to double the premium (flat IV), searched out to 4 daily sigmas
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
            money = (K - S0) if is_call else (S0 - K)               # >0 means OTM by that many points
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

    # --- heuristic potential score ---
    out["vol_pct"] = out["Vol"].rank(pct=True)
    fuel = out["OIchg"].clip(lower=0)
    out["fuel_pct"] = fuel / fuel.max() if fuel.max() > 0 else 0.0     # fresh writing = trapped writers if price runs through
    reward = 0.5 * (out["x1σ"] - 1) + 0.5 * (out["x1σ crush"] - 1)     # reward that survives an IV crush
    loss = (1 - out["xWrong"]).clip(lower=0, upper=1)
    liq = out["Spread%"].apply(lambda v: 0.5 if pd.isna(v) else 1.0 if v <= 3 else 0.7 if v <= 6 else 0.4 if v <= 12 else 0.15)
    prob = (2 * out["Delta"]).clip(upper=1.0) ** 0.5                    # rough odds of finishing in the money
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
    """st.dataframe that works on both old and new Streamlit width APIs."""
    try:
        st.dataframe(df, width="stretch", **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, **kw)


@st.cache_data(ttl=120, show_spinner=False)
def fetch_today_ohlc(scrip, seg, token, client_id):
    """Today's O/H/L/C from the market quote. Best effort; only used after the close when the daily candle is missing."""
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
# TOKEN EXPIRY, ALERTS, SIGNAL SCORECARD, DATA HEALTH (v9.0)
# ============================================================
SCORECARD_FILE = os.path.join(tempfile.gettempdir(), "bbg_scorecard_v1.json")
ALERT_FILE = os.path.join(tempfile.gettempdir(), "bbg_alerts_sent_v1.json")
SIGNAL_DEDUP_MIN = 30        # a same-direction signal is logged at most once per this many minutes
OUTCOME_HORIZONS = (15, 30, 60)
HORIZON_TOL_MIN = 10         # a horizon sample counts only if taken within this many minutes after the horizon
MIN_SAMPLE_FOR_VERDICT = 30  # below this many resolved signals the scorecard says "too few to conclude"


def _mins(now):
    return now.hour * 60 + now.minute


def is_trading_day(now):
    return now.weekday() < 5


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f)
    except Exception:
        pass


def token_expiry(token):
    """Expiry (IST) read from the JWT 'exp' claim, or None when the token is not a decodable JWT."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = data.get("exp")
        return datetime.fromtimestamp(float(exp), IST) if exp else None
    except Exception:
        return None


# ---------------- alerts ----------------
def _scrub(text, *secrets):
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def send_alert(text, tg_token="", tg_chat="", webhook=""):
    """Send to every configured channel. Returns [(channel, ok, detail)]. Never raises and never echoes secrets."""
    out = []
    if tg_token and tg_chat:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                              json={"chat_id": tg_chat, "text": text, "disable_web_page_preview": True}, timeout=8)
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
    """Send once per cooldown window per key (state kept in a file so reruns and restarts do not re-send)."""
    if not cfg.get("enabled") or not (cfg.get("tg_token") and cfg.get("tg_chat") or cfg.get("hook")):
        return []
    state = _load_json(ALERT_FILE, {})
    now_ts = time.time()
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
    return (f"📣 {r['index']} {r['signal']} @ {r['entry']:,.0f}\nTarget {tgt} | Stop {sl} | R:R {rr}\n"
            f"AI confidence {r['confidence']}% | chain agreement {r['net']:+d} | {r['status']}\n"
            f"Own-side exhaustion: {r.get('exh_own_flag') or 'none'} | news tone {r['tone']:+d}\n"
            f"{r['time']} IST. Analysis only, not trading advice.")


def post_webhook(url, payload):
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception:
        pass


# ---------------- signal scorecard ----------------
def load_scorecard():
    recs = _load_json(SCORECARD_FILE, [])
    return recs if isinstance(recs, list) else []


def save_scorecard(recs):
    _save_json(SCORECARD_FILE, recs)


def _dir(sig):
    return 1 if sig == "BUY" else -1


def make_record(kind, idx_name, expiry, m, sig, ai, tone, now):
    direction = sig["signal"] if kind == "VALIDATED" else sig["ai_signal"]
    ex = m.get("exh") or {}
    ready = bool(ex.get("ready"))
    own = (ex["buyer"] if direction == "BUY" else ex["seller"]) if ready else None
    return {
        "id": hashlib.md5(f"{now.timestamp()}{idx_name}{kind}{direction}".encode()).hexdigest()[:8],
        "kind": kind, "ts": now.timestamp(), "date": now.date().isoformat(), "time": now.strftime("%H:%M:%S"),
        "index": idx_name, "expiry": str(expiry)[:10], "signal": direction, "entry": round(float(m["spot"]), 2),
        "target": sig["target"] if kind == "VALIDATED" else None, "sl": sig["sl"] if kind == "VALIDATED" else None,
        "rr": sig["rr"] if kind == "VALIDATED" else None, "confidence": sig["confidence"], "net": sig["net"],
        "lean": sig["lean"], "checks": {c["name"]: c["v"] for c in sig["checks"]},
        "status": m["status"], "bias": m["score"], "pcr": round(float(m["pcr"]), 3), "atm_iv": round(float(m["atm_iv"]), 2),
        "sd": round(daily_sigma(m), 1),
        "gex_regime": m.get("gex_regime"), "regime_stable": m.get("regime_stable"), "net_gex": m.get("net_gex"),
        "zero_gamma_dist_sd": (round((m["zero_gamma"] - m["spot"]) / daily_sigma(m), 2) if m.get("zero_gamma") else None),
        "excess_basis": m.get("excess_basis"), "rr_25": m.get("rr_25"), "rr_shift": m.get("rr_shift"), "bf_25": m.get("bf_25"),
        "rejection_category": classify_rejection(sig["note"]) if kind == "REJECTED" else "None (validated)",
        "exh_buyer": ex["buyer"]["score"] if ready else None, "exh_seller": ex["seller"]["score"] if ready else None,
        "exh_own_flag": own["flag"] if own else None, "tone": tone, "news_score": ai.get("news_score"),
        "reason": sig["note"] if kind == "REJECTED" else "",
        "s15": None, "s30": None, "s60": None, "sclose": None, "first_touch": None, "mfe": 0.0, "mae": 0.0,
        "samples": 0, "last_sample_ts": None, "max_gap_min": 0.0, "resolved": False, "outcome": "OPEN",
    }


def maybe_log_signal(recs, idx_name, expiry, m, sig, ai, tone, now):
    """Log a validated signal, or an AI signal the validation rejected (so the filter itself can be judged)."""
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


def update_outcomes(recs, idx_name, spot, now, candles):
    """
    Called on every refresh. Samples the spot against open records (excursions, target/stop first touch, 15/30/60-minute
    marks) and finalises them after the close. Returns (changed, newly_resolved). Outcomes are only as fine as the
    refresh interval; days when the app was closed are never guessed from the daily high/low (it may pre-date the signal).
    """
    changed, resolved_now = False, []
    today = now.date().isoformat()
    live = market_open_now(now)
    closes = {c["date"]: c["c"] for c in (candles or [])}
    for r in recs:
        if r["resolved"] or r["index"] != idx_name:
            continue
        d = _dir(r["signal"])
        el = (now.timestamp() - r["ts"]) / 60.0
        same_day = r["date"] == today
        if same_day and live:
            fav = d * (spot - r["entry"])
            if r["last_sample_ts"]:
                r["max_gap_min"] = max(r["max_gap_min"], (now.timestamp() - r["last_sample_ts"]) / 60.0)
            r["last_sample_ts"] = now.timestamp()
            r["samples"] += 1
            r["mfe"] = round(max(r["mfe"], fav), 2)
            r["mae"] = round(max(r["mae"], -fav), 2)
            if r["first_touch"] is None and r["target"] is not None and r["sl"] is not None:
                if (d > 0 and spot >= r["target"]) or (d < 0 and spot <= r["target"]):
                    r["first_touch"] = "TARGET"
                elif (d > 0 and spot <= r["sl"]) or (d < 0 and spot >= r["sl"]):
                    r["first_touch"] = "STOP"
            for h in OUTCOME_HORIZONS:
                if r[f"s{h}"] is None and h <= el <= h + HORIZON_TOL_MIN:
                    r[f"s{h}"] = round(spot, 2)
            changed = True
        past_close = (r["date"] < today) or (same_day and not live and is_trading_day(now) and _mins(now) >= 15 * 60 + 30)
        if past_close:
            if r["date"] in closes:
                r["sclose"] = round(float(closes[r["date"]]), 2)
            elif same_day:
                r["sclose"] = round(spot, 2)          # after the close on the signal day, spot is the closing price
            r["resolved"] = True
            if r["first_touch"] in ("TARGET", "STOP"):
                r["outcome"] = r["first_touch"]
            elif r["target"] is not None:
                r["outcome"] = "TIMEOUT"
            else:
                r["outcome"] = "RESOLVED"
            changed = True
            resolved_now.append(r)
    return changed, resolved_now


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
                                      "confidence", "net", "lean", "status", "bias", "pcr", "atm_iv", "sd", "exh_buyer",
                                      "exh_seller", "exh_own_flag", "tone", "news_score", "reason", "rejection_category", "gex_regime", "regime_stable", "net_gex", "zero_gamma_dist_sd", "excess_basis", "rr_25", "rr_shift", "bf_25", "s15", "s30", "s60",
                                      "sclose", "first_touch", "mfe", "mae", "samples", "max_gap_min", "resolved", "outcome")}
        for nm, v in (r.get("checks") or {}).items():
            row[f"check:{nm}"] = v
        for key in ("s15", "s30", "s60", "sclose"):
            row[f"move_{key}"] = fav_points(r, key)
        rows.append(row)
    return pd.DataFrame(rows)


def merge_scorecard(existing, incoming):
    have = {r["id"] for r in existing}
    added = [r for r in incoming if isinstance(r, dict) and r.get("id") and r["id"] not in have]
    existing.extend(added)
    existing.sort(key=lambda r: r["ts"])
    return len(added)


# ---------------- data health ----------------
def data_health(c):
    """Pass / warn / fail checks on every data source the signals depend on. c is a context dict."""
    rows = []

    def add(name, status, detail):
        rows.append({"Check": name, "Status": status, "Detail": detail})

    now, m, df, spot = c["now"], c["m"], c["df"], c["spot"]
    live = market_open_now(now)

    tok = c["token"]
    exp = token_expiry(tok)
    if not tok.startswith("eyJ"):
        add("Dhan token", "FAIL", "Does not look like a JWT access token (is this the API key?)")
    elif exp is None:
        add("Dhan token", "WARN", "Looks like a JWT but the expiry could not be read, so no expiry warning is possible")
    else:
        left = (exp - now).total_seconds() / 3600.0
        if left <= 0:
            add("Dhan token", "FAIL", f"Expired {-left:.1f} h ago ({exp:%d %b %H:%M} IST). Generate a new token")
        elif left < 2:
            add("Dhan token", "WARN", f"Expires in {left:.1f} h ({exp:%d %b %H:%M} IST)")
        else:
            add("Dhan token", "OK", f"Expires {exp:%d %b %H:%M} IST ({left:.0f} h left)")

    n_strikes = len(df)
    add("Option chain", "OK" if n_strikes >= 20 and spot > 0 else "FAIL", f"{n_strikes} strikes, spot {spot:,.2f}")

    w = max(300, m["step"] * 6)
    near = df[(df["Strike"] >= spot - w) & (df["Strike"] <= spot + w)]
    if len(near):
        ltp_ok = ((near["CE_LTP"] > 0) & (near["PE_LTP"] > 0)).mean()
        ba_ok = ((near["CE_Bid"] > 0) & (near["CE_Ask"] > 0) & (near["PE_Bid"] > 0) & (near["PE_Ask"] > 0)).mean()
        add("Premiums (LTP)", "OK" if ltp_ok >= 0.8 else "WARN", f"{ltp_ok:.0%} of near-spot strikes have a call and put LTP")
        if live:
            add("Bid / ask quotes", "OK" if ba_ok >= 0.5 else "WARN", f"{ba_ok:.0%} of near-spot strikes quoted on both sides")
        else:
            add("Bid / ask quotes", "OK", "Market closed, quotes are not expected")

    gap_txt = f", CE/PE gap {m['iv_gap']:.1f} pts" if m["iv_gap"] else ""
    iv_ok = 5 <= m["atm_iv"] <= 60 and m["iv_gap"] <= 3
    add("ATM implied volatility", "OK" if iv_ok else "WARN", f"{m['atm_iv']:.1f}%{gap_txt}" + ("" if iv_ok else " (outside 5-60% or CE/PE mismatch: possible stale quotes)"))

    if m.get("surface_ok"):
        add("Synthetic future", "OK", f"F_syn {m['syn_fut']:,.1f}, excess over fair carry {m['excess_basis']:+.1f} pts")
    else:
        add("Synthetic future", "WARN", "No balanced near-ATM call/put mids: basis and skew checks are blind")

    dq = data_quality(m)
    add("Previous-day OI", "WARN" if dq else "OK", dq or "OI change baseline looks populated")

    pc, src = c["prev_close"], c.get("prev_src", "")
    if not pc or pc <= 0:
        add("Previous close", "WARN", "Unknown: day-change terms are skipped. Enter it manually or fix the candle feed")
    elif abs(pc - spot) / spot > 0.05:
        add("Previous close", "WARN", f"{pc:,.2f} is more than 5% from spot ({src})")
    else:
        add("Previous close", "OK", f"{pc:,.2f} ({src})")

    cs = c.get("candles") or []
    if c.get("candle_err"):
        add("Daily candles", "WARN", f"Failed: {str(c['candle_err'])[:90]}")
    elif len(cs) < 10:
        add("Daily candles", "WARN", f"Only {len(cs)} candles loaded")
    else:
        done = [x["date"] for x in cs if x["date"] < now.date().isoformat()]
        last = done[-1] if done else None
        stale = bool(last) and (now.date() - date.fromisoformat(last)).days > 4
        add("Daily candles", "WARN" if stale else "OK", f"{len(cs)} loaded, last completed session {last}" + (" (older than expected)" if stale else ""))

    hist = c.get("hist") or []
    if live and len(hist) >= 4:
        last4 = hist[-4:]
        frozen = len({round(h["spot"], 2) for h in last4}) == 1 and len({round(h["iv"], 3) for h in last4}) == 1 and len({round(h["pcr"], 4) for h in last4}) == 1
        add("Feed freshness", "WARN" if frozen else "OK",
            "Spot, IV and PCR identical across the last 4 snapshots: the feed may be stale" if frozen else "Values are changing between snapshots")
    else:
        add("Feed freshness", "OK", "Not enough snapshots yet, or market closed")

    if len(hist) >= 2 and live:
        today_ts = [h["ts"] for h in hist]
        gaps = [(b - a) / 60.0 for a, b in zip(today_ts, today_ts[1:])]
        mg = max(gaps)
        add("Snapshot history", "WARN" if mg > 10 else "OK", f"{len(hist)} snapshots today, longest gap {mg:.0f} min" + (" (the app was closed or refresh was off)" if mg > 10 else ""))
    else:
        add("Snapshot history", "OK", f"{len(hist)} snapshot(s) stored today")

    ni, ns_ = c.get("news_items") or [], c.get("news_status") or []
    ok_feeds = sum(1 for s in ns_ if ": OK" in s)
    if not c.get("live_news"):
        add("News feed", "OK", "Turned off")
    elif not ni:
        add("News feed", "WARN", "No headlines. " + " | ".join(ns_)[:120])
    else:
        newest = min(i["age_min"] for i in ni)
        add("News feed", "WARN" if newest > 360 else "OK", f"{len(ni)} headlines from {ok_feeds} feed(s), newest {age_label(newest)}")

    ai = c.get("ai") or {}
    lt = c.get("last_ai_time")
    if not c.get("groq_key"):
        add("AI Council", "WARN", "No Groq API key: the AI Council and signals are unavailable")
    elif "error" in ai and "signal" not in ai:
        add("AI Council", "WARN", f"Last call failed: {str(ai['error'])[:100]}")
    elif lt is None:
        add("AI Council", "WARN", "No verdict yet")
    else:
        age = (now - lt).total_seconds() / 60.0
        add("AI Council", "WARN" if (live and age > 60) else "OK", f"Last verdict {age:.0f} min ago ({ai.get('_model', '')})")

    add("Clock", "OK", f"{now:%a %d %b %H:%M:%S} IST, market {'OPEN' if live else 'CLOSED'}")

    if c.get("alerts_on"):
        ch = [n for n, ok in (("Telegram", c.get("tg_ok")), ("Webhook", c.get("hook_ok"))) if ok]
        add("Alerts", "OK" if ch else "WARN", ("Channels: " + ", ".join(ch)) if ch else "Alerts are on but no channel is configured")
    else:
        add("Alerts", "OK", "Off")

    writable = os.access(tempfile.gettempdir(), os.W_OK)
    add("Log storage", "OK" if writable else "FAIL",
        ("Temp storage writable. Download backups: it can be wiped on restart" if writable else "Temp folder is not writable: scorecard cannot be saved"))
    return rows


# ============================================================
# DEALER POSITIONING, VOLATILITY SURFACE, REJECTION BREAKDOWN, EXPORT IMPORT (v13.0)
# ============================================================
DIV_YIELD = 0.013            # approximate index dividend yield used for the fair-forward carry
GEX_BAND_PCT = 5.0           # |net GEX| below this % of gross GEX is NEUTRAL (stops regime flicker)
GEX_WINDOWS = (4, 8, 16)     # strikes each side of spot used to test that the regime is not a window artefact
BASIS_MIN_PTS = 8.0          # excess basis below this is noise
SURF_CHECK_NAME = "Synthetic basis vs fair carry"
USE_SURFACE_CHECK = {"on": False}   # observe-only until the scorecard shows it has edge
SURFACE_KEYS = ("syn_fut", "fair_fwd", "carry", "basis", "excess_basis", "surface_ok", "iv_25c", "iv_25p", "rr_25", "bf_25",
                "net_gex", "gross_gex", "gex_regime", "gex_regime_label", "regime_stable", "regime_by_window", "crossover",
                "zero_gamma", "total_gex_at_spot", "net_vex", "net_cex")
REGIME_LABEL = {
    "POSITIVE": "POSITIVE GAMMA (if dealers are long calls / short puts: mean-reverting)",
    "NEGATIVE": "NEGATIVE GAMMA (if dealers are long calls / short puts: momentum / breakout)",
    "NEUTRAL": "NEUTRAL GAMMA (net exposure near zero)",
}


def _mid_price(bid, ask, ltp):
    if bid > 0 and ask > 0 and ask >= bid:
        return 0.5 * (bid + ask)
    return ltp if ltp > 0 else 0.0


def synthetic_future(df, spot, t_years, step):
    """Put-call parity forward F = K + e^{rT}(C - P) from the 3 most balanced near-ATM strikes. None if no usable pair."""
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
    """IV at the 25-delta call and put, risk reversal (call - put) and butterfly. Rows without a delta are ignored."""
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
    """Adds per-strike GEX (calls +, puts -: ASSUMES dealers long calls / short puts), net vanna and net charm."""
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
        return -n * d2 / sig, n * d2 / (2.0 * t)          # forward form (r = 0); checked against finite differences

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
    """Strike where per-strike net GEX changes sign (call-dominated vs put-dominated OI). Not the aggregate zero-gamma level."""
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
    """Spot level where total signed gamma exposure changes sign, re-pricing every strike's gamma at each hypothetical spot."""
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


def surface_state(r):
    """How the (possibly observe-only) surface check related to the signal: agrees / neutral / disagrees / not logged."""
    ch = r.get("checks") or {}
    if SURF_CHECK_NAME not in ch:
        return "not logged"
    v = ch[SURF_CHECK_NAME] * (1 if r["signal"] == "BUY" else -1)
    return "agrees" if v > 0 else "disagrees" if v < 0 else "neutral"


def classify_rejection(note):
    n = (note or "").lower()
    if "rejected" in n:
        n = n.rsplit("rejected", 1)[-1]           # the last rejection reason; ignore leading cautions
    if not n.strip():
        return "None (validated)"
    if "agreement" in n:
        return "Agreement below +2"
    if "r:r" in n:
        return "Poor R:R"
    if "exhaustion" in n:
        return "Trend exhaustion"
    if "wall" in n:
        return "No usable OI wall"
    return "Other"


def rejection_category(r):
    return r.get("rejection_category") or classify_rejection(r.get("reason"))


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


def records_from_df(df):
    """Rebuild scorecard records from a CSV / XLSX export of scorecard_to_df (so earlier sessions are not lost)."""
    def val(x):
        try:
            if x is None or pd.isna(x):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(x, np.bool_):
            return bool(x)
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, np.floating):
            return float(x)
        return x

    chk_cols = [c for c in df.columns if str(c).startswith("check:")]
    recs = []
    for _, row in df.iterrows():
        r = {k: val(row[k]) for k in df.columns if not str(k).startswith("check:") and not str(k).startswith("move_")}
        if not r.get("id") or r.get("signal") not in ("BUY", "SELL"):
            continue
        r["id"] = re.sub(r"\.0$", "", str(r["id"]))
        try:
            r["ts"] = datetime.fromisoformat(f"{str(r.get('date'))[:10]} {str(r.get('time'))[:8]}").replace(tzinfo=IST).timestamp()
        except Exception:
            continue
        r["checks"] = {c[6:]: int(val(row[c]) or 0) for c in chk_cols}
        for k in ("mfe", "mae", "max_gap_min", "sd"):
            r[k] = float(r.get(k) or 0.0)
        r["samples"] = int(r.get("samples") or 0)
        r["resolved"] = bool(r.get("resolved"))
        r["outcome"] = r.get("outcome") or ("RESOLVED" if r["resolved"] else "OPEN")
        r["last_sample_ts"] = None
        for k in ("s15", "s30", "s60", "sclose", "target", "sl", "rr", "first_touch"):
            r.setdefault(k, None)
        recs.append(r)
    return recs


def esc(x):
    return html.escape(str(x if x is not None else "-"))


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL CONFIG")
    dhan_token = clean(st.text_input("DHAN ACCESS TOKEN", type="password", value=DEFAULT_DHAN_TOKEN,
                                     help="The long JWT (starts with 'eyJ'). NOT the API key/secret."))
    client_id = clean(st.text_input("DHAN CLIENT ID", value=DEFAULT_CLIENT_ID))
    groq_key = st.text_input("GROQ API KEY", type="password", value=DEFAULT_GROQ_KEY,
                             help="Paste your Groq API key here to activate the AI Council.").strip()

    groq_model = st.selectbox("GROQ MODEL", GROQ_MODELS, index=0)

    if not dhan_token:
        st.error("DHAN access token required. Paste it above.")
        st.stop()
    if not client_id:
        st.error("DHAN client ID required.")
        st.stop()

    with st.expander("🔧 DIAGNOSTICS"):
        st.write(f"Token length: **{len(dhan_token)}** | starts: `{dhan_token[:3]}…`")
        st.write(f"Client ID: `{client_id}`")
        st.write(f"Token pre-filled from secrets/env: **{bool(DEFAULT_DHAN_TOKEN)}**")
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

    _exp = token_expiry(dhan_token)
    if _exp:
        _left = (_exp - datetime.now(IST)).total_seconds() / 3600.0
        if _left <= 0:
            st.error(f"Dhan token EXPIRED {-_left:.1f} h ago. Generate a new one.")
        elif _left < 2:
            st.warning(f"Dhan token expires in {_left:.1f} h ({_exp:%H:%M} IST).")
        else:
            st.caption(f"Dhan token valid until {_exp:%d %b %H:%M} IST ({_left:.0f} h left)")

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
                              f"by {SKEW_COEF} x the IV shock per 1 sigma of spot move. Off = flat IV except in the crush/spike columns.")
    surface_check_on = st.toggle("COUNT SURFACE CHECK IN AGREEMENT SCORE", value=False, key="surface_check_on",
                                 help="Synthetic basis vs fair carry. Off = observe-only: it is logged with every signal but does not change "
                                      "the agreement score, until the scorecard shows it has edge.")
    USE_SURFACE_CHECK["on"] = surface_check_on
    _basis = st.selectbox("VOL TIME BASIS", ["Calendar (days/365)", "Trading (252 d x 375 min)"], index=0, key="time_basis",
                          help="Use Calendar unless your IV source is annualised on trading time. It changes the expected move and 1-sigma bands.")
    TIME_BASIS["mode"] = "calendar" if _basis.startswith("Calendar") else "trading"
    exh_lookback = st.slider("EXHAUSTION LOOKBACK (min)", 6, 45, 15, step=3,
                             help="History window used by the exhaustion monitor. With a 3-min refresh, 15 min = 5 snapshots.")
    st.markdown("---")
    st.markdown("### 🔔 SCORECARD & ALERTS")
    log_on = st.toggle("LOG SIGNALS TO SCORECARD", value=True, key="log_on",
                       help="Logs validated signals and the AI signals the filter rejected, then tracks what spot did afterwards.")
    alerts_on = st.toggle("SEND ALERTS", value=False, key="alerts_on",
                          help="Telegram and/or webhook. Alerts are only sent while this app is open and refreshing.")
    tg_token = st.text_input("TELEGRAM BOT TOKEN", type="password", value=get_secret("TELEGRAM_BOT_TOKEN", ""), key="tg_token").strip()
    tg_chat = st.text_input("TELEGRAM CHAT ID", value=get_secret("TELEGRAM_CHAT_ID", ""), key="tg_chat").strip()
    hook_url = st.text_input("ALERT WEBHOOK URL (optional)", type="password", value=get_secret("ALERT_WEBHOOK_URL", ""), key="hook_url").strip()
    log_hook = st.text_input("SCORECARD WEBHOOK URL (optional)", type="password", value=get_secret("LOG_WEBHOOK_URL", ""), key="log_hook",
                             help="Every logged / resolved signal is POSTed here as JSON, e.g. a Google Apps Script that appends a sheet row, so the log survives restarts.").strip()
    if st.button("Send test alert", key="test_alert"):
        _res = send_alert("BBG terminal test alert. If you can read this, alerts work.", tg_token, tg_chat, hook_url)
        if not _res:
            st.warning("No channel configured.")
        for _c, _ok, _d in _res:
            (st.success if _ok else st.error)(f"{_c}: {'sent' if _ok else 'failed ' + _d}")
    force_run = st.button("FORCE AI RUN")

    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="bbg")

# ============================================================
# FETCH + ANALYZE
# ============================================================
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token, client_id)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

if df.empty or spot == 0:
    st.warning("Option chain returned no data (market closed or invalid expiry).")
    st.stop()

m = compute_metrics(df, spot, prev_close, expiry, info["step"])
vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
hist = record_snapshot(m, f"{idx_name}|{expiry}")
_rr0 = next((h.get("rr_25") for h in hist if h.get("rr_25") is not None), None)
m["rr_shift"] = round(m["rr_25"] - _rr0, 2) if _rr0 is not None else 0.0
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
_last_try = st.session_state.get("last_ai_attempt")
_retry_ok = _last_try is None or (datetime.now(IST) - _last_try).total_seconds() >= AI_RETRY_SEC
if force_run or (changed and _retry_ok):
    st.session_state["last_ai_attempt"] = datetime.now(IST)
    ai = run_council(m, vel, news_text, groq_key, groq_model)
    if "error" not in ai:
        st.session_state.last_ai_verdict = ai
        st.session_state.last_ai_time = datetime.now(IST)
        st.session_state.last_ai_state = {
            "spot": m["spot"], "pcr": m["pcr"], "call_wall": m["call_wall"],
            "put_wall": m["put_wall"], "gamma_strike": m["gamma_strike"], "status": m["status"],
            "trap_active": bool(m["ce_trap"] or m["pe_trap"]),
            "gex_regime": m.get("gex_regime"),
            "news_hash": nhash,
            "exh_flag": m.get("exh_flag", ""),
        }
        gate_label = f"FRESH ({'FORCED' if force_run else reason}) | {ai.get('_model', groq_model)}"
    else:
        err = ai["error"]
        cached = st.session_state.get("last_ai_verdict")
        ai = cached if cached else ai
        gate_label = f"AI ERROR: {err}" + (" (showing cached)" if cached else "")
else:
    ai = st.session_state.get("last_ai_verdict", {"error": "No verdict yet."})
    t = st.session_state.get("last_ai_time")
    gate_label = f"CACHED {t.strftime('%H:%M:%S') if t else ''}"

# ---- risk-validated signal + premium scanner ----
now_ist = datetime.now(IST)
ai_ok = not ("error" in ai and "signal" not in ai)
sig = build_signal(m, ai) if ai_ok else None
scan, smeta = premium_scanner(m, horizon_days, iv_shock, skew=use_skew)

# ---- scorecard logging, outcome tracking, data health, alerts ----
try:
    candles_pre, candle_err = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id), ""
except Exception as _e:
    candles_pre, candle_err = [], str(_e)
if "scorecard" not in st.session_state:
    st.session_state["scorecard"] = load_scorecard()
card = st.session_state["scorecard"]
tone_now = headline_tone(news_items) if news_items else 0
new_recs = []
if log_on and sig is not None:
    _chg, _resolved = update_outcomes(card, idx_name, spot, now_ist, candles_pre)
    new_recs = maybe_log_signal(card, idx_name, expiry, m, sig, ai, tone_now, now_ist)
    if _chg or new_recs:
        save_scorecard(card)
    for _r in new_recs:
        post_webhook(log_hook, {"event": "signal_logged", **_r})
    for _r in _resolved:
        post_webhook(log_hook, {"event": "signal_resolved", **_r})

health_rows = data_health({
    "now": now_ist, "m": m, "df": m["df"], "spot": spot, "token": dhan_token, "prev_close": prev_close,
    "prev_src": auto_src if (auto_prev and auto_val) else "manual entry", "candles": candles_pre, "candle_err": candle_err,
    "hist": hist, "news_items": news_items, "news_status": news_status, "live_news": live_news, "ai": ai,
    "last_ai_time": st.session_state.get("last_ai_time"), "groq_key": groq_key, "alerts_on": alerts_on,
    "tg_ok": bool(tg_token and tg_chat), "hook_ok": bool(hook_url)})

alert_cfg = {"enabled": alerts_on, "tg_token": tg_token, "tg_chat": tg_chat, "hook": hook_url}
alert_results = []
if alerts_on:
    for _r in new_recs:
        if _r["kind"] == "VALIDATED":
            alert_results += fire_alert(f"sig_{_r['id']}", signal_alert_text(_r), 0, alert_cfg)
    _ex = m["exh"]
    if _ex.get("ready"):
        for _side, _lab in (("buyer", "BUYER"), ("seller", "SELLER")):
            if _ex[_side]["flag"] == "CONFIRMED":
                alert_results += fire_alert(
                    f"exh_{idx_name}_{_side}_{now_ist.date().isoformat()}",
                    f"⚠ {idx_name}: {_lab} exhaustion CONFIRMED ({_ex[_side]['score']}/100) at {spot:,.0f}. "
                    f"{now_ist:%H:%M} IST. Analysis only, not trading advice.", 60, alert_cfg)
    for _row in health_rows:
        _tok_soon = _row["Check"] == "Dhan token" and _row["Status"] == "WARN" and "Expires in" in _row["Detail"]
        if _row["Status"] == "FAIL" or _tok_soon:
            alert_results += fire_alert(f"health_{_row['Check']}_{_row['Status']}", f"🩺 {_row['Check']}: {_row['Detail']}",
                                        360 if _row["Check"] == "Dhan token" else 60, alert_cfg)


# ============================================================
# DASHBOARD
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f}" + (f"  ({spot - prev_close:+,.2f})" if prev_close > 0 else ""))
_mkt = "OPEN" if market_open_now(now_ist) else "CLOSED (spot, OI and premiums shown are the last close, not live)"
st.caption(f"{now_ist.strftime('%a %d %b %Y %H:%M:%S')} IST | market {_mkt} | dealer gamma {m['gex_regime']}"
           + (f" | synthetic future {m['syn_fut']:,.1f} (excess over fair carry {m['excess_basis']:+.1f})" if m["surface_ok"] else ""))
_hc = {"OK": 0, "WARN": 0, "FAIL": 0}
for _row in health_rows:
    _hc[_row["Status"]] += 1
st.caption(f"DATA HEALTH: {_hc['OK']} OK · {_hc['WARN']} warn · {_hc['FAIL']} fail (details in the Data Health tab)")
_fails = [r for r in health_rows if r["Status"] == "FAIL"]
if _fails:
    st.error("Data problem: " + "; ".join(f"{r['Check']}: {r['Detail']}" for r in _fails[:3]))
for _r in new_recs:
    st.info(f"Scorecard: logged {_r['kind']} {_r['signal']} at {_r['entry']:,.0f}")
_alert_fail = [f"{c}: {d}" for c, ok, d in alert_results if not ok]
if _alert_fail:
    st.warning("Alert delivery failed: " + " | ".join(_alert_fail))

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "📊 Dashboard & Engine", "📐 Day Levels", "🧭 Exhaustion", "🎯 Premium Scanner",
    "🤖 AI Council & News", "📋 Option Chain", "📒 Scorecard", "🩺 Data Health", "📈 GEX & Skew"])

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
      <div class="bbg-title">Dual-Force Engine</div>
      <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
      <div class="bbg-desc">{m['desc']}</div>
    </div>""", unsafe_allow_html=True)

    if prev_close > 0 and abs(prev_close - spot) / spot > 0.05:
        st.warning(f"PREV CLOSE ({prev_close:,.0f}) is more than 5% away from spot ({spot:,.0f}). "
                   "It is probably stale or wrong, and it skews the BIAS SCORE.")

    # ---- ENGINE DEBUG ROW ----
    es = m["engine"]
    st.caption(f"DUAL-FORCE ENGINE INPUTS (near-spot strikes, % of that side's OI | each leg needs >= {MIN_FLOW_PCT:.1f}%)")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("CE EXITS", f"{es['ce_exit_pct']:.1f}%", f"{es['ce_exits']:+,.0f} qty", delta_color="off")
    d2.metric("PE BUILDS", f"{es['pe_build_pct']:.1f}%", f"{es['pe_build']:+,.0f} qty", delta_color="off")
    d3.metric("PE EXITS", f"{es['pe_exit_pct']:.1f}%", f"{es['pe_exits']:+,.0f} qty", delta_color="off")
    d4.metric("CE BUILDS", f"{es['ce_build_pct']:.1f}%", f"{es['ce_build']:+,.0f} qty", delta_color="off")
    f1, f2, f3 = st.columns(3)
    f1.metric("BULL FORCE (PE build + CE exit)", f"{es['bull_force']:.1f}")
    f2.metric("BEAR FORCE (CE build + PE exit)", f"{es['bear_force']:.1f}")
    f3.metric("FORCE RATIO", f"{min(es['force_ratio'], 99):.2f}x", f"winner needs >= {DOMINANCE}x", delta_color="off")
    _zero = [n for n, k in (("CE EXITS", "ce_exit_pct"), ("PE BUILDS", "pe_build_pct"),
                            ("PE EXITS", "pe_exit_pct"), ("CE BUILDS", "ce_build_pct")) if es[k] == 0]
    if _zero:
        st.caption(f"{', '.join(_zero)} = 0.0% means no near-spot strike moved that way by more than {NOISE_PCT}% of that side's OI. "
                   "These legs compare today's OI with the PREVIOUS DAY's close, not with the last refresh.")
    _dq = data_quality(m)
    if _dq:
        st.warning("Data quality: " + _dq + ".")
    st.caption("Bullish blast = CE exits + PE builds both >= threshold AND bull force >= 1.5x bear force. "
               "Bearish blast = mirror image. Both sides active with no clear winner = CONFLICTED | WAIT.")

    if m["iv_gap"] > 3:
        st.caption(f"⚠ ATM IV mismatch: CE {m['atm_iv_ce']:.1f}% vs PE {m['atm_iv_pe']:.1f}% (often stale quotes after hours). "
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
    c9.metric("CE TRAP (RES)", f"{m['ce_trap']:,.0f}" if m["ce_trap"] else "—")
    c10.metric("PE TRAP (SUPP)", f"{m['pe_trap']:,.0f}" if m["pe_trap"] else "—")
    c11.metric("NET CE OI Δ", f"{m['dce']:+,.0f}")
    c12.metric("NET PE OI Δ", f"{m['dpe']:+,.0f}")

    # ---- JOBBER MICRO-LADDER PANEL ----
    st.markdown("#### ⚡ JOBBER MICRO-LADDER & ABSORPTION FEED")
    j1, j2, j3 = st.columns(3)
    j1.metric("LADDER IMBALANCE", f"{m['jobber']['ladder_imbalance']:+.3f}", "Range: -1.0 to +1.0")
    j2.metric("MAX PRESSURE POCKET", f"{m['jobber']['pocket_support']:,.0f}", "Closest High-Liquidity Node")
    j3.metric("MICRO-TURN STATUS", m["jobber"]["micro_turn"])

    st.markdown("#### 3-MIN OI VELOCITY (near spot)")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("CE BUILDS", f"{vel['ce_builds']:,.0f}")
    v2.metric("CE UNWINDS", f"{vel['ce_unwinds']:,.0f}")
    v3.metric("PE BUILDS", f"{vel['pe_builds']:,.0f}")
    v4.metric("PE UNWINDS", f"{vel['pe_unwinds']:,.0f}")
    if sum(vel.values()) == 0:
        st.caption("Velocity needs two snapshots ~3 min apart. It will populate after the next refresh.")


with tab2:
    # ---- DAY MOVEMENT LEVELS ----
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
    st.caption(f"★ = level within ±{dl['tol']:,.0f} pts of an OI level (wall, max pain, gamma strike): confluence makes a level more meaningful. "
               "Pivots: P=(H+L+C)/3, R1=2P−L, S1=2P−H, R2=P+(H−L), S2=P−(H−L). After 15:40 IST the basis is today's completed session. "
               "Ranges are probabilities from IV (assumes normal returns), not guarantees.")


with tab3:
    # ---- EXHAUSTION MONITOR ----
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
    with st.expander("SESSION HISTORY (spot / IV / PCR)"):
        if len(hist) >= 2:
            hdf = pd.DataFrame([{"Time": datetime.fromtimestamp(h["ts"], IST).strftime("%H:%M:%S"), "Spot": h["spot"],
                                 "ATM IV": round(h["iv"], 2), "PCR": round(h["pcr"], 3), "Flow edge": round(h["edge"], 2),
                                 "Status": h["status"]} for h in hist])
            st.line_chart(hdf.set_index("Time")[["Spot"]])
            st.line_chart(hdf.set_index("Time")[["ATM IV"]])
            show_df(hdf.iloc[::-1], hide_index=True)
        else:
            st.write("Waiting for more snapshots.")


with tab4:
    # ---- PREMIUM POTENTIAL SCANNER ----
    st.markdown("#### 🎯 PREMIUM POTENTIAL SCANNER")
    lean_now = sum(c["v"] for c in chain_checks(m) if c.get("counted", True))
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
                   f"scenario 1σ move ≈ {smeta['sd']:,.0f} pts | horizon {horizon_label} (time left {smeta['T0d']:.1f}d → {smeta['T1d']:.1f}d) | "
                   f"IV shock ±{smeta['crush']:.0f}%" + (f" | spot-vol link ±{smeta['skew'] * smeta['crush']:.1f}% IV per σ." if smeta["skew"] else "."))
        top = scan[scan["Side"] == fav].head(5) if fav else scan.head(6)
        other = scan[scan["Side"] != fav].head(3) if fav else None
        show_df(fmt_scanner(top), hide_index=True)
        if other is not None and not other.empty:
            with st.expander("Counter-trend side (against the chain lean)"):
                show_df(fmt_scanner(other), hide_index=True)

        if top.empty:      # the favoured side has no priced option: fall back to the overall ranking instead of crashing
            st.info("No priced option on the favoured side right now; showing the best-ranked contracts overall.")
            top = scan.head(6)
        b = top.iloc[0]
        sgn = 1 if b["Side"] == "CE" else -1
        s1 = m["spot"] + sgn * smeta["sd"]
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Top pick walk-through: {m['spot']:,.0f} spot, {b['Strike']:,.0f} {b['Side']} ({b['Money']}) at ₹{b['LTP']:,.1f}</div>
          <div class="agent">If spot moves 1σ in favour to ≈ <b>{s1:,.0f}</b>: premium ≈ <b>{b['x1σ']:.2f}x</b> (₹{b['LTP']*b['x1σ']:,.1f}); delta rises {b['Delta']:.2f} → {b['Delta@1σ']:.2f}, which is the gamma effect (OTM turning into ITM/ATM).</div>
          <div class="agent">With IV crush −{smeta['crush']:.0f}% on that same move: <b>{b['x1σ crush']:.2f}x</b> (₹{b['LTP']*b['x1σ crush']:,.1f}). With IV spike +{smeta['crush']:.0f}%: <b>{b['x1σ spike']:.2f}x</b> (₹{b['LTP']*b['x1σ spike']:,.1f}).</div>
          <div class="agent">If it goes the wrong way by 0.5σ: <b>{b['xWrong']:.2f}x</b> (₹{b['LTP']*b['xWrong']:,.1f}). Spot move needed just to double the premium: <b>{('>4' if pd.isna(b['Needs2x(σ)']) else f"{b['Needs2x(σ)']:.2f}")}σ</b>.</div>
        </div>""", unsafe_allow_html=True)
        st.caption("Scenario repricing with Black-Scholes anchored to the traded premium, constant per-strike IV shift, "
                   "no slippage. It shows how premiums react IF spot moves; it does not predict that the move happens. "
                   "Score = reward after IV crush ÷ wrong-way loss, weighted by liquidity, delta odds, volume and fresh OI.")


with tab5:
    # ---- LIVE NEWS PANEL ----
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
        ai_note = f" (AI said {sig['ai_signal']})" if sig["ai_signal"] != sig["signal"] else ""
        st.markdown(f"""
        <div class="bbg-panel" style="border:2px solid {scolor};">
          <div class="bbg-title">Risk-Validated Signal</div>
          <div class="bbg-big" style="color:{scolor};">{sig['signal']}{esc(ai_note)} &nbsp;|&nbsp; Confidence {sig['confidence']}%</div>
          <div class="bbg-desc">{lv}</div>
          <div class="bbg-desc" style="color:#ffab00;">{esc(sig['note']) if sig['note'] else ''}</div>
        </div>""", unsafe_allow_html=True)

        # option-chain cross-check
        d = sig["direction"]
        def icon(v):
            if d == 0:
                return "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
            return "✅" if v * d > 0 else "❌" if v * d < 0 else "➖"
        crow = "".join(f'<div class="agent">{icon(c["v"]) if c.get("counted", True) else "👁"} <b>{esc(c["name"])}:</b> {esc(c["detail"])}</div>'
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
          <div class="bbg-title">Option-chain cross-check &nbsp;|&nbsp; chain lean {lean_txt} ({sig['lean']:+d}) &nbsp;|&nbsp; {need}</div>
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
          <div class="agent"><b>Volatility:</b> {esc(ai.get('volatility_agent'))}</div>
          <div class="agent"><b>Sentiment{ns_txt}:</b> {esc(ai.get('news_agent'))}</div>
          <div class="agent"><b>Risk Officer:</b> {esc(ai.get('final_approval'))}</div>
        </div>""", unsafe_allow_html=True)


with tab6:
    st.caption(f"1σ range to expiry: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now(IST).strftime('%H:%M:%S')} IST")
    show_df(m["df"], hide_index=True)

with tab7:
    st.markdown("#### 📒 SIGNAL SCORECARD: does the engine have an edge?")
    recs = st.session_state["scorecard"]
    vals = [r for r in recs if r["kind"] == "VALIDATED"]
    rejs = [r for r in recs if r["kind"] == "REJECTED"]
    res60 = [r for r in recs if r.get("s60") is not None]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("LOGGED", len(recs))
    k2.metric("VALIDATED", len(vals))
    k3.metric("REJECTED BY FILTER", len(rejs))
    k4.metric("RESOLVED", sum(1 for r in recs if r["resolved"]))
    k5.metric("WITH 60-MIN OUTCOME", len(res60))
    st.caption(f"A signal is logged only while the market is open, at most once per {SIGNAL_DEDUP_MIN} min per direction. Outcomes are sampled on each "
               f"refresh (about every 3 min), so target/stop 'first touch' is approximate. Records are stored in the server's temp folder: "
               f"download a backup below or set a scorecard webhook.")
    if not recs:
        st.info("No signals logged yet. They appear here when the validated signal turns BUY/SELL, or when the AI signals but the filter rejects it.")
    else:
        if len(res60) < MIN_SAMPLE_FOR_VERDICT:
            st.warning(f"Only {len(res60)} signal(s) have a 60-minute outcome. Below about {MIN_SAMPLE_FOR_VERDICT} the numbers are noise, not evidence.")
        st.markdown("##### Hit rate and average move by horizon")
        show_df(scorecard_summary(recs), hide_index=True)
        ts_ = touch_stats(recs)
        st.caption(f"Validated signals with levels: target first {ts_['target']}, stop first {ts_['stop']}, neither by the close {ts_['timeout']}, still open {ts_['open']}"
                   + (f" | target-before-stop rate {ts_['target_rate']}%" if ts_["target_rate"] is not None else ""))

        def _hit(rs, key="s60"):
            v = [fav_points(r, key) for r in rs if r.get(key) is not None]
            return (sum(1 for x in v if x > 0) / len(v) * 100, len(v)) if v else (None, 0)
        hv, nv = _hit(vals)
        hr, nr = _hit(rejs)
        if hv is not None and hr is not None and nv >= 15 and nr >= 15:
            if hv >= hr + 10:
                st.success(f"The validation filter looks useful: validated signals hit {hv:.0f}% at 60 min versus {hr:.0f}% for the ones it rejected.")
            elif hr >= hv + 10:
                st.warning(f"Rejected signals did better ({hr:.0f}%) than validated ones ({hv:.0f}%): the filter may be too strict.")
            else:
                st.info(f"No clear difference between validated ({hv:.0f}%) and rejected ({hr:.0f}%) signals at 60 min.")
        else:
            st.caption("Needs at least 15 resolved validated and 15 rejected signals before the filter can be judged.")

        st.markdown("##### Which checks have edge? (60-min hit rate when the check agreed vs disagreed with the signal)")
        show_df(check_edge_table(recs), hide_index=True)
        g1, g2, g3 = st.columns(3)
        g1.caption("By chain agreement score")
        with g1:
            show_df(group_table(recs, lambda r: f"{r['net']:+d}"), hide_index=True)
        g2.caption("By AI confidence")
        with g2:
            show_df(group_table(recs, lambda r: "75+" if (r["confidence"] or 0) >= 75 else "60-74" if (r["confidence"] or 0) >= 60 else "<60"), hide_index=True)
        g3.caption("By own-side exhaustion")
        with g3:
            show_df(group_table(recs, lambda r: r.get("exh_own_flag") or "none"), hide_index=True)

        st.markdown("##### Why were signals rejected, and did it matter?")
        show_df(rejection_breakdown_table(recs), hide_index=True)
        _r1, _r2 = st.columns(2)
        _r1.caption("By dealer-gamma regime at signal time")
        with _r1:
            show_df(group_table(recs, lambda r: r.get("gex_regime") or "not logged"), hide_index=True)
        _r2.caption("Synthetic-basis check vs the signal's direction")
        with _r2:
            show_df(group_table(recs, surface_state), hide_index=True)

        curve = cumulative_curve(recs)
        if len(curve) >= 2:
            st.markdown("##### Cumulative move of validated signals (σ units, 60-min mark)")
            st.line_chart(curve.rename("Cumulative move (σ)"))
        st.markdown("##### Recent records")
        _df = scorecard_to_df(recs).iloc[::-1].head(25)
        _cols = [c for c in ("date", "time", "index", "kind", "signal", "entry", "target", "sl", "confidence", "net", "exh_own_flag",
                             "move_s15", "move_s30", "move_s60", "move_sclose", "first_touch", "outcome", "samples") if c in _df.columns]
        show_df(_df[_cols], hide_index=True)

    st.markdown("##### Export / backup")
    e1, e2 = st.columns(2)
    if recs:
        e1.download_button("📄 Scorecard CSV", scorecard_to_df(recs).to_csv(index=False), file_name="signal_scorecard.csv", mime="text/csv")
        e2.download_button("💾 Scorecard backup (JSON)", json.dumps(recs), file_name="signal_scorecard_backup.json", mime="application/json")
    _up = st.file_uploader("Merge a scorecard backup or export (JSON, CSV or XLSX)", type=["json", "csv", "xlsx"], key="sc_upload")
    if _up is not None and st.button("♻ MERGE", key="sc_merge"):
        try:
            _nm = _up.name.lower()
            _inc = json.load(_up) if _nm.endswith(".json") else records_from_df(pd.read_csv(_up) if _nm.endswith(".csv") else pd.read_excel(_up))
            _added = merge_scorecard(recs, _inc)
            save_scorecard(recs)
            st.success(f"Merged {_added} new record(s).")
        except Exception as e:
            st.error(f"Could not merge: {e}")
    if st.checkbox("I want to delete the whole scorecard", key="sc_reset_ok") and st.button("🗑 RESET SCORECARD", key="sc_reset"):
        st.session_state["scorecard"] = []
        save_scorecard([])
        st.rerun()

with tab8:
    st.markdown("#### 🩺 DATA HEALTH")
    _hdf = pd.DataFrame(health_rows)
    _hdf["Status"] = _hdf["Status"].map({"OK": "✅ OK", "WARN": "⚠️ WARN", "FAIL": "❌ FAIL"})
    show_df(_hdf, hide_index=True)
    st.caption("Checks run on every refresh. A WARN means a signal may be less reliable; a FAIL means the data cannot be trusted until it is fixed.")
    if st.button("Run Dhan profile check (/v2/profile)", key="hc_profile"):
        try:
            _pr = requests.get(PROFILE_URL, headers=auth_headers(dhan_token, client_id), timeout=10)
            st.code(f"HTTP {_pr.status_code}\n{_pr.text[:500]}")
        except Exception as e:
            st.error(f"Profile check failed: {e}")
    with st.expander("News feed status"):
        for _line in news_status:
            st.write(_line)

with tab9:
    st.markdown("#### 📈 DEALER POSITIONING & VOLATILITY SURFACE")
    st.warning("Assumption to test, not a fact: gamma exposure here treats dealers as long calls and short puts (the US convention). "
               "On NSE a large part of open interest is option writers short both sides, so the regime label is a hypothesis. "
               "The scorecard logs the regime with every signal so you can see whether it ever mattered.")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("REGIME (±8 strikes)", m["gex_regime"], f"{'stable across ±4/±8/±16' if m['regime_stable'] else 'DEPENDS ON WINDOW: ' + ', '.join(f'±{k}:{v}' for k, v in m['regime_by_window'].items())}", delta_color="off")
    g2.metric("ZERO-GAMMA LEVEL", f"{m['zero_gamma']:,.0f}" if m["zero_gamma"] else "none within ±3%",
              f"{m['zero_gamma'] - m['spot']:+,.0f} vs spot" if m["zero_gamma"] else "", delta_color="off")
    g3.metric("PER-STRIKE CROSSOVER", f"{m['crossover']:,.0f}" if m["crossover"] else "—", "call- vs put-dominated OI boundary", delta_color="off")
    g4.metric("NET GEX (±8)", f"{m['net_gex']:+,.3g}", f"{m['net_gex'] / m['gross_gex'] * 100:+.1f}% of gross" if m["gross_gex"] else "", delta_color="off")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("SYNTHETIC FUTURE", f"{m['syn_fut']:,.1f}" if m["surface_ok"] else "n/a", f"basis {m['basis']:+.1f} vs spot" if m["surface_ok"] else "no balanced near-ATM mids", delta_color="off")
    s2.metric("EXCESS OVER FAIR CARRY", f"{m['excess_basis']:+.1f} pts", f"fair {m['fair_fwd']:,.1f} (carry {m['carry']:+.1f})", delta_color="off")
    s3.metric("25Δ RISK REVERSAL", f"{m['rr_25']:+.2f}", f"{m.get('rr_shift', 0.0):+.2f} vs session open", delta_color="off")
    s4.metric("25Δ BUTTERFLY", f"{m['bf_25']:+.2f}", f"VEX {m['net_vex']:+,.3g} | CEX {m['net_cex']:+,.3g}/day", delta_color="off")
    st.caption("Basis alone is mostly cost of carry (it grows with time to expiry), so the check uses the EXCESS over the fair forward. "
               "Risk reversal is normally negative on index options (put skew); watch its change, not its level. "
               f"The surface check is {'COUNTED in' if USE_SURFACE_CHECK['on'] else 'logged only (observe-only), NOT counted in'} the agreement score.")
    _cdf = m["df"][(m["df"]["Strike"] >= spot - 10 * m["step"]) & (m["df"]["Strike"] <= spot + 10 * m["step"])].copy()
    if not _cdf.empty:
        _c1, _c2 = st.columns(2)
        with _c1:
            st.markdown("##### Signed net GEX per strike")
            st.bar_chart(_cdf.set_index("Strike")[["Net_GEX"]])
        with _c2:
            st.markdown("##### Implied volatility by strike")
            _iv = _cdf[(_cdf["CE_IV"] > 0) & (_cdf["PE_IV"] > 0)].set_index("Strike")[["CE_IV", "PE_IV"]]
            if not _iv.empty:
                st.line_chart(_iv)
