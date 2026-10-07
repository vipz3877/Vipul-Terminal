"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v7.1 (debugged)
Unified: v5.0/v6.0 engine (Gamma Blast / IV Spike, walls, traps, expected move, AI Council)
       + Jobber Microstructure Engine (ladder imbalance, pocket support, micro-turns)

Fixes in v7.1
 - Client ID is now an editable sidebar input and is passed to every API call
   (it used to be a hardcoded fallback that st.secrets could silently override).
 - Token is sanitised (whitespace / quotes / "Bearer " prefix removed).
 - API errors now show Dhan's real response body instead of a bare "401".
 - Diagnostics panel with a /v2/profile token test.
 - OI velocity bug fixed (PE delta used CE current OI).
 - build_signal now enforces a minimum R:R instead of a near-useless ordering check.
 - AI text is HTML-escaped before rendering.
 - Minimum gap between option-chain calls (Dhan limit: ~1 request / 3 sec).
 - Signal validation now cross-checks the option chain (7 independent reads, must net-agree) and
   derives target/SL from the nearest OI walls instead of the far global walls.
 - Live news: Google News RSS headlines feed the Sentiment agent; news changes can re-trigger the AI.
 - ATM IV for expected move = average of CE and PE IV.
 - Dual-Force engine v2: near-spot strikes only, thresholds relative to OI (%), dominance test,
   CONFLICTED state, and full flow breakdown passed to the AI Council.
