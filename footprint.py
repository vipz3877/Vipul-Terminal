"""
footprint.py - institutional-footprint layer for the options-buyer terminal (no Streamlit import).

WHY THIS EXISTS
  Option-chain OI is anonymous: a rise in call OI can be a writer's view, a covered-call hedge, or a
  market-maker hedge. It cannot say WHO is on the other side. What institutions do leave behind is in
  FUTURES (price + volume + open interest, plus the session VWAP) and in NSE's end-of-day
  participant-wise OI (FII / DII / Pro / Client). This module reads those and answers one question for
  an option BUYER: "is the money actually moving the way my trade needs?"

  It is a rule-based FILTER, not a prediction. Every threshold is a plain constant below, and the app
  logs the footprint fields on each signal so the scorecard / backtest_footprint.py can test it.

  v16: SENSEX support. Sensex futures trade on BSE (segment BSE_FNO), so the exchange and segment now
  come from EXCHANGES below. BSE index futures are usually much thinner than NSE's, so treat the
  Sensex footprint as weaker evidence than Nifty's.
"""
import io
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
UNDERLYING = {"NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "FINNIFTY": "FINNIFTY", "MIDCPNIFTY": "MIDCPNIFTY",
              "SENSEX": "SENSEX"}
# (exchange id in the scrip master, Dhan exchange segment). Anything not listed is NSE.
EXCHANGES = {"SENSEX": ("BSE", "BSE_FNO")}

# ---- tunable constants (all in daily-sigma units so they scale across indices) -----------------
VWAP_BAND_SIGMA = 0.08      # price must be this far from futures VWAP to count as "above/below"
PX_MOVE_SIGMA_30 = 0.08     # minimum 30-min move to classify a flow quadrant (scaled by sqrt(window/30))
OI_MIN_PCT = 0.05           # minimum futures-OI change (%) over the window to call it build-up / unwind
TREND60_SIGMA = 0.15        # 60-min price move that counts as a trend vote
OR_MINUTES = 15             # opening-range length
MIN_BARS = 20               # minimum 1-min bars before any footprint is produced
BIAS_THRESHOLD = 2.0        # |score| needed for BULLISH / BEARISH
ROLL_DAYS = 2               # inside this many days to expiry, use the next-month future (liquidity rolls)
MODES = ("OFF", "VETO AGAINST", "REQUIRE ALIGNED")


def exchange_for(idx_name):
    """(scrip-master exchange id, Dhan segment) for an index name as used by the app."""
    name = UNDERLYING.get(idx_name, idx_name)
    return EXCHANGES.get(name, ("NSE", "NSE_FNO"))


# =====================================================================================
# DATA: index future lookup + candles with OI (Dhan)
# =====================================================================================
def _headers(token, client_id):
    return {"access-token": token, "client-id": client_id, "Content-Type": "application/json", "Accept": "application/json"}


def pick_index_future(master_df, idx_name, today=None):
    """Choose the liquid index-future contract from the Dhan scrip master (pure function, testable)."""
    name = UNDERLYING.get(idx_name, idx_name)
    exch, segment = exchange_for(idx_name)
    today = today or datetime.now(IST).date()
    d = master_df.copy()
    d.columns = [str(c).strip().upper() for c in d.columns]
    col = lambda *keys: next((c for c in d.columns if all(k in c for k in keys)), None)
    c_exch, c_inst = col("EXCH", "ID"), col("INSTRUMENT", "NAME")
    c_sym, c_exp, c_id = col("TRADING", "SYMBOL"), col("EXPIRY", "DATE"), col("SECURITY", "ID")
    if not all((c_exch, c_inst, c_sym, c_exp, c_id)):
        raise RuntimeError("scrip master columns not recognised")
    d = d[(d[c_exch].astype(str).str.upper() == exch) & (d[c_inst].astype(str).str.upper() == "FUTIDX")]
    d = d[d[c_sym].astype(str).str.upper().str.match(rf"^{re.escape(name)}-.*FUT$")]
    d = d.assign(_exp=pd.to_datetime(d[c_exp], errors="coerce").dt.date).dropna(subset=["_exp"])
    d = d[d["_exp"] >= today].sort_values("_exp")
    if d.empty:
        raise RuntimeError(f"no live {name} index future found in scrip master ({exch})")
    row = d.iloc[0]
    if (row["_exp"] - today).days <= ROLL_DAYS and len(d) > 1:
        row = d.iloc[1]
    return {"security_id": str(int(float(row[c_id]))), "symbol": str(row[c_sym]), "expiry": row["_exp"].isoformat(),
            "segment": segment}


