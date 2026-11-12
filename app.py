        e4.metric("PIN (max pain)", f"{_xc['pin']:,.0f}", "PINNED: do not buy decay" if _xc.get("pinned") else f"{m['spot'] - _xc['pin']:+,.0f} vs spot", delta_color="off")
        _a, _b = _xc.get("A", {}), _xc.get("B", {})
        _dirtxt = {1: "UP", -1: "DOWN", 0: "none"}
        _pbrows = [
            {"Playbook": "A  Morning scalp (9:30-11:00)", "Window open": "YES" if _xc.get("a_window") else "no",
             "Trigger": _dirtxt[_a.get("dir", 0)], "Reading": _a.get("state", "")},
            {"Playbook": "B  Wall break (9:30-1:30)", "Window open": "YES" if (_xc.get("b_window") and not _xc.get("late")) else "no",
             "Trigger": _dirtxt[_b.get("dir", 0)] + (" (confirmed)" if _b.get("confirmed") else ""), "Reading": _b.get("state", "")},
            {"Playbook": "C  Late wall break + pin guard (1:30-2:30)", "Window open": "YES" if (_xc.get("b_window") and _xc.get("late")) else "no",
             "Trigger": _dirtxt[_b.get("dir", 0)] + (" (confirmed)" if _b.get("confirmed") else ""),
             "Reading": ("PINNED near max pain: no buying" if _xc.get("pinned") else _b.get("state", ""))},
        ]
        show_df(pd.DataFrame(_pbrows), hide_index=True)
        if _s and _s.get("playbook"):
            st.success(f"Active playbook {_s['playbook']}: time stop {_s['time_stop_min']} min, half size, exit by the time stop even if the stop has not been hit.")
        _be = (_s or {}).get("breakeven")
        if _be:
            st.caption(f"Expiry breakeven for the planned contract: index at {_be['breakeven']:,.0f}, a move of {_be['required_move']:,.0f} pts "
                       f"({(_be['vs_expected_left'] or 0):.2f}x the {m['exp_move']:,.0f} pts still expected today). Scalps exit earlier, so this is a measure of how expensive the premium is.")
        st.caption("Playbooks are rules of thumb, tagged on every signal so your scorecard can show which (if any) has an edge. "
                   "Opening-range, wall-break and pin triggers need intraday candles and about 10 minutes of OI history.")

    # ---- 3. Is it worth buying options now?
    st.markdown("##### Option price check (is buying expensive right now?)")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("ATM IV", f"{m['atm_iv']:.1f}%",
              f"{_ivp['pct']}th pct of last 30d ({_ivp['n']} samples)" if _ivp["pct"] is not None
              else f"percentile building ({_ivp['n']}/30 samples)", delta_color="off")
    v2.metric("REALISED VOL (10d)", f"{_rv:.1f}%" if _rv else "n/a",
              f"IV minus RV {m['atm_iv'] - _rv:+.1f} pts" if _rv else None, delta_color="off")
    v3.metric("ATM STRADDLE", f"{_straddle:,.0f}", f"expected daily move ±{_sd_pts:,.0f} pts", delta_color="off")
    v4.metric("EXPECTED MOVE TO EXPIRY (±)", f"{m['exp_move']:,.0f}", f"{m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}", delta_color="off")
    st.caption("When IV is well above recent realised volatility, buyers need a bigger move than the market has recently delivered just to break even. "
               "The percentile is built from samples this app records while it is open, so it needs weeks to become meaningful; keep BBG_DATA_DIR on a persistent disk.")

    # ---- 4. Level map
    st.markdown("##### Level map (confluence zones)")
    if _lv.empty:
        st.info("No levels available yet.")
    else:
        _above = _lv[_lv["Dist pts"] > 0].head(4)
        _below = _lv[_lv["Dist pts"] <= 0].head(4)
        show_df(pd.concat([_above.sort_values("Dist pts", ascending=False), _below]), hide_index=True)
        st.caption("Score adds up how many independent sources meet in a zone (OI walls and gamma flip count double). STRONG = 5 or more. "
                   "Use zones to judge room to run before your target and where price may stall or reverse; wait for rejection or acceptance before acting.")

    # ---- 5. Daily risk lock
    st.markdown("##### Daily risk lock")
    k1, k2, k3, k4 = st.columns(4)
    _pnl_in = k1.number_input("REALISED P&L TODAY ₹", value=float(_risk["pnl"]), step=500.0, key="dr_pnl")
    _tr_in = k2.number_input("TRADES TAKEN TODAY", min_value=0, value=int(_risk["trades"]), step=1, key="dr_trades")
    _ml_in = k3.number_input("DAILY MAX LOSS ₹", min_value=0.0, value=float(_risk["max_loss"]), step=500.0, key="dr_maxloss")
    _mt_in = k4.number_input("MAX TRADES / DAY", min_value=1, value=int(_risk["max_trades"]), step=1, key="dr_maxtrades")
    if st.button("SAVE DAILY RISK", key="dr_save"):
        save_daily_risk({"date": _today_iso, "pnl": float(_pnl_in), "trades": int(_tr_in),
                         "max_loss": float(_ml_in), "max_trades": int(_mt_in)})
        st.success("Saved. The lock applies to new signals on the next refresh.")
    st.caption("Defaults: max loss = 2 x your per-trade risk, 3 trades a day. The lock only blocks new signals here; it cannot stop orders you place elsewhere.")

    # ---- 6. Pre-trade checklist
    st.markdown("##### Pre-trade checklist")
    _rows = []

    def _chk(name, status, detail):
        _rows.append({"Check": name, "Status": status, "Detail": detail})

    _chk("Data gate", "PASS" if data_ok else "FAIL", "open" if data_ok else gate_reason)
    _chk("Signal", "PASS" if (_s and _s["signal"] != "WAIT") else "FAIL",
         _s["signal"] if _s else "none")
    _chk("Session", "FAIL" if _blocks else ("WARN" if _pnote else "PASS"), f"{_phase}. {_pnote}".strip())
    _chk("Daily risk lock", "FAIL" if _locked else "PASS", _lock_why or "within limits")
    _fpd = m.get("fp") or {}
    _chk("Futures footprint", "PASS" if _fpd.get("ok") else "WARN",
         f"bias {_fpd.get('bias')}" if _fpd.get("ok") else f"unavailable ({str(_fpd.get('why', ''))[:70]})")
    _bp2 = _s.get("buyer_plan") if _s else None
    _chk("Contract liquidity", "PASS" if _bp2 else "FAIL",
         f"spread {_bp2['spread_pct']:.1f}%, volume {_bp2['volume']:,}" if _bp2 else "no contract passed spread/volume/delta filters")
    if _ivp["pct"] is None:
        _chk("IV level", "WARN", f"history still building ({_ivp['n']}/30 samples)")
    else:
        _chk("IV level", "WARN" if _ivp["pct"] >= 85 else "PASS", f"{_ivp['pct']}th percentile of the last 30 days")
    if _rv:
        _gap = m["atm_iv"] - _rv
        _chk("IV vs realised vol", "WARN" if _gap > 5 else "PASS", f"IV is {_gap:+.1f} pts vs 10-day realised")
    _chk("Expiry day", "WARN" if m["dte"] == 0 else "PASS",
         "gamma and theta are extreme: size down, keep it short" if m["dte"] == 0 else f"{m['dte']} days to expiry")
    if _xc.get("active"):
        _chk("Expiry playbook", "PASS" if (_s and _s.get("playbook")) else "FAIL",
             f"playbook {_s['playbook']}, time stop {_s['time_stop_min']} min" if (_s and _s.get("playbook")) else "no confirmed trigger in your direction")
    if _s and _s["signal"] in ("BUY", "SELL") and not _lv.empty and _sd_pts:
        _want_up = _s["signal"] == "BUY"
        _blk = _lv[(_lv["Dist pts"] > 0) & (_lv["Strength"] == "STRONG")] if _want_up else _lv[(_lv["Dist pts"] < 0) & (_lv["Strength"] == "STRONG")]
        _blk = _blk[_blk["Dist σ"].fillna(9) <= 0.25]
        _chk("Room to run", "WARN" if len(_blk) else "PASS",
             (f"STRONG zone {int(_blk.iloc[0]['Zone'])} within 0.25 σ in your direction" if len(_blk) else "no STRONG zone within 0.25 σ ahead"))
    _event = st.checkbox("A major event is scheduled today (RBI / Fed / Budget / results)", key="td_event")
    _chk("Event risk", "WARN" if _event else "PASS", "expect IV crush and gaps" if _event else "none flagged by you")
    show_df(pd.DataFrame(_rows), hide_index=True)

    _m1 = st.checkbox("I know my exit (stop premium AND a time stop) before I enter", key="td_exit")
    _m2 = st.checkbox("Position size keeps my loss within my max risk", key="td_size")
    _fails = [r for r in _rows if r["Status"] == "FAIL"]
    _warns = [r for r in _rows if r["Status"] == "WARN"]
    if _fails:
        st.error("NO-GO: " + ", ".join(r["Check"] for r in _fails))
    elif not (_m1 and _m2):
        st.warning("Tick both confirmations above before entering.")
    elif _warns:
        st.warning("CAUTION: " + ", ".join(r["Check"] for r in _warns) + ". Entry is your call; consider smaller size.")
    else:
        st.success("GO: all checks pass. Execute manually and respect your stop.")
