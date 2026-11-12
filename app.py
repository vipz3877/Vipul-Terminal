"""
VIPUL OPTIONS BUYER SIGNAL TERMINAL v16.2 (v15.1 + Trade Desk, engine-led signals, session guard, daily risk lock)
v15.1 adds an INSTITUTIONAL FOOTPRINT layer (needs footprint.py next to this file):
 - Index-futures price / volume / open-interest flow + session VWAP decide whether money is really moving your way
 - Footprint filter: VETO AGAINST (default) / REQUIRE ALIGNED / OFF; blocked signals are still logged with footprint fields
 - Target distance capped (default 0.8 daily sigma) so far-away OI walls cannot inflate R:R
 - Previous-day NSE participant OI (FII / DII / Pro / Client) shown as context
Built on v14.0. Changes in v15.0:
 - Market-regime context from recent spot snapshots (advisory only, not a proven filter)
 - Manual trade lifecycle: confirm actual fill before premium stop/target monitoring; record actual exits and net P&L
 - Failed alert deliveries are persisted and retried on subsequent app reruns
 - Nonzero traded-volume requirement for selected option contracts
 - AI Council & News tab now renders headlines and each agent's verdict as readable text (no raw JSON dump)
 - DATA GATE: AI runs, snapshots and signals are paused when the market is closed / holiday / IV is unusable
 - Risk Officer veto (final_approval) and minimum AI confidence are now real gates
 - Agreement score is counted per FAMILY (flow / positioning / volatility) so correlated checks do not stack
 - New price-confirmed writer check (OI change + premium change), not just OI direction
 - Scale-invariant thresholds (% of near-spot OI, sigma units) replace hard-coded contract counts
 - Near-spot window scales with index level (no more fixed 300 points)
 - Daily sigma always uses trading-day basis (252)
 - Intraday 1-min candle high/low used for target/stop touches and MFE/MAE
 - Scorecard multi-session merge now merges MFE/MAE/horizon marks instead of last-writer-wins
 - Kelly sizing uses a Wilson lower bound on win rate, counts TIMEOUT as non-win, MIN_RR raised to 1.5
 - Exhaustion veto is optional (off by default) until it is validated on your own scorecard
 - Headline tone needs >= 4 keyword hits before it shows a number
 - Telegram: validated signals + outcomes by default (rejected optional), correct resolve time, alert state pruned
 - NET GEX shown in Rs crore, cross-index view cached 5 min
 - NSE holiday list via secret NSE_HOLIDAYS (comma separated YYYY-MM-DD); OI velocity normalized to 180-second equivalent; missing close data marked DATA_GAP
BUYER-ONLY: signals are BUY CALL / BUY PUT / WAIT; no writing/selling options and no order execution. Telegram is best-effort while Streamlit is running; this is not a 24/7 background monitor.
"""

import os
import re
import base64
import json
import math
import html
import time
import hashlib
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

try:
    import footprint as FP
    FP_IMPORT_ERR = ""
except Exception as _fp_e:      # app still runs; footprint just shows as unavailable
    FP, FP_IMPORT_ERR = None, str(_fp_e)

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

try:
    from groq import Groq
except ImportError:
    Groq = None

st.set_page_config(page_title="Vipul Options Buyer Terminal v16.2", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# CSS
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
.bbg-desc { font-size:14px; margin-top:6px; color:#ddd; }
.agent { font-size:13px; color:#ddd; margin:4px 0; }
.agent b { color:#ff9800; }
@keyframes flashGreen { 0%,100% {box-shadow:0 0 0 rgba(0,230,118,0);} 50% {box-shadow:0 0 28px rgba(0,230,118,.9);} }
@keyframes flashRed   { 0%,100% {box-shadow:0 0 0 rgba(255,23,68,0);} 50% {box-shadow:0 0 28px rgba(255,23,68,.9);} }
@keyframes flashAmber { 0%,100% {box-shadow:0 0 0 rgba(255,171,0,0);} 50% {box-shadow:0 0 22px rgba(255,171,0,.8);} }
.flash-green { animation: flashGreen 1s infinite; }
.flash-red   { animation: flashRed 1s infinite; }
.flash-amber { animation: flashAmber 1.4s infinite; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# CONFIG & GLOBAL INITIALIZATIONS