"""

import os
import re
import json
import math
import html
import time
import hashlib
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v7.1", layout="wide")

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

# ---- live news ----
NEWS_MAX_AGE_H = 24       # headlines older than this are ignored
NEWS_TOP_N = 10           # headlines sent to the AI / shown on screen
NEWS_MIN_GAP_SEC = 600    # a news change can re-trigger the AI at most this often
# Optional extra RSS feeds, comma-separated, e.g. in secrets: NEWS_EXTRA_FEEDS = "https://.../rss.xml,https://..."
NEWS_EXTRA_FEEDS = tuple(u.strip() for u in get_secret("NEWS_EXTRA_FEEDS", "").split(",") if u.strip())

INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50, "default_prev": 24500.0},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100, "default_prev": 55000.0},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50, "default_prev": 26000.0},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25, "default_prev": 12500.0},
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


def _n(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    # Simple throttle to respect the 1 req / 3 sec limit
    last = st.session_state.get("_last_chain_call", 0.0)
    wait = MIN_CHAIN_GAP_SEC - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    st.session_state["_last_chain_call"] = time.time()

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
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return spot, df
    return spot, df.sort_values("Strike").reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_prev_close(scrip, seg, token, client_id):
    """Best-effort previous close via Dhan market quote. Returns (value or None, source/message)."""
    try:
        r = requests.post(f"{BASE_URL}/marketfeed/quote", headers=auth_headers(token, client_id),
                          json={seg: [scrip]}, timeout=10)
        check(r)
        q = (r.json().get("data") or {}).get(seg, {}).get(str(scrip)) or {}
        ltp = _n(q.get("last_price"))
        net = _n(q.get("net_change"))
        if ltp > 0 and net != 0:
            return round(ltp - net, 2), "quote: LTP - net_change"
        close = _n((q.get("ohlc") or {}).get("close"))
        if close > 0:
            return round(close, 2), "quote: ohlc.close (may equal today's close after market hours)"
        return None, "quote returned no usable close"
    except Exception as e:
        return None, f"auto fetch failed: {e}"


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
        return max(1, (exp_date - date.today()).days)
    except ValueError:
        return 1


# ============================================================
# ANALYSIS & JOBBER MICROSTRUCTURE ENGINE
# ============================================================
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
    e = engine_sums(near)
    if near is None or near.empty:
        return "BALANCED ACCUMULATION", "WAIT", "No near-spot strikes available."

    base_bull = e["ce_exit_pct"] >= MIN_FLOW_PCT and e["pe_build_pct"] >= MIN_FLOW_PCT
    base_bear = e["pe_exit_pct"] >= MIN_FLOW_PCT and e["ce_build_pct"] >= MIN_FLOW_PCT
    bull_ok = base_bull and e["bull_force"] >= DOMINANCE * e["bear_force"]
    bear_ok = base_bear and e["bear_force"] >= DOMINANCE * e["bull_force"]

    if bull_ok:
        return ("BULLISH GAMMA BLAST", "BUY",
                f"Call writers exiting ({e['ce_exit_pct']:.1f}%) + put writers building ({e['pe_build_pct']:.1f}%); "
                f"bull force {e['bull_force']:.1f} vs bear {e['bear_force']:.1f}.")
    if bear_ok:
        return ("BEARISH GAMMA BLAST", "SELL",
                f"Put writers exiting ({e['pe_exit_pct']:.1f}%) + call writers building ({e['ce_build_pct']:.1f}%); "
                f"bear force {e['bear_force']:.1f} vs bull {e['bull_force']:.1f}.")
    if base_bull or base_bear:
        return ("CONFLICTED", "WAIT",
                f"Opposing flows both active with no clear winner (bull force {e['bull_force']:.1f} vs "
                f"bear force {e['bear_force']:.1f}, ratio {e['force_ratio']:.2f} < {DOMINANCE}). Stand aside.")

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
    exp_move = spot * (atm_iv / 100) * math.sqrt(dte / YEAR_DAYS)

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
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "jobber": jobber, "engine": engine, "struct": struct,
        "iv_gap": iv_gap, "atm_iv_ce": ce_iv, "atm_iv_pe": pe_iv,
    }


# ============================================================
# 3-MIN OI VELOCITY
# ============================================================
ZERO_VEL = {"ce_builds": 0.0, "ce_unwinds": 0.0, "pe_builds": 0.0, "pe_unwinds": 0.0}


def update_oi_velocity(m, key):
    now = datetime.now()
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
    if m["status"] != last["status"]:
        return True, "ENGINE_STATUS_CHANGE"
    trap_now = (m["ce_trap"] is not None) or (m["pe_trap"] is not None)
    if trap_now and not last.get("trap_active", False):
        return True, "35PT_TRAP_DETECTED"
    if vel["ce_unwinds"] > 40000 or vel["pe_unwinds"] > 40000:
        return True, "HIGH_UNWIND_VELOCITY"
    t = st.session_state.get("last_ai_time")
    gap_ok = t is None or (datetime.now() - t).total_seconds() >= NEWS_MIN_GAP_SEC
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
- Dual-Force flow (near-spot strikes only, % = share of that side's near-spot OI):
  CE exits {eg['ce_exits']:,.0f} ({eg['ce_exit_pct']:.1f}%) | CE builds {eg['ce_build']:,.0f} ({eg['ce_build_pct']:.1f}%) | CE net flow {eg['ce_net_flow']:+,.0f}
  PE exits {eg['pe_exits']:,.0f} ({eg['pe_exit_pct']:.1f}%) | PE builds {eg['pe_build']:,.0f} ({eg['pe_build_pct']:.1f}%) | PE net flow {eg['pe_net_flow']:+,.0f}
  Bull force {eg['bull_force']:.1f} vs Bear force {eg['bear_force']:.1f} (ratio {eg['force_ratio']:.2f}; a winner needs >= {DOMINANCE})
- OI structure around spot: {struct_txt}
- Definitions: Bull force = PE builds% + CE exits%; Bear force = CE builds% + PE exits%. Put OI BUILDING is bullish support, call OI BUILDING is bearish resistance. Do not call put builds bearish.
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
    lean = sum(c["v"] for c in checks)                      # + bullish / - bearish
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
        prev_close = st.number_input("PREV CLOSE", value=info["default_prev"], step=float(info["step"]),
                                     key=f"prev_{idx_name}",
                                     help="Yesterday's close. Used by the bias score.")
    live_news = st.toggle("LIVE NEWS FEED", value=True,
                          help="Fetch fresh market headlines (Google News RSS) for the Sentiment agent.")
    news = st.text_area("EXTRA NEWS / MACRO NOTES", placeholder="Optional: add your own notes on top of the live feed",
                        height=80)
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
if force_run or changed:
    ai = run_council(m, vel, news_text, groq_key, groq_model)
    if "error" not in ai:
        st.session_state.last_ai_verdict = ai
        st.session_state.last_ai_time = datetime.now()
        st.session_state.last_ai_state = {
            "spot": m["spot"], "pcr": m["pcr"], "call_wall": m["call_wall"],
            "put_wall": m["put_wall"], "gamma_strike": m["gamma_strike"], "status": m["status"],
            "trap_active": bool(m["ce_trap"] or m["pe_trap"]),
            "news_hash": nhash,
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

# ============================================================
# DASHBOARD
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f}  ({spot - prev_close:+,.2f})")

status = m["status"]
if "BULLISH GAMMA BLAST" in status:
    color, flash = "#00e676", "flash-green"
elif "BEARISH GAMMA BLAST" in status:
    color, flash = "#ff1744", "flash-red"
elif "TRAP" in status or "CONFLICTED" in status:
    color, flash = "#ffab00", "flash-amber"
else:
    color, flash = "#90a4ae", ""

st.markdown(f"""
<div class="bbg-panel {flash}" style="border:2px solid {color};">
  <div class="bbg-title">Dual-Force Engine</div>
  <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
  <div class="bbg-desc">{m['desc']}</div>
</div>""", unsafe_allow_html=True)

if abs(prev_close - spot) / spot > 0.05:
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
st.caption("Bullish blast = CE exits + PE builds both >= threshold AND bull force >= 1.5x bear force. "
           "Bearish blast = mirror image. Both sides active with no clear winner = CONFLICTED | WAIT.")

if m["iv_gap"] > 3:
    st.caption(f"⚠ ATM IV mismatch: CE {m['atm_iv_ce']:.1f}% vs PE {m['atm_iv_pe']:.1f}% (often stale quotes after hours). "
               f"Expected move uses the average ({m['atm_iv']:.1f}%).")

c1, c2, c3, c4 = st.columns(4)
c1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
c2.metric("PCR", f"{m['pcr']:.2f}")
c3.metric("EXPECTED MOVE (±)", f"{m['exp_move']:,.0f}", f"IV {m['atm_iv']:.1f}% | {m['dte']}d")
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
    sig = build_signal(m, ai)
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

st.caption(f"1σ range: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now().strftime('%H:%M:%S')}")

with st.expander("OPTION CHAIN DATA"):
    st.dataframe(m["df"], use_container_width=True)
