"""
gann_astro.py — Hima Reddy W.D. Gann + Vedic Financial Astrology Engine
Companion module for Vipul Options Buyer Terminal (app_v20_0.py).
"""
import math
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

try:
    import swisseph as swe
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    SWE_OK = True
except Exception:
    swe, SWE_OK = None, False

IST = timezone(timedelta(hours=5, minutes=30))
MUMBAI_LAT, MUMBAI_LON = 19.0760, 72.8777

RASHIS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
NAKSHATRAS = [
    ("Ashwini", "Ketu"), ("Bharani", "Venus"), ("Krittika", "Sun"),
    ("Rohini", "Moon"), ("Mrigashira", "Mars"), ("Ardra", "Rahu"),
    ("Punarvasu", "Jupiter"), ("Pushya", "Saturn"), ("Ashlesha", "Mercury"),
    ("Magha", "Ketu"), ("Purva Phalguni", "Venus"), ("Uttara Phalguni", "Sun"),
    ("Hasta", "Moon"), ("Chitra", "Mars"), ("Swati", "Rahu"),
    ("Vishakha", "Jupiter"), ("Anuradha", "Saturn"), ("Jyeshtha", "Mercury"),
    ("Mula", "Ketu"), ("Purva Ashadha", "Venus"), ("Uttara Ashadha", "Sun"),
    ("Shravana", "Moon"), ("Dhanishta", "Mars"), ("Shatabhisha", "Rahu"),
    ("Purva Bhadrapada", "Jupiter"), ("Uttara Bhadrapada", "Saturn"), ("Revati", "Mercury"),
]
TITHI_NAMES = [
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima",
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Amavasya",
]
DAY_LORDS = ["Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Sun"]  # Mon=0 .. Sun=6
CHALDEAN = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"]
FRIENDS = {
    "Sun": ["Moon", "Mars", "Jupiter"], "Moon": ["Sun", "Mercury"],
    "Mars": ["Sun", "Moon", "Jupiter"], "Mercury": ["Sun", "Venus"],
    "Jupiter": ["Sun", "Moon", "Mars"], "Venus": ["Mercury", "Saturn"],
    "Saturn": ["Mercury", "Venus"],
}
ENEMIES = {
    "Sun": ["Venus", "Saturn"], "Moon": [], "Mars": ["Mercury"],
    "Mercury": ["Moon"], "Jupiter": ["Mercury", "Venus"],
    "Venus": ["Sun", "Moon"], "Saturn": ["Sun", "Moon", "Mars"],
}
ELEMENTS = {
    0: "Fire", 4: "Fire", 8: "Fire",
    1: "Earth", 5: "Earth", 9: "Earth",
    2: "Air", 6: "Air", 10: "Air",
    3: "Water", 7: "Water", 11: "Water",
}


# ============================================================
# 1. W.D. GANN SQUARE OF 9, EIGHTHS & HIMA REDDY STRUCTURE
# ============================================================
def gann_sq9_levels(anchor):
    """45°, 90°, 180°, 360° Square of 9 resistance & support from anchor close."""
    if not anchor or anchor <= 0:
        return {}
    rt = math.sqrt(anchor)
    return {
        "pivot": round(anchor, 2),
        "r1": round((rt + 0.25) ** 2, 2), "r2": round((rt + 0.50) ** 2, 2),
        "r3": round((rt + 1.00) ** 2, 2), "r4": round((rt + 2.00) ** 2, 2),
        "s1": round((rt - 0.25) ** 2, 2), "s2": round((rt - 0.50) ** 2, 2),
        "s3": round((rt - 1.00) ** 2, 2), "s4": round((rt - 2.00) ** 2, 2),
    }


def gann_eighths(candles, spot):
    """60-day Gann Eighths (0/8 to 8/8) with 50% Master Balance Line."""
    if not candles or len(candles) < 5:
        return pd.DataFrame(), {}
    window = candles[-60:]
    hi = max(c["h"] for c in window)
    lo = min(c["l"] for c in window)
    rng = hi - lo
    if rng <= 0:
        return pd.DataFrame(), {}
    rows = []
    for i in range(8, -1, -1):
        lvl = lo + rng * (i / 8.0)
        tag = "50% MASTER BALANCE" if i == 4 else ("60D HIGH" if i == 8 else ("60D LOW" if i == 0 else ""))
        rows.append({
            "Eighth": f"{i}/8 ({i * 12.5:.1f}%)",
            "Price": round(lvl, 2),
            "Dist pts": round(lvl - spot, 1),
            "Note": tag,
        })
    pct = (spot - lo) / rng * 100.0
    return pd.DataFrame(rows), {"high": hi, "low": lo, "mid50": round(lo + 0.5 * rng, 2), "spot_pct": round(pct, 1)}


