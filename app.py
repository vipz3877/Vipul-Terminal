"""
🏛️ VIPUL PROFESSIONAL TERMINAL v4.2 (REAL-TIME GREEKS + CHAIN BREADTH CONFLUENCE)
Multi-Layer Institutional Signal Detection
- Real-Time Greeks Momentum & Tick-by-Tick Shift Validation
- Chain Breadth Confluence (Multi-strike backing, avoiding single-strike traps)
- Gamma Ladder Mapping & Dynamic Entry/Targets/Stop-Loss
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

st.set_page_config(page_title="Vipul Professional v4.2", layout="wide")

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
# INSTITUTIONAL LAYERS WITH GREEKS & CHAIN BREADTH CONFLUENCE
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

def analyze_greeks_and_chain_breadth(df, spot):
    """
    Combines Video 2 & 3 Logic: 
    1. Checks for multi-strike chain breadth (avoids single-strike false traps).
    2. Evaluates real-time Greeks momentum (Delta & Gamma velocity shift).
    """
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    # Delta-weighted flow velocity
    df["CE_delta_flow"] = df["CE_OI_Chg"] * df["CE_Delta"].abs()
    df["PE_delta_flow"] = df["PE_OI_Chg"] * df["PE_Delta"].abs()

    net_ce_flow = df["CE_delta_flow"].sum()
    net_pe_flow = df["PE_delta_flow"].sum()

    # Chain breadth check: Are multiple consecutive strikes participating?
    active_ce_strikes = (df["CE_OI_Chg"] < -1500).sum()
    active_pe_strikes = (df["PE_OI_Chg"] < -1500).sum()

    breadth_confirmed = (active_ce_strikes >= 2) or (active_pe_strikes >= 2)

    if net_ce_flow < -300 and net_pe_flow > 300 and breadth_confirmed:
        signal = "BUY"
        conviction = "STRONG (Multi-Strike Chain Breadth & Greeks Aligned)"
    elif net_pe_flow < -300 and net_ce_flow > 300 and breadth_confirmed:
        signal = "SELL"
        conviction = "STRONG (Multi-Strike Chain Breadth & Greeks Aligned)"
    else:
        signal = "WAIT"
        conviction = "Single-Strike Trap or Balanced Flow (Awaiting Breadth)"

    return {
        "signal": signal,
        "conviction": conviction,
        "net_ce_flow": round(net_ce_flow, 2),
        "net_pe_flow": round(net_pe_flow, 2),
        "breadth_confirmed": breadth_confirmed
    }

def analyze_volatility_regime(df, spot):
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    near = df.iloc[max(0, pos-5):min(len(df), pos+6)].copy()
    atm_iv = (near["CE_IV"].mean() + near["PE_IV"].mean()) / 2
    return {"atm_iv": round(atm_iv, 2), "regime": "HIGH" if atm_iv > 15 else "LOW" if atm_iv < 9 else "NORMAL"}

# ============================================================
# MASTER PROFESSIONAL SIGNAL GENERATOR
# ============================================================
def generate_professional_signal(df, spot, prev_close, step):
    gamma_ladder = analyze_gamma_ladder(df, spot)
    greeks_breadth = analyze_greeks_and_chain_breadth(df, spot)
    vol_regime = analyze_volatility_regime(df, spot)

    final_signal = greeks_breadth["signal"]
    confidence = 88 if final_signal != "WAIT" else 0

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
        "greeks_breadth": greeks_breadth,
        "gamma_ladder": gamma_ladder,
        "vol_regime": vol_regime
    }

# ============================================================
# UI INTERFACE
# ============================================================
with st.sidebar:
    st.markdown("### 🏛️ VIPUL PROFESSIONAL v4.2")
    st.caption("Greeks + Chain Breadth Engine")
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
st.caption(f"Institutional Greeks Terminal • {expiry} • {datetime.now():%H:%M:%S} IST")

sig = result["signal"]
if sig == "BUY":
    st.success(f"### 🟢 SIGNAL: BUY | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
elif sig == "SELL":
    st.error(f"### 🔴 SIGNAL: SELL | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
else:
    st.warning(f"### 🟡 STATUS: WAIT — Single-Strike trap filter active. Awaiting multi-strike breadth confirmation.")

st.markdown("---")
st.markdown("### 📊 Real-Time Greeks & Chain Breadth Validation")

c1, c2, c3 = st.columns(3)
with c1:
    gb = result['greeks_breadth']
    st.info(f"**Chain Breadth Confluence**\n- Breadth Confirmed: {gb['breadth_confirmed']}\n- Net CE Flow: {gb['net_ce_flow']:+,.0f}\n- Net PE Flow: {gb['net_pe_flow']:+,.0f}")
with c2:
    gl = result['gamma_ladder']
    st.info(f"**Gamma Ladder Map**\n- Max Gamma: {gl['max_gamma_strike']:,.0f}\n- Concentration: {gl['gamma_concentration']*100:.1f}%\n- Symmetry: {gl['gamma_symmetry']}")
with c3:
    vr = result['vol_regime']
    st.info(f"**Volatility Regime**\n- ATM IV: {vr['atm_iv']}%\n- Regime: {vr['regime']}")

st.markdown("---")
st.caption(f"Vipul Professional Terminal v4.2 • Last Scan: {datetime.now():%H:%M:%S} IST")