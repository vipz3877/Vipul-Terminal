"""
🤖 VIPUL WATCHDOG TERMINAL v3.0 (MASTER GANN + VIX + GREEKS VELOCITY EDITION)
Comprehensive Single Agent + Master Strike Behaviour Engine + Granular Strike Matrix
+ Dynamic Sidebar Inputs + 3-Min Watchdog + Expected Move Calculation (1σ Range) 
+ Flash Signals (Tab 2) + Master Greeks & OI Velocity Desk (Tab 3) + Master Gann Execution Desk (Tab 4)
+ Gann VIX Volatility Matrix
"""

import sys
import io

# Force UTF-8 encoding to prevent Windows ASCII/Emoji crash errors
try:
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except (AttributeError, TypeError):
    pass

try:
    if sys.stderr.encoding.lower() != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except (AttributeError, TypeError):
    pass

import os
import sqlite3
import json
import math
from datetime import datetime
import urllib.parse
import xml.etree.ElementTree as ET
import requests
import pandas as pd
import numpy as np
import streamlit as st

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="Vipul Watchdog Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

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
# DATABASE
# ============================================================
def init_db():
    con = sqlite3.connect("vipul_ai_desk.db")
    con.execute("""CREATE TABLE IF NOT EXISTS market_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, spot REAL, pcr REAL,
        ce_oi REAL, pe_oi REAL, atm_iv REAL, bias REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ai_verdicts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, spot REAL, verdict TEXT, confidence INTEGER,
        entry REAL, target REAL, stop_loss REAL, position_size TEXT,
        analysis TEXT)""")
    con.commit()
    con.close()

init_db()

# ============================================================
# DHAN API & DATA FUNCTIONS
# ============================================================
def auth_headers(token):
    return {
        "Content-Type": "application/json",
        "access-token": token,
        "client-id": CLIENT_ID
    }

def require_token(token):
    if not token:
        st.error("❌ DHAN Access Token is missing. Paste it in the sidebar.")
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
        raise RuntimeError("❌ Dhan Token expired (401 Unauthorized). Please update your token in the sidebar.")
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
            "CE_Vol": n(ce.get("volume")), "PE_Vol": n(pe.get("volume")),
            "CE_IV": n(ce.get("implied_volatility")), "PE_IV": n(pe.get("implied_volatility")),
            "CE_Delta": n(ce_greeks.get("delta")), "PE_Delta": n(pe_greeks.get("delta")),
            "CE_Gamma": n(ce_greeks.get("gamma")), "PE_Gamma": n(pe_greeks.get("gamma")),
            "CE_Theta": n(ce_greeks.get("theta")), "PE_Theta": n(pe_greeks.get("theta")),
        })
    
    df = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)
    return spot, df

@st.cache_data(ttl=60, show_spinner=False)
def fetch_india_vix():
    """Fetches Live India VIX via yfinance for Gann 3^2 and 4^2 analysis."""
    if yf is None:
        return 11.50
    try:
        vix_data = yf.Ticker("^INDIAVIX").history(period="1d")
        if not vix_data.empty:
            return float(vix_data["Close"].iloc[-1])
        return 11.50
    except Exception:
        return 11.50

# ============================================================
# NEWS & MACRO AGENT
# ============================================================
@st.cache_data(ttl=60, show_spinner=False)
def build_news_queries(index_name):
    return [
        f"{index_name} NIFTY India stock market",
        "US Federal Reserve Fed interest rates Treasury yields",
        "ECB interest rates global markets",
        "crude oil Brent WTI Middle East geopolitics",
        "India tariffs trade policy rupee dollar",
        "FII DII foreign institutional investors India flows",
        "RBI India inflation CPI GDP macro economy",
    ]

@st.cache_data(ttl=120, show_spinner=False)
def fetch_macro_news(index_name):
    items = []
    seen = set()
    for query in build_news_queries(index_name):
        try:
            q = urllib.parse.quote_plus(query)
            url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
            r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:6]:
                title_node = item.find('title')
                pub_node = item.find('pubDate')
                if title_node is None or not title_node.text:
                    continue
                title = title_node.text.strip()
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append({"title": title, "pub": pub_node.text if pub_node is not None else ""})
        except Exception:
            continue
    return items[:30]

def classify_news_topic(title):
    t = title.lower()
    groups = {
        "GLOBAL MACRO": ["fed", "federal reserve", "interest rate", "treasury", "bond yield", "ecb", "powell"],
        "GEOPOLITICS / COMMODITIES": ["crude", "brent", "wti", "oil", "middle east", "iran", "israel", "war", "tariff", "trade war"],
        "INSTITUTIONAL FLOWS": ["fii", "fpi", "dii", "foreign institutional", "institutional investor", "fund flow"],
        "INDIA MACRO": ["rbi", "inflation", "cpi", "gdp", "rupee", "inr", "india economy"],
        "MARKET SPECIFIC": ["nifty", "bank nifty", "sensex", "stock market", "equity"],
    }
    for topic, words in groups.items():
        if any(w in t for w in words):
            return topic
    return "GENERAL"

