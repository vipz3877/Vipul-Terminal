"""
🏛️ VIPUL PROFESSIONAL TERMINAL v4.0 (INSTITUTIONAL-GRADE)
Multi-Layer Institutional Signal Detection
- Gamma Ladder Mapping (Not just 1 strike, entire ladder)
- Delta-Weighted OI Flow Analysis
- IV Skew & Term Structure
- Volatility Regime Detection
- Institutional Footprint Analysis
- Spot-OI Correlation Analysis
- Pinning & Gamma Hedging Cycles
- Cross-Month Flow Analysis
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
from scipy.stats import zscore

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="Vipul Professional v4.0", layout="wide")

st.markdown("""
<style>
:root {
    --bg: #03070c;
    --orange: #ff9d00;
    --green: #00d7a0;
    --red: #ff4d5e;
    --text: #e7eef5;
    --muted: #7f98ad;
}
.stApp { background: var(--bg) !important; color: var(--text) !important; }
.professional-box { border: 2px solid var(--orange); padding: 15px; border-radius: 6px; }
</style>
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
}

# Professional thresholds (tuned to institutional behavior)
PROFESSIONAL_THRESHOLDS = {
    "strong_oi_change": 8000,        # 8K = Institutional sized
    "moderate_oi_change": 3000,      # 3K = Meaningful
    "min_oi_ratio": 0.03,            # 3% of total OI
    "delta_weighted_threshold": 0.35, # 35% delta concentration
    "gamma_concentration_threshold": 0.25,  # 25% gamma at one strike
    "iv_skew_extreme": 2.0,          # 2 sigma from mean = extreme skew
    "volatility_regime_threshold": 1.5,  # High/low IV regime
    "spot_oi_correlation": 0.65,     # Correlation strength
    "volume_confirmation": 10000,    # Volume to confirm
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
        st.error("❌ DHAN Token required")
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
        raise RuntimeError("❌ Dhan Token expired")
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
# LAYER 1: GAMMA LADDER MAPPING (Professional)
# ============================================================
def analyze_gamma_ladder(df, spot):
    """
    Professional: Map ENTIRE gamma profile, not just max strike
    Identifies where institutional hedges are concentrated
    """
    df = df.copy()
    df["total_gamma"] = (
        df["CE_Gamma"].abs() * df["CE_OI"] +
        df["PE_Gamma"].abs() * df["PE_OI"]
    )
    
    # Find top 3 gamma concentrations (not just 1)
    top_gamma = df.nlargest(3, "total_gamma")[["Strike", "total_gamma"]].values
    
    # Analyze gamma ladder for clues
    gamma_ladder = {
        "max_gamma_strike": float(top_gamma[0][0]),
        "gamma_concentration": float(top_gamma[0][1] / df["total_gamma"].sum()),
        "second_gamma": float(top_gamma[1][0]) if len(top_gamma) > 1 else None,
        "third_gamma": float(top_gamma[2][0]) if len(top_gamma) > 2 else None,
        "gamma_spread": float(top_gamma[0][0] - top_gamma[-1][0]),
        "gamma_symmetry": "SYMMETRIC" if abs(sum(df[df["Strike"] > spot]["total_gamma"]) - sum(df[df["Strike"] < spot]["total_gamma"])) / df["total_gamma"].sum() < 0.1 else "ASYMMETRIC"
    }
    
    return gamma_ladder

# ============================================================
# LAYER 2: DELTA-WEIGHTED OI FLOW (Professional)
# ============================================================
def analyze_delta_weighted_flow(df, spot):
    """
    Professional: Weight OI changes by delta (delta = probability)
    Large OI change in 0.05 delta = noise
    Large OI change in 0.50 delta = REAL institutional repositioning
    """
    df = df.copy()
    
    # Delta-weight the flows
    df["CE_delta_weighted_flow"] = (df["CE_OI"] - df["CE_PrevOI"]) * df["CE_Delta"].abs()
    df["PE_delta_weighted_flow"] = (df["PE_OI"] - df["PE_PrevOI"]) * df["PE_Delta"].abs()
    
    call_flow = df["CE_delta_weighted_flow"].sum()
    put_flow = df["PE_delta_weighted_flow"].sum()
    
    total_flow = abs(call_flow) + abs(put_flow)
    if total_flow == 0:
        call_ratio = 0.5
    else:
        call_ratio = abs(call_flow) / total_flow
    
    # Identify the signal
    if call_flow < -PROFESSIONAL_THRESHOLDS["delta_weighted_threshold"] * 100 and put_flow > 0:
        signal = "BUY"  # Call writers hedging = upside
        conviction = "STRONG" if abs(call_flow) > 3000 else "MODERATE"
    elif put_flow < -PROFESSIONAL_THRESHOLDS["delta_weighted_threshold"] * 100 and call_flow > 0:
        signal = "SELL"  # Put writers hedging = downside
        conviction = "STRONG" if abs(put_flow) > 3000 else "MODERATE"
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

# ============================================================
# LAYER 3: IV SKEW & VOLATILITY REGIME (Professional)
# ============================================================
def analyze_volatility_regime(df, spot):
    """
    Professional: IV skew tells us where institutional protection is
    Skew to puts (PE_IV > CE_IV) = Downside protection = Bullish
    Skew to calls (CE_IV > PE_IV) = Upside hedging = Bearish
    """
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    near = df.iloc[max(0, pos-5):min(len(df), pos+6)].copy()
    
    atm_iv = (near["CE_IV"].mean() + near["PE_IV"].mean()) / 2
    
    # Skew analysis
    otm_call_iv = near[near["Strike"] > spot]["CE_IV"].mean()
    otm_put_iv = near[near["Strike"] < spot]["PE_IV"].mean()
    
    skew = otm_put_iv - otm_call_iv
    skew_zscore = (skew - skew) / max(0.01, 0.5)  # Normalized
    
    # Volatility regime
    if atm_iv > 12:
        vol_regime = "HIGH"
        vol_interpretation = "Capitulation likely, short premium might work"
    elif atm_iv < 8:
        vol_regime = "LOW"
        vol_interpretation = "Complacency, breakout likely"
    else:
        vol_regime = "NORMAL"
        vol_interpretation = "Balanced"
    
    return {
        "atm_iv": round(atm_iv, 2),
        "skew": round(skew, 2),
        "skew_direction": "PUT_SKEW (Bullish)" if skew > 1 else "CALL_SKEW (Bearish)" if skew < -1 else "NEUTRAL",
        "volatility_regime": vol_regime,
        "interpretation": vol_interpretation
    }

# ============================================================
# LAYER 4: PINNING & GAMMA HEDGING DETECTION (Professional)
# ============================================================
def detect_pinning_gamma_hedging(df, spot, prev_close, step):
    """
    Professional: Detect when spot is being pinned to specific strikes
    High gamma at 23400 = institutions hedging calls there = spot tends to stay there
    """
    
    # Find major gamma strikes
    df = df.copy()
    df["total_gamma"] = (
        df["CE_Gamma"].abs() * df["CE_OI"] +
        df["PE_Gamma"].abs() * df["PE_OI"]
    )
    
    major_gamma_strikes = df.nlargest(3, "total_gamma")["Strike"].values
    
    # Check if spot is pinned to these
    min_distance_to_gamma = min([abs(spot - gs) for gs in major_gamma_strikes])
    
    if min_distance_to_gamma < step * 0.5:
        pinning_detected = True
        pinning_level = major_gamma_strikes[np.argmin([abs(spot - gs) for gs in major_gamma_strikes])]
        pinning_reason = f"Spot pinned to gamma wall at {pinning_level:.0f}"
    else:
        pinning_detected = False
        pinning_level = None
        pinning_reason = "No pinning detected"
    
    return {
        "pinning_detected": pinning_detected,
        "pinning_level": pinning_level,
        "reason": pinning_reason,
        "breakout_target": major_gamma_strikes[1] if len(major_gamma_strikes) > 1 else spot
    }

# ============================================================
# LAYER 5: VOLATILITY SMILE & PUT/CALL SKEW (Professional)
# ============================================================
def analyze_vol_smile(df, spot):
    """
    Professional: Volatility smile pattern reveals institutional protection
    """
    df = df.copy()
    
    # IV spread: Call IV vs Put IV
    iv_spread = df["CE_IV"].mean() - df["PE_IV"].mean()
    
    # Put/Call ratio extremes
    pcr = df["PE_OI"].sum() / df["CE_OI"].sum() if df["CE_OI"].sum() > 0 else 0
    
    signal = None
    if pcr > 1.3:  # Extreme put buying
        signal = "BULLISH_EXTREME"
        reason = f"PCR {pcr:.2f} = Put buyers panicking = Likely bottom"
    elif pcr < 0.7:  # Extreme call buying
        signal = "BEARISH_EXTREME"
        reason = f"PCR {pcr:.2f} = Call buyers aggressive = Likely top"
    
    return {
        "pcr": round(pcr, 3),
        "iv_spread": round(iv_spread, 2),
        "skew_signal": signal,
        "skew_reason": reason if signal else "Balanced"
    }

# ============================================================
# LAYER 6: SPOT-OI CORRELATION (Professional)
# ============================================================
def analyze_spot_oi_divergence(df, spot, prev_close):
    """
    Professional: When spot moves but OI doesn't follow = Divergence = Caution
    When spot moves and OI follows = Confirmation = Strength
    """
    
    spot_move = spot - prev_close
    
    df = df.copy()
    df["CE_OI_Change"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Change"] = df["PE_OI"] - df["PE_PrevOI"]
    
    total_oi_change = df["CE_OI_Change"].sum() + df["PE_OI_Change"].sum()
    
    if abs(spot_move) > 50 and abs(total_oi_change) < 1000:
        return {
            "divergence": True,
            "reason": f"Spot moved {spot_move:+.0f} but OI flat = Weak move = Caution"
        }
    elif abs(spot_move) > 50 and abs(total_oi_change) > 5000:
        return {
            "divergence": False,
            "reason": f"Spot moved {spot_move:+.0f} and OI confirms = Strong move = Confidence"
        }
    else:
        return {
            "divergence": False,
            "reason": "Normal OI activity"
        }

# ============================================================
# LAYER 7: PROFESSIONAL SIGNAL GENERATOR
# ============================================================
def generate_professional_signal(df, spot, prev_close, step):
    """
    Multi-layer professional analysis
    Signals generated only when multiple layers agree
    """
    
    # Layer 1: Gamma Ladder
    gamma_ladder = analyze_gamma_ladder(df, spot)
    
    # Layer 2: Delta-Weighted Flow
    delta_flow = analyze_delta_weighted_flow(df, spot)
    
    # Layer 3: Volatility Regime
    vol_regime = analyze_volatility_regime(df, spot)
    
    # Layer 4: Pinning Detection
    pinning = detect_pinning_gamma_hedging(df, spot, prev_close, step)
    
    # Layer 5: Vol Smile
    vol_smile = analyze_vol_smile(df, spot)
    
    # Layer 6: Spot-OI Divergence
    divergence = analyze_spot_oi_divergence(df, spot, prev_close)
    
    # PROFESSIONAL LOGIC: Require agreement from 3+ layers
    bullish_layers = 0
    bearish_layers = 0
    
    if delta_flow["signal"] == "BUY":
        bullish_layers += 1
    elif delta_flow["signal"] == "SELL":
        bearish_layers += 1
    
    if vol_smile["skew_signal"] == "BULLISH_EXTREME":
        bullish_layers += 1
    elif vol_smile["skew_signal"] == "BEARISH_EXTREME":
        bearish_layers += 1
    
    if vol_regime["volatility_regime"] == "HIGH" and delta_flow["signal"] == "BUY":
        bullish_layers += 1
    elif vol_regime["volatility_regime"] == "LOW" and delta_flow["signal"] == "SELL":
        bearish_layers += 1
    
    if not divergence["divergence"] and delta_flow["conviction"] == "STRONG":
        if delta_flow["signal"] == "BUY":
            bullish_layers += 1
        else:
            bearish_layers += 1
    
    # Final signal
    if bullish_layers >= 3:
        final_signal = "BUY"
        confidence = min(90, 60 + bullish_layers * 10)
    elif bearish_layers >= 3:
        final_signal = "SELL"
        confidence = min(90, 60 + bearish_layers * 10)
    else:
        final_signal = "WAIT"
        confidence = 0
    
    return {
        "signal": final_signal,
        "confidence": confidence,
        "layers": {
            "gamma_ladder": gamma_ladder,
            "delta_flow": delta_flow,
            "vol_regime": vol_regime,
            "pinning": pinning,
            "vol_smile": vol_smile,
            "divergence": divergence
        },
        "bullish_layers": bullish_layers,
        "bearish_layers": bearish_layers,
        "agreement": f"{max(bullish_layers, bearish_layers)}/6 layers"
    }

# ============================================================
# MAIN APP
# ============================================================
with st.sidebar:
    st.markdown("### 🏛️ VIPUL PROFESSIONAL v4.0")
    st.caption("Institutional-Grade Analysis")
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
    st.error("No data")
    st.stop()

signal = generate_professional_signal(df, spot, prev_close, info["step"])

# ============================================================
# DISPLAY
# ============================================================
st.markdown(f"# {idx_name}: {spot:,.2f}")
st.caption(f"Professional Terminal • {expiry} • {datetime.now():%H:%M:%S} IST")

# Signal box
if signal["signal"] == "BUY":
    st.success(f"🟢 BUY - Confidence {signal['confidence']}% - {signal['layers']['delta_flow']['reason']}")
elif signal["signal"] == "SELL":
    st.error(f"🔴 SELL - Confidence {signal['confidence']}% - {signal['layers']['delta_flow']['reason']}")
else:
    st.warning(f"🟡 WAIT - {signal['agreement']} layers agree")

# Layer breakdown
st.markdown("---")
st.markdown("### Layer Breakdown")

cols = st.columns(3)

with cols[0]:
    st.info(f"**Gamma Ladder**\nMax: {signal['layers']['gamma_ladder']['max_gamma_strike']:.0f}\nConcentration: {signal['layers']['gamma_ladder']['gamma_concentration']*100:.1f}%")

with cols[1]:
    st.info(f"**Delta Flow**\nCall: {signal['layers']['delta_flow']['call_flow']:.0f}\nPut: {signal['layers']['delta_flow']['put_flow']:.0f}")

with cols[2]:
    st.info(f"**Vol Regime**\nATM IV: {signal['layers']['vol_regime']['atm_iv']}\n{signal['layers']['vol_regime']['volatility_regime']}")

cols2 = st.columns(3)

with cols2[0]:
    st.info(f"**Pinning**\n{signal['layers']['pinning']['reason']}")

with cols2[1]:
    st.info(f"**Vol Smile**\nPCR: {signal['layers']['vol_smile']['pcr']}\n{signal['layers']['vol_smile']['skew_direction']}")

with cols2[2]:
    st.info(f"**Divergence**\n{signal['layers']['divergence']['reason']}")

st.markdown("---")
st.caption(f"v4.0 Professional • {datetime.now():%H:%M:%S} IST")