def find_index_future(idx_name, override_id="", timeout=40):
    """Resolve the future's security id. `override_id` (secret FUT_ID_<INDEX>) wins when provided."""
    if override_id:
        return {"security_id": str(override_id).strip(), "symbol": "override", "expiry": "",
                "segment": exchange_for(idx_name)[1]}
    r = requests.get(SCRIP_MASTER_URL, timeout=timeout)
    r.raise_for_status()
    return pick_index_future(pd.read_csv(io.StringIO(r.text), low_memory=False), idx_name)


def _epoch(ts):
    ts = float(ts)
    return ts + 315532800 if ts < 1.4e9 else ts          # same 1980-epoch safety used elsewhere in the app


def parse_candles(d):
    n = len(d.get("close", []))
    if n == 0:
        raise RuntimeError("empty futures candle response")
    oi = d.get("open_interest") or [0] * n
    df = pd.DataFrame({"ts": [_epoch(x) for x in d["timestamp"]], "o": d["open"], "h": d["high"], "l": d["low"],
                       "c": d["close"], "v": d["volume"], "oi": (list(oi) + [0] * n)[:n]}).astype(float)
    return df.sort_values("ts").reset_index(drop=True)


def fetch_future_candles(sec_id, token, client_id, day=None, interval=1, timeout=15, segment="NSE_FNO"):
    day = (day or datetime.now(IST).date()).isoformat()
    body = {"securityId": str(sec_id), "exchangeSegment": segment, "instrument": "FUTIDX", "interval": str(interval),
            "oi": True, "fromDate": f"{day} 09:15:00", "toDate": f"{day} 15:30:00"}
    r = requests.post(f"{BASE_URL}/charts/intraday", headers=_headers(token, client_id), json=body, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"Dhan intraday HTTP {r.status_code}: {r.text[:160]}")
    return parse_candles(r.json())


# =====================================================================================
# CORE: footprint from futures candles
# =====================================================================================
def _quadrant(dp_sig, oi_pct, px_thr):
    if abs(dp_sig) < px_thr or abs(oi_pct) < OI_MIN_PCT:
        return "NO CLEAR FLOW", 0.0
    if dp_sig > 0:
        return ("LONG BUILD-UP", 1.0) if oi_pct > 0 else ("SHORT COVERING", 0.5)
    return ("SHORT BUILD-UP", -1.0) if oi_pct > 0 else ("LONG UNWINDING", -0.5)