def calculate_news_impact(title):
    t = title.lower()
    bullish = ["rate cut", "cuts rates", "dovish", "eases", "stimulus", "falls", "cooling inflation", "inflows", "strong growth"]
    bearish = ["rate hike", "hikes rates", "hawkish", "yield rises", "yields rise", "inflation rises", "outflows", "war", "tariff", "oil rises", "crude rises"]
    b = sum(1 for w in bullish if w in t)
    r = sum(1 for w in bearish if w in t)
    if b > r: return "BULLISH"
    if r > b: return "BEARISH"
    return "NEUTRAL"

def news_signal(news_items):
    counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    for x in news_items:
        counts[calculate_news_impact(x["title"])] += 1
    if counts["BULLISH"] > counts["BEARISH"] * 1.5:
        bias = "BULLISH"
    elif counts["BEARISH"] > counts["BULLISH"] * 1.5:
        bias = "BEARISH"
    else:
        bias = "MIXED / NEUTRAL"
    return {"bias": bias, "counts": counts}

def format_macro_news(news_items, limit=12):
    if not news_items:
        return "No recent macro news available"
    return " | ".join(f"[{classify_news_topic(x['title'])}] {x['title']}" for x in news_items[:limit])

def run_news_agent(news_items, api_key):
    if not news_items:
        return {"bias": "NO DATA", "impact": "LOW", "summary": "No recent news available.", "topics": []}
    if not api_key or Groq is None:
        ns = news_signal(news_items)
        return {"bias": ns["bias"], "impact": "MEDIUM", "summary": "News classified locally; Groq news analysis disabled.", "topics": []}
    context = "\n".join(f"- {classify_news_topic(x['title'])}: {x['title']}" for x in news_items[:20])
    prompt = f"""Analyze ONLY the following macro/news headlines. Do not analyze option-chain data and do not create a trading entry, target, stop loss, BUY, SELL, or WAIT signal. Return JSON only.\n\n{context}\n\nFormat: {{"bias":"BULLISH/BEARISH/MIXED/NEUTRAL","impact":"HIGH/MEDIUM/LOW","summary":"one concise sentence","topics":["topic"]}}"""
    raw = ai_call_with_retry(prompt, api_key)
    parsed = parse_ai_response(raw) if raw else None
    if not parsed or "error" in parsed:
        ns = news_signal(news_items)
        return {"bias": ns["bias"], "impact": "MEDIUM", "summary": "Local headline classification used because news AI was unavailable.", "topics": []}
    return parsed

# ============================================================
# STRIKE DOMINANCE ENGINE & METRICS
# ============================================================
def calculate_max_pain(df, spot):
    best = None
    best_val = float("inf")
    for settle in df["Strike"]:
        pain = ((settle - df["Strike"]).clip(lower=0) * df["CE_OI"] +
                (df["Strike"] - settle).clip(lower=0) * df["PE_OI"]).sum()
        if pain < best_val:
            best_val = float(pain)
            best = float(settle)
    return best or spot

def calculate_expected_move(spot, atm_iv, dte=2):
    if dte <= 0 or spot <= 0 or atm_iv <= 0:
        return None, None, None
    exp_move = spot * (atm_iv / 100) * math.sqrt(dte / 252)
    lower = spot - exp_move
    upper = spot + exp_move
    return round(exp_move, 2), round(lower, 2), round(upper, 2)