def gann_structural_filters(candles, spot, today_iso):
    """4-Week Breakout, Friday High/Low Weekly Rule, Overbalancing (Price & Time), 50% Time Retracement."""
    closed = [c for c in (candles or []) if c.get("date", "") < today_iso]
    if len(closed) < 10:
        return {
            "breakout_4w": "Insufficient daily candles",
            "friday_rule": "N/A",
            "overbalance": "N/A",
            "time_50": "N/A",
        }

    # 1. 4-Week (20-trading-day) Breakout
    last20 = closed[-20:]
    hi4w = max(c["h"] for c in last20)
    lo4w = min(c["l"] for c in last20)
    if spot > hi4w:
        b4w = f"🟢 4W BREAKOUT (> {hi4w:,.1f})"
    elif spot < lo4w:
        b4w = f"🔴 4W BREAKDOWN (< {lo4w:,.1f})"
    else:
        b4w = f"⚪ INSIDE 4W ({lo4w:,.0f} – {hi4w:,.0f})"

    # 2. Friday High/Low Weekly Rule
    fri_idx = next(
        (i for i in range(len(closed) - 1, -1, -1)
         if datetime.strptime(closed[i]["date"], "%Y-%m-%d").weekday() == 4),
        -1,
    )
    fri_rule = "⚪ N/A"
    if fri_idx >= 4:
        wk = closed[fri_idx - 4: fri_idx + 1]
        wk_hi, wk_lo = max(c["h"] for c in wk), min(c["l"] for c in wk)
        fri = closed[fri_idx]
        if fri["l"] <= wk_lo:
            fri_rule = f"🔴 FRI ({fri['date']}) MADE WK LOW → Bearish Bias Next Wk"
        elif fri["h"] >= wk_hi:
            fri_rule = f"🟢 FRI ({fri['date']}) MADE WK HIGH → Bullish Bias Next Wk"
        else:
            fri_rule = f"⚪ FRI ({fri['date']}) MID-RANGE → Neutral"

    # 3. 2-Bar Swing Detection -> Overbalancing & 50% Time Retracement
    swings = []
    for i in range(2, len(closed) - 2):
        b = closed[i]
        if (b["h"] > closed[i - 1]["h"] and b["h"] > closed[i - 2]["h"]
                and b["h"] >= closed[i + 1]["h"] and b["h"] >= closed[i + 2]["h"]):
            swings.append({"type": "H", "idx": i, "price": b["h"], "date": b["date"]})
        if (b["l"] < closed[i - 1]["l"] and b["l"] < closed[i - 2]["l"]
                and b["l"] <= closed[i + 1]["l"] and b["l"] <= closed[i + 2]["l"]):
            swings.append({"type": "L", "idx": i, "price": b["l"], "date": b["date"]})

    overbal, time50 = "⚪ BALANCED", "⚪ No 50% Time Turn Today"
    if len(swings) >= 3:
        last, prev, prior = swings[-1], swings[-2], swings[-3]
        swing_bars = max(1, last["idx"] - prev["idx"])
        since_last = len(closed) - last["idx"]
        half_bars = max(1, round(swing_bars * 0.5))
        if abs(since_last - half_bars) <= 1:
            time50 = f"⚡ 50% TIME TURN ACTIVE ({since_last}/{swing_bars}d from {last['type']} on {last['date']})"
        else:
            time50 = f"Next 50% Time Turn in {max(0, half_bars - since_last)} trading day(s)"

        prev_pts, prev_bars = abs(prev["price"] - prior["price"]), abs(prev["idx"] - prior["idx"])
        curr_pts, curr_bars = abs(spot - last["price"]), since_last
        if curr_pts > prev_pts and curr_bars > prev_bars:
            overbal = f"⚠️ PRICE & TIME OVERBALANCED ({curr_pts:.0f} pts / {curr_bars}d)"
        elif curr_pts > prev_pts:
            overbal = f"⚠️ PRICE OVERBALANCED ({curr_pts:.0f} > {prev_pts:.0f} pts)"
        elif curr_bars > prev_bars:
            overbal = f"⚠️ TIME OVERBALANCED ({curr_bars}d > {prev_bars}d)"

    return {"breakout_4w": b4w, "friday_rule": fri_rule, "overbalance": overbal, "time_50": time50}


