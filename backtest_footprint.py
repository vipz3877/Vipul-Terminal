#!/usr/bin/env python3
"""
backtest_footprint.py - does the futures footprint actually predict where the index goes next?

It replays footprint.compute_footprint (the same code the app uses) minute by minute over past sessions of
an INDEX FUTURE (price + volume + open interest from Dhan) and measures what happened 15 / 30 / 60 minutes
later and into the close, split by BULLISH / BEARISH / NEUTRAL footprint.

Only the footprint is tested here (not the AI, not option-chain OI). Result is in index points: no option
premium, theta, spread or costs. Dhan serves history only for ACTIVE contracts, so you get roughly the life
of the current contract (about 1-3 months) per run; run again next month and keep the CSVs to build a longer record.

  python backtest_footprint.py --demo                          # offline self-test, synthetic data
  python backtest_footprint.py --fut-id <security id> --days 60
Env: DHAN_ACCESS_TOKEN, DHAN_CLIENT_ID.  Find the id in the app's Footprint tab, or in the Dhan scrip master.
"""
import argparse
import math
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

import footprint as FP

IST = FP.IST
HORIZONS = (15, 30, 60)


def fetch_bars(fut_id, days, token, client_id):
    end = datetime.now(IST).date()
    start = end - timedelta(days=days)
    body = {"securityId": str(fut_id), "exchangeSegment": "NSE_FNO", "instrument": "FUTIDX", "interval": "1", "oi": True,
            "fromDate": f"{start.isoformat()} 09:15:00", "toDate": f"{end.isoformat()} 15:30:00"}
    r = requests.post(f"{FP.BASE_URL}/charts/intraday", headers=FP._headers(token, client_id), json=body, timeout=90)
    if not r.ok:
        sys.exit(f"Dhan HTTP {r.status_code}: {r.text[:300]}")
    return FP.parse_candles(r.json())


def demo_bars(days, drift_sd, seed=7):
    rng = np.random.default_rng(seed)
    out, d, px = [], date(2026, 3, 2), 22000.0
    while len(out) < days:
        if d.weekday() < 5:
            n = 375
            r, prev, oi, rows = rng.normal(0, 3.2, n) + rng.normal(0, drift_sd), 0.0, 1.2e7, []
            t0 = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp()
            for i in range(n):
                prev = r[i]
                o = px
                px = px + prev
                oi += 2500 * (0.35 + abs(prev) / 3.2) * rng.normal(0.6, 1.0) * (1 if rng.random() < 0.6 else -1)
                rows.append((t0 + 60 * i, o, max(o, px) + 1, min(o, px) - 1, px, float(rng.integers(900, 1800)), oi))
            out.append(pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "oi"]))
        d += timedelta(days=1)
    return pd.concat(out, ignore_index=True)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p, den = k / n, 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return 100 * (mid - half), 100 * (mid + half)


def replay(bars, step_min, warm_min):
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    days = {d: g.reset_index(drop=True) for d, g in bars.groupby("date")}
    closes = {d: float(g["c"].iloc[-1]) for d, g in days.items()}
    order = sorted(days)
    rec = []
    for k, d in enumerate(order):
        if k < 6:
            continue                                   # need history for a realised-vol estimate
        past = [closes[x] for x in order[max(0, k - 21):k]]
        rets = np.diff(np.log(past))
        sd = float(np.std(rets, ddof=1) * past[-1]) if len(rets) >= 4 else 0.0
        if sd <= 0:
            continue
        g, prev_close = days[d], closes[order[k - 1]]
        end_close, n = float(g["c"].iloc[-1]), len(g)
        for t in range(warm_min, n - 1, step_min):
            if t > 330:
                break
            fp = FP.compute_footprint(g.iloc[: t + 1], sd, prev_close)
            if not fp.get("ok"):
                continue
            px = float(g["c"].iloc[t])
            row = {"date": d, "t": t, "bias": fp["bias"], "score": fp["score"], "quad30": fp["quad30"], "sd": sd,
                   "m_close": end_close - px}
            for h in HORIZONS:
                row[f"m{h}"] = float(g["c"].iloc[t + h]) - px if t + h < n else np.nan
            rec.append(row)
    return pd.DataFrame(rec)