def calculate_metrics(df, spot, prev_close):
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    lo = max(0, pos - 8)
    hi = min(len(df), pos + 9)
    near = df.iloc[lo:hi].copy()
    
    near["CE_OI_Change"] = near["CE_OI"] - near["CE_PrevOI"]
    near["PE_OI_Change"] = near["PE_OI"] - near["PE_PrevOI"]
    
    ce_oi = near["CE_OI"].sum()
    pe_oi = near["PE_OI"].sum()
    pcr = pe_oi / ce_oi if ce_oi > 0 else 0
    
    dce = near["CE_OI_Change"].sum()
    dpe = near["PE_OI_Change"].sum()
    
    avg_ce_iv = near["CE_IV"].mean()
    avg_pe_iv = near["PE_IV"].mean()
    atm_iv = (avg_ce_iv + avg_pe_iv) / 2
    
    gamma_df = near.copy()
    gamma_df["total_gamma"] = gamma_df["CE_Gamma"].abs() * gamma_df["CE_OI"] + gamma_df["PE_Gamma"].abs() * gamma_df["PE_OI"]
    max_gamma_idx = gamma_df["total_gamma"].idxmax()
    gamma_strike = float(gamma_df.loc[max_gamma_idx, "Strike"])
    
    call_wall = float(near.loc[near["CE_OI"].idxmax(), "Strike"]) if not near.empty else spot
    put_wall = float(near.loc[near["PE_OI"].idxmax(), "Strike"]) if not near.empty else spot
    
    atm_strike = float(df.loc[atm_idx, "Strike"])
    strike_distance = spot - atm_strike
    within_35pt_boundary = abs(strike_distance) <= 35
    
    score = 50
    if dce < 0 and dpe > 0: score += 20  
    if within_35pt_boundary: score += 10 
    if spot - prev_close > 20: score += 10
    elif spot - prev_close < -20: score -= 10
    if pcr > 1.1: score += 5
    elif pcr < 0.9: score -= 5
    
    score = max(0, min(100, score))
    bias = "BULLISH" if score >= 60 else "BEARISH" if score <= 40 else "NEUTRAL"
    
    return {
        "near": near,
        "spot": spot,
        "atm_strike": atm_strike,
        "strike_distance": strike_distance,
        "within_35pt_boundary": within_35pt_boundary,
        "pcr": round(pcr, 3),
        "dce": dce / 1000,
        "dpe": dpe / 1000,
        "atm_iv": round(atm_iv, 2),
        "gamma_strike": gamma_strike,
        "max_pain": calculate_max_pain(df, spot),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "score": score,
        "bias": bias,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
    }

class StrikeDominanceEngine:
    def __init__(self, df, spot, step=50):
        self.df = df.copy()
        self.spot = spot
        self.step = step
        self.atm_idx = (df["Strike"] - spot).abs().idxmin()
    
    def get_neighbor_avg(self, strike, side='PE', window=3):
        try:
            idx = self.df[self.df['Strike'] == strike].index[0]
        except IndexError:
            idx = self.atm_idx
        pos = self.df.index.get_loc(idx)
        lo = max(0, pos - window)
        hi = min(len(self.df), pos + window + 1)
        neighbors = self.df.iloc[lo:hi]
        
        if side == 'PE':
            changes = (neighbors["PE_OI"] - neighbors["PE_PrevOI"]).values
        else:
            changes = (neighbors["CE_OI"] - neighbors["CE_PrevOI"]).values
        return np.mean(changes) if len(changes) > 0 else 0.0

    def find_dominant_strike(self, side='PE'):
        dominant_strike = None
        max_vel = 0.0
        if self.df.empty:
            return None, 0.0
        for _, row in self.df.iterrows():
            strike = row['Strike']
            if side == 'PE':
                oi_chg = row['PE_OI'] - row['PE_PrevOI']
                n_avg = self.get_neighbor_avg(strike, 'PE', 3)
            else:
                oi_chg = row['CE_OI'] - row['CE_PrevOI']
                n_avg = self.get_neighbor_avg(strike, 'CE', 3)
            velocity = oi_chg / (abs(n_avg) + 1.0)
            if abs(velocity) > abs(max_vel):
                max_vel = velocity
                dominant_strike = strike
        return dominant_strike, float(max_vel) if dominant_strike else 0.0

    def analyze(self):
        put_strike, put_vel = self.find_dominant_strike('PE')
        call_strike, call_vel = self.find_dominant_strike('CE')
        
        if put_vel > call_vel and put_vel > 1.2:
            signal = "BUY"
            dom_strike = put_strike
            conf = min(95, int(50 + abs(put_vel) * 10))
            reason = f"Put dominance detected at strike {put_strike} with high relative OI velocity ({put_vel:.2f}x)."
        elif call_vel > put_vel and call_vel > 1.2:
            signal = "SELL"
            dom_strike = call_strike
            conf = min(95, int(50 + abs(call_vel) * 10))
            reason = f"Call dominance detected at strike {call_strike} with high relative OI velocity ({call_vel:.2f}x)."
        else:
            signal = "WAIT"
            dom_strike = None
            conf = 0
            reason = "Market flow is balanced or lacks exceptional strike velocity. Awaiting structural confirmation."
            
        return {
            "signal": signal,
            "dominant_strike": dom_strike,
            "confidence": conf,
            "reason": reason,
            "put_strike": put_strike,
            "put_velocity": put_vel,
            "call_strike": call_strike,
            "call_velocity": call_vel
        }