def compute_footprint(df, daily_sd, prev_close=None):
    """df: 1-minute futures bars (ts,o,h,l,c,v,oi) for ONE session. Returns a dict; ok=False if unusable."""
    if df is None or len(df) < MIN_BARS or not daily_sd or daily_sd <= 0:
        return {"ok": False, "why": f"need >= {MIN_BARS} one-minute futures bars and a valid sigma", "bias": "UNAVAILABLE"}
    d = df.reset_index(drop=True)
    c, h, l, v, oi = (d[k].to_numpy(float) for k in ("c", "h", "l", "v", "oi"))
    last, sd, n = float(c[-1]), float(daily_sd), len(d)

    vsum = np.cumsum(v)
    typ = (h + l + c) / 3.0
    vwap_arr = np.where(vsum > 0, np.cumsum(typ * v) / np.where(vsum > 0, vsum, 1), np.nan)
    vwap = float(vwap_arr[-1]) if np.isfinite(vwap_arr[-1]) else float(np.nanmean(typ))
    vwap_dist = (last - vwap) / sd
    v_vwap = 1.0 if vwap_dist >= VWAP_BAND_SIGMA else -1.0 if vwap_dist <= -VWAP_BAND_SIGMA else 0.0

    wins = {}
    avg_per_bar = float(np.mean(v)) if n else 0.0
    for w in (15, 30, 60):
        if n > w:
            dp = last - c[-1 - w]
            base_oi = oi[-1 - w]
            oi_pct = (oi[-1] - base_oi) / base_oi * 100.0 if base_oi > 0 else 0.0
            thr = PX_MOVE_SIGMA_30 * math.sqrt(w / 30.0)
            label, vote = _quadrant(dp / sd, oi_pct, thr)
            vol_ratio = float(v[-w:].sum() / (avg_per_bar * w)) if avg_per_bar > 0 else 0.0
            wins[w] = {"dp": dp, "dp_sigma": dp / sd, "oi_pct": oi_pct, "quad": label, "vote": vote, "vol_ratio": vol_ratio}
    q30 = wins.get(30, {"quad": "N/A", "vote": 0.0})
    v_flow = q30["vote"]

    v_trend = 0.0
    if 60 in wins:
        v_trend = 1.0 if wins[60]["dp_sigma"] >= TREND60_SIGMA else -1.0 if wins[60]["dp_sigma"] <= -TREND60_SIGMA else 0.0

    orb, v_orb = "FORMING", 0.0
    if n > OR_MINUTES + 1:
        orh, orl = float(h[:OR_MINUTES].max()), float(l[:OR_MINUTES].min())
        orb = "ABOVE RANGE" if last > orh else "BELOW RANGE" if last < orl else "INSIDE RANGE"
        v_orb = 1.0 if orb == "ABOVE RANGE" else -1.0 if orb == "BELOW RANGE" else 0.0

    hi, lo = float(h.max()), float(l.min())
    flags = []
    if n >= 30 and 15 in wins:
        recent_hi = float(h[-10:].max()) >= hi - 1e-9
        recent_lo = float(l[-10:].min()) <= lo + 1e-9
        weak = wins[15]["oi_pct"] < 0 or wins[15]["vol_ratio"] < 0.8
        if recent_hi and weak:
            flags.append("New day-high on falling OI / thin volume: breakout lacks participation (fragile).")
        if recent_lo and weak:
            flags.append("New day-low on falling OI / thin volume: breakdown lacks participation (fragile).")
    if q30["quad"] == "SHORT COVERING":
        flags.append("Rally is short-covering (OI falling): fuel runs out once shorts are done.")
    if q30["quad"] == "LONG UNWINDING":
        flags.append("Fall is long-unwinding (OI falling): selling pressure fades once longs are out.")
    if prev_close and prev_close > 0:
        gap = (float(d["o"].iloc[0]) - prev_close) / sd
        if abs(gap) >= 0.3:
            flags.append(f"Opened {gap:+.2f} sigma from prev close (gap).")

    score = v_vwap + v_flow + v_trend + v_orb
    bias = "BULLISH" if score >= BIAS_THRESHOLD else "BEARISH" if score <= -BIAS_THRESHOLD else "NEUTRAL"
    return {"ok": True, "bias": bias, "score": round(score, 2), "last": last, "vwap": vwap, "vwap_dist": round(vwap_dist, 3),
            "orb": orb, "quad30": q30["quad"], "windows": wins, "flags": flags,
            "votes": {"VWAP side": v_vwap, "30m flow": v_flow, "60m trend": v_trend, "Opening range": v_orb},
            "day_hi": hi, "day_lo": lo, "bars": n, "asof": int(d["ts"].iloc[-1])}


def footprint_veto(direction, fp, mode):
    """direction: 'BUY' (call) or 'SELL' (put). Returns (blocked, reason). Fail-open except in REQUIRE mode."""
    if mode == "OFF" or direction not in ("BUY", "SELL"):
        return False, ""
    want = "BULLISH" if direction == "BUY" else "BEARISH"
    opp = "BEARISH" if direction == "BUY" else "BULLISH"
    if not fp or not fp.get("ok"):
        if mode == "REQUIRE ALIGNED":
            return True, "futures footprint unavailable (aligned flow is required)"
        return False, ""
    if fp["bias"] == opp:
        return True, (f"trading against the futures footprint ({fp['bias']}: score {fp['score']:+.1f}, "
                      f"{fp['quad30']}, price {'above' if fp['vwap_dist'] > 0 else 'below'} VWAP by {abs(fp['vwap_dist']):.2f} sigma)")
    if mode == "REQUIRE ALIGNED" and fp["bias"] != want:
        return True, f"futures footprint is {fp['bias']} (score {fp['score']:+.1f}); aligned flow is required"
    return False, ""


