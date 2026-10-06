"""
VIPUL PROFESSIONAL TERMINAL v4.5 (HIGH-SPEED VISUAL FLASH UI)
Instant Dual-Force Gamma Blast & IV Spike Alert System
"""

import os
import requests
import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="Vipul Professional v4.5", layout="wide")

# ---------- STYLES (reconstructed; the original block was lost) ----------
st.markdown("""
<style>
@keyframes flashGreen { 0%,100% {box-shadow:0 0 0 rgba(0,200,83,0);} 50% {box-shadow:0 0 28px rgba(0,200,83,.9);} }
@keyframes flashRed   { 0%,100% {box-shadow:0 0 0 rgba(255,23,68,0);} 50% {box-shadow:0 0 28px rgba(255,23,68,.9);} }
@keyframes flashAmber { 0%,100% {box-shadow:0 0 0 rgba(255,171,0,0);} 50% {box-shadow:0 0 22px rgba(255,171,0,.8);} }
.status-box { padding:18px; border-radius:10px; margin:10px 0 18px 0; }
.status-title { font-size:26px; font-weight:800; }
.status-desc { font-size:15px; margin-top:6px; }
.flash-green { animation: flashGreen 1s infinite; }
.flash-red   { animation: flashRed 1s infinite; }
.flash-amber { animation: flashAmber 1.4s infinite; }
</style>
""", unsafe_allow_html=True)


# ---------- CONFIG ----------
def get_secret(name, default=""):
    """Read from st.secrets first, then environment variable."""
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


# ---------- DATA ----------
@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(scrip, seg, token):
    r = requests.post(
        EXPIRY_URL,
        headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("data", [])


@st.cache_data(ttl=30, show_spinner=False)
def fetch_option_chain(scrip, seg, expiry, token):
    r = requests.post(
        OPTIONCHAIN_URL,
        headers=auth_headers(token),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry},
        timeout=12,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    spot = float(data.get("last_price") or 0)

    def n(x):
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

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
            "CE_OI": n(ce.get("oi")), "PE_OI": n(pe.get("oi")),
            "CE_PrevOI": n(ce.get("previous_oi")), "PE_PrevOI": n(pe.get("previous_oi")),
            "CE_Gamma": n(ce_g.get("gamma")), "PE_Gamma": n(pe_g.get("gamma")),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return spot, df
    return spot, df.sort_values("Strike").reset_index(drop=True)


# ---------- ANALYSIS ----------
def analyze_gamma_ladder(df, spot):
    df = df.copy()
    df["total_gamma"] = df["CE_Gamma"].abs() * df["CE_OI"] + df["PE_Gamma"].abs() * df["PE_OI"]
    total_g = df["total_gamma"].sum()
    if total_g == 0:
        return {"max_gamma_strike": spot, "concentration": 0}
    top = df.nlargest(1, "total_gamma")[["Strike", "total_gamma"]].values
    return {"max_gamma_strike": float(top[0][0]), "concentration": float(top[0][1] / total_g)}


def detect_iv_spike_vs_gamma_blast(df):
    df = df.copy()
    df["CE_OI_Chg"] = df["CE_OI"] - df["CE_PrevOI"]
    df["PE_OI_Chg"] = df["PE_OI"] - df["PE_PrevOI"]

    ce_exits = df[df["CE_OI_Chg"] < -1000]["CE_OI_Chg"].sum()
    pe_exits = df[df["PE_OI_Chg"] < -1000]["PE_OI_Chg"].sum()
    ce_build = df[df["CE_OI_Chg"] > 1000]["CE_OI_Chg"].sum()
    pe_build = df[df["PE_OI_Chg"] > 1000]["PE_OI_Chg"].sum()

    if ce_exits < -3000 and pe_build > 3000:
        return {"status": "BULLISH GAMMA BLAST", "signal": "BUY",
                "desc": "Call writers capitulating and Put writers building floors."}
    elif pe_exits < -3000 and ce_build > 3000:
        return {"status": "BEARISH GAMMA BLAST", "signal": "SELL",
                "desc": "Put writers capitulating and Call writers building ceilings."}
    elif ce_exits < -3000 and pe_build <= 1000:
        return {"status": "IV SPIKE BULL TRAP", "signal": "WAIT",
                "desc": "Isolated Call short covering without Put support. Trap risk."}
    elif pe_exits < -3000 and ce_build <= 1000:
        return {"status": "IV SPIKE BEAR TRAP", "signal": "WAIT",
                "desc": "Isolated Put short covering without Call support. Trap risk."}
    else:
        return {"status": "BALANCED ACCUMULATION", "signal": "WAIT",
                "desc": "Market in range compression. Awaiting trigger."}


# ---------- SIDEBAR ----------
with st.sidebar:
    st.markdown("### VIPUL TERMINAL v4.5")
    dhan_token = st.text_input("DHAN TOKEN", type="password", value=DEFAULT_DHAN_TOKEN)
    idx_name = st.selectbox("INDEX", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]

    require_token(dhan_token)

    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except Exception as e:
        expiries = []
        st.error(f"Could not load expiries: {e}")

    if not expiries:
        st.warning("No expiries available. Check your token and try again.")
        st.stop()

    expiry = st.selectbox("EXPIRY", expiries)
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN)", value=True)
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="v45")

# ---------- MAIN ----------
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

if df.empty:
    st.warning("Option chain returned no data (market closed or invalid expiry).")
    st.stop()

gamma_ladder = analyze_gamma_ladder(df, spot)
analysis = detect_iv_spike_vs_gamma_blast(df)

st.markdown(f"## {idx_name} Spot: {spot:,.2f}")

status = analysis["status"]
signal = analysis["signal"]

if "BULLISH GAMMA BLAST" in status:
    color, bg, flash = "#00c853", "rgba(0,200,83,0.15)", "flash-green"
elif "BEARISH GAMMA BLAST" in status:
    color, bg, flash = "#ff1744", "rgba(255,23,68,0.15)", "flash-red"
elif "TRAP" in status:
    color, bg, flash = "#ffab00", "rgba(255,171,0,0.15)", "flash-amber"
else:
    color, bg, flash = "#90a4ae", "rgba(144,164,174,0.12)", ""

st.markdown(
    f"""
    <div class="status-box {flash}" style="border:2px solid {color};background:{bg};">
        <div class="status-title" style="color:{color};">{status} &nbsp;|&nbsp; {signal}</div>
        <div class="status-desc">{analysis['desc']}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
c1.metric("Max Gamma Strike", f"{gamma_ladder['max_gamma_strike']:,.0f}")
c2.metric("Gamma Concentration", f"{gamma_ladder['concentration'] * 100:.1f}%")
c3.metric("Distance from Spot", f"{gamma_ladder['max_gamma_strike'] - spot:+,.0f}")

st.caption(f"Last updated: {pd.Timestamp.now().strftime('%H:%M:%S')}")

with st.expander("Option chain data"):
    st.dataframe(df, use_container_width=True)