def scan_for_live_triggers(df, spot):
    triggers = []
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    near = df.iloc[max(0, pos-5):min(len(df), pos+6)].copy()
    
    for _, row in near.iterrows():
        ce_oi_chg = row["CE_OI"] - row["CE_PrevOI"]
        pe_oi_chg = row["PE_OI"] - row["PE_PrevOI"]
        strike_distance = spot - row["Strike"]
        
        if ce_oi_chg < 0 and pe_oi_chg > 0 and abs(strike_distance) <= 35:
            triggers.append({
                "strike": row["Strike"],
                "ce_change": ce_oi_chg,
                "pe_change": pe_oi_chg,
                "distance": strike_distance
            })
    return triggers

# ============================================================
# AI AGENT
# ============================================================
def ai_call_with_retry(prompt, api_key, retries=2):
    if Groq is None:
        return "ERROR: groq library not installed. Run: pip install groq"
    if not api_key:
        return "ERROR: GROQ_API_KEY is missing. Paste your Groq API key in the sidebar."

    for attempt in range(retries):
        try:
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the AI analysis layer of the Vipul Watchdog Trading Terminal. "
                            "Analyze only the supplied market data. Do not invent missing data. "
                            "Return valid JSON only when the user prompt requests JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_completion_tokens=1800,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            if attempt == retries - 1:
                return f"ERROR: {str(e)}"
            continue

    return "ERROR: Retries exhausted"

def run_comprehensive_ai_agent(market_data, engine_result, news, api_key, exp_move, lower_range, upper_range):
    spot = market_data['spot']
    atm_strike = market_data['atm_strike']
    strike_distance = market_data['strike_distance']
    within_boundary = market_data['within_35pt_boundary']
    bias = market_data['bias']
    score = market_data['score']
    pcr = market_data['pcr']
    dce = market_data['dce']
    dpe = market_data['dpe']
    max_pain = market_data['max_pain']
    put_wall = market_data['put_wall']
    call_wall = market_data['call_wall']
    signal = engine_result['signal']
    
    if signal == 'BUY':
        entry = spot
        target = min(call_wall, upper_range)
        stop_loss = max(put_wall, lower_range)
    elif signal == 'SELL':
        entry = spot
        target = max(put_wall, lower_range)
        stop_loss = min(call_wall, upper_range)
    else:
        entry = spot
        target = spot
        stop_loss = spot

    strike_summary = []
    for _, row in market_data['near'].iterrows():
        strike_summary.append(f"Strike {row['Strike']}: CE OI Chg={row['CE_OI_Change']:+.0f}, PE OI Chg={row['PE_OI_Change']:+.0f}")
    strike_context = " | ".join(strike_summary)

    prompt = f"""Analyze this option-chain data using the Master Strike Dominance methodology.

SPOT: {spot:.2f} | Reference ATM Strike: {atm_strike} | Distance: {strike_distance:+.1f} pts | Within 35-Pt Boundary: {within_boundary}
PCR: {pcr:.3f} | Master Bias Score: {score}/100 ({bias})
ENGINE SIGNAL: {signal} | REASON: {engine_result['reason']}

EXPECTED DAILY MOVE (1σ Range, 68% Probability):
Expected Move: ₹{exp_move}
Lower Range (Spot - 1σ): {lower_range:.2f}
Upper Range (Spot + 1σ): {upper_range:.2f}

Validate Entry/Target/SL against this 1σ probability range and institutional walls.

GRANULAR STRIKE BREAKDOWN (Near ATM):
{strike_context}

AGGREGATE FLOW:
CE Net Change: {dce:+.1f}K | PE Net Change: {dpe:+.1f}K
Max Pain: {max_pain:.0f} | Put Wall: {put_wall:.0f} | Call Wall: {call_wall:.0f}
NEWS IS EXCLUDED FROM THE MARKET-STRUCTURE SIGNAL.

Return ONLY this JSON format (no markdown code blocks, no preamble):
{{
  "signal": "{signal}", 
  "market_psychology": "Bulls taking control",
  "confidence": {engine_result['confidence']}, 
  "entry_price": {entry}, 
  "target_price": {target}, 
  "stop_loss": {stop_loss}, 
  "position_size": "1 LOT", 
  "risk_reward": "1:2", 
  "why_this_signal": "{engine_result['reason']}",
  "greeks_insight": "Insight based on Max Pain and strike proximity.",
  "oi_analysis": "Detailed breakdown of strike-specific CE/PE OI changes.",
  "volume_insight": "Insight based on active volume around active strikes.",
  "order_block_zones": "Major institutional strike zones and 35-point boundary evaluation."
}}"""

    return ai_call_with_retry(prompt, api_key)