def report(res, thin, demo):
    r = res[res["t"] % thin == 0]
    L = [f"{'=' * 76}", f"FOOTPRINT BACKTEST | {res['date'].nunique()} sessions | {len(res)} bars, {len(r)} after thinning to every {thin} min"
         + (" | SYNTHETIC DEMO (meaningless)" if demo else ""), "=" * 76,
         "Hit = index moved in the footprint's direction. 'Base' = chance of an UP move over the same horizon with no filter."]
    L.append(f"\n{'Horizon':8} {'Base up%':>9}")
    for h in HORIZONS:
        v = r[f"m{h}"].dropna()
        L.append(f"{h:>3} min  {100 * (v > 0).mean():>8.1f}%   (n={len(v)})")
    for side, sign in (("BULLISH", 1), ("BEARISH", -1)):
        sub = r[r["bias"] == side]
        L.append(f"\n{side} footprint: n={len(sub)} bars on {sub['date'].nunique()} sessions")
        L.append(f"  {'Horizon':8} {'n':>4} {'Hit%':>6} {'95% CI':>13} {'Avg pts':>8} {'vs base':>8}")
        for h in HORIZONS:
            v = (sub[f"m{h}"] * sign).dropna()
            base = (r[f"m{h}"].dropna() * sign > 0).mean() * 100
            if len(v):
                k = int((v > 0).sum())
                lo, hi = wilson(k, len(v))
                L.append(f"  {h:>3} min  {len(v):>4} {100 * k / len(v):>5.1f}% {lo:>5.1f}-{hi:<5.1f}% {v.mean():>8.1f} {100 * k / len(v) - base:>+7.1f}")
    both = r[r["bias"].isin(["BULLISH", "BEARISH"])]
    L.append("\nTRADING WITH vs AGAINST the footprint (60 min, index points)")
    if len(both):
        sgn = both["bias"].map({"BULLISH": 1, "BEARISH": -1})
        v = (both["m60"] * sgn).dropna()
        L.append(f"  with footprint   : n={len(v)}  hit {100 * (v > 0).mean():.1f}%  avg {v.mean():+.1f} pts")
        L.append(f"  against footprint: n={len(v)}  hit {100 * (v < 0).mean():.1f}%  avg {-v.mean():+.1f} pts")
    L.append("\nBY 30-MIN FLOW QUADRANT (60 min forward, raw index move)")
    L.append(f"  {'Quadrant':16} {'n':>4} {'Up%':>6} {'Avg pts':>8}")
    for q, g in r.groupby("quad30"):
        v = g["m60"].dropna()
        if len(v):
            L.append(f"  {q:16} {len(v):>4} {100 * (v > 0).mean():>5.1f}% {v.mean():>8.1f}")
    L += ["\nHOW TO READ", "  * Trust it only if the BULLISH/BEARISH hit% clears the base rate AND the 95% CI excludes it, on 30+ independent bars.",
          "  * Sessions are the real sample size; bars within a session are correlated.",
          "  * If the edge is not there, set the app's footprint filter to OFF and treat it as information only."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fut-id", default="")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--step-min", type=int, default=5, help="evaluate every N minutes")
    ap.add_argument("--warm-min", type=int, default=30, help="skip the first N minutes of each session")
    ap.add_argument("--thin", type=int, default=30, help="keep bars every N minutes for the statistics")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--demo-drift", type=float, default=0.8, help="per-session trend strength (pts/min); 0 = pure random walk (null test)")
    a = ap.parse_args()
    if a.demo:
        bars = demo_bars(max(a.days, 40), a.demo_drift)
    else:
        tok, cid = os.getenv("DHAN_ACCESS_TOKEN", "").strip(), os.getenv("DHAN_CLIENT_ID", "").strip()
        if not (a.fut_id and tok and cid):
            sys.exit("Need --fut-id plus DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID (or use --demo).")
        bars = fetch_bars(a.fut_id, a.days, tok, cid)
    print(f"{len(bars):,} one-minute bars loaded")
    res = replay(bars, a.step_min, a.warm_min)
    if res.empty:
        sys.exit("No usable bars after warm-up (need at least ~7 sessions).")
    text = report(res, a.thin, a.demo)
    print(text)
    tag = "demo" if a.demo else f"fut{a.fut_id}"
    res.to_csv(f"fp_backtest_{tag}.csv", index=False)
    open(f"fp_backtest_{tag}.txt", "w", encoding="utf-8").write(text)
    print(f"\nSaved fp_backtest_{tag}.csv and fp_backtest_{tag}.txt")


if __name__ == "__main__":
    main()
