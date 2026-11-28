"""
gann_astro.py — Full-Fledged Hima Reddy W.D. Gann + P.V.R. Narasimha Rao Vedic Astrology Engine
Companion module for Vipul Options Buyer Terminal (app_v20_0.py).

Implements:
- W.D. Gann: Square of 9 (45/90/180/360), 60-Day Gann Eighths (50% Rule), 5-Min 2-Bar Engine,
  4-Week Breakout, Friday High/Low Rule, Price & Time Overbalancing, 50% Time Retracement.
- Vedic Astrology (Lahiri Sidereal via built-in Moshier ephemeris):
  * Full 5-Limb Panchanga: Tithi (+Lord & Group), Vara, Nakshatra (+Pada/Sector), 27 Sun-Moon Yogas, 11 Karanas (+Bhadra)
  * Compound Relationships (Panchadha Maitri) + Live Hora & Full Intraday Hora Schedule
  * Exact Upagraha Windows (Kaala, Mrityu, Artha Prahara, Yamaghantaka, Gulika/Maandi) + Rahu Kaal
  * Sarvatobhadra Chakra (SBC 9x9, 28 Nakshatras incl. Abhijit) Benefic/Malefic Vedha
  * Latta (Planetary Kick: Forward Sun/Mars/Jup/Sat, Backward Moon/Merc/Ven/Rahu)
  * Tara Bala (9-Star Cycle) & Rasi Gochara Vedha
  * Planetary Dignities, Combustion (Asta), Age Avasthas (Yuva/Mrita), Alertness Avasthas
  * Special Graha Drishti (Mars 4/8, Jup 5/9, Sat 3/10), Rasi Drishti, Argala & Virodhargala on Moon
  * Tajaka Applying/Separating Aspects (Poorna/Vartamana Ithasala vs Eesarpha) with exact Deeptamsa orbs
  * Intraday Turn Windows (Hora Lagna, Ghati Lagna, Moon Pada/Rashi Ingress, Gann Sq9 Time Angles)
  * 10-Day Forward Financial Astrology Roadmap
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

RASHIS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
RASHI_LORDS = [
    "Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury",
    "Venus", "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter",
]
ELEMENTS = {
    0: "Fire", 4: "Fire", 8: "Fire",
    1: "Earth", 5: "Earth", 9: "Earth",
    2: "Air", 6: "Air", 10: "Air",
    3: "Water", 7: "Water", 11: "Water",
}
MODALITY = {
    0: "Movable", 3: "Movable", 6: "Movable", 9: "Movable",
    1: "Fixed",   4: "Fixed",   7: "Fixed",   10: "Fixed",
    2: "Dual",    5: "Dual",    8: "Dual",    11: "Dual",
}

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
SECTOR_MAP = {
    "Sun": "PSU / Energy / Power",
    "Moon": "FMCG / Dairy / Consumer",
    "Mars": "Realty / Defence / Metals",
    "Mercury": "IT / Telecom / Media",
    "Jupiter": "Banking / Financials / NBFC",
    "Venus": "Auto / Luxury / Durables",
    "Saturn": "Infra / Oil & Gas / Mining",
    "Rahu": "Tech / Aviation / Pharma",
    "Ketu": "Chemicals / Niche / Turnarounds",
}

TITHI_NAMES = [
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima",
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Amavasya",
]
TITHI_LORDS = [
    "Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu",
    "Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
    "Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu",
    "Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Rahu",
]

# Table 5: 27 Sun-Moon Yogas
SUN_MOON_YOGAS = [
    ("Vishkambha", "Trap/Block"), ("Preeti", "Smooth"), ("Ayushman", "Smooth"),
    ("Saubhagya", "Bullish"), ("Shobhana", "Bullish"), ("Atiganda", "Danger/Vol"),
    ("Sukarman", "Trend"), ("Dhriti", "Steady"), ("Shoola", "Pain/Reversal"),
    ("Ganda", "Danger/Trap"), ("Vriddhi", "Growth/Trend"), ("Dhruva", "Fixed/Trend"),
    ("Vyaghata", "Blow/Shock"), ("Harshana", "Bullish"), ("Vajra", "Hard/Chop"),
    ("Siddhi", "Clean Trend"), ("Vyatipata", "Great Fall"), ("Variyan", "Smooth"),
    ("Parigha", "Obstacle"), ("Shiva", "Clean"), ("Siddha", "Clean Trend"),
    ("Sadhya", "Steady"), ("Shubha", "Bullish"), ("Shukla", "Bullish"),
    ("Brahma", "Clean"), ("Indra", "Trend"), ("Vaidhriti", "Trap/Fall"),
]
BAD_SM_YOGAS = {"Vishkambha", "Atiganda", "Shoola", "Ganda", "Vyaghata", "Vajra", "Vyatipata", "Parigha", "Vaidhriti"}

MOVABLE_KARANAS = ["Bava", "Balava", "Kaulava", "Taitila", "Garija", "Vanija", "Vishti (Bhadra)"]

DAY_LORDS = ["Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Sun"]  # Mon=0 .. Sun=6
CHALDEAN = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"]
WEEKDAY_ORDER = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]

# Table 7: Natural Relationships
FRIENDS = {
    "Sun": ["Moon", "Mars", "Jupiter"], "Moon": ["Sun", "Mercury"],
    "Mars": ["Sun", "Moon", "Jupiter"], "Mercury": ["Sun", "Venus"],
    "Jupiter": ["Sun", "Moon", "Mars"], "Venus": ["Mercury", "Saturn"],
    "Saturn": ["Mercury", "Venus"], "Rahu": ["Venus", "Saturn"], "Ketu": ["Mars", "Jupiter"],
}
ENEMIES = {
    "Sun": ["Venus", "Saturn"], "Moon": [], "Mars": ["Mercury"],
    "Mercury": ["Moon"], "Jupiter": ["Mercury", "Venus"],
    "Venus": ["Sun", "Moon"], "Saturn": ["Sun", "Moon", "Mars"],
    "Rahu": ["Sun", "Moon", "Mars"], "Ketu": ["Sun", "Moon"],
}

# Table 6: Dignities (rashi indices 0..11)
DIGNITIES = {
    "Sun":     {"own": [4],     "exalt": 0,  "debil": 6,  "mt": 4},
    "Moon":    {"own": [3],     "exalt": 1,  "debil": 7,  "mt": 1},
    "Mars":    {"own": [0, 7],  "exalt": 9,  "debil": 3,  "mt": 0},
    "Mercury": {"own": [2, 5],  "exalt": 5,  "debil": 11, "mt": 5},
    "Jupiter": {"own": [8, 11], "exalt": 3,  "debil": 9,  "mt": 8},
    "Venus":   {"own": [1, 6],  "exalt": 11, "debil": 5,  "mt": 6},
    "Saturn":  {"own": [9, 10], "exalt": 6,  "debil": 0,  "mt": 10},
    "Rahu":    {"own": [10],    "exalt": 2,  "debil": 8,  "mt": 5},
    "Ketu":    {"own": [7],     "exalt": 8,  "debil": 2,  "mt": 11},
}

# Combustion orbs (degrees from Sun)
COMBUST_ORB = {"Moon": 12.0, "Mars": 17.0, "Mercury": 14.0, "Jupiter": 11.0, "Venus": 10.0, "Saturn": 15.0}

# Tajaka Deeptamsa (aspect orbs, Table p.375) & Speed hierarchy (faster -> slower)
TAJAKA_ORB = {"Sun": 15.0, "Moon": 12.0, "Mars": 8.0, "Mercury": 7.0, "Jupiter": 9.0, "Venus": 7.0, "Saturn": 9.0}
TAJAKA_SPEED_RANK = {"Moon": 1, "Mercury": 2, "Venus": 3, "Sun": 4, "Mars": 5, "Jupiter": 6, "Saturn": 7}

# Table 64: Tara Bala
TARA_NAMES = [
    ("Janma", "Mixed"), ("Sampat", "Good/Wealth"), ("Vipat", "Bad/Danger"),
    ("Kshema", "Good/Safe"), ("Pratyak", "Bad/Obstacle"), ("Sadhana", "Good/Trend"),
    ("Naidhana", "Bad/Trap"), ("Mitra", "Good/Friend"), ("Parama Mitra", "Best/Trend"),
]

# Section 26.8: 28-Nakshatra Sarvatobhadra Chakra (9x9 grid border coordinates: row 0..8, col 0..8)
# Verified against Book Example 114 (Punarvasu (5,8) -> West Moola (5,0), NW P.Bhadra (0,3), SW U.Phalguni (8,5))
SBC_28_COORDS = {
    "Krittika": (1, 8), "Rohini": (2, 8), "Mrigashira": (3, 8), "Ardra": (4, 8),
    "Punarvasu": (5, 8), "Pushya": (6, 8), "Ashlesha": (7, 8),
    "Magha": (8, 7), "Purva Phalguni": (8, 6), "Uttara Phalguni": (8, 5), "Hasta": (8, 4),
    "Chitra": (8, 3), "Swati": (8, 2), "Vishakha": (8, 1),
    "Anuradha": (7, 0), "Jyeshtha": (6, 0), "Mula": (5, 0), "Purva Ashadha": (4, 0),
    "Uttara Ashadha": (3, 0), "Abhijit": (2, 0), "Shravana": (1, 0),
    "Dhanishta": (0, 1), "Shatabhisha": (0, 2), "Purva Bhadrapada": (0, 3),
    "Uttara Bhadrapada": (0, 4), "Revati": (0, 5), "Ashwini": (0, 6), "Bharani": (0, 7),
}
SBC_COORD_TO_NAK = {v: k for k, v in SBC_28_COORDS.items()}


# ============================================================
# 1. W.D. GANN SQUARE OF 9, EIGHTHS & HIMA REDDY STRUCTURE
# ============================================================
def gann_sq9_levels(anchor):
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


def gann_sq9_table(gann, spot):
    if not gann:
        return pd.DataFrame()
    levels = [
        ("D-R4 (360°)", gann["r4"], "Extreme Blow-Off Resistance"),
        ("D-R3 (180°)", gann["r3"], "Exhaustion Ceiling — Book Calls"),
        ("D-R2 (90°)",  gann["r2"], "Upside T1 — Trail SL to Breakeven"),
        ("D-R1 (45°)",  gann["r1"], "BUY CALL Breakout Wall"),
        ("PIVOT (0°)",  gann["pivot"], "Daily Anchor / Bias Divider"),
        ("D-S1 (45°)",  gann["s1"], "BUY PUT Breakdown Wall"),
        ("D-S2 (90°)",  gann["s2"], "Downside T1 — Trail SL to Breakeven"),
        ("D-S3 (180°)", gann["s3"], "Exhaustion Floor — Book Puts"),
        ("D-S4 (360°)", gann["s4"], "Extreme Capitulation Support"),
    ]
    rows = []
    for name, price, role in levels:
        dist = round(price - spot, 1)
        status = "◄ AT SPOT" if abs(dist) <= 15 else ("ABOVE" if dist > 0 else "BELOW")
        rows.append({"Level": name, "Price": round(price, 2), "Dist (pts)": f"{dist:+.1f}", "Zone": status, "Execution Role": role})
    return pd.DataFrame(rows)


def gann_eighths(candles, spot):
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
        tag = "★ 50% MASTER BALANCE" if i == 4 else ("60D HIGH" if i == 8 else ("60D LOW" if i == 0 else ("MAJOR FLOOR" if i == 7 else "")))
        rows.append({
            "Eighth": f"{i}/8 ({i * 12.5:.1f}%)",
            "Price": round(lvl, 2),
            "Dist (pts)": f"{lvl - spot:+.1f}",
            "Significance": tag,
        })
    pct = max(0.0, min(100.0, (spot - lo) / rng * 100.0))
    return pd.DataFrame(rows), {"high": hi, "low": lo, "mid50": round(lo + 0.5 * rng, 2), "spot_pct": round(pct, 1)}


def gann_structural_filters(candles, spot, today_iso):
    closed = [c for c in (candles or []) if c.get("date", "") < today_iso]
    if len(closed) < 10:
        return {
            "breakout_4w": "Insufficient daily candles", "b4w_tone": "neutral",
            "friday_rule": "N/A", "fri_tone": "neutral",
            "overbalance": "N/A", "ov_tone": "neutral",
            "time_50": "N/A", "t50_tone": "neutral",
        }
    last20 = closed[-20:]
    hi4w, lo4w = max(c["h"] for c in last20), min(c["l"] for c in last20)
    if spot > hi4w:
        b4w, b4w_tone = f"4W BREAKOUT (> {hi4w:,.1f})", "bull"
    elif spot < lo4w:
        b4w, b4w_tone = f"4W BREAKDOWN (< {lo4w:,.1f})", "bear"
    else:
        b4w, b4w_tone = f"INSIDE 4W ({lo4w:,.0f} – {hi4w:,.0f})", "neutral"

    fri_idx = next(
        (i for i in range(len(closed) - 1, -1, -1)
         if datetime.strptime(closed[i]["date"], "%Y-%m-%d").weekday() == 4),
        -1,
    )
    fri_rule, fri_tone = "N/A", "neutral"
    if fri_idx >= 4:
        wk = closed[fri_idx - 4: fri_idx + 1]
        wk_hi, wk_lo = max(c["h"] for c in wk), min(c["l"] for c in wk)
        fri = closed[fri_idx]
        if fri["l"] <= wk_lo:
            fri_rule, fri_tone = f"FRI ({fri['date']}) MADE WK LOW → Bearish Bias", "bear"
        elif fri["h"] >= wk_hi:
            fri_rule, fri_tone = f"FRI ({fri['date']}) MADE WK HIGH → Bullish Bias", "bull"
        else:
            fri_rule, fri_tone = f"FRI ({fri['date']}) MID-RANGE → Neutral", "neutral"

    swings = []
    for i in range(2, len(closed) - 2):
        b = closed[i]
        if (b["h"] > closed[i - 1]["h"] and b["h"] > closed[i - 2]["h"]
                and b["h"] >= closed[i + 1]["h"] and b["h"] >= closed[i + 2]["h"]):
            swings.append({"type": "H", "idx": i, "price": b["h"], "date": b["date"]})
        if (b["l"] < closed[i - 1]["l"] and b["l"] < closed[i - 2]["l"]
                and b["l"] <= closed[i + 1]["l"] and b["l"] <= closed[i + 2]["l"]):
            swings.append({"type": "L", "idx": i, "price": b["l"], "date": b["date"]})

    overbal, ov_tone = "BALANCED", "neutral"
    time50, t50_tone = "No 50% Time Turn Today", "neutral"
    if len(swings) >= 3:
        last, prev, prior = swings[-1], swings[-2], swings[-3]
        swing_bars = max(1, last["idx"] - prev["idx"])
        since_last = len(closed) - last["idx"]
        half_bars = max(1, round(swing_bars * 0.5))
        rem = half_bars - since_last
        if abs(rem) <= 1:
            time50, t50_tone = f"⚡ 50% TIME TURN ACTIVE ({since_last}/{swing_bars}d from {last['type']} {last['date']})", "warn"
        elif rem <= 0:
            time50, t50_tone = f"⚡ 50% TIME WINDOW PASSED ({since_last}/{swing_bars}d)", "warn"
        else:
            time50 = f"Next 50% Time Turn in {rem} trading day(s)"

        prev_pts, prev_bars = abs(prev["price"] - prior["price"]), abs(prev["idx"] - prior["idx"])
        curr_pts, curr_bars = abs(spot - last["price"]), since_last
        if curr_pts > prev_pts and curr_bars > prev_bars:
            overbal, ov_tone = f"PRICE & TIME OVERBALANCED ({curr_pts:.0f} pts / {curr_bars}d)", "warn"
        elif curr_pts > prev_pts:
            overbal, ov_tone = f"PRICE OVERBALANCED ({curr_pts:.0f} > {prev_pts:.0f} pts)", "warn"
        elif curr_bars > prev_bars:
            overbal, ov_tone = f"TIME OVERBALANCED ({curr_bars}d > {prev_bars}d)", "warn"

    return {
        "breakout_4w": b4w, "b4w_tone": b4w_tone,
        "friday_rule": fri_rule, "fri_tone": fri_tone,
        "overbalance": overbal, "ov_tone": ov_tone,
        "time_50": time50, "t50_tone": t50_tone,
    }


def evaluate_5m_two_bar(intraday_1m, gann):
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
    c1, c2 = b5.iloc[-3], b5.iloc[-2]
    r1, s1 = gann["r1"], gann["s1"]
    out.update({
        "ready": True, "bars": len(b5) - 1,
        "close_above_r1": bool(c1["c"] > r1 and c2["c"] > r1),
        "close_below_s1": bool(c1["c"] < s1 and c2["c"] < s1),
        "reject_res": bool((c1["h"] >= r1 - 10 or c2["h"] >= r1 - 10) and c1["c"] < r1 and c2["c"] < r1 and c2["c"] < c1["c"]),
        "bounce_sup": bool((c1["l"] <= s1 + 10 or c2["l"] <= s1 + 10) and c1["c"] > s1 and c2["c"] > s1 and c2["c"] > c1["c"]),
        "sl_long": round(min(float(c1["l"]), float(c2["l"])), 2),
        "sl_short": round(max(float(c1["h"]), float(c2["h"])), 2),
    })
    return out

def gann_sq9_time_turns(anchor_price, now_ist):
    """Pure W.D. Gann Square of 9 intraday time angles from anchor price (09:15 - 15:30 IST)."""
    if not anchor_price or anchor_price <= 0:
        return pd.DataFrame()
    t_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")
    ang = (math.sqrt(anchor_price) * 180.0 - 225.0) % 360.0
    rows = []
    for mult, lbl in (
        (0.25, "Gann Sq9 Quarter (90° Harmonic)"),
        (0.50, "Gann Sq9 Half (180° Harmonic)"),
        (1.00, "Gann Sq9 Primary Angle"),
    ):
        m_g = int(((ang * mult) % 360.0) / 360.0 * 375)
        if 15 <= m_g <= 360:
            gt = t_open + timedelta(minutes=m_g)
            rows.append({
                "IST Time": fmt(gt),
                "_sort": m_g,
                "Gann Cycle": lbl,
                "Angle": f"{(ang * mult) % 360.0:.0f}° from √{anchor_price:,.0f}",
                "Action": "Watch for 5m 2-Bar reversal or breakout acceleration",
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(sorted(rows, key=lambda x: x["_sort"])).drop(columns=["_sort"], errors="ignore")


# ============================================================
# 2. FULL-FLEDGED VEDIC ASTROLOGY ENGINE (P.V.R. NARASIMHA RAO)
# ============================================================
def _jd_utc(dt_ist):
    u = dt_ist.astimezone(timezone.utc)
    return swe.julday(u.year, u.month, u.day, u.hour + u.minute / 60.0 + u.second / 3600.0)


def _sid_lon(jd, planet_id):
    flags = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    out = swe.calc_ut(jd, planet_id, flags)
    xx = out[0] if (isinstance(out, (tuple, list)) and len(out) > 0 and isinstance(out[0], (tuple, list))) else out
    return float(xx[0]) % 360.0, float(xx[3])


def _approx_mumbai_sun(dt_ist):
    doy = dt_ist.timetuple().tm_yday
    offset_min = 38.0 * math.cos(2 * math.pi * (doy - 15) / 365.0)
    sr = dt_ist.replace(hour=6, minute=37, second=0, microsecond=0) + timedelta(minutes=offset_min)
    ss = dt_ist.replace(hour=18, minute=40, second=0, microsecond=0) - timedelta(minutes=offset_min * 0.8)
    return sr, ss


def _nak28_name(lon):
    """28-Nakshatra name including Abhijit (last quarter of U.Ashadha: 276°40' to 280°53'20")."""
    if 276.666667 <= lon < 280.888889:
        return "Abhijit"
    return NAKSHATRAS[int(lon // (360.0 / 27.0))][0]


def _compound_rel(p1, p2, rashi_map):
    """Table 7 + Table 8: Panchadha Maitri (Natural + Temporary 2/3/4/10/11/12)."""
    if p1 == p2:
        return "Adhimitra"
    nat = 1 if p2 in FRIENDS.get(p1, []) else (-1 if p2 in ENEMIES.get(p1, []) else 0)
    r1, r2 = rashi_map.get(p1, 0), rashi_map.get(p2, 0)
    house = ((r2 - r1) % 12) + 1
    temp = 1 if house in (2, 3, 4, 10, 11, 12) else -1
    tot = nat + temp
    if tot >= 2:
        return "Adhimitra"
    if tot == 1:
        return "Mitra"
    if tot == 0:
        return "Sama"
    if tot == -1:
        return "Satru"
    return "Adhisatru"


def _sbc_vedha_targets(nak28):
    """Returns set of 28-Nakshatras hit by SBC Vedha (straight + 2 diagonals) from nak28."""
    if nak28 not in SBC_28_COORDS:
        return set()
    r0, c0 = SBC_28_COORDS[nak28]
    # Determine inward directions based on which border (r0, c0) sits on
    if c0 == 8:    # East border -> West, NW, SW
        dirs = [(0, -1), (-1, -1), (1, -1)]
    elif c0 == 0:  # West border -> East, NE, SE
        dirs = [(0, 1), (-1, 1), (1, 1)]
    elif r0 == 0:  # North border -> South, SW, SE
        dirs = [(1, 0), (1, -1), (1, 1)]
    else:          # South border -> North, NW, NE
        dirs = [(-1, 0), (-1, -1), (-1, 1)]

    hit = set()
    for dr, dc in dirs:
        r, c = r0 + dr, c0 + dc
        while 0 <= r <= 8 and 0 <= c <= 8:
            if (r, c) in SBC_COORD_TO_NAK:
                hit.add(SBC_COORD_TO_NAK[(r, c)])
            r += dr
            c += dc
    return hit


def _latta_checks(lons, target_nak_idx):
    """Section 26.7: Forward & Backward Planetary Kicks (Latta) on target 27-Nakshatra index."""
    # (planet, offset_1based, direction +1 fwd / -1 bwd)
    rules = [
        ("Sun", 12, 1), ("Mars", 3, 1), ("Jupiter", 6, 1), ("Saturn", 8, 1),
        ("Moon", 22, -1), ("Mercury", 7, -1), ("Venus", 5, -1), ("Rahu", 9, -1),
    ]
    hits = []
    for p, off, d in rules:
        if p not in lons:
            continue
        p_nak = int(lons[p] // (360.0 / 27.0))
        kicked = (p_nak + d * (off - 1)) % 27
        if kicked == target_nak_idx:
            hits.append(f"{p} ({'Fwd' if d > 0 else 'Bwd'} {off}th)")
    return hits


def _tajaka_yogas(lons, spds):
    """Chapters 28-29: Tajaka Applying (Ithasala) vs Separating (Eesarpha) aspects with exact Deeptamsa orbs."""
    planets = ["Moon", "Mercury", "Venus", "Sun", "Mars", "Jupiter", "Saturn"]
    rows = []
    aspect_types = {
        0: ("Conjunction", "Malefic/Event"),
        6: ("Opposition (7th)", "Malefic/Reversal"),
        3: ("Square (4th)", "Malefic/Friction"),
        9: ("Square (10th)", "Malefic/Friction"),
        4: ("Trine (5th)", "Benefic/Trend"),
        8: ("Trine (9th)", "Benefic/Trend"),
        2: ("Sextile (3rd)", "Benefic/Support"),
        10: ("Sextile (11th)", "Benefic/Support"),
    }
    for i in range(len(planets)):
        for j in range(i + 1, len(planets)):
            p_fast, p_slow = planets[i], planets[j]
            r_f, r_s = int(lons[p_fast] // 30), int(lons[p_slow] // 30)
            sign_diff = (r_s - r_f) % 12
            if sign_diff not in aspect_types:
                continue
            deg_f, deg_s = lons[p_fast] % 30.0, lons[p_slow] % 30.0
            orb = abs(deg_s - deg_f)
            max_orb = min(TAJAKA_ORB[p_fast], TAJAKA_ORB[p_slow])
            if orb > max_orb:
                continue
            f_retro = spds[p_fast] < 0
            # Applying (Ithasala) if faster direct is behind slower, OR faster retro is ahead of slower
            applying = (deg_f < deg_s and not f_retro) or (deg_f > deg_s and f_retro)
            if applying:
                ytype = "⚡ POORNA ITHASALA (<1°)" if orb <= 1.0 else "🟢 VARTAMANA ITHASALA (Applying)"
            else:
                ytype = "⚪ EESARPHA (Separating/Spent)"
            asp_name, nature = aspect_types[sign_diff]
            rows.append({
                "Pair": f"{p_fast} – {p_slow}",
                "Aspect": asp_name,
                "Orb": f"{orb:.2f}°",
                "Tajaka Yoga": ytype,
                "Market Nature": nature,
            })
    return pd.DataFrame(rows)


def _argala_on_moon(lons, rashi_map):
    """Chapter 10: Primary Argalas (2/4/11) & Virodhargalas (12/10/3) on Moon's Rashi."""
    m_r = rashi_map["Moon"]
    is_ketu_moon = (rashi_map.get("Ketu") == m_r)
    step = -1 if is_ketu_moon else 1

    def planets_in_house(h):
        target_r = (m_r + step * (h - 1)) % 12
        return [p for p, r in rashi_map.items() if p != "Moon" and r == target_r]

    pairs = [(2, 12, "Sustenance"), (4, 10, "Mood/Flow"), (11, 3, "Gains/Catalyst"), (5, 9, "Secondary")]
    notes = []
    for a_h, v_h, lbl in pairs:
        ap = planets_in_house(a_h)
        vp = planets_in_house(v_h)
        if not ap:
            continue
        if len(ap) > len(vp):
            notes.append(f"{lbl} ({a_h}th: {','.join(ap)} unobstructed)")
        elif len(vp) > len(ap):
            notes.append(f"{lbl} ({a_h}th blocked by {v_h}th {','.join(vp)})")
        else:
            notes.append(f"{lbl} ({a_h}th {','.join(ap)} vs {v_h}th {','.join(vp)} balanced)")
    return "  |  ".join(notes) if notes else "No active Argala on Moon"


def compute_vedic_state(now_ist, anchor_price=None):
    """Full-Fledged Live IST Vedic State from P.V.R. Narasimha Rao's Textbook."""
    sr, ss = _approx_mumbai_sun(now_ist)
    day_idx = now_ist.weekday()  # Mon=0 .. Sun=6
    day_lord = DAY_LORDS[day_idx]
    sun0 = (day_idx + 1) % 7  # Sun=0 .. Sat=6
    seg = (ss - sr) / 8
    win = lambda s: (sr + (s - 1) * seg, sr + s * seg)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")
    in_w = lambda w: w[0] <= now_ist <= w[1]

    # Table 10: Daytime 8-part Upagraha segments (1-based)
    # 1st part = weekday lord, then weekday order; part after Saturn is lord-less
    day_seq = []
    cur_w = sun0
    for _ in range(8):
        if len(day_seq) > 0 and day_seq[-1] == "Saturn":
            day_seq.append("None")
        else:
            day_seq.append(WEEKDAY_ORDER[cur_w % 7])
            cur_w += 1
    upagraha_wins = {}
    up_map = {"Sun": "Kaala", "Mars": "Mrityu", "Mercury": "Artha Prahara", "Jupiter": "Yamaghantaka", "Saturn": "Gulika/Maandi"}
    for idx_s, ruler in enumerate(day_seq, 1):
        if ruler in up_map:
            upagraha_wins[up_map[ruler]] = win(idx_s)

    r_seg = [8, 2, 7, 5, 6, 4, 3][sun0]
    rahu_w = win(r_seg)
    yama_w = upagraha_wins.get("Yamaghantaka", win([5, 4, 3, 2, 1, 7, 6][sun0]))
    gul_w = upagraha_wins.get("Gulika/Maandi", win([7, 6, 5, 4, 3, 2, 1][sun0]))
    kaala_w = upagraha_wins.get("Kaala")
    mrityu_w = upagraha_wins.get("Mrityu")

    if in_w(rahu_w):
        kaal_short, kaal_detail, is_kaal_trap = "RAHU KAAL", f"{fmt(rahu_w[0])}–{fmt(rahu_w[1])} (No Chase)", True
    elif in_w(yama_w):
        kaal_short, kaal_detail, is_kaal_trap = "YAMAGANDA", f"{fmt(yama_w[0])}–{fmt(yama_w[1])} (Whipsaw)", True
    elif mrityu_w and in_w(mrityu_w):
        kaal_short, kaal_detail, is_kaal_trap = "MRITYU (MARS)", f"{fmt(mrityu_w[0])}–{fmt(mrityu_w[1])} (Spike/Trap)", True
    elif kaala_w and in_w(kaala_w):
        kaal_short, kaal_detail, is_kaal_trap = "KAALA (SUN)", f"{fmt(kaala_w[0])}–{fmt(kaala_w[1])} (Pressure)", False
    elif in_w(gul_w):
        kaal_short, kaal_detail, is_kaal_trap = "GULIKA/MAANDI", f"{fmt(gul_w[0])}–{fmt(gul_w[1])} (Caution)", False
    else:
        kaal_short, kaal_detail, is_kaal_trap = "CLEAR", "No Kaal / Upagraha Trap", False

    base = {
        "ok": SWE_OK,
        "kaal_short": kaal_short,
        "kaal_detail": kaal_detail,
        "kaal_alert": f"{kaal_short} ({kaal_detail})",
        "is_kaal_trap": is_kaal_trap,
        "kaal_summary": (
            f"Rahu {fmt(rahu_w[0])}–{fmt(rahu_w[1])}  |  Yama {fmt(yama_w[0])}–{fmt(yama_w[1])}  |  "
            f"Gulika {fmt(gul_w[0])}–{fmt(gul_w[1])}"
            + (f"  |  Mrityu {fmt(mrityu_w[0])}–{fmt(mrityu_w[1])}" if mrityu_w else "")
        ),
    }
    if not SWE_OK:
        base["error"] = "Install pysweph (`pip install pysweph`) for live Lahiri Vedic calculations."
        return base

    jd = _jd_utc(now_ist)
    p_ids = {
        "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
        "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN, "Rahu": swe.MEAN_NODE,
    }
    lons, spds, rashi_map, retros = {}, {}, {}, []
    for name, pid in p_ids.items():
        lon, spd = _sid_lon(jd, pid)
        lons[name], spds[name], rashi_map[name] = lon, spd, int(lon // 30)
        if name not in ("Sun", "Moon", "Rahu") and spd < 0:
            retros.append(f"{name}(R)")
    lons["Ketu"] = (lons["Rahu"] + 180.0) % 360.0
    spds["Ketu"] = spds["Rahu"]
    rashi_map["Ketu"] = int(lons["Ketu"] // 30)

    # ---- FULL 5-LIMB PANCHANGA ----
    diff = (lons["Moon"] - lons["Sun"]) % 360.0
    tithi_num = int(diff // 12.0) + 1
    paksha = "Shukla" if tithi_num <= 15 else "Krishna"
    tithi_lord = TITHI_LORDS[tithi_num - 1]
    tithi_grp = ["Nanda", "Bhadra", "Jaya", "Rikta", "Poorna"][(tithi_num - 1) % 5]
    tithi_label = f"{paksha} {TITHI_NAMES[tithi_num - 1]} ({tithi_num}/30)"

    # All 11 Karanas (60 half-tithis)
    half_tithi = int(diff // 6.0) + 1  # 1..60
    if half_tithi == 1:
        karana_name = "Kimstughna"
    elif half_tithi == 58:
        karana_name = "Shakuni"
    elif half_tithi == 59:
        karana_name = "Chatushpada"
    elif half_tithi == 60:
        karana_name = "Naga"
    else:
        karana_name = MOVABLE_KARANAS[(half_tithi - 2) % 7]
    is_bhadra = "Vishti" in karana_name

    # All 27 Sun-Moon Yogas (Table 5)
    sm_sum = (lons["Sun"] + lons["Moon"]) % 360.0
    sm_idx = int(sm_sum // (360.0 / 27.0))
    sm_yoga_name, sm_yoga_note = SUN_MOON_YOGAS[sm_idx]
    is_bad_sm_yoga = sm_yoga_name in BAD_SM_YOGAS

    # Nakshatra & Pada
    nak_idx = int(lons["Moon"] // (360.0 / 27.0))
    nak_name, nak_lord = NAKSHATRAS[nak_idx]
    pada = int((lons["Moon"] % (360.0 / 27.0)) // (360.0 / 108.0)) + 1
    sector_focus = SECTOR_MAP.get(nak_lord, "Broad Market")

    # 4-Element Regime
    el_counts = {"Fire": 0, "Earth": 0, "Air": 0, "Water": 0}
    for p in ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"):
        el_counts[ELEMENTS[rashi_map[p]]] += 1
    trend_score = el_counts["Fire"] + el_counts["Earth"]
    fade_score = el_counts["Air"] + el_counts["Water"]
    regime = "TREND" if trend_score > fade_score else "FADE_WALLS"

    # ---- GRAHA TABLE WITH DIGNITIES, COMBUSTION, AGE & ALERTNESS AVASTHAS ----
    sun_lon = lons["Sun"]
    graha_rows = []
    combust_list = []
    for name in ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"):
        lon = lons[name]
        r_idx = rashi_map[name]
        deg = lon % 30.0
        n_idx = int(lon // (360.0 / 27.0))
        d = DIGNITIES[name]
        r_lord = RASHI_LORDS[r_idx]
        if r_idx == d["exalt"]:
            dig, alert = "Exalted", "Jaagrita (Awake)"
        elif r_idx == d["debil"]:
            dig, alert = "Debilitated", "Sushupta (Asleep)"
        elif r_idx == d["mt"] and deg < 20:
            dig, alert = "Moolatrikona", "Jaagrita (Awake)"
        elif r_idx in d["own"]:
            dig, alert = "Own Sign", "Jaagrita (Awake)"
        else:
            crel = _compound_rel(name, r_lord, rashi_map)
            dig = crel
            alert = "Swapna (Medium)" if crel in ("Adhimitra", "Mitra", "Sama") else "Sushupta (Asleep)"

        # Age Avastha (Table 35: odd vs even sign)
        is_odd = (r_idx % 2 == 0)  # 0=Aries (odd)
        age_deg = deg if is_odd else (30.0 - deg)
        if age_deg < 6:
            age_av = "Saisava (25%)"
        elif age_deg < 12:
            age_av = "Kumaara (50%)"
        elif age_deg < 18:
            age_av = "Yuva (100%)"
        elif age_deg < 24:
            age_av = "Vriddha (Low)"
        else:
            age_av = "Mrita (Dead)"

        # Combustion check
        comb = ""
        if name in COMBUST_ORB:
            ang = abs(lon - sun_lon) % 360.0
            ang = min(ang, 360.0 - ang)
            if ang <= COMBUST_ORB[name]:
                comb = f"🔥 Combust ({ang:.1f}°)"
                combust_list.append(name)

        motion = "Retro (R)" if (name not in ("Sun", "Moon", "Rahu", "Ketu") and spds[name] < 0) else ("Node" if name in ("Rahu", "Ketu") else "Direct")
        graha_rows.append({
            "Graha": name, "Rashi": RASHIS[r_idx], "Deg": f"{deg:.2f}°",
            "Nakshatra": NAKSHATRAS[n_idx][0], "Dignity (Compound)": dig,
            "Age Avastha": age_av, "Alertness": alert, "Motion / Asta": f"{motion} {comb}".strip(),
        })

    # ---- COMPOUND HORA FRIENDSHIP & SCHEDULE ----
    hrs_now = max(0, int((now_ist - sr).total_seconds() // 3600))
    day_start_idx = CHALDEAN.index(day_lord)
    hora_lord = CHALDEAN[(day_start_idx + hrs_now) % 7]

    def _hora_eval(hl):
        rd = _compound_rel(hl, day_lord, rashi_map)
        rn = _compound_rel(hl, nak_lord, rashi_map)
        if rd in ("Adhimitra", "Mitra") and rn not in ("Satru", "Adhisatru"):
            return "FRIEND", f"🟢 Clean Flow ({rd}/{rn})", SECTOR_MAP.get(hl, "")
        if rd in ("Satru", "Adhisatru") or rn in ("Satru", "Adhisatru"):
            return "ENEMY", f"🔴 Whipsaw / Cut Size ({rd}/{rn})", SECTOR_MAP.get(hl, "")
        return "NEUTRAL", f"⚪ Standard 2-Bar ({rd}/{rn})", SECTOR_MAP.get(hl, "")

    h_rel, h_note, _ = _hora_eval(hora_lord)
    hora_verdict = f"{hora_lord.upper()} HORA ({h_rel}) — {h_note}"

    hora_rows = []
    for h in range(2, 11):
        hs = sr + timedelta(hours=h)
        he = hs + timedelta(hours=1)
        hl = CHALDEAN[(day_start_idx + h) % 7]
        rel, note, sec = _hora_eval(hl)
        hora_rows.append({
            "Window (IST)": f"{fmt(hs)} – {fmt(he)}", "Hora Lord": hl, "Compound Rel": rel,
            "Trading Guidance": note, "Sector Focus": sec, "Status": "◄ LIVE" if (hs <= now_ist < he) else "",
        })

    # ---- SARVATOBHADRA CHAKRA VEDHA + LATTA + TARA BALA ----
    moon_nak28 = _nak28_name(lons["Moon"])
    mal_vedha, ben_vedha = [], []
    malefics = {"Sun", "Mars", "Saturn", "Rahu", "Ketu"}
    for p in ("Sun", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"):
        p_n28 = _nak28_name(lons[p])
        if moon_nak28 in _sbc_vedha_targets(p_n28):
            (mal_vedha if p in malefics else ben_vedha).append(f"{p}({p_n28})")

    latta_hits = _latta_checks(lons, nak_idx)

    # Tara Bala relative to NSE Day Open Moon Nakshatra
    t_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    t_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    m_open, _ = _sid_lon(_jd_utc(t_open), swe.MOON)
    m_close, _ = _sid_lon(_jd_utc(t_close), swe.MOON)
    open_nak_idx = int(m_open // (360.0 / 27.0))
    tara_pos = ((nak_idx - open_nak_idx) % 27) % 9
    tara_name, tara_note = TARA_NAMES[tara_pos]

    # ---- SPECIAL LAGNAS (HL, GL) + MOON PADA/RASHI INGRESS + GANN TIME WINDOWS ----
    sun_sr, _ = _sid_lon(_jd_utc(sr), swe.SUN)
    mins_since_sr = max(0.0, (now_ist - sr).total_seconds() / 60.0)
    hl_lon = (sun_sr + mins_since_sr * 0.5) % 360.0
    gl_lon = (sun_sr + mins_since_sr * 1.25) % 360.0

    turn_windows = []
    # 1. Moon Rashi Ingress during NSE hours
    r_open, r_close = int(m_open // 30), int(m_close // 30)
    ingress = "None during NSE hours"
    if r_open != r_close:
        bound = ((r_open + 1) * 30) % 360
        mins = round(((bound - m_open) % 360) / max((m_close - m_open) % 360, 0.01) * 375)
        ing_t = t_open + timedelta(minutes=mins)
        ingress = f"🚨 {fmt(ing_t)} IST — Moon: {RASHIS[r_open]} → {RASHIS[r_close]}"
        turn_windows.append({
            "IST Time": fmt(ing_t), "_sort": mins, "Type": "🚨 MOON RASHI INGRESS",
            "Trigger": f"Moon {RASHIS[r_open]} → {RASHIS[r_close]}",
            "Sector Focus": SECTOR_MAP.get(RASHI_LORDS[r_close], ""),
        })

    # 2. Moon Pada Shifts during NSE hours
    p_open, p_close = int(m_open // (360.0 / 108.0)), int(m_close // (360.0 / 108.0))
    if p_open != p_close:
        bound = ((p_open + 1) * (360.0 / 108.0)) % 360
        mins = round(((bound - m_open) % 360) / max((m_close - m_open) % 360, 0.01) * 375)
        pt = t_open + timedelta(minutes=mins)
        new_nak_lord = NAKSHATRAS[int(((p_open + 1) % 108) // 4)][1]
        turn_windows.append({
            "IST Time": fmt(pt), "_sort": mins, "Type": "🌙 MOON PADA SHIFT",
            "Trigger": f"Navamsa Pada {(p_open % 4)+1} → {((p_open+1) % 4)+1}",
            "Sector Focus": SECTOR_MAP.get(new_nak_lord, ""),
        })

    # 3. Hora Lagna (HL — Money) Ingresses during NSE hours (moves 30° / 60 min)
    m_open_sr = (t_open - sr).total_seconds() / 60.0
    hl_open = (sun_sr + m_open_sr * 0.5) % 360.0
    deg_to_next_hl = 30.0 - (hl_open % 30.0)
    cur_m = deg_to_next_hl / 0.5
    cur_r = (int(hl_open // 30) + 1) % 12
    while cur_m <= 375:
        ht = t_open + timedelta(minutes=round(cur_m))
        turn_windows.append({
            "IST Time": fmt(ht), "_sort": round(cur_m), "Type": "💰 HORA LAGNA INGRESS",
            "Trigger": f"HL enters {RASHIS[cur_r]} (Wealth/Flow Shift)",
            "Sector Focus": SECTOR_MAP.get(RASHI_LORDS[cur_r], ""),
        })
        cur_m += 60.0
        cur_r = (cur_r + 1) % 12

    # Pure Jyotish only: no Gann time angles mixed in

    turns_df = pd.DataFrame(sorted(turn_windows, key=lambda x: x["_sort"])).drop(columns=["_sort"], errors="ignore")

    # ---- MACRO 6/8 & 5/9 YOGAS + SPECIAL GRAHA DRISHTI + TAJAKA + ARGALA ----
    yogas = []
    for p1, p2, lbl68, lbl59 in [
        ("Mars", "Saturn", "Sharp Selloff / High Vol", "Controlled Trend"),
        ("Sun", "Saturn", "Institutional Friction", "Steady Grind"),
        ("Mars", "Rahu", "Violent Whipsaw", "Aggressive Momentum"),
        ("Jupiter", "Venus", "Choppy Rotation", "Smooth Rally"),
    ]:
        dist = ((rashi_map[p2] - rashi_map[p1]) % 12) + 1
        if dist in (6, 8):
            yogas.append(f"🔴 {p1}-{p2} 6/8 Shadashtak ({lbl68})")
        elif dist in (5, 9):
            yogas.append(f"🟢 {p1}-{p2} 5/9 Navpancham ({lbl59})")

    # Special Graha Drishti hitting Moon
    m_r = rashi_map["Moon"]
    moon_aspects = []
    for p, spec in [("Mars", (4, 7, 8)), ("Jupiter", (5, 7, 9)), ("Saturn", (3, 7, 10))]:
        h_from_p = ((m_r - rashi_map[p]) % 12) + 1
        if h_from_p in spec:
            moon_aspects.append(f"{p} {h_from_p}th aspect on Moon")

    tajaka_df = _tajaka_yogas(lons, spds)
    argala_txt = _argala_on_moon(lons, rashi_map)

    base.update({
        "tithi": tithi_label, "tithi_lord": tithi_lord, "tithi_group": tithi_grp,
        "karana": karana_name, "is_bhadra": is_bhadra,
        "sm_yoga": f"{sm_yoga_name} ({sm_yoga_note})", "is_bad_sm_yoga": is_bad_sm_yoga,
        "nakshatra_name": f"{nak_name} Pada {pada}", "nakshatra_lord": nak_lord,
        "nakshatra": f"{nak_name} Pada {pada} ({nak_lord})", "sector_focus": sector_focus,
        "elements": f"F:{el_counts['Fire']} E:{el_counts['Earth']} A:{el_counts['Air']} W:{el_counts['Water']}",
        "trend_score": trend_score, "fade_score": fade_score, "regime": regime,
        "retros": ", ".join(retros) if retros else "None",
        "combust": ", ".join(combust_list) if combust_list else "None",
        "hora_verdict": hora_verdict, "hora_rel": h_rel,
        "hl_rashi": RASHIS[int(hl_lon // 30)], "gl_rashi": RASHIS[int(gl_lon // 30)],
        "sbc_mal_vedha": ", ".join(mal_vedha) if mal_vedha else "None",
        "sbc_ben_vedha": ", ".join(ben_vedha) if ben_vedha else "None",
        "sbc_mal_count": len(mal_vedha),
        "latta": ", ".join(latta_hits) if latta_hits else "Clear",
        "tara_bala": f"{tara_name} ({tara_note})",
        "argala_moon": argala_txt,
        "moon_aspects": "  |  ".join(moon_aspects) if moon_aspects else "None",
        "rashi_ingress": ingress,
        "yogas": "  |  ".join(yogas) if yogas else "⚪ No Major 6/8 or 5/9 Yoga",
        "hora_df": pd.DataFrame(hora_rows),
        "graha_df": pd.DataFrame(graha_rows),
        "tajaka_df": tajaka_df,
        "turns_df": turns_df,
    })
    return base


def compute_10day_roadmap(start_dt_ist):
    """Next 10 NSE trading days forward astrology outlook (evaluated at 09:30 AM IST)."""
    if not SWE_OK:
        return pd.DataFrame()
    rows = []
    cur = start_dt_ist.replace(hour=9, minute=30, second=0, microsecond=0)
    added = 0
    while added < 10:
        if cur.weekday() < 5:
            st_ = compute_vedic_state(cur)
            traps = []
            if st_.get("is_bhadra"):
                traps.append("BHADRA")
            if st_.get("is_bad_sm_yoga"):
                traps.append(st_["sm_yoga"].split(" ")[0])
            if st_.get("sbc_mal_count", 0) >= 2:
                traps.append(f"SBC-Vedha({st_['sbc_mal_count']})")
            if st_.get("latta") != "Clear":
                traps.append("LATTA")
            rows.append({
                "Date": cur.strftime("%Y-%m-%d (%a)"),
                "Nakshatra (Lord)": f"{st_['nakshatra_name']} ({st_['nakshatra_lord']})",
                "Tithi & Karana": f"{st_['tithi'].split(' (')[0]} | {st_['karana']}",
                "Sun-Moon Yoga": st_["sm_yoga"],
                "Regime": f"{'🔥 TREND' if st_['regime'] == 'TREND' else '🛡️ FADE'} ({st_['elements']})",
                "Trap Flags": "⚠️ " + ", ".join(traps) if traps else "✅ Clean",
                "Trading Plan": (
                    "🔴 Defensive (50% Size) — High Whipsaw / Reversal Risk" if len(traps) >= 2
                    else ("🟡 Caution (50% Size) — Expect Trap / Chop" if len(traps) == 1
                          else ("🟢 Clean (100% Size) — Strong Directional Flow" if st_["regime"] == "TREND"
                                else "🟢 Clean (100% Size) — Range-Bound / Mean-Reverting"))
                ),
                "Sector Focus": st_["sector_focus"],
            })
            added += 1
        cur += timedelta(days=1)
    return pd.DataFrame(rows)


# ============================================================
# 1B. ADVANCED PURE W.D. GANN ENGINE (BOOKS #1 - #12)
# ============================================================
GANN_SEASONAL_DATES = [
    (2, 4, "Mid-Season Power Date (Feb 4)"),
    (3, 21, "Spring Equinox Seasonal Turn (Mar 21)"),
    (5, 5, "Mid-Season Power Date (May 5)"),
    (6, 21, "Summer Solstice Seasonal Turn (Jun 21)"),
    (7, 23, "1/3 Year Seasonal Turn (Jul 23)"),
    (8, 5, "Mid-Season Power Date (Aug 5)"),
    (9, 22, "Autumn Equinox Seasonal Turn (Sep 22)"),
    (11, 8, "Mid-Season Power Date (Nov 8)"),
    (11, 22, "2/3 Year Seasonal Turn (Nov 22)"),
    (12, 21, "Winter Solstice Seasonal Turn (Dec 21)"),
]
GANN_MONTHLY_TURN_DAYS = [(6, 7), (9, 10), (14, 15), (19, 20), (23, 24), (29, 31)]
GANN_DAY_CYCLES = [3.5, 7, 14, 21, 23, 30, 45, 49, 63, 72, 81, 90, 108, 120, 144, 180]
GANN_144_DIVISIONS = [36, 45, 48, 54, 63, 72, 81, 90, 96, 108, 126, 144]


def compute_gann_3day_swings(candles):
    """Builds Gann's 3-Day Swing Chart (3 consecutive higher H/L = up, 3 lower L/H = down)."""
    if not candles or len(candles) < 6:
        return {"ready": False, "trend": "NEUTRAL", "last_top": None, "last_bot": None, "swings_df": pd.DataFrame()}

    swings = []
    mode = None  # "UP" or "DOWN"
    streak = 0
    ext_high = candles[0]["h"]
    ext_high_date = candles[0]["date"]
    ext_low = candles[0]["l"]
    ext_low_date = candles[0]["date"]

    for i in range(1, len(candles)):
        b = candles[i]
        prev = candles[i - 1]
        if b["h"] > ext_high:
            ext_high, ext_high_date = b["h"], b["date"]
        if b["l"] < ext_low:
            ext_low, ext_low_date = b["l"], b["date"]

        if b["h"] > prev["h"] and b["l"] > prev["l"]:
            if mode == "UP":
                streak += 1
            else:
                mode, streak = "UP", 1
            if streak >= 3 and (not swings or swings[-1]["Type"] != "TOP"):
                swings.append({"Type": "TOP", "Date": ext_high_date, "Price": round(ext_high, 2), "Idx": i})
                ext_low, ext_low_date = b["l"], b["date"]
        elif b["l"] < prev["l"] and b["h"] < prev["h"]:
            if mode == "DOWN":
                streak += 1
            else:
                mode, streak = "DOWN", 1
            if streak >= 3 and (not swings or swings[-1]["Type"] != "BOTTOM"):
                swings.append({"Type": "BOTTOM", "Date": ext_low_date, "Price": round(ext_low, 2), "Idx": i})
                ext_high, ext_high_date = b["h"], b["date"]
        else:
            streak = 0

    tops = [s for s in swings if s["Type"] == "TOP"]
    bots = [s for s in swings if s["Type"] == "BOTTOM"]
    last_top = tops[-1]["Price"] if tops else None
    last_bot = bots[-1]["Price"] if bots else None

    if len(tops) >= 2 and len(bots) >= 2:
        if tops[-1]["Price"] > tops[-2]["Price"] and bots[-1]["Price"] > bots[-2]["Price"]:
            trend = "UP (Higher Tops & Bottoms)"
        elif tops[-1]["Price"] < tops[-2]["Price"] and bots[-1]["Price"] < bots[-2]["Price"]:
            trend = "DOWN (Lower Tops & Bottoms)"
        else:
            trend = "TRANSITION / MIXED"
    elif len(swings) >= 1:
        trend = "UP" if swings[-1]["Type"] == "TOP" else "DOWN"
    else:
        trend = "NEUTRAL"

    df_sw = pd.DataFrame(swings[-12:]).drop(columns=["Idx"], errors="ignore") if swings else pd.DataFrame()
    return {"ready": bool(swings), "trend": trend, "last_top": last_top, "last_bot": last_bot, "swings_df": df_sw}


def gann_intraday_4m_degrees(now_ist):
    """Gann 4-Minute Intraday Degree Cycle (1° per 4 minutes from 09:15 AM IST open)."""
    t_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")
    rows = []
    for deg, lbl in [
        (15, "15° (1 Hr / 60m)"),
        (30, "30° (2 Hr / 120m)"),
        (45, "45° (3 Hr / 180m — Half Day)"),
        (60, "60° (4 Hr / 240m)"),
        (75, "75° (5 Hr / 300m)"),
        (90, "90° (6 Hr / 360m — Late Turn)"),
    ]:
        mins = deg * 4
        if 0 <= mins <= 375:
            gt = t_open + timedelta(minutes=mins)
            rows.append({
                "IST Time": fmt(gt),
                "4m Cycle": lbl,
                "Elapsed": f"{mins}m ({mins // 60}h {mins % 60}m)",
                "Watch": "Check 5m 2-Bar breakout/reversal & volume",
            })
    return pd.DataFrame(rows)


def gann_time_cycle_radar(candles, now_ist):
    """Computes Gann calendar-day turn counts from recent daily swing highs/lows + seasonal/monthly dates."""
    today = now_ist.date()
    closed = [c for c in (candles or []) if c.get("date", "") < today.isoformat()]
    rows = []

    # Check swing pivots from last 60 closed days
    sub = closed[-60:] if len(closed) >= 5 else closed
    pivots = []
    for i in range(1, len(sub) - 1):
        if sub[i]["h"] >= sub[i - 1]["h"] and sub[i]["h"] >= sub[i + 1]["h"]:
            pivots.append(("SWING HIGH", sub[i]["date"], sub[i]["h"]))
        if sub[i]["l"] <= sub[i - 1]["l"] and sub[i]["l"] <= sub[i + 1]["l"]:
            pivots.append(("SWING LOW", sub[i]["date"], sub[i]["l"]))

    for kind, d_str, px in pivots[-6:]:
        try:
            p_date = datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            continue
        elapsed = (today - p_date).days
        for cyc in GANN_DAY_CYCLES:
            diff = abs(elapsed - cyc)
            if diff <= 2:
                target_d = p_date + timedelta(days=int(round(cyc)))
                rows.append({
                    "Window": f"{target_d.strftime('%d %b')} ({cyc:g}d)",
                    "Anchor": f"{kind} {px:,.0f} ({d_str})",
                    "Status": "⚡ ACTIVE TODAY" if diff == 0 else f"Within {diff}d (Elapsed {elapsed}d)",
                })

    # Upcoming Seasonal & Mid-Season dates within next 25 days
    for offset in range(-1, 25):
        cand_d = today + timedelta(days=offset)
        for m_no, d_no, lbl in GANN_SEASONAL_DATES:
            if cand_d.month == m_no and cand_d.day == d_no:
                rows.append({
                    "Window": cand_d.strftime("%d %b (%a)"),
                    "Anchor": lbl,
                    "Status": "⚡ ACTIVE TODAY" if offset == 0 else (f"In {offset} day(s)" if offset > 0 else "Yesterday"),
                })

    # Monthly natural turn windows today
    for d1, d2 in GANN_MONTHLY_TURN_DAYS:
        if d1 <= today.day <= d2:
            rows.append({
                "Window": f"{today.strftime('%b')} {d1}–{d2}",
                "Anchor": "Monthly Natural Turn Window",
                "Status": "⚡ ACTIVE TODAY",
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame([{
        "Window": today.strftime("%d %b (%a)"),
        "Anchor": "No exact Gann day-count or seasonal turn on today's date",
        "Status": "Follow 1x1 / 5m 2-Bar rules",
    }])


def gann_price_percentage_levels(candles, spot):
    """Gann percentage levels (12.5%, 25%, 33.3%, 50%, 66.7%, 75%, 100%) from 60-day range & swing extremes."""
    if not candles or len(candles) < 5:
        return pd.DataFrame()
    win = candles[-60:]
    hi = max(c["h"] for c in win)
    lo = min(c["l"] for c in win)
    rng = hi - lo
    if rng <= 0:
        return pd.DataFrame()

    levels = [
        ("60D High (100% of Range)", hi),
        ("75% Range Level", lo + 0.75 * rng),
        ("66.7% (2/3) Harmonic", lo + (2.0 / 3.0) * rng),
        ("50% Master Balance Point", lo + 0.50 * rng),
        ("33.3% (1/3) Harmonic", lo + (1.0 / 3.0) * rng),
        ("25% Range Level", lo + 0.25 * rng),
        ("60D Low (0% of Range)", lo),
        ("50% of 60D High", 0.50 * hi),
    ]
    rows = []
    for name, px in levels:
        dist = px - spot
        rows.append({
            "Gann Level": name,
            "Price": round(px, 2),
            "Dist (pts)": f"{dist:+.1f}",
            "Position": "ABOVE SPOT" if dist > 0 else "BELOW SPOT",
        })
    return pd.DataFrame(rows)


def build_pure_gann_signal(spot, gann, tb, swings=None):
    """100% Pure W.D. Gann / Hima Reddy binary command (BUY CE / BUY PE / BOOK / NO TRADE)."""
    if not gann:
        return {"signal": "⛔ NO TRADE", "badge": "NO DATA", "color": "#ffab00",
                "action": "Waiting for previous close anchor."}

    # Narrow-range dead-zone guard when spot is inside tight 45° walls with no 2-bar signal
    in_box = (gann["s1"] <= spot <= gann["r1"])
    no_tb_trig = not any([tb.get("close_above_r1"), tb.get("close_below_s1"), tb.get("reject_res"), tb.get("bounce_sup")])

    # 1. 180° EXHAUSTION — BOOK PROFITS
    if spot <= gann["s3"] + 15:
        return {
            "signal": f"🏁 BOOK PUTS  |  D-S3 ({gann['s3']:,.0f}) HIT",
            "badge": "BOOK PUTS", "color": "#ffab00",
            "action": f"180° floor reached. No fresh PE here. Reversal CE only on 5m 2-bar close > {gann['s3'] + 25:,.0f}.",
        }
    if spot >= gann["r3"] - 15:
        return {
            "signal": f"🏁 BOOK CALLS  |  D-R3 ({gann['r3']:,.0f}) HIT",
            "badge": "BOOK CALLS", "color": "#ffab00",
            "action": f"180° ceiling reached. No fresh CE here. Reversal PE only on 5m 2-bar close < {gann['r3'] - 25:,.0f}.",
        }

    # 2. CONFIRMED 5M 2-BAR BREAKOUT / BREAKDOWN BEYOND 45° WALLS
    if tb["close_below_s1"]:
        t1 = gann["s2"] if spot > gann["s2"] else gann["s3"]
        sl = tb["sl_short"] if tb["sl_short"] else gann["pivot"]
        sw_note = f" | 3D Swing: {swings['trend']}" if (swings and swings.get("ready")) else ""
        return {
            "signal": f"🔴 BUY PE  |  T1: {t1:,.0f}  |  T2: {gann['s3']:,.0f}  |  SL: {sl:,.1f}",
            "badge": "BUY PE", "color": "#ff1744",
            "action": f"Confirmed 5m 2-bar close < D-S1 ({gann['s1']:,.0f}). Trail SL to entry at T1.{sw_note}",
        }

    if tb["close_above_r1"]:
        t1 = gann["r2"] if spot < gann["r2"] else gann["r3"]
        sl = tb["sl_long"] if tb["sl_long"] else gann["pivot"]
        sw_note = f" | 3D Swing: {swings['trend']}" if (swings and swings.get("ready")) else ""
        return {
            "signal": f"🟢 BUY CE  |  T1: {t1:,.0f}  |  T2: {gann['r3']:,.0f}  |  SL: {sl:,.1f}",
            "badge": "BUY CE", "color": "#00e676",
            "action": f"Confirmed 5m 2-bar close > D-R1 ({gann['r1']:,.0f}). Trail SL to entry at T1.{sw_note}",
        }

    # 3. CONFIRMED 2-BAR REJECTION / BOUNCE AT 45° WALLS
    if tb["reject_res"]:
        sl = tb["sl_short"] if tb["sl_short"] else gann["r2"]
        return {
            "signal": f"🔴 BUY PE (WALL REJECTION)  |  T1: {gann['pivot']:,.0f}  |  SL: {sl:,.1f}",
            "badge": "BUY PE", "color": "#ff1744",
            "action": f"2-bar rejection at D-R1 ({gann['r1']:,.0f}). Target Pivot {gann['pivot']:,.0f}.",
        }

    if tb["bounce_sup"]:
        sl = tb["sl_long"] if tb["sl_long"] else gann["s2"]
        return {
            "signal": f"🟢 BUY CE (WALL BOUNCE)  |  T1: {gann['pivot']:,.0f}  |  SL: {sl:,.1f}",
            "badge": "BUY CE", "color": "#00e676",
            "action": f"2-bar bounce at D-S1 ({gann['s1']:,.0f}). Target Pivot {gann['pivot']:,.0f}.",
        }

    # 4. NO CONFIRMED TRIGGER — STRICT NO TRADE
    if spot < gann["s1"]:
        trig = f"BUY PE only on 5m 2-bar close < {gann['s1']:,.0f}."
    elif spot > gann["r1"]:
        trig = f"BUY CE only on 5m 2-bar close > {gann['r1']:,.0f}."
    elif in_box and no_tb_trig:
        trig = f"Gann Dead-Zone / Range Box ({gann['s1']:,.0f}–{gann['r1']:,.0f}): Do not trade inside range; wait for 5m 2-bar breakout."
    elif spot >= gann["pivot"]:
        trig = f"Wait for 5m 2-bar close > {gann['r1']:,.0f} (CE) or rejection at D-R1."
    else:
        trig = f"Wait for 5m 2-bar close < {gann['s1']:,.0f} (PE) or bounce at D-S1."

    return {
        "signal": f"⛔ NO TRADE  |  BOX {gann['s1']:,.0f} – {gann['r1']:,.0f}",
        "badge": "NO TRADE", "color": "#ffab00",
        "action": trig,
    }


# ============================================================
# 4. PURE VEDIC ASTROLOGY VERDICT (100% JYOTISH — ZERO GANN)
# ============================================================
def build_plain_vedic_verdict(v):
    """100% Pure Vedic Astrology verdict — zero Gann terminology."""
    if not v or not v.get("ok"):
        return {
            "headline": "⛔ ASTRO ENGINE OFFLINE",
            "color": "#ffab00",
            "badge": "NO DATA",
            "style": "Install pysweph to enable live Vedic analysis.",
            "traps_txt": "N/A",
            "action": "Check requirements.txt.",
        }

    traps = []
    if v.get("is_bhadra"):
        traps.append("Bhadra Stop-Hunt Trap")
    if v.get("is_kaal_trap"):
        traps.append(f"{v.get('kaal_short')} Window")
    if v.get("is_bad_sm_yoga"):
        traps.append(f"Bad Yoga ({v.get('sm_yoga', '').split(' ')[0]})")
    if v.get("sbc_mal_count", 0) >= 2:
        traps.append(f"SBC Malefic Vedha ({v['sbc_mal_count']})")
    if v.get("latta") and v.get("latta") != "Clear":
        traps.append("Latta Sudden Kick")
    if v.get("hora_rel") == "ENEMY":
        traps.append("Enemy Hora (Whipsaw)")

    is_trend = v.get("regime") == "TREND"
    style = (
        "DIRECTIONAL ENERGY (Fire+Earth) — Favors sustained one-way momentum."
        if is_trend
        else "RANGE-BOUND / VOLATILE (Air+Water) — Favors mean-reversion and sudden swings."
    )

    if len(traps) >= 2:
        headline = f"🔴 HIGH TRAP RISK  |  CUT SIZE TO 50%  |  {'DIRECTIONAL' if is_trend else 'RANGE-BOUND'}"
        color, badge = "#ff1744", "50% SIZE — DEFENSIVE"
    elif len(traps) == 1:
        headline = f"🟡 CAUTION ({traps[0].upper()})  |  TRADE 50% SIZE  |  {'DIRECTIONAL' if is_trend else 'RANGE-BOUND'}"
        color, badge = "#ffab00", "50% SIZE — CAUTION"
    else:
        if is_trend:
            headline = "🟢 CLEAN DIRECTIONAL ENERGY  |  100% SIZE ALLOWED"
            color, badge = "#00e676", "100% SIZE — DIRECTIONAL"
        else:
            headline = "🟢 CLEAN RANGE-BOUND ENERGY  |  100% SIZE ALLOWED"
            color, badge = "#00e676", "100% SIZE — RANGE-BOUND"

    traps_txt = " + ".join(traps) if traps else "None — Clear Sky"
    action = f"ENERGY: {style}  |  ACTIVE TRAPS: {traps_txt}  |  SECTORS: {v.get('sector_focus', 'Broad Market')}"
    return {
        "headline": headline,
        "color": color,
        "badge": badge,
        "style": style,
        "traps_txt": traps_txt,
        "action": action,
    }


def build_full_day_vedic_plan(dt_ist):
    """Builds a complete 09:15 AM - 03:30 PM IST pure-Jyotish intraday analysis & action plan."""
    if not SWE_OK:
        return {"ok": False, "error": "Install pysweph for live Vedic calculations."}

    day_dt = dt_ist.replace(hour=9, minute=30, second=0, microsecond=0)
    base_v = compute_vedic_state(day_dt)
    sr, ss = _approx_mumbai_sun(day_dt)
    day_idx = day_dt.weekday()
    sun0 = (day_idx + 1) % 7
    seg = (ss - sr) / 8
    win = lambda s: (sr + (s - 1) * seg, sr + s * seg)
    fmt = lambda d: d.strftime("%I:%M %p").lstrip("0")

    # Day Kaal / Upagraha windows
    r_seg = [8, 2, 7, 5, 6, 4, 3][sun0]
    rahu_w = win(r_seg)
    day_seq = []
    cur_w = sun0
    for _ in range(8):
        if len(day_seq) > 0 and day_seq[-1] == "Saturn":
            day_seq.append("None")
        else:
            day_seq.append(WEEKDAY_ORDER[cur_w % 7])
            cur_w += 1
    up_wins = {}
    up_map = {"Sun": "Kaala", "Mars": "Mrityu", "Jupiter": "Yamaganda", "Saturn": "Gulika"}
    for idx_s, ruler in enumerate(day_seq, 1):
        if ruler in up_map:
            up_wins[up_map[ruler]] = win(idx_s)
    yama_w = up_wins.get("Yamaganda", win([5, 4, 3, 2, 1, 7, 6][sun0]))
    mrityu_w = up_wins.get("Mrityu")

    t_open = day_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    t_close = day_dt.replace(hour=15, minute=30, second=0, microsecond=0)

    cuts = {t_open, t_close}
    for h in range(2, 10):
        ht = sr + timedelta(hours=h)
        if t_open < ht < t_close:
            cuts.add(ht.replace(second=0, microsecond=0))
    for w in (rahu_w, yama_w, mrityu_w):
        if w:
            for pt in w:
                pt_c = pt.replace(second=0, microsecond=0)
                if t_open < pt_c < t_close:
                    cuts.add(pt_c)

    m_open, _ = _sid_lon(_jd_utc(t_open), swe.MOON)
    m_close, _ = _sid_lon(_jd_utc(t_close), swe.MOON)
    rev_times = []
    r_open, r_close = int(m_open // 30), int(m_close // 30)
    if r_open != r_close:
        bound = ((r_open + 1) * 30) % 360
        mins = round(((bound - m_open) % 360) / max((m_close - m_open) % 360, 0.01) * 375)
        ing_t = t_open + timedelta(minutes=mins)
        rev_times.append(f"{fmt(ing_t)} (Moon {RASHIS[r_open]} → {RASHIS[r_close]})")
        if t_open < ing_t < t_close:
            cuts.add(ing_t)

    p_open, p_close = int(m_open // (360.0 / 108.0)), int(m_close // (360.0 / 108.0))
    if p_open != p_close:
        bound = ((p_open + 1) * (360.0 / 108.0)) % 360
        mins = round(((bound - m_open) % 360) / max((m_close - m_open) % 360, 0.01) * 375)
        pt = t_open + timedelta(minutes=mins)
        rev_times.append(f"{fmt(pt)} (Moon Pada {(p_open % 4)+1} → {((p_open+1) % 4)+1})")
        if t_open < pt < t_close:
            cuts.add(pt)

    # Exact time when Bhadra (Vishti Karana) ends during NSE hours (if currently active)
    if base_v.get("is_bhadra"):
        cur_scan = t_open
        while cur_scan <= t_close:
            st_scan = compute_vedic_state(cur_scan)
            if not st_scan.get("is_bhadra"):
                rev_times.append(f"{fmt(cur_scan)} (Bhadra Karana ends — obstruction lifts)")
                cuts.add(cur_scan)
                break
            cur_scan += timedelta(minutes=15)

    sorted_cuts = sorted(cuts)
    timeline = []
    best_wins, avoid_wins = [], []

    for i in range(len(sorted_cuts) - 1):
        s_t, e_t = sorted_cuts[i], sorted_cuts[i + 1]
        if (e_t - s_t).total_seconds() < 600:
            continue
        mid_t = s_t + (e_t - s_t) / 2
        st_ = compute_vedic_state(mid_t)
        w_label = f"{fmt(s_t)} – {fmt(e_t)}"

        traps = []
        if st_.get("is_kaal_trap"):
            traps.append(st_["kaal_short"])
        if st_.get("is_bhadra"):
            traps.append("Bhadra")
        if st_.get("hora_rel") == "ENEMY":
            traps.append("Enemy Hora")
        if st_.get("latta") != "Clear":
            traps.append("Latta")

        h_lord = st_["hora_verdict"].split(" ")[0].title()
        is_tr = st_["regime"] == "TREND"

        if st_.get("is_kaal_trap") or len(traps) >= 3:
            energy = f"🔴 TRAP / WHIPSAW ({', '.join(traps[:2])})"
            behavior = "Stop-loss hunting, fake breakouts, sudden spikes."
            action = "AVOID NEW ENTRIES — Stay flat or book existing profits."
            avoid_wins.append(f"{w_label} ({', '.join(traps[:2])})")
        elif st_.get("hora_rel") == "ENEMY" or len(traps) == 2:
            energy = f"🟡 CHOPPY / CAUTIOUS ({h_lord} Hora)"
            behavior = "Two-way swings, weak follow-through."
            action = "50% SIZE ONLY — Quick scalps, strict SL."
        elif st_.get("hora_rel") == "FRIEND" and not traps:
            energy = f"🟢 CLEAN FLOW ({h_lord} Friend Hora)"
            behavior = "Sustained directional moves with clean follow-through." if is_tr else "Clean swings between support & resistance."
            action = "BEST WINDOW (100% Size) — Take high-conviction setups."
            best_wins.append(f"{w_label} ({h_lord} Hora)")
        else:
            energy = f"⚪ BALANCED ({h_lord} Neutral Hora)"
            behavior = "Steady directional drift." if is_tr else "Range-bound / mean-reverting moves."
            action = "STANDARD RULES — Wait for clear confirmation."
            if not traps:
                best_wins.append(f"{w_label} ({h_lord} Hora)")

        timeline.append({
            "Time (IST)": w_label,
            "Market Energy": energy,
            "Expected Behavior": behavior,
            "Suggested Action": action,
        })

    is_trend_day = base_v["regime"] == "TREND"
    day_traps = []
    if base_v.get("is_bhadra"):
        day_traps.append("Bhadra")
    if base_v.get("is_bad_sm_yoga"):
        day_traps.append(base_v["sm_yoga"].split(" ")[0])
    if base_v.get("sbc_mal_count", 0) >= 2:
        day_traps.append(f"SBC Vedha ({base_v['sbc_mal_count']})")
    if base_v.get("latta") != "Clear":
        day_traps.append("Latta Kick")

    if len(day_traps) >= 2:
        verdict_title = f"🔴 DEFENSIVE / HIGH-TRAP SESSION — 50% SIZE ONLY ({'DIRECTIONAL' if is_trend_day else 'RANGE-BOUND'})"
        verdict_color = "#ff1744"
        verdict_summary = (
            f"Multiple astro traps active ({', '.join(day_traps)}). Expect stop-hunts and sharp whipsaws. "
            f"Do NOT chase breakouts; trade strictly during the clean windows below at 50% position size."
        )
    elif len(day_traps) == 1:
        verdict_title = f"🟡 CAUTIOUS SESSION ({day_traps[0].upper()}) — 50% SIZE ({'DIRECTIONAL' if is_trend_day else 'RANGE-BOUND'})"
        verdict_color = "#ffab00"
        verdict_summary = (
            f"{day_traps[0]} is active today. Keep position size at 50%, respect avoid windows, and book profits quickly."
        )
    else:
        verdict_title = (
            "🟢 CLEAN DIRECTIONAL SESSION — 100% SIZE ALLOWED"
            if is_trend_day
            else "🟢 CLEAN RANGE-BOUND SESSION — 100% SIZE ALLOWED"
        )
        verdict_color = "#00e676"
        verdict_summary = (
            "No major astro traps active. Clean Fire+Earth directional energy favors riding trend moves."
            if is_trend_day
            else "No major astro traps active. Air+Water energy favors clean mean-reversion and range plays."
        )

    return {
        "ok": True,
        "date_str": day_dt.strftime("%A, %d %b %Y"),
        "verdict_title": verdict_title,
        "verdict_color": verdict_color,
        "verdict_summary": verdict_summary,
        "best_windows": "  |  ".join(best_wins[:3]) if best_wins else "No fully trap-free window — scalp only",
        "avoid_windows": "  |  ".join(avoid_wins[:3]) if avoid_wins else f"Rahu {fmt(rahu_w[0])}–{fmt(rahu_w[1])}",
        "reversal_times": "  |  ".join(rev_times) if rev_times else "No Moon Rashi/Pada shift during NSE hours",
        "sector_focus": base_v["sector_focus"],
        "timeline_df": pd.DataFrame(timeline),
    }