def parse_ai_response(response):
    try:
        if response.startswith("ERROR:"):
            return {"error": response}
        
        json_str = response.strip()
        if "```" in json_str:
            parts = json_str.split("```")
            for part in parts:
                if part.startswith("json"):
                    json_str = part[4:]
                elif part.startswith("{"):
                    json_str = part
                    break
        start_idx = json_str.find('{')
        end_idx = json_str.rfind('}')
        if start_idx == -1 or end_idx == -1:
            return {"error": f"Failed to parse JSON structure from AI response: {response[:200]}"}
        return json.loads(json_str[start_idx:end_idx+1])
    except Exception as e:
        return {"error": f"JSON Parse Exception: {str(e)} | Raw Response: {response[:200]}"}

# ============================================================
# MAIN APP CONFIGURATION & DATA FETCHING
# ============================================================
with st.sidebar:
    st.markdown("### ⚡ WATCHDOG DESK")
    st.caption("VIPUL STRIKE DOMINANCE v3.0")
    st.divider()
    
    dhan_token = st.text_input("DHAN ACCESS TOKEN", value=DEFAULT_DHAN_TOKEN, type="password", help="Update your daily 24-hr Dhan token here")
    groq_key = st.text_input("GROQ API KEY", type="password", help="Get your Groq API key from Groq Console")
    
    st.divider()
    idx_name = st.selectbox("INDEX", list(INDEX_MAP.keys()))
    info = INDEX_MAP[idx_name]
    
    try:
        expiries = get_expiries(info["scrip"], info["seg"], dhan_token)
    except Exception as e:
        st.error(f"Expiry fetch error (Check Token): {e}")
        st.stop()
    
    expiry = st.selectbox("EXPIRY", expiries) if expiries else st.stop()
    prev_close = st.number_input("PREV CLOSE", value=23500.0, step=float(info["step"]))
    manual_vix = st.number_input("MANUAL INDIA VIX (Override)", value=0.0, step=0.1, help="If 0.0, fetches live from Yahoo Finance")
    
    st.divider()
    st.markdown("### 🔄 AUTOMATED LOOP")
    auto_refresh = st.toggle("AUTO REFRESH (3 MIN SCAN)", value=True)
    
    if auto_refresh and st_autorefresh:
        st_autorefresh(interval=180000, limit=None, key="watchdog_loop")
    
    ai_enabled = st.toggle("ENABLE AI", value=True)

require_token(dhan_token)

symbol = f"{idx_name}|{expiry}"
try:
    spot, df = fetch_option_chain(info["scrip"], info["seg"], expiry, dhan_token)
except Exception as e:
    st.error(f"API Error: {e}")
    st.stop()

if df.empty:
    st.error("No data returned from Dhan API.")
    st.stop()

if manual_vix > 0:
    vix_level = manual_vix
else:
    vix_level = fetch_india_vix()

m = calculate_metrics(df, spot, prev_close)
news_items = fetch_macro_news(idx_name)
news = format_macro_news(news_items)
news_result = run_news_agent(news_items, groq_key)

exp_move, lower_range, upper_range = calculate_expected_move(spot, m["atm_iv"], dte=2)

engine = StrikeDominanceEngine(df, spot, info["step"])
engine_result = engine.analyze()

# ============================================================
# TAB LAYOUT CREATION (4 TABS)
# ============================================================
tab1, tab2, tab3, tab4 = st.tabs([
    "⚡ Live Surveillance Matrix", 
    "🎯 Flash Signal Desk", 
    "📊 Master Greeks & OI Desk",
    "📐 Master Gann Execution Desk"
])

