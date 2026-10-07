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
"""

import os
import json
import math
import html
import time
from datetime import datetime, date

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
GROQ_MODEL = get_secret("GROQ_MODEL", "llama-3.3-70b-versatile")

BASE_URL = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{BASE_URL}/optionchain"
EXPIRY_URL = f"{BASE_URL}/optionchain/expirylist"
PROFILE_URL = f"{BASE_URL}/profile"

YEAR_DAYS = 365
VELOCITY_WINDOW_SEC = 150
MIN_CHAIN_GAP_SEC = 3.0   # Dhan option chain limit: ~1 request / 3 sec
MIN_RR = 1.0              # minimum reward:risk to accept a BUY/SELL

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


def detect_gamma_blast(df):
    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()
    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()

    if ce_exits < -3000 and pe_build > 3000:
        return "BULLISH GAMMA BLAST", "BUY", "Call writers capitulating + Put writers building floors."
    if pe_exits < -3000 and ce_build > 3000:
        return "BEARISH GAMMA BLAST", "SELL", "Put writers capitulating + Call writers building ceilings."
    if ce_exits < -3000 and pe_build <= 1000:
        return "IV SPIKE BULL TRAP", "WAIT", "Isolated Call short covering without Put support. Trap risk."
    if pe_exits < -3000 and ce_build <= 1000:
        return "IV SPIKE BEAR TRAP", "WAIT", "Isolated Put short covering without Call support. Trap risk."
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


def compute_metrics(df, spot, prev_close, expiry_str, step):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    pcr = df["PE_OI"].sum() / df["CE_OI"].sum() if df["CE_OI"].sum() > 0 else 1.0
    dce, dpe = float(df["CE_OI_Chg"].sum()), float(df["PE_OI_Chg"].sum())

    atm_idx = (df["Strike"] - spot).abs().idxmin()
    atm_iv = float(df.loc[atm_idx, "CE_IV"]) or float(df.loc[atm_idx, "PE_IV"]) or 12.0
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
    status, signal, desc = detect_gamma_blast(df)
    jobber = calculate_jobber_microstructure(df, spot)

    return {
        "df": df, "near": near, "spot": spot, "step": step, "pcr": pcr,
        "dce": dce, "dpe": dpe, "battle": interpret_oi_battle(dce, dpe),
        "atm_iv": atm_iv, "dte": dte, "exp_move": round(exp_move, 2),
        "lower_1sigma": round(spot - exp_move, 2), "upper_1sigma": round(spot + exp_move, 2),
        "gamma_strike": gamma_strike, "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain,
        "ce_trap": ce_trap, "pe_trap": pe_trap,
        "score": score, "bias": bias, "status": status, "engine_signal": signal, "desc": desc,
        "jobber": jobber,
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
def evaluate_market_state_change(m, vel):
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
    return False, "STATE_UNCHANGED"


# ============================================================
# GROQ MULTI-AGENT COUNCIL
# ============================================================
def run_council(m, vel, news, api_key):
    if Groq is None:
        return {"error": "groq package not installed (pip install groq)."}
    if not api_key:
        return {"error": "No GROQ API key provided in sidebar."}

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
- Call Wall: {m['call_wall']:.0f} | Put Wall: {m['put_wall']:.0f} | Max Pain: {m['max_pain']:.0f} | Gamma strike: {m['gamma_strike']:.0f}
- 35pt traps: CE resistance={m['ce_trap']} | PE support={m['pe_trap']}
- 3-Min Flow: CE build={vel['ce_builds']:,.0f}, CE unwind={vel['ce_unwinds']:,.0f} | PE build={vel['pe_builds']:,.0f}, PE unwind={vel['pe_unwinds']:,.0f}
- Strikes near spot:
{chain_summary}
- News: {news or 'None provided'}

AGENTS:
1. Price Action Agent: spot vs structural levels
2. Order Flow Agent: OI changes and 35pt traps
3. Volatility Agent: IV regime, DTE decay, gamma
4. Sentiment Agent: news impact on direction
5. Risk Officer: final approval; reject if evidence is conflicting

Return ONLY this JSON:
{{"price_action_agent":"1 sentence","order_flow_agent":"1 sentence","volatility_agent":"1 sentence",
"news_agent":"1 sentence","final_approval":"synthesis","signal":"BUY or SELL or WAIT","confidence":0-100}}"""

    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        txt = resp.choices[0].message.content.strip()
        txt = txt.replace("```json", "").replace("```", "").strip()
        return json.loads(txt)
    except Exception as e:
        return {"error": f"Groq call failed: {e}"}


