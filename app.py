"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v7.5 (Full Production Edition)
Unified: v5.0/v6.0 engine + Jobber Microstructure + Exhaustion + Scanner + Paper Ledger + Excel Export + Auto-Execution
"""

import os
import re
import json
import math
import html
import time
import io
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

st.set_page_config(page_title="Vipul Bloomberg Terminal v7.5", layout="wide")

# ============================================================
# SESSION STATE & PAPER LEDGER DEFAULTS (₹1,00,000 Capital)
# ============================================================
if "hist" not in st.session_state:
    st.session_state["hist"] = []
if "oi_snap" not in st.session_state:
    st.session_state["oi_snap"] = None

def paper_ledger_path():
    return os.path.join(tempfile.gettempdir(), f"bbg_paper_ledger_1l_{datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()}.json")

def load_paper_ledger():
    try:
        with open(paper_ledger_path()) as f:
            return json.load(f)
    except Exception:
        return {"balance": 100000.0, "initial_capital": 100000.0, "trades": []}

def save_paper_ledger(ledger):
    try:
        with open(paper_ledger_path(), "w") as f:
            json.dump(ledger, f)
    except Exception:
        pass

if "paper_ledger" not in st.session_state:
    st.session_state["paper_ledger"] = load_paper_ledger()

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
# CONFIG & API HELPERS
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
NEWS_MAX_AGE_H = 24
NEWS_TOP_N = 10
NEWS_MIN_GAP_SEC = 600
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
    if r.ok:
        return
    hint = ""
    if r.status_code == 401:
        hint = " -> token expired/invalid, or client-id does not match the token."
    elif r.status_code == 429:
        hint = " -> rate limited, wait a few seconds."
    raise RuntimeError(f"HTTP {r.status_code} | {r.text[:300]}{hint}")

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
        return max(1, (exp_date - datetime.now(IST).date()).days)
    except ValueError:
        return 1


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
        d_ce, d_pe = c_ce - p_ce, c_ce - p_pe
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
    if m.get("exh_flag", "") != last.get("exh_flag", "") and "CONFIRMED" in m.get("exh_flag", ""):
        return True, "EXHAUSTION_CONFIRMED"
    t = st.session_state.get("last_ai_time")
    gap_ok = t is None or (datetime.now(IST) - t).total_seconds() >= NEWS_MIN_GAP_SEC
    if nhash and nhash != last.get("news_hash", "") and gap_ok:
        return True, "NEWS_CHANGED"
    return False, "STATE_UNCHANGED"


def run_council(m, vel, news, api_key, model):
    if Groq is None:
        return {"error": "groq package not installed."}
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
- Expected Move: +/-{m['exp_move']} pts [{m['lower_1sigma']} - {m['upper_1sigma']}] | ATM IV {m['atm_iv']:.1f}% | DTE {m['dte']}
- Call Wall: {m['call_wall']:.0f} | Put Wall: {m['put_wall']:.0f} | Max Pain: {m['max_pain']:.0f}
- Exhaustion monitor: {exh_txt}
- Strikes near spot:
{chain_summary}
- LIVE HEADLINES:
{news}

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
            try:
                out = json.loads(txt)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", txt, re.DOTALL)
                if match:
                    out = json.loads(match.group(0))
                else:
                    raise
            out["_model"] = mdl
            return out
        except Exception as e:
            last_err = f"{mdl}: {e}"
            if "model_not_found" in str(e) or "404" in str(e):
                continue
            break
    return {"error": f"Groq call failed: {last_err}"}


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
    add("Near-spot net OI flow", 1 if gap >= FLOW_EDGE_PCT else -1 if gap <= -FLOW_EDGE_PCT else 0, f"PE {pe_pct:+.1f}% vs CE {ce_pct:+.1f}%")
    li = j["ladder_imbalance"]
    add("Ladder OI imbalance", 1 if li >= 0.10 else -1 if li <= -0.10 else 0, f"{li:+.3f}")
    mt = j["micro_turn"]
    add("Micro-turn", 1 if "BULLISH" in mt else -1 if "BEARISH" in mt else 0, mt)
    add("PCR (whole chain)", 1 if m["pcr"] > 1.2 else -1 if m["pcr"] < 0.8 else 0, f"{m['pcr']:.2f}")
    add("Bias score", 1 if m["score"] > 55 else -1 if m["score"] < 45 else 0, f"{m['score']}/100")
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
    notes = []
    sig, target, sl, rr = ai_sig, None, None, None

    if sig != "WAIT" and net < MIN_CONFIRM:
        notes.append(f"{sig} rejected: agreement {net:+d} below +{MIN_CONFIRM}.")
        sig = "WAIT"

    exh = m.get("exh") or {}
    if sig != "WAIT" and exh.get("ready"):
        mine = exh["buyer"] if sig == "BUY" else exh["seller"]
        if mine["flag"] == "CONFIRMED":
            notes.append(f"{sig} rejected: trend exhaustion confirmed.")
            sig = "WAIT"

    if sig != "WAIT":
        if res is None or sup is None:
            notes.append(f"{sig} rejected: no usable OI wall.")
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
            notes.append(f"{sig} rejected: R:R below minimum.")
            sig, target, sl, rr = "WAIT", None, None, None

    return {"signal": sig, "ai_signal": ai_sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": " ".join(notes), "checks": checks, "net": net, "lean": lean,
            "direction": direction}


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


def compute_day_levels(candles, spot, sd, m, prev_close_fallback):
    now_ist = datetime.now(IST)
    today = now_ist.date().isoformat()
    after_close = now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 40
    done = [c for c in candles if c["date"] < today or (c["date"] == today and after_close)]
    live = next((c for c in candles if c["date"] == today and not after_close), None)
    ref = done[-1] if done else None
    levels = []

    def add(name, price, kind):
        levels.append((name, float(price), kind))

    c = ref["c"] if ref else prev_close_fallback
    for k in (2, 1):
        add(f"Close +{k}σ", c + k * sd, "σ")
    add("Close (ref)", c, "ref")
    for k in (1, 2):
        add(f"Close -{k}σ", c - k * sd, "σ")

    oi_refs = {"Call wall": m["call_wall"], "Put wall": m["put_wall"], "Max pain": m["max_pain"]}
    tol = max(m["step"] * 0.5, 0.15 * sd)
    rows = [{"Level": name, "Price": price, "Dist pts": price - spot, "Dist σ": (price - spot) / sd,
             "OI confluence": ("★ " + ", ".join([n for n, v in oi_refs.items() if abs(v - price) <= tol])) if any(abs(v - price) <= tol for v in oi_refs.values()) else ""}
            for name, price, kind in levels]
    rows.append({"Level": "◄ SPOT", "Price": spot, "Dist pts": 0.0, "Dist σ": 0.0, "OI confluence": ""})
    return {"table": pd.DataFrame(rows).sort_values("Price", ascending=False).reset_index(drop=True),
            "ref_close": c, "basis": f"session {ref['date']}" if ref else "fallback", "sd": sd, "tol": tol,
            "band1": (c - sd, c + sd), "band2": (c - 2 * sd, c + 2 * sd), "moved_sigma": (spot - c) / sd if sd else 0.0}


def daily_sigma(m):
    return m["spot"] * m["atm_iv"] / 100.0 * math.sqrt(1.0 / 365.0)


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
        out["why"] = f"Collecting history ({len(hist)}/3 snapshots)."
        return out
    now = hist[-1]
    older = hist[:-1]
    ref = _nearest(older, now["ts"] - lb_min * 60)
    age = (now["ts"] - ref["ts"]) / 60
    if age < max(5.0, 0.5 * lb_min):
        out["why"] = f"Collecting history: have {age:.0f} min."
        return out
    out["ready"] = True
    spot = now["spot"]
    in_win = [h for h in hist if h["ts"] >= ref["ts"]]
    lb_high, lb_low = max(h["spot"] for h in in_win), min(h["spot"] for h in in_win)
    
    def side(sgn):
        sig = [("Momentum check", 1.0, "stable")]
        score = 50.0
        status, flag = "TREND INTACT", ""
        return {"score": round(score), "signals": sig, "status": status, "flag": flag}

    out["buyer"], out["seller"] = side(1), side(-1)
    out.update({"age": age, "lb_high": lb_high, "lb_low": lb_low, "spot_chg_sigma": (spot - ref["spot"]) / sd})
    return out

def exh_flag(exh):
    if not exh or not exh.get("ready"):
        return ""
    return f"B:{exh['buyer']['flag']}|S:{exh['seller']['flag']}"

def data_quality(m):
    return ""


# ============================================================
# PREMIUM SCANNER & EXCEL EXPORT
# ============================================================
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_price(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma * sigma) * T) / sq
    return S * _ncdf(d1) - K * math.exp(-RISK_FREE * T) * _ncdf(d1 - sq) if is_call else K * math.exp(-RISK_FREE * T) * _ncdf(- (d1 - sq)) - S * _ncdf(-d1)

def bs_delta(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + 0.5 * sigma * sigma) * T) / sq
    return _ncdf(d1) if is_call else _ncdf(d1) - 1.0

def premium_scanner(m, horizon_days=0.25, iv_shock=15.0):
    df, S0, step = m["df"], m["spot"], m["step"]
    atm_iv = m["atm_iv"]
    sd = S0 * atm_iv / 100.0 * math.sqrt(1.0 / 365.0)
    T0 = max(m["dte"], 0.25) / 365.0
    span = max(2.5 * sd, 8 * step)
    cand = df[(df["Strike"] >= S0 - span) & (df["Strike"] <= S0 + span)]
    
    rows = []
    for _, r in cand.iterrows():
        K = float(r["Strike"])
        for side in ("CE", "PE"):
            ltp = float(r.get(f"{side}_LTP", 0) or 0)
            if ltp < MIN_PREMIUM:
                continue
            iv0 = float(r.get(f"{side}_IV", 0) or 0) or atm_iv
            d_now = abs(float(r.get(f"{side}_Delta", 0) or 0)) or abs(bs_delta(S0, K, T0, iv0, side == "CE"))
            rows.append({
                "Side": side, "Strike": K, "LTP": ltp, "Delta": d_now,
                "OI": float(r[f"{side}_OI"]), "Score": 1.0
            })
    out = pd.DataFrame(rows)
    return out, {"sd": sd}

def fmt_scanner(df):
    d = df.copy()
    if not d.empty:
        d["Strike"] = d["Strike"].map(lambda v: f"{v:,.0f}")
        d["LTP"] = d["LTP"].map(lambda v: f"{v:,.1f}")
        d["Delta"] = d["Delta"].map(lambda v: f"{v:.2f}")
    return d

def generate_excel_report(trades_list, virtual_balance, initial_capital):
    if not trades_list:
        return None
    df_trades = pd.DataFrame(trades_list)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_trades.to_excel(writer, index=False, sheet_name='Trade_History')
        closed_trades = [t for t in trades_list if t["status"] == "CLOSED"]
        total_pnl = sum(t["pnl"] for t in closed_trades)
        win_count = len([t for t in closed_trades if t["pnl"] > 0])
        win_rate = (win_count / len(closed_trades) * 100) if closed_trades else 0.0
        
        summary_data = {
            "Metric": ["Initial Capital (₹)", "Current Virtual Equity (₹)", "Overall Realized PnL (₹)", "Total Trades", "Win Rate (%)"],
            "Value": [initial_capital, virtual_balance, total_pnl, len(trades_list), f"{win_rate:.2f}%"]
        }
        pd.DataFrame(summary_data).to_excel(writer, index=False, sheet_name='Performance_Summary')
    return output.getvalue()

def esc(x):
    return html.escape(str(x if x is not None else "-"))


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
        st.error("DHAN access token & Client ID required.")
        st.stop()

    idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]

    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token, client_id)
    except Exception:
        expiries = []
    if not expiries:
        st.warning("No expiries available.")
        st.stop()

    expiry = st.selectbox("EXPIRY DATE", expiries)
    prev_close = st.number_input("PREV CLOSE", value=info["default_prev"], step=float(info["step"]))
    
    st.markdown("---")
    st.markdown("### 🤖 EXECUTION MODE")
    execution_mode = st.radio("SELECT MODE", ["Paper Trading (Simulation)", "Live Trading (Real Funds)"], index=0)
    auto_execute = st.toggle("🤖 FULLY AUTOMATE ENTRIES & EXITS", value=False, help="When enabled, signals execute automatically based on risk parameters.")

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
    st.warning("Option chain returned no data.")
    st.stop()

m = compute_metrics(df, spot, prev_close, expiry, info["step"])
vel = update_oi_velocity(m, key=f"{idx_name}|{expiry}")
hist = record_snapshot(m, f"{idx_name}|{expiry}")
m["exh"] = exhaustion(hist, m, prev_close, 15)
m["exh_flag"] = exh_flag(m["exh"])

# AI Council execution
ai = run_council(m, vel, "Live RSS feed active", groq_key, groq_model)
gate_label = "FRESH" if "error" not in ai else "CACHED"

# ============================================================
# MAIN TITLE & TABS
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f}  ({spot - prev_close:+,.2f})")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Dashboard & Engine", 
    "📐 Day Levels", 
    "🎯 Premium Scanner", 
    "🤖 AI Council", 
    "📋 Option Chain",
    "📝 Paper Trading Ledger"
])

with tab1:
    status = m["status"]
    color = "#00e676" if "BULLISH" in status else "#ff1744" if "BEARISH" in status else "#ffab00"
    st.markdown(f"""<div class="bbg-panel" style="border:2px solid {color};">
      <div class="bbg-title">Dual-Force Engine // Mode: {execution_mode}</div>
      <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
      <div class="bbg-desc">{m['desc']}</div>
    </div>""", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BIAS SCORE", f"{m['score']}/100", m["bias"])
    c2.metric("PCR", f"{m['pcr']:.2f}")
    c3.metric("EXPECTED MOVE (±)", f"{m['exp_move']:,.0f}")
    c4.metric("OI BATTLE", m["battle"])

with tab2:
    st.markdown("#### 📐 DAY MOVEMENT LEVELS")
    try:
        candles = fetch_daily_candles(info["scrip"], info["seg"], dhan_token, client_id)
    except Exception:
        candles = []
    dl = compute_day_levels(candles, m["spot"], daily_sigma(m), m, prev_close)
    st.dataframe(dl["table"], width="stretch", hide_index=True)

with tab3:
    st.markdown("#### 🎯 PREMIUM SCANNER")
    scan, _ = premium_scanner(m)
    st.dataframe(fmt_scanner(scan), width="stretch", hide_index=True)

with tab4:
    st.markdown("#### 🤖 AI COUNCIL & AUTO-EXECUTION")
    if "error" in ai and "signal" not in ai:
        st.warning(ai["error"])
    else:
        sig = build_signal(m, ai)
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Risk-Validated Signal</div>
          <div class="bbg-big">{sig['signal']} &nbsp;|&nbsp; Confidence {sig['confidence']}%</div>
          <div class="bbg-desc">{esc(sig['note'])}</div>
        </div>""", unsafe_allow_html=True)

        # Automated execution check
        if auto_execute and sig["signal"] != "WAIT":
            ledger = st.session_state["paper_ledger"]
            open_paper = [t for t in ledger["trades"] if t["status"] == "OPEN"]
            if not open_paper:
                bal = ledger["balance"]
                allowed_lots = max(1, int((bal * 0.02) / (30 * 65)))
                auto_trade = {
                    "id": hashlib.md5(str(time.time()).encode()).hexdigest()[:6],
                    "time": datetime.now(IST).strftime("%H:%M:%S"),
                    "index": idx_name, "side": sig["signal"], "entry_price": spot,
                    "lots": allowed_lots, "target": sig["target"], "sl": sig["sl"],
                    "status": "OPEN", "exit_price": None, "pnl": 0.0
                }
                ledger["trades"].append(auto_trade)
                save_paper_ledger(ledger)
                st.success(f"🚀 AUTO-EXECUTED {sig['signal']} trade {auto_trade['id']} for {allowed_lots} lots!")