# ============================================================
# TAB 1: LIVE STRIKE SURVEILLANCE & METRICS TERMINAL
# ============================================================
with tab1:
    st.title(f"{idx_name} : {spot:,.2f}")
    st.caption(f"LIVE WATCHDOG (3M REFRESH) • {expiry} • Ref Strike: {m['atm_strike']} ({m['strike_distance']:+.1f} pts) • 35-Pt Zone: {'✅ Active' if m['within_35pt_boundary'] else '⚠️ Outside'} • {datetime.now():%H:%M:%S} IST")

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("MASTER SCORE", f"{m['score']:.0f}/100", m["bias"])
    col2.metric("PCR", f"{m['pcr']:.3f}")
    col3.metric("ATM IV", f"{m['atm_iv']:.2f}%")
    col4.metric("GAMMA", f"{int(m['gamma_strike']):,}")
    col5.metric("MAX PAIN", f"{int(m['max_pain']):,}")
    col6.metric("WALLS", f"{int(m['put_wall'])}/{int(m['call_wall'])}")

    st.divider()

    st.subheader("Expected Move & 1σ Probability Range")
    col_em1, col_em2, col_em3, col_em4 = st.columns(4)
    col_em1.metric("EXPECTED MOVE", f"₹{exp_move}")
    col_em2.metric("LOWER (1σ)", f"{lower_range:,.2f}")
    col_em3.metric("CURRENT", f"{spot:,.2f}")
    col_em4.metric("UPPER (1σ)", f"{upper_range:,.2f}")
    st.caption(f"1σ Range (~68% probability) • Formula: Spot × IV × √(DTE/252)")

    st.divider()

    st.subheader("📉 Gann VIX Volatility Matrix")
    vix_col1, vix_col2 = st.columns([1, 3])
    
    vix_col1.metric("INDIA VIX", f"{vix_level:.2f}")
    
    if vix_level >= 16:
        vix_col2.success(f"🟢 **CAPITULATION BOTTOM (VIX > 4²):** VIX crossed 16. Institutional panic is peaking. Prepare to Buy Weakness for a long-term bottom.")
    elif vix_level <= 10.5:
        vix_col2.error(f"🔴 **COMPLACENCY TOP (VIX ~ 3²):** VIX nearing 9. Premiums are dead. High probability of topping out. Sell Strength.")
    else:
        vix_col2.warning(f"🟡 **BLEEDING PHASE (11 - 15):** VIX is between 3² and 4². No clear bottom formed yet. More pain ahead. Avoid aggressive long positions.")
    
    st.divider()

    live_alerts = scan_for_live_triggers(df, spot)
    if live_alerts:
        st.info("🚨 REAL-TIME INSTITUTIONAL TRIGGER DETECTED! Call writers are unwinding while Put support locks in.")
        for alert in live_alerts:
            st.write(f"👉 **Strike {alert['strike']}**: Call OI Dropped by `{int(alert['ce_change']):,}` | Put OI Built by `+{int(alert['pe_change']):,}` (Distance: `{alert['distance']:+.1f} pts`)")
        st.divider()

    st.subheader("Live Active Strike Surveillance Matrix")
    near_table = m["near"][["Strike", "CE_LTP", "CE_OI", "CE_OI_Change", "PE_OI_Change", "PE_OI", "PE_LTP"]].copy()
    near_table.columns = ["Strike", "CE LTP", "CE OI", "CE OI Chg", "PE OI Chg", "PE OI", "PE LTP"]
    near_table["Strike"] = near_table["Strike"].astype(int)
    for col in ["CE OI", "CE OI Chg", "PE OI Chg", "PE OI"]:
        if col in near_table.columns:
            near_table[col] = near_table[col].apply(lambda x: f"{int(x):,}")
    st.dataframe(near_table, use_container_width=True)

    st.divider()

    st.subheader("Separate Macro News Signal")
    ns1, ns2, ns3 = st.columns(3)
    ns1.metric("NEWS BIAS", str(news_result.get("bias", "MIXED")))
    ns2.metric("NEWS IMPACT", str(news_result.get("impact", "MEDIUM")))
    counts = news_signal(news_items)["counts"]
    ns3.metric("HEADLINE MIX", f"B {counts['BULLISH']} / S {counts['BEARISH']}")
    st.info(news_result.get("summary", "No news summary available."))

# ============================================================
# TAB 2: FLASH SIGNAL EXECUTOR DESK (ENTRY, TARGET, SL)
# ============================================================
with tab2:
    st.subheader("Flash Signal Desk (Entry, Target & Stop Loss)")
    st.write("Evaluates structural option walls, expected move boundaries, and strike velocity to flash actionable trade setups.")

    if not ai_enabled:
        st.info("ℹ️ Enable AI in sidebar to activate Flash Signals.")
    elif not groq_key:
        st.warning("❌ Please paste your Groq API key in the sidebar to flash trade setups.")
    else:
        with st.spinner("🤖 Analyzing institutional boundaries and flashing trade levels..."):
            raw_resp = run_comprehensive_ai_agent(m, engine_result, news_items, groq_key, exp_move, lower_range, upper_range)
            verdict = parse_ai_response(raw_resp)
            
            if verdict and "error" in verdict:
                st.error(f"⚠️ AI Error: {verdict['error']}")
            elif verdict:
                sig_type = verdict.get("signal", engine_result['signal'])
                
                def safe_fmt(val):
                    try: return f"{float(val):,.2f}"
                    except (ValueError, TypeError): return str(val)

                entry_str = safe_fmt(verdict.get('entry_price', spot))
                target_str = safe_fmt(verdict.get('target_price', spot))
                sl_str = safe_fmt(verdict.get('stop_loss', spot))
                rr_str = verdict.get('risk_reward', 'N/A')
                psyche = verdict.get('market_psychology', 'Neutral')
                
                if sig_type == "BUY":
                    st.success(f"### 🟢 FLASH SIGNAL: {sig_type} [{psyche}] | 🎯 ENTRY: {entry_str} | 🚀 TARGET: {target_str} | 🛑 SL: {sl_str} | R:R: {rr_str}")
                elif sig_type == "SELL":
                    st.error(f"### 🔴 FLASH SIGNAL: {sig_type} [{psyche}] | 🎯 ENTRY: {entry_str} | 🚀 TARGET: {target_str} | 🛑 SL: {sl_str} | R:R: {rr_str}")
                else:
                    st.warning(f"### 🟡 STATUS: {sig_type} [{psyche}] — Awaiting structural confirmation (No setup active)")
                
                st.subheader("Strike Dominance Rationale")
                st.info(verdict.get('why_this_signal', 'N/A'))
                
                with st.expander("📋 Full Technical Breakdown"):
                    col_ex1, col_ex2 = st.columns(2)
                    with col_ex1:
                        st.markdown("**Greeks & Magnet Insight**")
                        st.write(verdict.get('greeks_insight', 'N/A'))
                        st.markdown("**OI Analysis**")
                        st.write(verdict.get('oi_analysis', 'N/A'))
                    with col_ex2:
                        st.markdown("**Volume & Participant Flow**")
                        st.write(verdict.get('volume_insight', 'N/A'))
                        st.markdown("**Order Blocks & Boundaries**")
                        st.write(verdict.get('order_block_zones', 'N/A'))

