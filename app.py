    lean_now = sum(c["v"] for c in chain_checks(m))
    fav = "CE" if lean_now > 0 else "PE" if lean_now < 0 else None
    if "BULLISH GAMMA" in m["status"]:
        fav = "CE"
    elif "BEARISH GAMMA" in m["status"]:
        fav = "PE"
    if scan.empty:
        st.info("No priced options found (needs last_price from the option chain; market may be closed or premiums < "
                f"{MIN_PREMIUM:.0f}).")
    else:
        fav_txt = {"CE": "CALLS (bullish side)", "PE": "PUTS (bearish side)"}.get(fav, "NO CLEAR SIDE - both shown, treat as low edge")
        st.caption(f"Chain lean {lean_now:+d} | status {m['status']} → favoured: {fav_txt}. "
                   f"1σ daily move ≈ {smeta['sd']:,.0f} pts | horizon {horizon_label} (time left {smeta['T0d']:.1f}d → {smeta['T1d']:.1f}d) | "
                   f"IV shock ±{smeta['crush']:.0f}%" + (f" | spot-vol link ±{smeta['skew'] * smeta['crush']:.1f}% IV per σ." if smeta["skew"] else "."))
        top = scan[scan["Side"] == fav].head(5) if fav else scan.head(6)
        other = scan[scan["Side"] != fav].head(3) if fav else None
        show_df(fmt_scanner(top), hide_index=True)
        if other is not None and not other.empty:
            with st.expander("Counter-trend side (against the chain lean)"):
                show_df(fmt_scanner(other), hide_index=True)

        if top.empty:      # the favoured side has no priced option: fall back to the overall ranking instead of crashing
            st.info("No priced option on the favoured side right now; showing the best-ranked contracts overall.")
            top = scan.head(6)
        b = top.iloc[0]
        sgn = 1 if b["Side"] == "CE" else -1
        s1 = m["spot"] + sgn * smeta["sd"]
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Top pick walk-through: {m['spot']:,.0f} spot, {b['Strike']:,.0f} {b['Side']} ({b['Money']}) at ₹{b['LTP']:,.1f}</div>
          <div class="agent">If spot moves 1σ in favour to ≈ <b>{s1:,.0f}</b>: premium ≈ <b>{b['x1σ']:.2f}x</b> (₹{b['LTP']*b['x1σ']:,.1f}); delta rises {b['Delta']:.2f} → {b['Delta@1σ']:.2f}, which is the gamma effect (OTM turning into ITM/ATM).</div>
          <div class="agent">With IV crush −{smeta['crush']:.0f}% on that same move: <b>{b['x1σ crush']:.2f}x</b> (₹{b['LTP']*b['x1σ crush']:,.1f}). With IV spike +{smeta['crush']:.0f}%: <b>{b['x1σ spike']:.2f}x</b> (₹{b['LTP']*b['x1σ spike']:,.1f}).</div>
          <div class="agent">If it goes the wrong way by 0.5σ: <b>{b['xWrong']:.2f}x</b> (₹{b['LTP']*b['xWrong']:,.1f}). Spot move needed just to double the premium: <b>{('>4' if pd.isna(b['Needs2x(σ)']) else f"{b['Needs2x(σ)']:.2f}")}σ</b>.</div>
        </div>""", unsafe_allow_html=True)
        st.caption("Scenario repricing with Black-Scholes anchored to the traded premium, constant per-strike IV shift, "
                   "no slippage. It shows how premiums react IF spot moves; it does not predict that the move happens. "
                   "Score = reward after IV crush ÷ wrong-way loss, weighted by liquidity, delta odds, volume and fresh OI.")


with tab5:
    # ---- LIVE NEWS PANEL ----
    st.markdown("#### LIVE MARKET NEWS")
    if news_items:
        tone = headline_tone(news_items)
        tcol = "#00e676" if tone > 15 else "#ff1744" if tone < -15 else "#90a4ae"
        rows = []
        for i in news_items:
            link = i["link"] if i["link"].startswith(("http://", "https://")) else ""
            t = esc(i["title"])
            t = f'<a href="{html.escape(link, quote=True)}" target="_blank" style="color:#ddd;">{t}</a>' if link else t
            rows.append(f'<div class="agent"><b>{esc(age_label(i["age_min"]))}</b> · {esc(i["source"])} — {t}</div>')
        st.markdown(f"""<div class="bbg-panel">
          <div class="bbg-title">Headline tone (keyword backstop): <span style="color:{tcol};">{tone:+d}</span></div>
          {''.join(rows)}</div>""", unsafe_allow_html=True)
    else:
        st.warning("No headlines available. " + " | ".join(news_status))
    with st.expander("NEWS FEED STATUS"):
        for line in news_status:
            st.write(line)

    st.markdown("#### AI COUNCIL")
    st.caption(f"Gate: {gate_label}")
    if "error" in ai and "signal" not in ai:
        st.warning(ai["error"])
    else:
        scolor = {"BUY": "#00e676", "SELL": "#ff1744"}.get(sig["signal"], "#90a4ae")
        lv = ""
        if sig["target"] is not None:
            rr_txt = f"1:{sig['rr']:.1f}" if sig["rr"] else "n/a"
            lv = (f"Entry {sig['entry']:,.0f} &nbsp;|&nbsp; Target {sig['target']:,.0f} &nbsp;|&nbsp; "
                  f"SL {sig['sl']:,.0f} &nbsp;|&nbsp; R:R {rr_txt}")
        ai_note = f" (AI said {sig['ai_signal']})" if sig["ai_signal"] != sig["signal"] else ""
        st.markdown(f"""
        <div class="bbg-panel" style="border:2px solid {scolor};">
          <div class="bbg-title">Risk-Validated Signal</div>
          <div class="bbg-big" style="color:{scolor};">{sig['signal']}{esc(ai_note)} &nbsp;|&nbsp; Confidence {sig['confidence']}%</div>
          <div class="bbg-desc">{lv}</div>
          <div class="bbg-desc" style="color:#ffab00;">{esc(sig['note']) if sig['note'] else ''}</div>
        </div>""", unsafe_allow_html=True)

        # option-chain cross-check
        d = sig["direction"]
        def icon(v):
            if d == 0:
                return "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
            return "✅" if v * d > 0 else "❌" if v * d < 0 else "➖"
        crow = "".join(f'<div class="agent">{icon(c["v"])} <b>{esc(c["name"])}:</b> {esc(c["detail"])}</div>'
                       for c in sig["checks"])
        lean_txt = "BULLISH" if sig["lean"] > 0 else "BEARISH" if sig["lean"] < 0 else "NEUTRAL"
        sres, ssup = m["struct"]["res"], m["struct"]["sup"]
        lvl_txt = (f"Nearest OI resistance {sres['level']:,.0f} ({sres['oi']/1e5:,.1f}L, chg {sres['chg']/1e5:+,.1f}L)" if sres else "No nearby resistance") \
            + " &nbsp;|&nbsp; " + \
            (f"Nearest OI support {ssup['level']:,.0f} ({ssup['oi']/1e5:,.1f}L, chg {ssup['chg']/1e5:+,.1f}L)" if ssup else "No nearby support") \
            + f" &nbsp;|&nbsp; Global walls {m['call_wall']:,.0f} / {m['put_wall']:,.0f}"
        need = f"AI call agreement {sig['net']:+d} (needs +{MIN_CONFIRM})" if d else "no directional call to confirm"
        st.markdown(f"""
        <div class="bbg-panel">
          <div class="bbg-title">Option-chain cross-check &nbsp;|&nbsp; chain lean {lean_txt} ({sig['lean']:+d}) &nbsp;|&nbsp; {need}</div>
          {crow}
          <div class="agent" style="color:#888;">{lvl_txt}</div>
        </div>""", unsafe_allow_html=True)

        try:
            ns = int(float(ai.get("news_score", 0)))
        except (TypeError, ValueError):
            ns = None
        ns_txt = f" (score {ns:+d})" if ns is not None else ""
        st.markdown(f"""
        <div class="bbg-panel">
          <div class="agent"><b>Price Action:</b> {esc(ai.get('price_action_agent'))}</div>
          <div class="agent"><b>Order Flow:</b> {esc(ai.get('order_flow_agent'))}</div>
          <div class="agent"><b>Volatility:</b> {esc(ai.get('volatility_agent'))}</div>
          <div class="agent"><b>Sentiment{ns_txt}:</b> {esc(ai.get('news_agent'))}</div>
          <div class="agent"><b>Risk Officer:</b> {esc(ai.get('final_approval'))}</div>
        </div>""", unsafe_allow_html=True)


with tab6:
    st.caption(f"1σ range to expiry: {m['lower_1sigma']:,.0f} to {m['upper_1sigma']:,.0f}  |  Updated {datetime.now(IST).strftime('%H:%M:%S')} IST")
    show_df(m["df"], hide_index=True)
