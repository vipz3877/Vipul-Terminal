"""
VIPUL BLOOMBERG PROFESSIONAL TERMINAL v5.0
Unified Engine: Bias Scoring, 35-Point Traps, Expected Move & Dual-Force Gamma Blasts
"""

import os
import math
from datetime import datetime, date

import requests
import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="Vipul Bloomberg Terminal v5.0", layout="wide")

# ============================================================
# BLOOMBERG TERMINAL CSS STYLING (reconstructed)
# ============================================================
st.markdown("""
<style>
.stApp { background-color:#000000; color:#ff9800; font-family:'Courier New',monospace; }
section[data-testid="stSidebar"] { background-color:#0b0b0b; border-right:1px solid #333; }
h1,h2,h3,h4 { color:#ff9800 !important; font-family:'Courier New',monospace; letter-spacing:1px; }
[data-testid="stMetricValue"] { color:#ffffff; }
[data-testid="stMetricLabel"] { color:#ff9800; }
.bbg-panel { border:1px solid #333; background:#0f0f0f; padding:14px; border-radius:4px; margin-bottom:12px; }
.bbg-title { font-size:12px; color:#888; letter-spacing:2px; text-transform:uppercase; }
.bbg-big { font-size:26px; font-weight:800; }
.bbg-desc { font-size:14px; margin-top:6px; color:#ddd; }
@keyframes flashGreen { 0%,100% {box-shadow:0 0 0 rgba(0,230,118,0);} 50% {box-shadow:0 0 28px rgba(0,230,118,.9);} }
@keyframes flashRed   { 0%,100% {box-shadow:0 0 0 rgba(255,23,68,0);} 50% {box-shadow:0 0 28px rgba(255,23,68,.9);} }
@keyframes flashAmber { 0%,100% {box-shadow:0 0 0 rgba(255,171,0,0);} 50% {box-shadow:0 0 22px rgba(255,171,0,.8);} }
.flash-green { animation: flashGreen 1s infinite; }
.flash-red   { animation: flashRed 1s infinite; }
.flash-amber { animation: flashAmber 1.4s infinite; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# CONFIGURATION & API
# ============================================================
def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


CLIENT_ID = get_secret("DHAN_CLIENT_ID", "1108425500")
DEFAULT_DHAN_TOKEN = get_secret("DHAN_ACCESS_TOKEN", "")

OPTIONCHAIN_URL = "https://api.dhan.co/v2/optionchain"
EXPIRY_URL = "https://api.dhan.co/v2/optionchain/expirylist"

INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50, "default_prev": 24500.0},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100, "default_prev": 55000.0},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50, "default_prev": 26000.0},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25, "default_prev": 12500.0},
}


def auth_headers(token):
    return {"Content-Type": "application/json", "access-token": token, "client-id": CLIENT_ID}


def require_token(token):
    if not token:
        st.error("DHAN Token required. Paste it in the sidebar.")
        st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token):
    r = requests.post(
        EXPIRY_URL, headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def _n(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token):
    r = requests.post(
        OPTIONCHAIN_URL, headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
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
            "CE_Gamma": _n(ce_g.get("gamma")),   # FIXED: removed stray walrus operator
            "PE_Gamma": _n(pe_g.get("gamma")),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return spot, df
    return spot, df.sort_values("Strike").reset_index(drop=True)


def parse_dte(expiry_str):
    """Calendar days to expiry (minimum 1)."""
    try:
        exp_date = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
        return max(1, (exp_date - date.today()).days)
    except ValueError:
        return 1


# ============================================================
# UNIFIED QUANTITATIVE ENGINE
# ============================================================
def run_quantitative_analysis(df, spot, prev_close, expiry_str):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    # PCR
    total_ce_oi = df["CE_OI"].sum()
    total_pe_oi = df["PE_OI"].sum()
    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

    # Expected move (calendar days -> 365-day year)
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    atm_iv = float(df.loc[atm_idx, "CE_IV"]) or 12.0
    dte = parse_dte(expiry_str)
    exp_move = spot * (atm_iv / 100) * math.sqrt(dte / 365)

    # Walls & gamma anchor
    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    max_gamma_strike = float(df.loc[df["total_gamma"].idxmax(), "Strike"]) if df["total_gamma"].sum() > 0 else spot
    call_wall = float(df.loc[df["CE_OI"].idxmax(), "Strike"])
    put_wall = float(df.loc[df["PE_OI"].idxmax(), "Strike"])

    # 35-point trap detection
    near = df[(df["Strike"] >= spot - 300) & (df["Strike"] <= spot + 300)]
    ce_trap_res, pe_trap_supp = None, None
    if not near.empty:
        i = near["CE_OI_Chg"].idxmax()
        if near.loc[i, "CE_OI_Chg"] > 10000:
            ce_trap_res = float(near.loc[i, "Strike"]) + 35
        j = near["PE_OI_Chg"].idxmax()
        if near.loc[j, "PE_OI_Chg"] > 10000:
            pe_trap_supp = float(near.loc[j, "Strike"]) - 35

    # Dual-force engine
    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()
    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()

    # Bias score
    score = 50
    if ce_exits < -5000 and pe_build > 5000:
        score += 25
    elif pe_exits < -5000 and ce_build > 5000:
        score -= 25
    if spot - prev_close > 20:
        score += 10
    elif spot - prev_close < -20:
        score -= 10
    if pcr > 1.2:
        score += 8
    elif pcr < 0.8:
        score -= 8
    score = max(0, min(100, score))
    bias_label = "BULLISH" if score > 55 else "BEARISH" if score < 45 else "NEUTRAL"

    # Final status
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
        "call_wall": call_wall, "put_wall": put_wall,
        "ce_trap": ce_trap_res, "pe_trap": pe_trap_supp, "atm_iv": atm_iv, "dte": dte,
    }


# ============================================================
# SIDEBAR CONTROLS
# ============================================================
with st.sidebar:
    st.markdown("### BBG // TERMINAL CONFIG")
    dhan_token = st.text_input("DHAN TOKEN", type="password", value=DEFAULT_DHAN_TOKEN)
    require_token(dhan_token)

    idx_name = st.selectbox("INDEX SELECTION", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]

    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except Exception as e:
        expiries = []
        st.error(f"Could not load expiries: {e}")

    if not expiries:
        st.warning("No expiries available. Check your token.")
        st.stop()

    expiry = st.selectbox("EXPIRY DATE", expiries)
    prev_close = st.number_input(
        "PREV CLOSE", value=info["default_prev"], step=float(info["step"]),
        help="Enter yesterday's closing value. Used for the bias score.",
    )

    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="bbg")

# ============================================================
# FETCH
# ============================================================
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

if df.empty or spot == 0:
    st.warning("Option chain returned no data (market closed or invalid expiry).")
    st.stop()

analysis = run_quantitative_analysis(df, spot, prev_close, expiry)

# ============================================================
# BLOOMBERG DASHBOARD INTERFACE (reconstructed)
# ============================================================
st.markdown(f"## {idx_name} // SPOT {spot:,.2f}  ({spot - prev_close:+,.2f})")

status = analysis["status"]
if "BULLISH GAMMA BLAST" in status:
    color, flash = "#00e676", "flash-green"
elif "BEARISH GAMMA BLAST" in status:
    color, flash = "#ff1744", "flash-red"
elif "TRAP" in status:
    color, flash = "#ffab00", "flash-amber"
else:
    color, flash = "#90a4ae", ""

st.markdown(
    f"""
    <div class="bbg-panel {flash}" style="border:2px solid {color};">
        <div class="bbg-title">Dual-Force Engine</div>
        <div class="bbg-big" style="color:{color};">{status} &nbsp;|&nbsp; {analysis['signal']}</div>
        <div class="bbg-desc">{analysis['desc']}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("BIAS SCORE", f"{analysis['score']}/100", analysis["bias"])
c2.metric("PCR", f"{analysis['pcr']:.2f}")
c3.metric("EXPECTED MOVE (±)", f"{analysis['exp_move']:,.0f}", f"ATM IV {analysis['atm_iv']:.1f}% | {analysis['dte']}d")
c4.metric("MAX GAMMA STRIKE", f"{analysis['max_gamma']:,.0f}", f"{analysis['max_gamma'] - spot:+,.0f} vs spot")

c5, c6, c7, c8 = st.columns(4)
c5.metric("CALL WALL (RES)", f"{analysis['call_wall']:,.0f}")
c6.metric("PUT WALL (SUPP)", f"{analysis['put_wall']:,.0f}")
c7.metric("CE TRAP LEVEL", f"{analysis['ce_trap']:,.0f}" if analysis["ce_trap"] else "—")
c8.metric("PE TRAP LEVEL", f"{analysis['pe_trap']:,.0f}" if analysis["pe_trap"] else "—")

st.caption(
    f"Range from expected move: {spot - analysis['exp_move']:,.0f} to {spot + analysis['exp_move']:,.0f}"
    f"  |  Updated {datetime.now().strftime('%H:%M:%S')}"
)

with st.expander("OPTION CHAIN DATA"):
    st.dataframe(df, use_container_width=True)