# ============================================================
# TAB 3: MASTER GREEKS & OI VELOCITY DESK (ATM CENTERED)
# ============================================================
with tab3:
    st.markdown("### 📊 Master Greeks & OI Velocity Desk")
    st.write("Real-time monitoring of tick-by-tick Greek pressure mapped against local institutional OI walls, perfectly centered around live ATM.")

    # 1. Correctly find ATM index and slice 8 strikes below and 9 above spot
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    pos = df.index.get_loc(atm_idx)
    lo = max(0, pos - 8)
    hi = min(len(df), pos + 9)
    local_chain = df.iloc[lo:hi].copy()

    if local_chain.empty:
        st.warning("No strikes found near ATM. Check data feed.")
    else:
        # 2. Identify Active Structural Walls within Local Range
        put_wall_strike = float(local_chain.loc[local_chain['PE_OI'].idxmax()]['Strike'])
        call_wall_strike = float(local_chain.loc[local_chain['CE_OI'].idxmax()]['Strike'])

        g_col1, g_col2, g_col3 = st.columns(3)
        g_col1.metric("Spot Price", f"{spot:,.2f}")
        g_col2.metric("Active Support (Put Wall)", f"{put_wall_strike:,.0f}")
        g_col3.metric("Active Resistance (Call Wall)", f"{call_wall_strike:,.0f}")

        st.markdown("---")
        st.markdown("#### ⚡ Complete Options Matrix: OI, Delta, Gamma & Theta")

        # 3. Render Extended Table with Gamma & Theta
        greeks_table = local_chain[[
            'Strike', 'CE_OI', 'CE_Delta', 'CE_Gamma', 'CE_Theta', 
            'PE_Theta', 'PE_Gamma', 'PE_Delta', 'PE_OI'
        ]].copy()
        
        greeks_table.columns = [
            'Strike', 'CE OI', 'CE Delta', 'CE Gamma', 'CE Theta', 
            'PE Theta', 'PE Gamma', 'PE Delta', 'PE OI'
        ]
        greeks_table['Strike'] = greeks_table['Strike'].astype(int)

        for col in greeks_table.columns:
            if col != 'Strike' and 'OI' not in col:
                greeks_table[col] = greeks_table[col].apply(lambda x: f"{float(x):.4f}")
            elif 'OI' in col:
                greeks_table[col] = greeks_table[col].apply(lambda x: f"{int(x):,}")

        st.dataframe(greeks_table.style.highlight_max(subset=['CE OI', 'PE OI'], color='rgba(0, 128, 0, 0.2)'), use_container_width=True)

        st.markdown("#### 🧠 Institutional Intent Synthesis")
        if spot > (put_wall_strike + call_wall_strike) / 2:
            st.success(f"**Bias: BULLISH to Neutral** | Spot is leaning toward the upper half of the range, supported by PE OI concentration at {put_wall_strike:,.0f}.")
        else:
            st.error(f"**Bias: BEARISH to Neutral** | Spot is leaning toward the lower half of the range, pressured by CE resistance near {call_wall_strike:,.0f}.")