def footprint_text(fp, pc=None):
    """Compact text block for the AI council prompt."""
    if not fp or not fp.get("ok"):
        return "- Futures footprint: unavailable"
    w = fp["windows"]
    s = (f"- Futures footprint (institutional proxy): {fp['bias']} score {fp['score']:+.1f}; price {fp['vwap_dist']:+.2f} sigma vs session VWAP; "
         f"30m flow {fp['quad30']} (OI {w.get(30, {}).get('oi_pct', 0):+.2f}%); opening range {fp['orb']}")
    if fp["flags"]:
        s += " | caution: " + " ".join(fp["flags"])
    if pc and pc.get("ok"):
        s += f"\n- Previous-day participant OI: {pc['summary']}"
    return s


# =====================================================================================
# NSE participant-wise OI (end of day; context only)
# =====================================================================================
def parse_participant_csv(text):
    """Parse NSE fao_participant_oi_DDMMYYYY.csv text -> {participant: {metric: value}}."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if "client type" in ln.lower()), None)
    if start is None:
        raise ValueError("participant OI header not found")
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    df.columns = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in df.columns]
    ct = next(c for c in df.columns if "client type" in c)
    pick = lambda *k: next((c for c in df.columns if all(x in c for x in k)), None)
    cols = {"fut_long": pick("future", "index", "long"), "fut_short": pick("future", "index", "short"),
            "call_long": pick("option", "index", "call", "long"), "call_short": pick("option", "index", "call", "short"),
            "put_long": pick("option", "index", "put", "long"), "put_short": pick("option", "index", "put", "short")}
    if any(v is None for v in cols.values()):
        raise ValueError("participant OI columns not recognised")
    out = {}
    for _, r in df.iterrows():
        who = str(r[ct]).strip().upper()
        if who in ("FII", "DII", "PRO", "CLIENT"):
            out[who] = {k: float(str(r[c]).replace(",", "")) for k, c in cols.items()}
    if "FII" not in out:
        raise ValueError("FII row missing")
    return out


def summarize_participants(today, prev=None):
    f = today["FII"]
    net = f["fut_long"] - f["fut_short"]
    pct_long = f["fut_long"] / (f["fut_long"] + f["fut_short"]) * 100 if (f["fut_long"] + f["fut_short"]) else float("nan")
    opt_bias = (f["call_long"] - f["call_short"]) - (f["put_long"] - f["put_short"])
    txt = f"FII index futures net {net:+,.0f} contracts ({pct_long:.0f}% long)"
    if prev and "FII" in prev:
        pn = prev["FII"]["fut_long"] - prev["FII"]["fut_short"]
        txt += f", change vs previous day {net - pn:+,.0f}"
    txt += f"; FII index-option net call-minus-put exposure {opt_bias:+,.0f}"
    pro = today.get("PRO")
    if pro:
        txt += f"; Pro futures net {pro['fut_long'] - pro['fut_short']:+,.0f}"
    return {"fii_fut_net": net, "fii_fut_pct_long": pct_long, "fii_opt_bias": opt_bias, "summary": txt}


def fetch_participant_context(today=None, back_days=8, timeout=15):
    """Latest two available daily files (NSE publishes after close). Best-effort; never raises."""
    today = today or datetime.now(IST).date()
    hosts = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
    hdr = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
    got = []
    for k in range(back_days):
        d = today - timedelta(days=k)
        if d.weekday() >= 5:
            continue
        fn = f"fao_participant_oi_{d.strftime('%d%m%Y')}.csv"
        for h in hosts:
            try:
                r = requests.get(f"{h}/content/nsccl/{fn}", headers=hdr, timeout=timeout)
                if r.ok and "client" in r.text.lower():
                    got.append((d, parse_participant_csv(r.text)))
                    break
            except Exception:
                continue
        if len(got) == 2:
            break
    if not got:
        return {"ok": False, "why": "no participant OI file reachable (NSE may block automated requests)"}
    asof, cur = got[0]
    prev = got[1][1] if len(got) > 1 else None
    s = summarize_participants(cur, prev)
    s.update({"ok": True, "asof": asof.isoformat(), "raw": cur})
    return s


# =====================================================================================
if __name__ == "__main__":      # offline self-test on synthetic data
    rng = np.random.default_rng(1)
    n = 120
    base = 25000 + np.cumsum(rng.normal(0.8, 3, n))                       # steady uptrend
    df = pd.DataFrame({"ts": 1.76e9 + np.arange(n) * 60.0, "o": base, "h": base + 2, "l": base - 2, "c": base,
                       "v": rng.integers(800, 1500, n).astype(float), "oi": 1.0e7 + np.cumsum(rng.normal(900, 300, n))})
    fp = compute_footprint(df, 250.0, 25000)
    print("uptrend + rising OI ->", fp["bias"], fp["score"], fp["quad30"], fp["orb"])
    assert fp["bias"] == "BULLISH"
    df2 = df.copy()
    df2["c"] = 2 * df["c"].iloc[0] - df["c"]; df2["h"] = df2["c"] + 2; df2["l"] = df2["c"] - 2; df2["o"] = df2["c"]
    fp2 = compute_footprint(df2, 250.0, 25000)
    print("downtrend + rising OI ->", fp2["bias"], fp2["score"], fp2["quad30"])
    assert fp2["bias"] == "BEARISH"
    df3 = df.copy(); df3["oi"] = 1.0e7 - np.cumsum(rng.normal(900, 300, n))   # same rally, OI falling
    fp3 = compute_footprint(df3, 250.0, 25000)
    print("uptrend + FALLING OI ->", fp3["bias"], fp3["score"], fp3["quad30"], fp3["flags"][:1])
    print("veto BUY vs bearish:", footprint_veto("BUY", fp2, "VETO AGAINST"))
    print("veto SELL vs bullish:", footprint_veto("SELL", fp, "VETO AGAINST"))
    print("neutral REQUIRE:", footprint_veto("BUY", {"ok": True, "bias": "NEUTRAL", "score": 0.5, "quad30": "x", "vwap_dist": 0}, "REQUIRE ALIGNED"))
    print("unavailable VETO (fail-open):", footprint_veto("BUY", None, "VETO AGAINST"))
    master = pd.DataFrame({"SEM_EXM_EXCH_ID": ["NSE"] * 4 + ["BSE"] * 4,
                           "SEM_INSTRUMENT_NAME": ["FUTIDX"] * 8,
                           "SEM_TRADING_SYMBOL": ["NIFTY-Oct2026-FUT", "NIFTY-Nov2026-FUT", "BANKNIFTY-Oct2026-FUT", "NIFTY-Dec2026-FUT",
                                                  "SENSEX-Oct2026-FUT", "SENSEX-Nov2026-FUT", "SENSEX50-Oct2026-FUT", "BANKEX-Oct2026-FUT"],
                           "SEM_EXPIRY_DATE": ["2026-10-27 14:30:00", "2026-11-24 14:30:00", "2026-10-27 14:30:00", "2026-12-29 14:30:00",
                                               "2026-10-29 14:30:00", "2026-11-26 14:30:00", "2026-10-29 14:30:00", "2026-10-29 14:30:00"],
                           "SEM_SMST_SECURITY_ID": [111, 222, 333, 444, 555, 666, 777, 888]})
    print("pick (before expiry roll):", pick_index_future(master, "NIFTY 50", date(2026, 10, 20)))
    print("pick (inside roll window):", pick_index_future(master, "NIFTY 50", date(2026, 10, 26)))
    sx = pick_index_future(master, "SENSEX", date(2026, 10, 20))
    print("pick SENSEX (BSE, current month):", sx)
    assert sx["security_id"] == "555" and sx["segment"] == "BSE_FNO"
    sx2 = pick_index_future(master, "SENSEX", date(2026, 10, 28))
    print("pick SENSEX (inside roll window, next month):", sx2)
    assert sx2["security_id"] == "666"
    assert pick_index_future(master, "NIFTY 50", date(2026, 10, 20))["segment"] == "NSE_FNO"
    print("override segment:", find_index_future("SENSEX", "12345"))
    csv = ("Participant wise Open Interest (no. of contracts) in Futures & Options Segment as on 27-Oct-2026\n"
           "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,"
           "Option Index Put Long,Option Index Call Short,Option Index Put Short,Total Long Contracts,Total Short Contracts\n"
           "Client,200000,100000,1,1,500000,400000,300000,350000,1,1\nDII,50000,60000,1,1,1,1,1,1,1,1\n"
           "FII,80000,150000,1,1,100000,90000,120000,60000,1,1\nPro,30000,20000,1,1,1,1,1,1,1,1\nTOTAL,1,1,1,1,1,1,1,1,1,1\n")
    cur = parse_participant_csv(csv)
    print("participants:", summarize_participants(cur, cur)["summary"])
    print("SELF-TEST OK")