def evaluate_5m_two_bar(intraday_1m, gann):
    """Resamples 1-min Dhan candles into closed 5-min bars for Hima Reddy's 2-Bar Close & 2-Bar SL."""
    out = {
        "ready": False, "close_above_r1": False, "close_below_s1": False,
        "reject_res": False, "bounce_sup": False, "sl_long": None, "sl_short": None, "bars": 0,
    }
    if not intraday_1m or len(intraday_1m) < 10 or not gann:
        return out
    df = pd.DataFrame(intraday_1m)
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df = df.set_index("dt").sort_index()
    b5 = df.resample("5min").agg({"h": "max", "l": "min", "c": "last"}).dropna()
    if len(b5) < 3:
        return out
    # Use last 2 COMPLETED 5-min bars (drop currently forming bar)
    c1, c2 = b5.iloc[-3], b5.iloc[-2]
    r1, s1 = gann["r1"], gann["s1"]
    out.update({
        "ready": True,
        "bars": len(b5) - 1,
        "close_above_r1": bool(c1["c"] > r1 and c2["c"] > r1),
        "close_below_s1": bool(c1["c"] < s1 and c2["c"] < s1),
        "reject_res": bool((c1["h"] >= r1 - 10 or c2["h"] >= r1 - 10) and c1["c"] < r1 and c2["c"] < r1 and c2["c"] < c1["c"]),
        "bounce_sup": bool((c1["l"] <= s1 + 10 or c2["l"] <= s1 + 10) and c1["c"] > s1 and c2["c"] > s1 and c2["c"] > c1["c"]),
        "sl_long": round(min(float(c1["l"]), float(c2["l"])), 2),
        "sl_short": round(max(float(c1["h"]), float(c2["h"])), 2),
    })
    return out


# ============================================================
# 2. VEDIC FINANCIAL ASTROLOGY ENGINE (LIVE IST VIA PYSWEPH)
# ============================================================
def _jd_utc(dt_ist):
    u = dt_ist.astimezone(timezone.utc)
    return swe.julday(u.year, u.month, u.day, u.hour + u.minute / 60.0 + u.second / 3600.0)


def _sid_lon(jd, planet_id):
    # FLG_MOSEPH uses the built-in Moshier ephemeris (no external .se1 files needed on Streamlit Cloud)
    flags = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    res, _ = swe.calc_ut(jd, planet_id, flags)
    return float(res[0]) % 360.0, float(res[3])


def _approx_mumbai_sun(dt_ist):
    """Approximate Mumbai sunrise/sunset in IST (accurate within ~5 min year-round)."""
    doy = dt_ist.timetuple().tm_yday
    offset_min = 38.0 * math.cos(2 * math.pi * (doy - 15) / 365.0)
    sr = dt_ist.replace(hour=6, minute=37, second=0, microsecond=0) + timedelta(minutes=offset_min)
    ss = dt_ist.replace(hour=18, minute=40, second=0, microsecond=0) - timedelta(minutes=offset_min * 0.8)
    return sr, ss


