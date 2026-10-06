"""
🏛️ VIPUL PROFESSIONAL TERMINAL v4.5 (HIGH-SPEED VISUAL FLASH UI)
Instant Dual-Force Gamma Blast & IV Spike Alert System
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

st.set_page_config(page_title="Vipul Professional v4.5", layout="wide")

st.markdown("""

""", unsafe_allow_html=True)

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
            "CE_Gamma": n(ce_g.get("gamma")), "PE_Gamma": n(pe_g.get("gamma")),
        })
    return spot, pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)

def analyze_gamma_ladder(df, spot):
    df = df.copy()
    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    total_g = df["total_gamma"].sum()
    if total_g == 0: return {"max_gamma_strike": spot, "concentration": 0}
    top_gamma = df.nlargest(1, "total_gamma")[["Strike", "total_gamma"]].values
    return {"max_gamma_strike": float(top_gamma[0][0]), "concentration": float(top_gamma[0][1] / total_g)}

def detect_iv_spike_vs_gamma_blast(df):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()
    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()

    if ce_exits < -3000 and pe_build > 3000:
        return {"status": "BULLISH GAMMA BLAST", "signal": "BUY", "desc": "Call writers capitulating and Put writers building floors."}
    elif pe_exits < -3000 and ce_build > 3000:
        return {"status": "BEARISH GAMMA BLAST", "signal": "SELL", "desc": "Put writers capitulating and Call writers building ceilings."}
    elif ce_exits < -3000 and pe_build <= 1000:
        return {"status": "IV SPIKE BULL TRAP", "signal": "WAIT", "desc": "Isolated Call short covering without Put support. Trap risk."}
    elif pe_exits < -3000 and ce_build <= 1000:
        return {"status": "IV SPIKE BEAR TRAP", "signal": "WAIT", "desc": "Isolated Put short covering without Call support. Trap risk."}
    else:
        return {"status": "BALANCED ACCUMULATION", "signal": "WAIT", "desc": "Market in range compression. Awaiting trigger."}

with st.sidebar:
    st.markdown("### VIPUL TERMINAL v4.5")
    dhan_token = st.text_input("DHAN TOKEN", type="password", value=DEFAULT_DHAN_TOKEN)
    idx_name = st.selectbox("INDEX", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]
    
    try: expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except: expiries = []
    
    expiry = st.selectbox("EXPIRY", expiries) if expiries else st.stop()
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh: st_autorefresh(interval=180000, limit=None, key="v45")

require_token(dhan_token)
spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)

gamma_ladder = analyze_gamma_ladder(df, spot)
analysis = detect_iv_spike_vs_gamma_blast(df)

st.markdown(f"## {idx_name} Spot: {spot:,.2f}")

status = analysis["status"]
if "BULLISH GAMMA BLAST" in status:
    st.markdown('