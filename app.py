"""
VIPUL OPTIONS BUYER SIGNAL TERMINAL v16.0 (Clean & Secure)
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
except Exception as _fp_e:
    FP, FP_IMPORT_ERR = None, str(_fp_e)

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

try:
    from groq import Groq
except ImportError:
    Groq = None

st.set_page_config(page_title="Vipul Options Buyer Terminal v16.0", layout="wide", initial_sidebar_state="expanded")

# ============================================================
# CSS & CONFIG
# ============================================================
st.markdown("""
<style>
.stApp { background-color:#000; color:#ff9800; font-family:'Courier New',monospace; }
section[data-testid="stSidebar"] { background-color:#0b0b0b; border-right:1px solid #333; }
h1,h2,h3,h4 { color:#ff9800 !important; font-family:'Courier New',monospace; letter-spacing:1px; }
[data-testid="stMetricValue"] { color:#fff; }
[data-testid="stMetricLabel"] { color:#ff9800; }
.bbg-panel { border:1px solid #333; background:#0f0f0f; padding:14px; border-radius:4px; margin-bottom:12px; }
</style>
""", unsafe_allow_html=True)

def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)

def clean(s):
    s = (s or "").strip().strip('"').strip("'").strip()
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s.replace("\n", "").replace("\r", "").replace(" ", "")

DEFAULT_CLIENT_ID = clean(get_secret("DHAN_CLIENT_ID", ""))
DEFAULT_DHAN_TOKEN = clean(get_secret("DHAN_ACCESS_TOKEN", ""))
DEFAULT_GROQ_KEY = get_secret("GROQ_API_KEY", "").strip()
DEFAULT_TG_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_TG_CHAT = get_secret("TELEGRAM_CHAT_ID", "").strip()
NSE_HOLIDAYS = {x.strip() for x in get_secret("NSE_HOLIDAYS", "").split(",") if x.strip()}
IST = timezone(timedelta(hours=5, minutes=30))

st.info("Vipul Options Buyer Terminal v16.0 loaded successfully. Ensure your secrets are configured in Streamlit Cloud.")