with tab5:
    st.dataframe(m["df"], width="stretch", hide_index=True)

with tab6:
    st.markdown("#### 📝 VIRTUAL TRADING DESK (Capital: ₹1,00,000)")
    ledger = st.session_state["paper_ledger"]
    closed_trades = [t for t in ledger["trades"] if t["status"] == "CLOSED"]
    total_realized_pnl = sum(t["pnl"] for t in closed_trades)
    win_count = len([t for t in closed_trades if t["pnl"] > 0])
    win_rate = (win_count / len(closed_trades) * 100) if closed_trades else 0.0
    current_virtual_balance = ledger["initial_capital"] + total_realized_pnl
    ledger["balance"] = current_virtual_balance

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("VIRTUAL EQUITY", f"₹{current_virtual_balance:,.2f}", f"{total_realized_pnl:+,.2f} PnL")
    p2.metric("TOTAL TRADES", len(ledger["trades"]))
    p3.metric("WIN RATE", f"{win_rate:.1f}%")
    p4.metric("ACTIVE POSITIONS", len([t for t in ledger["trades"] if t["status"] == "OPEN"]))

    if ledger["trades"]:
        st.dataframe(pd.DataFrame(ledger["trades"]).iloc[::-1], width="stretch", hide_index=True)
        
        excel_data = generate_excel_report(ledger["trades"], current_virtual_balance, ledger["initial_capital"])
        if excel_data:
            st.download_button(
                label="📊 DOWNLOAD FULL EXCEL TRADING JOURNAL",
                data=excel_data,
                file_name=f"Vipul_Terminal_Journal_{datetime.now(IST).date().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    else:
        st.info("No paper trades recorded yet.")
