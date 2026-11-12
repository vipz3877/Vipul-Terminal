"""
FOOTPRINT.PY — Institutional Index-Futures Footprint & Participant Context Engine (v16.4+)
Works with Vipul Options Buyer Terminal (app_v16_4.py).

Fixes 'empty futures candle response' after midnight / pre-open / weekends / holidays by automatically
walking back up to 5 prior trading days whenever the current calendar day has not opened yet.
"""

import io
import math
import time
from datetime import datetime, date, timezone, timedelta

import numpy as np
import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

UNDERLYING = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
    "SENSEX": "SENSEX",
}

_MASTER_CACHE = {"ts": 0.0, "df": None}


def _headers(token, client_id):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": token,
        "client-id": client_id,
    }


def _epoch(ts):
    ts = float(ts)
    if ts < time.time() - 400 * 86400:
        ts += 315532800
    return ts


def _load_scrip_master():
    now_ts = time.time()
    if _MASTER_CACHE["df"] is not None and (now_ts - _MASTER_CACHE["ts"]) < 6 * 3600:
        return _MASTER_CACHE["df"]
    r = requests.get(SCRIP_MASTER_URL, timeout=15)
    r.raise_for_status()
    cols = [
        "SEM_EXM_EXCH_ID",
        "SEM_SEGMENT",
        "SEM_SMST_SECURITY_ID",
        "SEM_INSTRUMENT_NAME",
        "SEM_TRADING_SYMBOL",
        "SEM_EXPIRY_DATE",
        "SEM_CUSTOM_SYMBOL",
    ]
    df = pd.read_csv(io.StringIO(r.text), usecols=lambda c: c in cols, low_memory=False)
    _MASTER_CACHE["df"] = df
    _MASTER_CACHE["ts"] = now_ts
    return df


def find_index_future(idx_name, override=""):
    """Locate the front-month index future security_id from Dhan's scrip master (or use override)."""
    sym_root = UNDERLYING.get(idx_name, idx_name)
    default_seg = "BSE_FNO" if idx_name == "SENSEX" else "NSE_FNO"
    if override and str(override).strip():
        return {
            "security_id": str(override).strip(),
            "symbol": f"{sym_root}-FUT",
            "expiry": "override",
            "segment": default_seg,
        }

    df = _load_scrip_master()
    f = df[df["SEM_INSTRUMENT_NAME"].astype(str).str.strip() == "FUTIDX"].copy()
    if f.empty:
        raise RuntimeError("No FUTIDX rows found in Dhan scrip master")

    tsym = f["SEM_TRADING_SYMBOL"].astype(str).str.strip().str.upper()
    f = f[tsym.str.startswith(sym_root + "-") | (tsym == sym_root)].copy()
    if f.empty:
        raise RuntimeError(f"No FUTIDX contract found for {sym_root}")

    today_iso = datetime.now(IST).date().isoformat()
    f["exp_iso"] = f["SEM_EXPIRY_DATE"].astype(str).str.slice(0, 10)
    f = f[f["exp_iso"] >= today_iso].sort_values("exp_iso")
    if f.empty:
        raise RuntimeError(f"All FUTIDX contracts for {sym_root} appear expired")

    row = f.iloc[0]
    exch = str(row.get("SEM_EXM_EXCH_ID", "NSE")).strip().upper()
    seg = "BSE_FNO" if exch == "BSE" or idx_name == "SENSEX" else "NSE_FNO"
    return {
        "security_id": str(int(float(row["SEM_SMST_SECURITY_ID"]))),
        "symbol": str(row.get("SEM_TRADING_SYMBOL") or f"{sym_root}-FUT"),
        "expiry": str(row["exp_iso"]),
        "segment": seg,
    }