# ============================================================
# SIGNAL VALIDATION
# ============================================================
def build_signal(m, ai):
    sig = str(ai.get("signal", "WAIT")).upper()
    if sig not in ("BUY", "SELL", "WAIT"):
        sig = "WAIT"
    try:
        conf = int(float(ai.get("confidence", 0)))
    except (TypeError, ValueError):
        conf = 0

    entry = m["spot"]
    note = ""
    target = sl = None
    valid = True

    if sig == "BUY":
        target, sl = m["call_wall"], m["put_wall"]
        valid = target > entry > sl
    elif sig == "SELL":
        target, sl = m["put_wall"], m["call_wall"]
        valid = sl > entry > target

    if sig != "WAIT" and not valid:
        note = f"{sig} rejected: entry/target/SL ordering invalid vs walls."
        sig, target, sl = "WAIT", None, None

    rr = None
    if target is not None and sl is not None and abs(entry - sl) > 0:
        rr = abs(target - entry) / abs(entry - sl)
        if rr < MIN_RR:
            note = (note + " " if note else "") + f"{sig} rejected: R:R 1:{rr:.2f} below minimum 1:{MIN_RR:.1f}."
            sig, target, sl, rr = "WAIT", None, None, None
        elif abs(target - entry) > m["exp_move"] * 1.5:
            note = (note + " " if note else "") + "Target is beyond 1.5x expected move."

    return {"signal": sig, "entry": entry, "target": target, "sl": sl, "rr": rr,
            "confidence": conf, "note": note}


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
    prev_close = st.number_input("PREV CLOSE", value=info["default_prev"], step=float(info["step"]),
                                 help="Yesterday's close. Used by the bias score.")
    news = st.text_area("NEWS / MACRO NOTES", placeholder="Paste headlines for the Sentiment agent (optional)",
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

# ---- AI gating + cache ----
changed, reason = evaluate_market_state_change(m, vel)
if force_run or changed:
    ai = run_council(m, vel, news, groq_key)
    if "error" not in ai:
        st.session_state.last_ai_verdict = ai
        st.session_state.last_ai_time = datetime.now()
        st.session_state.last_ai_state = {
            "spot": m["spot"], "pcr": m["pcr"], "call_wall": m["call_wall"],
            "put_wall": m["put_wall"], "gamma_strike": m["gamma_strike"], "status": m["status"],
            "trap_active": bool(m["ce_trap"] or m["pe_trap"]),
        }
        gate_label = f"FRESH ({'FORCED' if force_run else reason})"
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
elif "TRAP" in status:
    color, flash = "#ffab00", "flash-amber"
else:
    color, flash = "#90a4ae", ""

st.markdown(f"""
<div class="bbg-panel {flash}" style="border:2px solid {color};">
  <div class="bbg-title">Dual-Force Engine</div>
  <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {m['engine_signal']}</div>
  <div class="bbg-desc">{m['desc']}</div>
</div>""", unsafe_allow_html=True)

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
    st.markdown(f"""
    <div class="bbg-panel" style="border:2px solid {scolor};">
      <div class="bbg-title">Risk-Validated Signal</div>
      <div class="bbg-big" style="color:{scolor};">{sig['signal']} &nbsp;|&nbsp; Confidence {sig['confidence']}%</div>
      <div class="bbg-desc">{lv}</div>
      <div class="bbg-desc" style="color:#ffab00;">{esc(sig['note']) if sig['note'] else ''}</div>
    </div>""", unsafe_allow_html=True)
    st.markdown(f"""
    <div class="bbg-panel">
      <div class="agent"><b>Price Action:</b> {esc(ai.get('price_action_agent'))}</div>
      <div class="agent"><b>Order Flow:</b> {esc(ai.get('order_flow_agent'))}</div>
      <div class="agent"><b>Volatility:</b> {esc(ai.get('volatility_agent'))}</div>
      <div class="agent"><b>Sentiment:</b> {esc(ai.get('news_agent'))}</div>
      <div class="agent"><b>Risk Officer:</b> {esc(ai.get('final_approval'))}</div>
    </div>""", unsafe_allow_html=True)

st.caption(f"1σ range: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now().strftime('%H:%M:%S')}")

with st.expander("OPTION CHAIN DATA"):
    st.dataframe(m["df"], use_container_width=True)
