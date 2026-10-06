"""
🏛️ VIPUL PROFESSIONAL TERMINAL v4.4 (IV SPIKE vs GAMMA BLAST ENGINE)
Option Chain Institutional Surveillance for Option Buyers
- Dual-Force Gamma Blast Detection (Bullish/Bearish)
- IV Spike & Single-Side Trap Filter
- Gamma Ladder Mapping & Dynamic Trade Levels
"""

import sys
import io

try:
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except (AttributeError, TypeError):
    pass

import os
import json
import math
from datetime import datetime
import requests
import pandas as pd
import numpy as np
import streamlit as st

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="Vipul Professional v4.4", layout="wide")

st.markdown("""

""", unsafe_allow_html=True)

# ============================================================
# CONFIGURATION
# ============================================================
CLIENT_ID = "1108425500"
DEFAULT_DHAN_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

OPTIONCHAIN_URL = "https://api.dhan.co/v2/optionchain"
EXPIRY_URL = "https://api.dhan.co/v2/optionchain/expirylist"

INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}

# ============================================================
# DHAN API
# ============================================================
def auth_headers(token):
    return {
        "Content-Type": "application/json",
        "access-token": token,
        "client-id": CLIENT_ID
    }

def require_token(token):
    if not token:
        st.error("❌ DHAN Token required. Paste it in the sidebar.")
        st.stop()

@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(data.get("remarks", "Unknown error"))
    return data.get("data", [])

@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token):
    require_token(token)
    r = requests.post(OPTIONCHAIN_URL, headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12)
    if r.status_code == 401:
        raise RuntimeError("❌ Dhan Token expired (401 Unauthorized)")
    r.raise_for_status()
    
    data = r.json().get("data", {})
    spot = float(data.get("last_price") or 0)
    
    rows = []
    for strike_str, item in (data.get("oc") or {}).items():
        try:
            strike = float(strike_str)
        except:
            continue
        ce = item.get("ce") or {}
        pe = item.get("pe") or {}
        ce_greeks = ce.get("greeks") or {}
        pe_greeks = pe.get("greeks") or {}
        
        def n(x):
            try: return float(x or 0)
            except: return 0.0
        
        rows.append({
            "Strike": strike,
            "CE_LTP": n(ce.get("last_price")), "PE_LTP": n(pe.get("last_price")),
            "CE_OI": n(ce.get("oi")), "PE_OI": n(pe.get("oi")),
            "CE_PrevOI": n(ce.get("previous_oi")), "PE_PrevOI": n(pe.get("previous_oi")),
            "CE_IV": n(ce.get("implied_volatility")), "PE_IV": n(pe.get("implied_volatility")),
            "CE_Delta": n(ce_greeks.get("delta")), "PE_Delta": n(pe_greeks.get("delta")),
            "CE_Gamma": n(ce_greeks.get("gamma")), "PE_Gamma": n(pe_greeks.get("gamma")),
            "CE_Vega": n(ce_greeks.get("vega")), "PE_Vega": n(pe_greeks.get("vega")),
            "CE_Vol": n(ce.get("volume")), "PE_Vol": n(pe.get("volume")),
        })
    
    return spot, pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)

# ============================================================
# INSTITUTIONAL LAYER: IV SPIKE (TRAP) vs DUAL-FORCE GAMMA BLAST
# ============================================================
def analyze_gamma_ladder(df, spot):
    df = df.copy()
    df["total_gamma"] = (
        df["CE_Gamma"].abs() * df["CE_OI"] +
        df["PE_Gamma"].abs() * df["PE_OI"]
    )
    total_g = df["total_gamma"].sum()
    if total_g == 0:
        return {"max_gamma_strike": spot, "gamma_concentration": 0, "gamma_symmetry": "SYMMETRIC"}
        
    top_gamma = df.nlargest(3, "total_gamma")[["Strike", "total_gamma"]].values
    return {
        "max_gamma_strike": float(top_gamma[0][0]),
        "gamma_concentration": float(top_gamma[0][1] / total_g),
        "gamma_symmetry": "SYMMETRIC" if abs(sum(df[df["Strike"] > spot]["total_gamma"]) - sum(df[df["Strike"] < spot]["total_gamma"])) / total_g < 0.1 else "ASYMMETRIC"
    }

def detect_iv_spike_vs_gamma_blast(df):
    """
    Strict Dual-Force Video Logic:
    - IV SPIKE (Trap): One-sided short covering / exit without aggressive opposing buildup.
    - GAMMA BLAST (Explosion): Dual Force Act -> Call writers exiting while Put writers aggressively build (Bullish Blast) 
      OR Put writers exiting while Call writers aggressively build (Bearish Blast).
    """
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()  # Call writers exiting
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()  # Put writers exiting

    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()   # Call writers building
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()   # Put writers building

    # DUAL FORCE BULLISH GAMMA BLAST: Call writers exiting + Put writers aggressively building
    if ce_exits < -3000 and pe_build > 3000:
        status = "BULLISH GAMMA BLAST"
        signal = "BUY"
        description = "Dual-Force Active: Call writers trapped/exiting + Put writers aggressively building. Gamma explosion likely."
        confidence = 95
    # DUAL FORCE BEARISH GAMMA BLAST: Put writers exiting + Call writers aggressively building
    elif pe_exits < -3000 and ce_build > 3000:
        status = "BEARISH GAMMA BLAST"
        signal = "SELL"
        description = "Dual-Force Active: Put writers trapped/exiting + Call writers aggressively building. Downside gamma blast active."
        confidence = 95
    # SINGLE SIDED IV SPIKE / TRAP WARNING
    elif ce_exits < -3000 and pe_build <= 1000:
        status = "IV SPIKE (BULL TRAP)"
        signal = "WAIT"
        description = "Single-side Call exit detected without Put support. Temporary IV spike risk; potential bull trap."
        confidence = 40
    elif pe_exits < -3000 and ce_build <= 1000:
        status = "IV SPIKE (BEAR TRAP)"
        signal = "WAIT"
        description = "Single-side Put exit detected without Call support. Temporary IV spike risk; potential bear trap."
        confidence = 40
    else:
        status = "BALANCED / ACCUMULATION"
        signal = "WAIT"
        description = "No explosive dual-force gamma blast or IV spike trap detected. Awaiting institutional trigger."
        confidence = 0

    return {
        "status": status,
        "signal": signal,
        "description": description,
        "confidence": confidence,
        "ce_exits": ce_exits,
        "pe_exits": pe_exits,
        "ce_build": ce_build,
        "pe_build": pe_build
    }