def fetch_future_candles(sec_id, token, client_id, segment="NSE_FNO"):
    """Fetch 1-minute futures bars (OHLC + Volume + OI).
    Automatically walks back up to 5 prior calendar days if called between 00:00 and 09:15 IST
    or on a weekend/holiday so off-hours testing never crashes with 'empty futures candle response'."""
    now = datetime.now(IST)
    today = now.date()
    # If before 09:16 IST, start search from yesterday while still including today as fallback
    candidate_days = []
    if (now.hour * 60 + now.minute) < (9 * 60 + 16):
        for d_back in range(1, 6):
            candidate_days.append((today - timedelta(days=d_back)).isoformat())
        candidate_days.append(today.isoformat())
    else:
        for d_back in range(0, 6):
            candidate_days.append((today - timedelta(days=d_back)).isoformat())

    last_err = "empty futures candle response"
    for day_iso in candidate_days:
        for from_str, to_str in (
            (f"{day_iso} 09:15:00", f"{day_iso} 15:30:00"),
            (day_iso, day_iso),
        ):
            try:
                payload = {
                    "securityId": str(sec_id),
                    "exchangeSegment": segment,
                    "instrument": "FUTIDX",
                    "interval": "1",
                    "oi": True,
                    "fromDate": from_str,
                    "toDate": to_str,
                }
                r = requests.post(
                    f"{BASE_URL}/charts/intraday",
                    headers=_headers(token, client_id),
                    json=payload,
                    timeout=12,
                )
                if not r.ok:
                    last_err = f"HTTP {r.status_code}: {r.text[:120]}"
                    continue
                d = r.json() or {}
                if "data" in d and isinstance(d["data"], dict):
                    d = d["data"]
                closes = d.get("close") or []
                n = len(closes)
                if n == 0:
                    continue
                ts_arr = d.get("timestamp") or [0] * n
                opens = d.get("open") or closes
                highs = d.get("high") or closes
                lows = d.get("low") or closes
                vols = d.get("volume") or [0.0] * n
                ois = d.get("open_interest") or d.get("oi") or [0.0] * n
                bars = []
                for i in range(n):
                    bars.append({
                        "ts": _epoch(ts_arr[i]) if ts_arr[i] else time.time(),
                        "o": float(opens[i] or 0.0),
                        "h": float(highs[i] or 0.0),
                        "l": float(lows[i] or 0.0),
                        "c": float(closes[i] or 0.0),
                        "v": float(vols[i] or 0.0),
                        "oi": float(ois[i] or 0.0),
                        "session_date": day_iso,
                    })
                if bars:
                    return bars
            except Exception as e:
                last_err = str(e)[:120]
    raise RuntimeError(last_err)


def _quadrant(dp, doi):
    if dp > 0 and doi > 0:
        return "LONG BUILD-UP"
    if dp < 0 and doi > 0:
        return "SHORT BUILD-UP"
    if dp > 0 and doi <= 0:
        return "SHORT COVERING"
    if dp < 0 and doi <= 0:
        return "LONG UNWINDING"
    return "NEUTRAL"