# ============================================================
# TAB 4: MASTER GANN EXECUTION DESK
# ============================================================
with tab4:
    st.header("📐 Master Gann Execution Desk")

    # --- SECTION 1: STATIC GANN & LIVE OI CONFLUENCE ---
    st.subheader("1. Static Gann & Live OI Confluence")
    st.write("Cross-referencing live option premiums against Static Gann Natural Squares to identify institutional order blocks and OI traps.")
    
    GANN_SQUARES = [101, 121, 145, 169, 197, 225, 257, 289, 325, 361, 401]
    
    if df.empty:
        st.warning("Option chain data is empty. Cannot calculate Gann confluences.")
    else:
        atm_idx = (df["Strike"] - spot).abs().idxmin()
        pos = df.index.get_loc(atm_idx)
        confluence_df = df.iloc[max(0, pos-10):min(len(df), pos+11)].copy()
        
        gann_matches = []
        
        for _, row in confluence_df.iterrows():
            strike = int(row["Strike"])
            for side in ["CE", "PE"]:
                ltp = row[f"{side}_LTP"]
                oi = row[f"{side}_OI"]
                oi_chg = row[f"{side}_PrevOI"]
                oi_change_live = oi - oi_chg
                
                if ltp <= 50 or ltp > 420:
                    continue
                    
                closest_sq = min(GANN_SQUARES, key=lambda x: abs(x - ltp))
                diff = abs(ltp - closest_sq)
                
                if diff <= 3:
                    target_sq = GANN_SQUARES[GANN_SQUARES.index(closest_sq) + 1] if closest_sq != 401 else "MAX"
                    sl_sq = GANN_SQUARES[GANN_SQUARES.index(closest_sq) - 1] if closest_sq != 101 else "MIN"
                    
                    gann_matches.append({
                        "Type": side,
                        "Strike": strike,
                        "Live Premium": round(ltp, 2),
                        "Gann Anchor": closest_sq,
                        "Target": target_sq,
                        "Stop Loss": sl_sq,
                        "OI Buildup": f"{oi_change_live:+.0f}",
                        "Total OI": f"{oi:,.0f}"
                    })
        
        if gann_matches:
            st.success("### 🔥 ACTIVE GANN PREMIUM ZONES")
            gann_table = pd.DataFrame(gann_matches)
            st.dataframe(gann_table, use_container_width=True)
            st.info("💡 **Execution Logic:** If the Spot hits your Gann Support/Resistance level, enter the Strike where the 'Live Premium' is resting on a 'Gann Anchor'. Positive OI buildup confirms institutional defense.")
        else:
            st.warning("No option premiums are currently resting directly on a Static Gann Square. Wait for price-time alignment.")

    st.divider()

    # --- SECTION 2: ASTRO-GANN PREMIUM ISOLATION DESK ---
    st.subheader("2. Astro-Gann Premium Isolation Desk")
    st.write("Execute purely on Option Premium behavior within pre-calculated Price-Time Square zones. Spot price is ignored.")

    col_z1, col_z2, col_z3, col_z4 = st.columns(4)
    target_strike = col_z1.number_input("Target Strike", value=int(m['atm_strike']), step=50)
    opt_type = col_z2.selectbox("Option Type", ["CE", "PE"])
    zone_upper = col_z3.number_input("Zone Upper Band", value=142.0)
    zone_lower = col_z4.number_input("Zone Lower Band", value=127.0)

    strike_data = df[df["Strike"] == target_strike]
    
    if not strike_data.empty:
        live_ltp = float(strike_data[f"{opt_type}_LTP"].iloc[0])
        
        st.markdown(f"### Live Tracking: **{target_strike} {opt_type}** | LTP: **₹{live_ltp:.2f}**")
        
        if live_ltp > zone_upper:
            st.info(f"⏳ **WAITING:** Premium is above the zone. Wait for a dip into **{zone_lower} - {zone_upper}** to act as Support.")
        elif zone_lower <= live_ltp <= zone_upper:
            st.success(f"🟢 **ZONE ACTIVE:** Premium has entered the Astro-Gann band. \n* **ACTION:** BUY {opt_type}.\n* **STOP-LOSS:** 3-Min Candle Close below **{zone_lower}**.")
        elif live_ltp < zone_lower:
            st.error(f"🔴 **RESISTANCE FLIP:** Premium broke below support. The zone is now Resistance. \n* **ACTION:** SELL {opt_type} (or buy hedge) on a bounce into **{zone_lower} - {zone_upper}**.\n* **STOP-LOSS:** 3-Min Candle Close above **{zone_upper}**.")
            
        st.caption("⚠️ **Risk Rule:** If the 3-minute candle closes outside the boundary twice today, cease trading this strike.")
    else:
        st.warning("Target Strike not found in current live Option Chain.")

st.divider()
st.caption(f"Watchdog Terminal v3.0 (Gann + Greeks Integration) • Last Scan: {datetime.now():%H:%M:%S} IST")