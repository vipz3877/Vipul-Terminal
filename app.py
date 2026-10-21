# VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.3 FIXED EDITION
# All prior bugs fixed: session-state init loop, slider guards, inline disclaimer,
# safe_json_parse, and the SyntaxError on queries list.

import streamlit as st
import json
import re
import math
import time
import hashlib
import numpy as np
import pandas as pd
import requests
from datetime import datetime, date, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.parse  # Added for news query encoding
import xml.etree.ElementTree as ET

try:
    from groq import Groq
except ImportError:
    Groq = None

# -------------------------------------------------------
# 1. SESSION STATE INITIALIZATION LOOP (FIX: prevents crashes)
# -------------------------------------------------------
DEFAULT_STATE = {
    "index_name": "NIFTY 50",
    "noise_pct": 0.5,
    "gex_threshold": 0.25,
    "vol_surface_step": 50,
    "dte_filter": 30,
    "confluence_cutoff": 3,
    "ai_temperature": 0.7,
    "telegram_enabled": False,
    "data_update_ttl": 60,
    "regime_mode": "mixed",
    "vega_target": 0.0,
    "gamma_mode": "long",
    "slippage_pct": 0.1,
    "capital_alloc": 100000,
}
for key, default in DEFAULT_STATE.items():
    st.session_state.setdefault(key, default)

# -------------------------------------------------------
# 2. INDIAN INDEX MAPPINGS (exact NSE/Dhan scrip/step)
# -------------------------------------------------------
INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}

# -------------------------------------------------------
# 3. safe_json_parse() - three-tier fallback, NEVER crashes app
# -------------------------------------------------------
def safe_json_parse(output):
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass
    match = re.search(r'```json\s*(\{.*\})```', output, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except: pass
    match = re.search(r'\{[^}]+\}', output)
    if match:
        try:
            return json.loads(match.group())
        except: pass
    return None

# -------------------------------------------------------
# 4. Dhan API Helpers (v2, uses st.secrets)
# -------------------------------------------------------
def dhan_auth_headers():
    """Read Dhan credentials from Streamlit Cloud secrets."""
    try:
        general = st.secrets["general"]
        client_id = str(general.get("DHAN_CLIENT_ID", "")).strip()
        access_token = str(general.get("DHAN_ACCESS_TOKEN", "")).strip()
    except Exception as exc:
        raise RuntimeError(
            "Missing [general] Dhan credentials in Streamlit Secrets. "
            "Add DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
        ) from exc
    if not client_id or not access_token:
        raise RuntimeError(
            "Dhan credentials are blank. Check DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in Streamlit Secrets."
        )
    return {"client-id": client_id, "access-token": access_token}