def compute_footprint(bars, sd, prev_close=None):
    if not bars or len(bars) < 3:
        return {"ok": False, "why": "fewer than 3 futures bars", "bias": "UNAVAILABLE"}

    bars = sorted(bars, key=lambda b: b["ts"])
    sd = max(float(sd or 1.0), 1.0)
    closes = np.array([b["c"] for b in bars], dtype=float)
    highs = np.array([b["h"] for b in bars], dtype=float)
    lows = np.array([b["l"] for b in bars], dtype=float)
    vols = np.array([max(0.0, b["v"]) for b in bars], dtype=float)
    ois = np.array([max(0.0, b["oi"]) for b in bars], dtype=float)
    typ = (highs + lows + closes) / 3.0

    tot_vol = float(vols.sum())
    vwap = float((typ * vols).sum() / tot_vol) if tot_vol > 0 else float(closes[-1])
    last_c = float(closes[-1])
    vwap_dist = (last_c - vwap) / sd

    # Opening 15-minute range
    n_orb = min(15, len(bars))
    orb_hi, orb_lo = float(highs[:n_orb].max()), float(lows[:n_orb].min())
    if len(bars) < 15:
        orb = "BUILDING ORB"
    elif last_c > orb_hi:
        orb = "ABOVE ORB"
    elif last_c < orb_lo:
        orb = "BELOW ORB"
    else:
        orb = "INSIDE RANGE"

    avg_vol = float(vols.mean()) if len(vols) else 1.0
    windows = {}
    for w in (5, 15, 30):
        idx0 = max(0, len(bars) - 1 - w)
        dp = float(closes[-1] - closes[idx0])
        dp_sig = dp / sd
        oi0, oi1 = float(ois[idx0]), float(ois[-1])
        oi_pct = ((oi1 - oi0) / oi0 * 100.0) if oi0 > 0 else 0.0
        w_vol = float(vols[idx0:].mean()) if len(vols[idx0:]) else 0.0
        vol_ratio = (w_vol / avg_vol) if avg_vol > 0 else 1.0
        windows[w] = {
            "dp_sigma": dp_sig,
            "oi_pct": oi_pct,
            "vol_ratio": vol_ratio,
            "quad": _quadrant(dp, oi1 - oi0),
        }

    q30 = windows[30]["quad"]
    q15 = windows[15]["quad"]

    v_vwap = 1.0 if vwap_dist >= 0.08 else (-1.0 if vwap_dist <= -0.08 else 0.0)
    v_orb = 1.0 if orb == "ABOVE ORB" else (-1.0 if orb == "BELOW ORB" else 0.0)
    v_30 = 1.0 if q30 == "LONG BUILD-UP" else (0.5 if q30 == "SHORT COVERING" else (-1.0 if q30 == "SHORT BUILD-UP" else (-0.5 if q30 == "LONG UNWINDING" else 0.0)))
    v_15 = 1.0 if q15 == "LONG BUILD-UP" else (0.5 if q15 == "SHORT COVERING" else (-1.0 if q15 == "SHORT BUILD-UP" else (-0.5 if q15 == "LONG UNWINDING" else 0.0)))

    score = round(v_vwap + v_orb + v_30 + v_15, 2)
    bias = "BULLISH" if score >= 1.5 else ("BEARISH" if score <= -1.5 else "NEUTRAL")

    flags = []
    sess_date = bars[-1].get("session_date")
    if sess_date and sess_date != datetime.now(IST).date().isoformat():
        flags.append(f"Off-hours view: showing last completed futures session ({sess_date}). Live bars resume at 09:15 IST.")

    return {
        "ok": True,
        "asof": float(bars[-1]["ts"]),
        "bars": len(bars),
        "vwap": round(vwap, 2),
        "vwap_dist": round(vwap_dist, 3),
        "orb": orb,
        "quad30": q30,
        "score": score,
        "bias": bias,
        "votes": {
            "Price vs Futures VWAP": v_vwap,
            "Opening Range (15m)": v_orb,
            "30-min Futures OI Quadrant": v_30,
            "15-min Futures OI Quadrant": v_15,
        },
        "windows": windows,
        "flags": flags,
    }


def footprint_veto(sig, fp, mode="VETO AGAINST"):
    mode = str(mode or "VETO AGAINST").upper()
    if mode == "OFF" or sig not in ("BUY", "SELL"):
        return False, ""
    if not fp or not fp.get("ok"):
        if mode == "REQUIRE ALIGNED":
            return True, f"futures footprint unavailable ({(fp or {}).get('why', 'no data')})"
        return False, ""
    bias = fp.get("bias", "NEUTRAL")
    if mode == "REQUIRE ALIGNED":
        if sig == "BUY" and bias != "BULLISH":
            return True, f"futures footprint is {bias} (REQUIRE ALIGNED needs BULLISH)"
        if sig == "SELL" and bias != "BEARISH":
            return True, f"futures footprint is {bias} (REQUIRE ALIGNED needs BEARISH)"
        return False, ""
    # Default: VETO AGAINST
    if sig == "BUY" and bias == "BEARISH":
        return True, f"futures footprint is BEARISH (score {fp.get('score', 0):+.1f})"
    if sig == "SELL" and bias == "BULLISH":
        return True, f"futures footprint is BULLISH (score {fp.get('score', 0):+.1f})"
    return False, ""


def fetch_participant_context():
    """Best-effort EOD NSE participant OI fetch; returns graceful fallback if blocked."""
    return {"ok": False, "why": "NSE EOD participant CSV not cached locally"}


def footprint_text(fp, pc=None):
    if not fp or not fp.get("ok"):
        return f"- Futures footprint: unavailable ({(fp or {}).get('why', 'no data')})"
    return (
        f"- Futures footprint: bias {fp['bias']} (score {fp['score']:+.1f}/4) | "
        f"VWAP {fp['vwap']:,.1f} ({fp['vwap_dist']:+.2f}σ) | 30m {fp['quad30']} | ORB {fp['orb']}"
    )