def compute_vedic_state(now_ist):
    """Live IST Tithi, Bhadra Karana, Nakshatra, 4-Element Regime, Hora, Kaals, Rashi Ingress & Yogas."""
    sr, ss = _approx_mumbai_sun(now_ist)
    day_idx = now_ist.weekday()  # Mon=0 .. Sun=6
    day_lord = DAY_LORDS[day_idx]

    # Dynamic Rahu / Yamaganda / Gulika Kaal (Sun=0..Sat=6 mapping)
    sun0 = (day_idx + 1) % 7
    seg = (ss - sr) / 8
    r_seg = [8, 2, 7, 5, 6, 4, 3][sun0]
    g_seg = [7, 6, 5, 4, 3, 2, 1][sun0]
    y_seg = [5, 4, 3, 2, 1, 7, 6][sun0]
    win = lambda s: (sr + (s - 1) * seg, sr + s * seg)
    rahu_w, gul_w, yama_w = win(r_seg), win(g_seg), win(y_seg)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")
    in_w = lambda w: w[0] <= now_ist <= w[1]

    if in_w(rahu_w):
        kaal_alert, is_kaal_trap = f"⛔ RAHU KAAL ACTIVE ({fmt(rahu_w[0])}–{fmt(rahu_w[1])})", True
    elif in_w(yama_w):
        kaal_alert, is_kaal_trap = f"⚠️ YAMAGANDA ACTIVE ({fmt(yama_w[0])}–{fmt(yama_w[1])})", True
    elif in_w(gul_w):
        kaal_alert, is_kaal_trap = f"🟡 GULIKA KAAL ({fmt(gul_w[0])}–{fmt(gul_w[1])})", False
    else:
        kaal_alert, is_kaal_trap = "🟢 CLEAR (No Kaal Trap)", False

    base = {
        "ok": SWE_OK,
        "kaal_alert": kaal_alert,
        "is_kaal_trap": is_kaal_trap,
        "kaal_summary": f"Rahu {fmt(rahu_w[0])}–{fmt(rahu_w[1])} | Yama {fmt(yama_w[0])}–{fmt(yama_w[1])} | Gulika {fmt(gul_w[0])}–{fmt(gul_w[1])}",
    }
    if not SWE_OK:
        base["error"] = "Install pysweph (`pip install pysweph`) for live Lahiri Tithi, Bhadra, Hora & Rashi Ingress."
        return base

    jd = _jd_utc(now_ist)
    p_ids = {
        "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
        "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN, "Rahu": swe.MEAN_NODE,
    }
    lons, retros = {}, []
    for name, pid in p_ids.items():
        lon, spd = _sid_lon(jd, pid)
        lons[name] = lon
        if name not in ("Sun", "Moon", "Rahu") and spd < 0:
            retros.append(f"{name}(R)")

    # Live Tithi & Bhadra (Vishti) Karana
    diff = (lons["Moon"] - lons["Sun"]) % 360.0
    tithi_num = int(diff // 12.0) + 1  # 1..30
    paksha = "Shukla" if tithi_num <= 15 else "Krishna"
    tithi_label = f"{paksha} {TITHI_NAMES[tithi_num - 1]} ({tithi_num}/30)"
    half_tithi = int(diff // 6.0) + 1  # 1..60 Karana index
    is_bhadra = half_tithi in (8, 15, 22, 29, 36, 43, 50, 57)

    # Nakshatra & Pada
    nak_idx = int(lons["Moon"] // (360.0 / 27.0))
    nak_name, nak_lord = NAKSHATRAS[nak_idx]
    pada = int((lons["Moon"] % (360.0 / 27.0)) // (360.0 / 108.0)) + 1

    # 4-Element Count (7 visible planets)
    el_counts = {"Fire": 0, "Earth": 0, "Air": 0, "Water": 0}
    for p in ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"):
        el_counts[ELEMENTS[int(lons[p] // 30)]] += 1
    regime = "TREND" if (el_counts["Fire"] + el_counts["Earth"]) > (el_counts["Air"] + el_counts["Water"]) else "FADE_WALLS"

    # Live Mumbai Hora + Friendship
    hrs = max(0, int((now_ist - sr).total_seconds() // 3600))
    hora_lord = CHALDEAN[(CHALDEAN.index(day_lord) + hrs) % 7]
    rel_day = "FRIEND" if day_lord in FRIENDS[hora_lord] else ("ENEMY" if day_lord in ENEMIES[hora_lord] else "NEUTRAL")
    rel_nak = "FRIEND" if nak_lord in FRIENDS[hora_lord] else ("ENEMY" if nak_lord in ENEMIES[hora_lord] else "NEUTRAL")
    if rel_day == "FRIEND" and rel_nak != "ENEMY":
        hora_verdict = f"🟢 {hora_lord.upper()} HORA (FRIEND) — Clean Directional Flow"
    elif rel_day == "ENEMY" or rel_nak == "ENEMY":
        hora_verdict = f"🔴 {hora_lord.upper()} HORA (ENEMY) — Whipsaw / Cut Size"
    else:
        hora_verdict = f"⚪ {hora_lord.upper()} HORA (NEUTRAL) — Standard 2-Bar Rules"

    # Moon Rashi Ingress during NSE hours (09:15 - 15:30 IST)
    t_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    t_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    m_open, _ = _sid_lon(_jd_utc(t_open), swe.MOON)
    m_close, _ = _sid_lon(_jd_utc(t_close), swe.MOON)
    r_open, r_close = int(m_open // 30), int(m_close // 30)
    ingress = "None during NSE hours"
    if r_open != r_close:
        bound = ((r_open + 1) * 30) % 360
        dtot = (m_close - m_open) % 360
        dbound = (bound - m_open) % 360
        mins = round((dbound / max(dtot, 0.01)) * 375)
        ing_t = t_open + timedelta(minutes=mins)
        ingress = f"🚨 {fmt(ing_t)} IST — Moon: {RASHIS[r_open]} → {RASHIS[r_close]}"

    # Shadashtak (6/8) & Navpancham (5/9) Yogas
    yogas = []
    for p1, p2, lbl68, lbl59 in [
        ("Mars", "Saturn", "Sharp Selloff / High Vol", "Controlled Trend"),
        ("Sun", "Saturn", "Institutional Friction", "Steady Grind"),
        ("Mars", "Rahu", "Violent Whipsaw", "Aggressive Momentum"),
        ("Jupiter", "Venus", "Choppy Rotation", "Smooth Rally"),
    ]:
        dist = ((int(lons[p2] // 30) - int(lons[p1] // 30)) % 12) + 1
        if dist in (6, 8):
            yogas.append(f"🔴 {p1}-{p2} 6/8 Shadashtak ({lbl68})")
        elif dist in (5, 9):
            yogas.append(f"🟢 {p1}-{p2} 5/9 Navpancham ({lbl59})")

    base.update({
        "tithi": tithi_label,
        "is_bhadra": is_bhadra,
        "nakshatra": f"{nak_name} Pada {pada} ({nak_lord})",
        "elements": f"F:{el_counts['Fire']} E:{el_counts['Earth']} A:{el_counts['Air']} W:{el_counts['Water']}",
        "regime": regime,
        "retros": ", ".join(retros) if retros else "None",
        "hora_verdict": hora_verdict,
        "rashi_ingress": ingress,
        "yogas": " | ".join(yogas) if yogas else "⚪ No Major 6/8 or 5/9 Yoga",
    })
    return base


# ============================================================
# 3. CRISP BINARY SIGNAL ENGINE (NO DIPLOMATIC HEDGING)
# ============================================================
def build_crisp_gann_astro_signal(spot, gann, tb, vedic):
    if not gann:
        return {"signal": "⛔ NO DATA", "color": "#ffab00", "action": "Waiting for previous close anchor."}

    is_trend = vedic.get("regime", "FADE_WALLS") == "TREND"
    trap_tags = []
    if vedic.get("is_bhadra"):
        trap_tags.append("⚠️ BHADRA ACTIVE")
    if vedic.get("is_kaal_trap"):
        trap_tags.append("⛔ RAHU/YAMA KAAL")
    prefix = (" | ".join(trap_tags) + " — 50% LOTS | ") if trap_tags else ""

    # 1. 180° Gann Exhaustion Check
    if spot <= gann["s3"] + 15:
        return {
            "signal": f"{prefix}🏁 D-S3 (180°) FLOOR HIT ({gann['s3']:,.0f}) — BOOK ALL PUTS",
            "color": "#ffab00",
            "action": f"Puts exhausted at {gann['s3']:,.0f}. Buy CE ONLY on 5m 2-Bar close > {gann['s3'] + 25:,.0f}.",
        }
    if spot >= gann["r3"] - 15:
        return {
            "signal": f"{prefix}🏁 D-R3 (180°) CEILING HIT ({gann['r3']:,.0f}) — BOOK ALL CALLS",
            "color": "#ffab00",
            "action": f"Calls exhausted at {gann['r3']:,.0f}. Buy PE ONLY on 5m 2-Bar close < {gann['r3'] - 25:,.0f}.",
        }

    # 2. Active Breakdown / Breakout Beyond 45° Walls
    if tb["close_below_s1"] or spot < gann["s1"]:
        nxt = gann["s2"] if spot > gann["s2"] else gann["s3"]
        sl_txt = f"{tb['sl_short']:,.1f}" if tb["sl_short"] else f"{gann['pivot']:,.0f}"
        if tb["close_below_s1"]:
            return {
                "signal": f"{prefix}🔴 BUY PUT (PE) — 5m 2-Bar Breakdown < D-S1 ({gann['s1']:,.0f}) | T1: {nxt:,.0f} | 2-Bar SL: {sl_txt}",
                "color": "#ff1744",
                "action": f"PUTS ONLY. T1: {gann['s2']:,.0f} (move SL to breakeven), T2: {gann['s3']:,.0f}. Invalidated > {gann['pivot']:,.0f}.",
            }
        return {
            "signal": f"{prefix}🔴 BEARISH BELOW D-S1 ({gann['s1']:,.0f}) — Awaiting 5m 2-Bar Close | Next Floor: {nxt:,.0f}",
            "color": "#ff1744",
            "action": f"PUTS ONLY on 5m 2-Bar close < {gann['s1']:,.0f}. Target {nxt:,.0f}.",
        }

    if tb["close_above_r1"] or spot > gann["r1"]:
        nxt = gann["r2"] if spot < gann["r2"] else gann["r3"]
        sl_txt = f"{tb['sl_long']:,.1f}" if tb["sl_long"] else f"{gann['pivot']:,.0f}"
        if tb["close_above_r1"]:
            return {
                "signal": f"{prefix}🟢 BUY CALL (CE) — 5m 2-Bar Breakout > D-R1 ({gann['r1']:,.0f}) | T1: {nxt:,.0f} | 2-Bar SL: {sl_txt}",
                "color": "#00e676",
                "action": f"CALLS ONLY. T1: {gann['r2']:,.0f} (move SL to breakeven), T2: {gann['r3']:,.0f}. Invalidated < {gann['pivot']:,.0f}.",
            }
        return {
            "signal": f"{prefix}🟢 BULLISH ABOVE D-R1 ({gann['r1']:,.0f}) — Awaiting 5m 2-Bar Close | Next Ceiling: {nxt:,.0f}",
            "color": "#00e676",
            "action": f"CALLS ONLY on 5m 2-Bar close > {gann['r1']:,.0f}. Target {nxt:,.0f}.",
        }

    # 3. Fade-Walls Reversals (Only in FADE_WALLS Regime)
    if not is_trend and tb["reject_res"]:
        return {
            "signal": f"{prefix}🔴 FADE WALL: BUY PUT (PE) — 2-Bar Rejection at D-R1 ({gann['r1']:,.0f}) | T1: {gann['pivot']:,.0f}",
            "color": "#ff1744",
            "action": f"FADE PLAY back to Pivot {gann['pivot']:,.0f}. Strict 2-Bar High SL ({tb['sl_short']}).",
        }
    if not is_trend and tb["bounce_sup"]:
        return {
            "signal": f"{prefix}🟢 FADE WALL: BUY CALL (CE) — 2-Bar Bounce at D-S1 ({gann['s1']:,.0f}) | T1: {gann['pivot']:,.0f}",
            "color": "#00e676",
            "action": f"FADE PLAY back to Pivot {gann['pivot']:,.0f}. Strict 2-Bar Low SL ({tb['sl_long']}).",
        }

    # 4. Inside 45° Box — True No-Trade Zone
    watch = f"Watch CE > {gann['r1']:,.0f}" if spot >= gann["pivot"] else f"Watch PE < {gann['s1']:,.0f}"
    return {
        "signal": f"{prefix}⛔ NO TRADE — Inside 45° Box ({gann['s1']:,.0f}–{gann['r1']:,.0f}). {watch}.",
        "color": "#ffab00",
        "action": (
            "TREND REGIME: Wait for 5m 2-Bar close outside box."
            if is_trend
            else f"FADE WALLS: Ignore mid-range chop ({spot:,.0f}). Trade ONLY at {gann['s1']:,.0f} or {gann['r1']:,.0f}."
        ),
    }
