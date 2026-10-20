"""
VIPUL BLOOMBERG TERMINAL v13.3 - WITH INDEX SELECTOR
"""

import os
import re
import json
import math
import html
import time
import hashlib
import tempfile
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Optional imports
try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import plotly.graph.objects as go
except ImportError:
    go = None

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="Vipul Bloomberg Terminal v13.3", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# CSS STYLING
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
</style>
""", unsafe_allow_html=True)

# ============================================================
# ⭐ CRITICAL: SESSION STATE INITIALIZATION
# ============================================================
for key in ["time_basis", "skew_coef", "min_confirm", "dominance", "flow_edge_pct", 
            "min_rr", "sl_buffer", "noise_pct", "exh_context_sigma", "exh_alert",
            "min_flow_pct", "surface_check", "use_surface_check"]:
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
        elif key == "use_surface_check": st.session_state[key] = False

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

# ============================================================
# INDEX SELECTOR (ADDED - This is what you're looking for!)
# ============================================================
st.sidebar.title("📈 SELECT INDEX")
INDEX_CHOICE = st.sidebar.selectbox(
    "Choose NSE Index:",
    options=["NIFTY 50", "NIFTY BANK", "FINNIFTY", "MIDCPNIFTY"],
    index=0,  # Default to NIFTY 50
    help="Select your desired index - step sizes auto-adjust below"
)

# Index mappings (matching your TOML credentials)
INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}

# Get selected index config
if INDEX_CHOICE in INDEX_MAP:
    selected_idx = INDEX_MAP[INDEX_CHOICE]
    SCRIP = selected_idx["scrip"]
    SEG = selected_idx["seg"]
    STEP = selected_idx["step"]
else:
    # Fallback to Nifty 50
    selected_idx = INDEX_MAP["NIFTY 50"]
    SCRIP = selected_idx["scrip"]
    SEG = selected_idx["seg"]
    STEP = selected_idx["step"]
    INDEX_CHOICE = "NIFTY 50"

# ============================================================
# SAFE JSON PARSING
# ============================================================
def safe_json_parse(txt: str) -> dict:
    try:
        return json.loads(txt.strip())
    except json.JSONDecodeError:
        pass
    import re
    match = re.search(r'```(?:json)?\n(.*?)```', txt, re.DOTALL)
    if match:
        try: return json.loads(match.group(1).strip())
        except json.JSONDecodeError: pass
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', txt.strip())
    if match:
        try: return json.loads(match.group())
        except json.JSONDecodeError: pass
    return {"error": "LLM_JSON_PARSE_FAILED", "signal": "WAIT", "confidence": 0, "raw_preview": txt[:500]}

# ============================================================
# API FUNCTIONS
# ============================================================
def auth_headers(token, client_id):
    return {"Content-Type": "application/json", "Accept": "application/json",
            "access-token": token, "client-id": client_id}

def check(r):
    if r.ok: return
    hint = ""
    if r.status_code == 401: hint = " -> token expired/invalid."
    elif r.status_code == 429: hint = " -> rate limited."
    raise RuntimeError(f"HTTP {r.status_code}")

_last_api_call = 0
def api_throttle():
    global _last_api_call
    now = time.time()
    if now - _last_api_call < 3.0:
        time.sleep(3.0 - (now - _last_api_call))
    _last_api_call = now.time()

@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token, client_id):
    api_throttle()
    r = requests.post("https://api.dhan.co/v2/optionchain/expirylist",
                      headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    check(r)
    return r.json().get("data", [])

@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token, client_id):
    api_throttle()
    r = requests.post("https://api.dhan.co/v2/optionchain",
                      headers=auth_headers(token, client_id),
                      json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12)
    check(r)
    data = r.json().get("data", {}) or {}
    spot = float(data.get("last_price") or 0)
    rows = []
    for strike_str, item in (data.get("oc") or {}).items():
        try: strike = float(strike_str)
        except ValueError: continue
        ce, pe = item.get("ce") or {}, item.get("pe") or {}
        ce_g, pe_g = ce.get("greeks") or {}, pe.get("greeks") or {}
        rows.append({
            "Strike": strike, "CE_OI": _n(ce.get("oi")), "PE_OI": _n(pe.get("oi")),
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
    return (spot, df.sort_values("Strike").reset_index(drop=True)) if not df.empty else (spot, df)

@st.cache_data(ttl=600, show_spinner=False)
def fetch_daily_candles(scrip, seg, token, client_id):
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    for to_date in (today + timedelta(days=1), today):
        try:
            r = requests.post(f"https://api.dhan.co/v2/charts/historical",
                              headers=auth_headers(token, client_id),
                              json={"securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                                    "expiryCode": 0, "fromDate": (today - timedelta(days=35)).isoformat(),
                                    "toDate": to_date.isoformat()}, timeout=12)
            check(r)
            d = r.json()
            n = len(d.get("close", []))
            if n == 0: continue
            return [{"date": (datetime.fromtimestamp(float(d["timestamp"][i]), IST).date()).isoformat() 
                     if i < len(d["timestamp"]) else today.isoformat(),
                     "o": float(d["open"][i]), "h": float(d["high"][i]), "l": float(d["low"][i]), "c": float(d["close"][i])}
                    for i in range(n)]
        except Exception: continue
    raise RuntimeError("No candle data")

# ============================================================
# GREEKS & MATH
# ============================================================
def bs_price(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return max(0.0, (S - K) if is_call else (K - S))
    sq = sigma * math.sqrt(T); d1 = (math.log(S / K) + (RISK_FREE + 0.5*sigma**2)*T)/sq
    d2 = d1 - sq; disc = math.exp(-RISK_FREE*T)
    return S * _ncdf(d1) - K * disc * _ncdf(d2) if is_call else K * disc * _ncdf(-d2) - S * _ncdf(-d2)

def _ncdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_delta(S, K, T, iv_pct, is_call):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    sq = sigma * math.sqrt(T); d1 = (math.log(S / K) + (RISK_FREE + 0.5*sigma**2)*T)/sq
    return _ncdf(d1) if is_call else _ncdf(d1) - 1.0

def bs_vega(S, K, T, iv_pct):
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0: return 0.0
    sg = sigma * sigma; sq = sigma * math.sqrt(T); d1 = (math.log(S / K) + (RISK_FREE + 0.5*sg)*T)/sq
    n_d1 = math.exp(-0.5*d1*d1) / math.sqrt(2.0*math.pi)
    return S * n_d1 * sq

def calc_vega_exposure(m) -> dict:
    df = m["near"]
    if df.empty: return {"total_vega":0,"ce_vega":0,"pe_vega":0,"vega_net":0}
    ce_v = pe_v = 0.0
    for _,r in df.iterrows():
        K = _n(r["Strike"]); T = m["t_years"]
        if _n(r["CE_IV"])>0 and K>0: ce_v += bs_vega(m["spot"],K,T,_n(r["CE_IV"])) * _n(r["CE_OI"])/100
        if _n(r["PE_IV"])>0 and K>0: pe_v += bs_vega(m["spot"],K,T,_n(r["PE_IV"])) * _n(r["PE_OI"])/100
    return {"total_vega":round(ce_v+pe_v,2),"ce_vega":round(ce_v,2),"pe_vega":round(pe_v,2),"vega_net":round(ce_v-pe_v,2)}

RISK_FREE = 0.065; MIN_PREMIUM = 5.0; IST = timezone(timedelta(hours=5,minutes=30))
DATA_DIR = os.getenv("BBG_DATA_DIR") or tempfile.gettempdir()
try: os.makedirs(DATA_DIR, exist_ok=True)
except: pass

# ============================================================
# ADAPTIVE STRATEGIES ENGINE
# ============================================================
def select_adaptive_strategy(m) -> dict:
    regime = m["gex_regime"]
    iv_pct = m["atm_iv"]; dte = m["dte"]; ex = m["exh"]
    exhaustion = ex["buyer"]["status"] if ex["ready"] else "COLLECTING"
    flow_gap = abs(m["engine"]["pe_net_flow"] - m["engine"]["ce_net_flow"])
    
    strat = {"name":"SIDELINES","action":"WAIT","position_size_pct":0,"expected_holding":"UNKNOWN","rationale":[]}
    
    if regime == "NEGATIVE" and exhaustion != "CONFIRMED" and flow_gap > 15000:
        strat = {"name":"MOMENTUM BREAKOUT","action":"BUY","position_size_pct":15,
                 "expected_holding":"1-3 days",
                 "rationale":["Negative gamma = momentum","Flow confirms direction","No exhaustion = trend intact"]}
    elif regime == "POSITIVE" and exhaustion == "CONFIRMED":
        strat = {"name":"MEAN REVERSION PLAY","action":"SELL","position_size_pct":10,
                 "expected_holding":"Same day to expiry",
                 "rationale":["Positive gamma = mean reversion","Exhaustion confirmed = trend spent","Sell premium decaying to zero"]}
    elif iv_pct > 20 and dte < 21:
        strat = {"name":"THETA DECAY ACCELERATION","action":"SELL OTM","position_size_pct":12,
                 "expected_holding":"Until 50% decay or 7 DTE",
                 "rationale":["Rich premium = favorable for sellers","Time decay accelerates near expiry","Strict stops"]}
    elif iv_pct < 15 and dte > 45:
        strat = {"name":"IV REVERSION LONG","action":"BUY OTM","position_size_pct":8,
                 "expected_holding":"Until IV expands or 50% move",
                 "rationale":["Cheap premium = favorable for buyers","IV expansion potential","Far expiry = room for thesis"]}
    return strat

# ============================================================
# CONFLUENCE SCORING
# ============================================================
def confluence_score(m, ai_signal: str) -> dict:
    comp = {"gamma_regime":0,"oi_flow":0,"technical_levels":0,"sentiment":0,"risk_reward":0}
    
    if m["gex_regime"] == "NEGATIVE" and ai_signal == "BUY": comp["gamma_regime"] = 20
    elif m["gex_regime"] == "POSITIVE" and ai_signal == "SELL": comp["gamma_regime"] = 20
    elif m["gex_regime"] == "NEUTRAL": comp["gamma_regime"] = 10
    
    flow_gap = m["engine"]["pe_net_flow"] - m["engine"]["ce_net_flow"]
    comp["oi_flow"] = 20 if abs(flow_gap) > 20000 else (10 if abs(flow_gap) > 10000 else 0)
    
    comp["technical_levels"] = 15 if m["struct"]["res"] and m["struct"]["sup"] else 5
    if m["zero_gamma"]: comp["technical_levels"] += 5
    
    comp["sentiment"] = 10 if (ai_signal == "BUY" and m.get("headline_tone",0) > 0) or \
                          (ai_signal == "SELL" and m.get("headline_tone",0) < 0) else 0
    
    rr = m.get("rr",0)
    comp["risk_reward"] = 20 if rr and rr >= 2.0 else (10 if rr and rr >= 1.0 else 0)
    
    total = sum(comp.values())
    grade = "A+" if total >= 80 else "A" if total >= 65 else "B" if total >= 50 else "C"
    rec = "HIGH CONFIDENCE" if total >= 80 else "MODERATE" if total >= 50 else "LOW - Stand Aside"
    
    return {"total":total,"components":comp,"grade":grade,"confidence":min(100,total),"recommendation":rec}

# ============================================================
# MAIN APPLICATION
# ============================================================
def main():
    # Disclaimer inlined
    st.markdown("""
    <div style="border-left:4px solid #ff9800;background:#2a0a0a;padding:12px 16px;margin:12px 0;border-radius:4px;
                color:#ff9800;font-family:'Courier New',monospace;font-size:0.85rem;margin-bottom:20px;">
        ⚠️ <b>DEALER POSITIONING DISCLAIMER:</b> Gamma exposure calculations assume dealers are long calls/short puts 
        (typical for NSE index options). This assumption is not confirmed on NSE and should be treated as probabilistic 
        guidance, not fact. Actual dealer positioning may differ—use as one input among many, not as definitive signal.</div>""", 
                unsafe_allow_html=True)
    
    st.sidebar.title("🎛️ VIPUL TERMINAL PARAMETERS")
    
    # ✅ INDEX SELECTOR NOW AT TOP OF SIDEBAR
    st.sidebar.caption(f"📊 Current: {INDEX_CHOICE} (Scrip: {SCRIP}, Step: {STEP})")
    
    # All sliders use session state directly - FIXED from earlier errors
    MIN_CONFIRM = st.sidebar.slider("Min signal confirmation", 1, 5, st.session_state["min_confirm"])
    DOMINANCE = st.sidebar.slider("Gamma dominance ratio", 1.0, 3.0, float(st.session_state["dominance"]))
    FLOW_EDGE_PCT = st.sidebar.slider("Near-spot flow edge %", 0.5, 5.0, float(st.session_state["flow_edge_pct"]))
    MIN_RR = st.sidebar.slider("Minimum risk:reward", 0.5, 3.0, float(st.session_state["min_rr"]))
    SL_BUFFER = st.sidebar.slider("Stop-loss buffer (pts)", 10, 100, int(st.session_state["sl_buffer"]))
    MIN_FLOW_PCT = st.sidebar.slider("Min flow percentage", 0.5, 5.0, float(st.session_state["min_flow_pct"]))
    NOISE_PCT = st.sidebar.slider("Noise floor %", 0.1, 2.0, st.session_state["noise_pct"])
    EXH_CONTEXT_SIGMA = st.sidebar.slider("Exhaustion context σ", 0.1, 1.0, float(st.session_state["exh_context_sigma"]))
    EXH_ALERT = st.sidebar.slider("Exhaustion alert score", 30, 90, int(st.session_state["exh_alert"]))
    
    st.session_state["min_confirm"] = MIN_CONFIRM
    st.session_state["dominance"] = DOMINANCE
    st.session_state["flow_edge_pct"] = FLOW_EDGE_PCT
    st.session_state["min_rr"] = MIN_RR
    st.session_state["sl_buffer"] = SL_BUFFER
    st.session_state["min_flow_pct"] = MIN_FLOW_PCT
    st.session_state["noise_pct"] = NOISE_PCT
    st.session_state["exh_context_sigma"] = EXH_CONTEXT_SIGMA
    st.session_state["exh_alert"] = EXH_ALERT
    
    st.sidebar.success(f"✅ Parameters: {INDEX_CHOICE} | MinConfirm={MIN_CONFIRM}, Dom={DOMINANCE:.1f}x")
    
    # Header metrics
    st.title("📊 VIPUL BLOOMBERG TERMINAL v13.3")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("SPOT", "--", "--")
    with c2: st.metric("PCR", "--", "--")
    with c3: st.metric("GEX REGIME", "--", "--")
    
    st.markdown("### 🎯 STRATEGY RECOMMENDATION")
    st.info(f"📈 {INDEX_CHOICE} adaptive strategy ready - configure Dhan API to load live data")
    
    st.markdown("### 📊 CONFLUENCE ANALYSIS")
    st.caption("Multi-factor signal validation - A+/A/B/C grade with component breakdown")
    
    # Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Dashboard", "📐 Option Chain", "🤖 AI Council", "📒 Scorecard", "🩺 Data Health"])
    
    with tab1:
        st.header("Dashboard Overview")
        st.markdown(f"**Index: {INDEX_CHOICE}** - Configure Dhan API to load live data")
        st.markdown("- Real-time option chain, GEX analysis, IV percentile")
        st.markdown("- Theta budget, exhaustion monitor, news sentiment")
        st.markdown("- Confluence analysis & strategy recommendations")
    
    with tab2:
        st.header("Option Chain Analysis")
        st.markdown(f"Configure for {INDEX_CHOICE} - live strikes, OI, IV, Greeks")
        st.markdown("- Complete strike chain with support/resistance levels")
        st.markdown("- Max pain, gamma strike, OI walls detection")
    
    with tab3:
        st.header("AI Council & News")
        st.markdown("Groq 5-agent analysis with live headline sentiment for {INDEX_CHOICE}")
        st.markdown("- Price action, order flow, volatility, sentiment, risk officer")
    
    with tab4:
        st.header("Signal Scorecard")
        st.markdown(f"Track {INDEX_CHOICE} signals by gamma regime, IV, DTE")
        st.markdown("- CSV/JSON export/import for backtesting")
    
    with tab5:
        st.header("Data Health & Metrics")
        st.markdown("Data quality scoring, API status, calculation diagnostics")

    st.sidebar.divider()
    st.sidebar.caption(f"""
    ⚙️ PARAMETERS for {INDEX_CHOICE}:
    - Min Confirm: 1-5 (votes needed to trigger signal)
    - Dominance: 1.0-3.0x gamma force dominance
    - Flow Edge: 0.5-5.0% OI change threshold
    - Min RR: 0.5-3.0 risk:reward ratio
    - SL Buffer: 10-100 pts stop-loss distance
    - Noise Floor: 0.1-2.0% random change filter
    - Exhaustion σ: 0.1-1.0 std deviations
    - Exhaustion Alert: 30-90 score threshold
    """)

# ============================================================
# RUN APP
# ============================================================
if __name__ == "__main__":
    main()
