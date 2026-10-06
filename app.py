"""
🏛️ VIPUL BLOOMBERG PROFESSIONAL TERMINAL v5.0
Unified Engine: Bias Scoring, 35-Point Traps, Expected Move & Dual-Force Gamma Blasts
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
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="Vipul Bloomberg Terminal v5.0", layout="wide")

# ============================================================
# BLOOMBERG TERMINAL CSS STYLING
# ============================================================
st.markdown("""

""", unsafe_allow_html=True)

# ============================================================
# CONFIGURATION & API
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

def auth_headers(token):
    return {"Content-Type": "application/json", "access-token": token, "client-id": CLIENT_ID}

def require_token(token):
    if not token:
        st.error("DHAN Token required. Paste it in the sidebar.")
        st.stop()

@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token):
    r = requests.post(EXPIRY_URL, headers=auth_headers(token), json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("data", [])

@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token):
    require_token(token)
    r = requests.post(OPTIONCHAIN_URL, headers=auth_headers(token), json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12)
    r.raise_for_status()
    data = r.json().get("data", {})
    spot = float(data.get("last_price") or 0)
    
    rows = []
    for strike_str, item in (data.get("oc") or {}).items():
        try: strike = float(strike_str)
        except: continue
        ce, pe = item.get("ce") or {}, item.get("pe") or {}
        ce_g, pe_g = ce.get("greeks") or {}, pe.get("greeks") or {}
        def n(x):
            try: return float(x or 0)
            except: return 0.0
        rows.append({
            "Strike": strike,
            "CE_OI": n(ce.get("oi")), "PE_OI": n(pe.get("oi")),
            "CE_PrevOI": n(ce.get("previous_oi")), "PE_PrevOI": n(pe.get("previous_oi")),
            "CE_IV": n(ce.get("implied_volatility")), "PE_IV": n(pe.get("implied_volatility")),
            "CE_Gamma": n_val := n(ce_g.get("gamma")),
            "PE_Gamma": n(pe_g.get("gamma")),
        })
    return spot, pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)

def parse_dte(expiry_str):
    try:
        exp_date = datetime.strptime(expiry_str, "%Y-%m-%d")
        delta_days = (exp_date - datetime.now()).days
        return max(1, delta_days)
    except:
        return 1

# ============================================================
# UNIFIED QUANTITATIVE ENGINE (v4.3 + v4.5 Logic)
# ============================================================
def run_quantitative_analysis(df, spot, prev_close, expiry_str):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    # PCR Calculation
    total_ce_oi = df["CE_OI"].sum()
    total_pe_oi = df["PE_OI"].sum()
    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

    # Expected Move Calculation (v4.3 Logic)
    atm_row = df.iloc[(df['Strike'] - spot).abs().argsort()[:1]]
    atm_iv = float(atm_row["CE_IV"].values[0]) if not atm_row.empty else 12.0
    dte = parse_dte(expiry_str)
    exp_move = spot * (atm_iv / 100) * math.sqrt(dte / 252)

    # Walls & Gamma Anchor
    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    max_gamma_strike = float(df.loc[df["total_gamma"].idxmax(), "Strike"]) if not df.empty else spot
    call_wall = float(df.loc[df["CE_OI"].idxmax(), "Strike"]) if not df.empty else spot
    put_wall = float(df.loc[df["PE_OI"].idxmax(), "Strike"]) if not df.empty else spot

    # 35-Point Trap Detection (v4.3 Logic)[cite: 6]
    near = df[(df["Strike"] >= spot - 300) & (df["Strike"] <= spot + 300)]
    ce_trap_res, pe_trap_supp = None, None
    if not near.empty:
        max_ce_idx = near["CE_OI_Chg"].idxmax()
        if near.loc[max_ce_idx, "CE_OI_Chg"] > 10000:
            ce_trap_res = float(near.loc[max_ce_idx, "Strike"]) + 35
        max_pe_idx = near["PE_OI_Chg"].idxmax()
        if near.loc[max_pe_idx, "PE_OI_Chg"] > 10000:
            pe_trap_supp = float(near.loc[max_pe_idx, "Strike"]) - 35

    # Dual-Force Gamma Blast & IV Spike Engine
    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()
    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()

    # Bias Score Computation (v4.3 Logic)[cite: 6]
    score = 50
    if ce_exits < -5000 and pe_build > 5000: score += 25
    elif pe_exits < -5000 and ce_build > 5000: score -= 25
    if spot - prev_close > 20: score += 10
    elif spot - prev_close < -20: score -= 10
    if pcr > 1.2: score += 8
    elif pcr < 0.8: score -= 8
    score = max(0, min(100, score))
    bias_label = "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRAL"

    # Final Status Detection
    if ce_exits < -3000 and pe_build > 3000:
        status, signal, desc = "BULLISH GAMMA BLAST", "BUY", "Call writers capitulating + Put writers building floors."
    elif pe_exits < -3000 and ce_build > 3000:
        status, signal, desc = "BEARISH GAMMA BLAST", "SELL", "Put writers capitulating + Call writers building ceilings."
    elif ce_exits < -3000 and pe_build <= 1000:
        status, signal, desc = "IV SPIKE BULL TRAP", "WAIT", "Isolated Call short covering without Put support. Trap risk."
    elif pe_exits < -3000 and ce_build <= 1000:
        status, signal, desc = "IV SPIKE BEAR TRAP", "WAIT", "Isolated Put short covering without Call support. Trap risk."
    else:
        status, signal, desc = "BALANCED ACCUMULATION", "WAIT", "Market in range compression. Awaiting trigger."

    return {
        "status": status, "signal": signal, "desc": desc, "score": score, "bias": bias_label,
        "pcr": pcr, "exp_move": round(exp_move, 2), "max_gamma": max_gamma_strike,
        "call_wall": call_wall, "put_wall": put_wall, "ce_trap": ce_trap_res, "pe_trap": pe_trap_supp
    }

# ============================================================
# SIDEBAR CONTROLS
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL CONFIG")
    dhan_token = st.text_input("DHAN TOKEN", type="password", value=DEFAULT_DHAN_TOKEN)
    idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]
    
    try: expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except: expiries = []
    
    expiry = st.selectbox("EXPIRY DATE", expiries) if expiries else st.stop()
    prev_close = st.number_input("PREV CLOSE", value=float(info["step"] * 450), step=float(info["step"]))
    
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh: st_autorefresh(interval=180000, limit=None, key="bbg")

require_token(dhan_token)
spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)
analysis = run_quantitative_analysis(df, spot, prev_close, expiry)

# ============================================================
# BLOOMBERG DASHBOARD INTERFACE
# ============================================================
st.markdown(f'