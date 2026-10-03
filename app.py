"""
🏛️ VIPUL PROFESSIONAL TERMINAL v4.1 (PRODUCTION HARDENED)
Multi-Layer Institutional Signal Detection
- Gamma Ladder Mapping & Concentration
- Delta-Weighted OI Flow Analysis
- IV Skew & Volatility Regime Detection
- Pinning & Gamma Hedging Cycles
- Spot-OI Correlation & Dynamic Entry/Targets
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

st.set_page_config(page_title="Vipul Professional v4.1", layout="wide")

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

PROFESSIONAL_THRESHOLDS = {
    "delta_weighted_threshold": 0.35, 
    "gamma_concentration_threshold": 0.25,
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
# LAYERS 1-6: INSTITUTIONAL ANALYTICS
# ============================================================
def analyze_gamma_ladder(df, spot):
    df = df.copy()
    df["total_gamma"] = (
        df["CE_Gamma"].abs() * df["CE_OI"] +
        df["PE_Gamma"].abs() * df["PE_OI"]
    )
    total_g = df["total_gamma"].sum()
    if total_g == 0:
        return {"max_gamma_strike": spot, "gamma_concentration": 0, "gamma_spread": 0, "gamma_symmetry": "SYMMETRIC"}
        
    top_gamma = df.nlargest(3, "total_gamma")[["Strike", "total_gamma"]].values
    
    return {
        "max_gamma_strike": float(top_gamma[0][0]),
        "gamma_concentration": float(top_gamma[0][1] / total_g),
        "second_gamma": float(top_gamma[1][0]) if len(top_gamma) > 1 else None,
        "gamma_spread": float(top_gamma[0][0] - top_gamma[-1][0]),
        "gamma_symmetry": "SYMMETRIC" if abs(sum(df[df["Strike"] > spot]["total_gamma"]) - sum(df[df["Strike"] < spot]["total_gamma"])) / total_g < 0.1 else "ASYMMETRIC"
    }

def analyze_delta_weighted_flow(df, spot):
    df = df.copy()
    df["CE_delta_weighted_flow"] = (df["CE_OI"] - df["CE_PrevOI"]) * df["CE_Delta"].abs()
    df["PE_delta_weighted_flow"] = (df["PE_OI"] - df["PE_PrevOI"]) * df["PE_Delta"].abs()
    
    call_flow = df["CE_delta_weighted_flow"].sum()
    put_flow = df["PE_delta_weighted_flow"].sum()
    total_flow = abs(call_flow) + abs(put_flow)
    call_ratio = abs(call_flow) / total_flow if total_flow > 0 else 0.5
    
    if call_flow < -PROFESSIONAL_THRESHOLDS["delta_weighted_threshold"] * 100 and put_flow > 0:
        signal = "BUY"
        conviction = "STRONG" if abs(call_flow) > 1000 else "MODERATE"
    elif put_flow < -PROFESSIONAL_THRESHOLDS["delta_weighted_threshold"] * 100 and call_flow > 0:
        signal = "SELL"
        conviction = "STRONG" if abs(put_flow) > 1000 else "MODERATE"
    else:
        signal = "WAIT"
        conviction = "NONE"
    
    return {
        "signal": signal,
        "conviction": conviction,
        "call_flow": round(call_flow, 2),
        "put_flow": round(put_flow, 2),
        "call_ratio": round(call_ratio, 3),
        "reason": f"Delta-weighted flow: Call {call_flow:.0f}, Put {put_flow:.0f}"
    }

def analyze_volatility_regime(df, spot):
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    near = df.iloc[max(0, pos-5):min(len(df), pos+6)].copy()
    
    atm_iv = (near["CE_IV"].mean() + near["PE_IV"].mean()) / 2
    otm_call_iv = near[near["Strike"] > spot]["CE_IV"].mean()
    otm_put_iv = near[near["Strike"] < spot]["PE_IV"].mean()
    
    skew = otm_put_iv - otm_call_iv if not math.isnan(otm_put_iv - otm_call_iv) else 0.0
    
    if atm_iv > 15:
        vol_regime = "HIGH"
        vol_interpretation = "Capitulation risk elevated, premium decay active"
    elif atm_iv < 9:
        vol_regime = "LOW"
        vol_interpretation = "Complacency, breakout expansion likely"
    else:
        vol_regime = "NORMAL"
        vol_interpretation = "Balanced volatility structure"
    
    return {
        "atm_iv": round(atm_iv, 2),
        "skew": round(skew, 2),
        "skew_direction": "PUT_SKEW (Bullish)" if skew > 0.5 else "CALL_SKEW (Bearish)" if skew < -0.5 else "NEUTRAL",
        "volatility_regime": vol_regime,
        "interpretation": vol_interpretation
    }

def detect_pinning_gamma_hedging(df, spot, prev_close, step):
    df = df.copy()
    df["total_gamma"] = (
        df["CE_Gamma"].abs() * df["CE_OI"] +
        df["PE_Gamma"].abs() * df["PE_OI"]
    )
    if df["total_gamma"].sum() == 0:
        return {"pinning_detected": False, "pinning_level": None, "reason": "No gamma data", "breakout_target": spot}
        
    major_gamma_strikes = df.nlargest(3, "total_gamma")["Strike"].values
    min_distance_to_gamma = min([abs(spot - gs) for gs in major_gamma_strikes])
    
    if min_distance_to_gamma < step * 0.5:
        pinning_level = major_gamma_strikes[np.argmin([abs(spot - gs) for gs in major_gamma_strikes])]
        return {
            "pinning_detected": True,
            "pinning_level": pinning_level,
            "reason": f"Spot pinned to gamma wall at {pinning_level:.0f}",
            "breakout_target": major_gamma_strikes[1] if len(major_gamma_strikes) > 1 else spot
        }
    return {
        "pinning_detected": False,
        "pinning_level": None,
        "reason": "No structural pinning detected",
        "breakout_target": major_gamma_strikes[0]
    }

def analyze_vol_smile(df, spot):
    iv_spread = df["CE_IV"].mean() - df["PE_IV"].mean()
    total_ce_oi = df["CE_OI"].sum()
    pcr = df["PE_OI"].sum() / total_ce_oi if total_ce_oi > 0 else 1.0
    
    signal = None
    reason = "Balanced"
    if pcr > 1.3:
        signal = "BULLISH_EXTREME"
        reason = f"PCR {pcr:.2f} = Heavy put buying / Hedging exhaustion"
    elif pcr < 0.7:
        signal = "BEARISH_EXTREME"
        reason = f"PCR {pcr:.2f} = Heavy call aggression / Top heavy"
        
    return {
        "pcr": round(pcr, 3),
        "iv_spread": round(iv_spread, 2),
        "skew_signal": signal,
        "skew_reason": reason
    }

def analyze_spot_oi_divergence(df, spot, prev_close):
    spot_move = spot - prev_close
    df = df.copy()
    total_oi_change = (df["CE_OI"] - df["CE_PrevOI"]).sum() + (df["PE_OI"] - df["PE_PrevOI"]).sum()
    
    if abs(spot_move) > 40 and abs(total_oi_change) < 500:
        return {"divergence": True, "reason": f"Spot moved {spot_move:+.0f} but OI flat = Weak breadth"}
    elif abs(spot_move) > 40 and abs(total_oi_change) > 3000:
        return {"divergence": False, "reason": f"Spot moved {spot_move:+.0f} with OI backing = High conviction"}
    return {"divergence": False, "reason": "Standard multi-strike OI rotation"}

# ============================================================
# PROFESSIONAL SIGNAL GENERATOR + EXECUTION LEVELS
# ============================================================
def generate_professional_signal(df, spot, prev_close, step):
    gamma_ladder = analyze_gamma_ladder(df, spot)
    delta_flow = analyze_delta_weighted_flow(df, spot)
    vol_regime = analyze_volatility_regime(df, spot)
    pinning = detect_pinning_gamma_hedging(df, spot, prev_close, step)
    vol_smile = analyze_vol_smile(df, spot)
    divergence = analyze_spot_oi_divergence(df, spot, prev_close)
    
    bullish_layers = 0
    bearish_layers = 0
    
    if delta_flow["signal"] == "BUY": bullish_layers += 1
    elif delta_flow["signal"] == "SELL": bearish_layers += 1
    
    if vol_smile["skew_signal"] == "BULLISH_EXTREME": bullish_layers += 1
    elif vol_smile["skew_signal"] == "BEARISH_EXTREME": bearish_layers += 1
    
    if vol_regime["volatility_regime"] == "HIGH" and delta_flow["signal"] == "BUY": bullish_layers += 1
    elif vol_regime["volatility_regime"] == "LOW" and delta_flow["signal"] == "SELL": bearish_layers += 1
    
    if not divergence["divergence"] and delta_flow["conviction"] == "STRONG":
        if delta_flow["signal"] == "BUY": bullish_layers += 1
        else: bearish_layers += 1
        
    if bullish_layers >= 2:
        final_signal = "BUY"
        confidence = min(92, 55 + bullish_layers * 15)
    elif bearish_layers >= 2:
        final_signal = "SELL"
        confidence = min(92, 55 + bearish_layers * 15)
    else:
        final_signal = "WAIT"
        confidence = 0
        
    # Calculate Actionable Entry, Target & Stop Loss Levels
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
        "layers": {
            "gamma_ladder": gamma_ladder,
            "delta_flow": delta_flow,
            "vol_regime": vol_regime,
            "pinning": pinning,
            "vol_smile": vol_smile,
            "divergence": divergence
        },
        "agreement": f"{max(bullish_layers, bearish_layers)}/4 active agreement factors"
    }

# ============================================================
# UI INTERFACE
# ============================================================
with st.sidebar:
    st.markdown("### 🏛️ VIPUL PROFESSIONAL v4.1")
    st.caption("Institutional-Grade Engine")
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
st.caption(f"Professional Institutional Terminal • {expiry} • {datetime.now():%H:%M:%S} IST")

sig = result["signal"]
if sig == "BUY":
    st.success(f"### 🟢 SIGNAL: BUY | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
elif sig == "SELL":
    st.error(f"### 🔴 SIGNAL: SELL | CONFIDENCE: {result['confidence']}% | ENTRY: {result['entry']} | TARGET: {result['target']} | STOP LOSS: {result['stop_loss']} | R:R: {result['risk_reward']}")
else:
    st.warning(f"### 🟡 STATUS: WAIT — Multi-layer alignment pending ({result['agreement']})")

st.markdown("---")
st.markdown("### 📊 Institutional Layer Breakdown")

cols = st.columns(3)
with cols[0]:
    gl = result['layers']['gamma_ladder']
    st.info(f"**Gamma Ladder Map**\n- Max Gamma: {gl['max_gamma_strike']:,.0f}\n- Concentration: {gl['gamma_concentration']*100:.1f}%\n- Symmetry: {gl['gamma_symmetry']}")

with cols[1]:
    dflow = result['layers']['delta_flow']
    st.info(f"**Delta-Weighted Flow**\n- Call Flow: {dflow['call_flow']:+,.0f}\n- Put Flow: {dflow['put_flow']:+,.0f}\n- Conviction: {dflow['conviction']}")

with cols[2]:
    vr = result['layers']['vol_regime']
    st.info(f"**Volatility Regime**\n- ATM IV: {vr['atm_iv']}%\n- Regime: {vr['volatility_regime']}\n- Skew: {vr['skew_direction']}")

cols2 = st.columns(3)
with cols2[0]:
    pin = result['layers']['pinning']
    st.info(f"**Gamma Hedging / Pinning**\n- Active: {pin['pinning_detected']}\n- Level: {pin['pinning_level']}\n- {pin['reason']}")

with cols2[1]:
    vs = result['layers']['vol_smile']
    st.info(f"**Vol Smile & PCR**\n- PCR Ratio: {vs['pcr']}\n- Signal: {vs['skew_signal'] or 'Neutral'}\n- {vs['skew_reason']}")

with cols2[2]:
    div = result['layers']['divergence']
    st.info(f"**Spot-OI Divergence**\n- Divergence: {div['divergence']}\n- {div['reason']}")

st.markdown("---")
st.caption(f"Vipul Professional Terminal v4.1 • Last Scan: {datetime.now():%H:%M:%S} IST")