# ============================================================
# MASTER SIGNAL GENERATOR
# ============================================================
def generate_professional_signal(df, spot, prev_close, step):
    gamma_ladder = analyze_gamma_ladder(df, spot)
    blast_analysis = detect_iv_spike_vs_gamma_blast(df)

    final_signal = blast_analysis["signal"]
    confidence = blast_analysis["confidence"]

    max_gamma = gamma_ladder["max_gamma_strike"]
    if final_signal == "BUY":
        entry = spot
        target = max(max_gamma, spot + (step * 2))
        stop_loss = spot - (step * 1.5)
    elif final_signal == "SELL":
        entry = spot
        target = min(max_gamma, spot - (step * 2))
        stop_loss = spot + (step * 1.5)
    else:
        entry, target, stop_loss = spot, spot, spot

    risk = abs(entry - stop_loss)
    reward = abs(target - entry)
    rr = f"1:{reward/risk:.1f}" if risk > 0 else "0:0"

    return {
        "signal": final_signal,
        "confidence": confidence,
        "entry": round(entry, 2),
        "target": round(target, 2),
        "stop_loss": round(stop_loss, 2),
        "risk_reward": rr,
        "blast_analysis": blast_analysis,
        "gamma_ladder": gamma_ladder
    }

# ============================================================
# UI INTERFACE
# ============================================================
with st.sidebar:
    st.markdown("### 🏛️ VIPUL PROFESSIONAL v4.4")
    st.caption("IV Spike vs Gamma Blast Engine")
    st.divider()
    
    dhan_token = st.text_input("DHAN TOKEN", type="password", value=DEFAULT_DHAN_TOKEN)
    groq_key = st.text_input("GROQ KEY (Optional)", type="password")
    
    st.divider()
    idx_name = st.selectbox("INDEX", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]
    
    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except Exception as e:
        st.error(f"Error: {e}")
        st.stop()
    
    expiry = st.selectbox("EXPIRY", expiries) if expiries else st.stop()
    prev_close = st.number_input("PREV CLOSE", value=23500.0, step=float(info["step"]))
    
    st.divider()
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="professional")

require_token(dhan_token)

try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)
except Exception as e:
    st.error(f"API Error: {e}")
    st.stop()

if df.empty:
    st.error("No option data returned.")
    st.stop()

result = generate_professional_signal(df, spot, prev_close, info["step"])

# ============================================================
# DISPLAY DASHBOARD
# ============================================================
st.markdown(f"# 🏛️ {idx_name} : {spot:,.2f}")
st.caption(f"Gamma Blast & IV Spike Terminal • {expiry} • {datetime.now():%H:%M:%S} IST")

sig = result["signal"]
ba = result["blast_analysis"]

if "GAMMA BLAST" in ba["status"]:
    if sig == "BUY":
        st.success(f"### 🚀 {ba['status']} DETECTED | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
    else:
        st.error(f"### 🚀 {ba['status']} DETECTED | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
elif "IV SPIKE" in ba["status"]:
    st.warning(f"### ⚠️ {ba['status']} — Single-side short covering. High risk of false trap! Stand aside.")
else:
    st.warning(f"### 🟡 STATUS: {ba['status']} — Awaiting Dual-Force institutional trigger.")

st.markdown("---")
st.markdown("### 📊 Dual-Force Gamma Blast & Trap Detector Breakdown")

c1, c2 = st.columns(2)
with c1:
    st.info(f"**Market Structure & Trap Status**\n- Current State: **{ba['status']}**\n- Call Exits (Covering): {ba['ce_exits']:+,.0f}\n- Put Exits (Covering): {ba['pe_exits']:+,.0f}\n- Call Building: {ba['ce_build']:+,.0f}\n- Put Building: {ba['pe_build']:+,.0f}\n- **Rule Analysis:** {ba['description']}")
with c2:
    gl = result['gamma_ladder']
    st.info(f"**Gamma Ladder Anchor**\n- Max Gamma Strike: {gl['max_gamma_strike']:,.0f}\n- Concentration: {gl['gamma_concentration']*100:.1f}%\n- Symmetry: {gl['gamma_symmetry']}")

st.markdown("---")
st.caption(f"Vipul Professional Terminal v4.4 • Last Scan: {datetime.now():%H:%M:%S} IST")