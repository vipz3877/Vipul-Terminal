    st.session_state["vega_target"]=st.sidebar.slider("Vega Target:",0.0,100.0,value=st.session_state["vega_target"],
        help="Target vega exposure")
    st.session_state["slippage_pct"]=st.sidebar.slider("Slippage %:",0.01,5.0,value=st.session_state["slippage_pct"],
        help="Max expected slippage")
    st.session_state["capital_alloc"]=st.sidebar.number_input("Capital Allocation (₹):",min_value=10000,
        max_value=100000000,value=st.session_state["capital_alloc"],
        help="Total capital for position sizing")

    # --- NEWS QUERIES (FIX: semicolons replaced with commas) ---
    queries = ['Nifty OR Sensex OR "Dalal Street"', "India stocks FII DII", 'RBI OR "US Fed" OR crude OR rupee']
    if "BANK" in chosen_idx.upper(): queries.insert(0,'"Bank Nifty" OR "banking stocks"')
    # -------------------------------------------------------

    # Load data
    with st.spinner("Fetching option chain from Dhan..."):
        try:
            exp = fetch_expiries(scrip, seg) or []
        except Exception as exc:
            st.error(f"Could not fetch Dhan expiries: {exc}")
            st.stop()
        expiry = st.selectbox("Expiry Date:", exp) if exp else "N/A"
        try:
            spot, df = _fetch_chain_raw(scrip, seg, expiry)
        except Exception as exc:
            st.error(f"Could not fetch/parse Dhan option chain: {exc}")
            st.stop()
    if spot <= 0 or df.empty:
        st.error("Dhan returned no usable option-chain rows. Check the selected expiry, API credentials, and Dhan response format.")
        st.stop()

    m=compute_metrics(df,spot,None,expiry,step)  # prev_close optional inside

    # News fetch (simplified, using the fixed queries list)
    live_n=st.sidebar.toggle("Live News Feed",value=True)
    news_items,nhash=[],""; t_ist=datetime.now(timezone.utc)+timedelta(hours=5.5)
    if live_n:
        try:
            for q in queries:
                r=requests.get(f"https://news.google.com/rss/search?q={urllib.parse.quote(q)+' when:1d'}&hl=en-IN&gl=IN&ceid=IN:en",timeout=8)
                if r.ok:
                    root=ET.fromstring(r.content)
                    for it in root.iter("item"):
                        title=(it.findtext("title") or "").strip()
                        if not title: continue
                        src_el=it.find("source")
                        source=(src_el.text or "").strip() if src_el is not None and src_el.text else "feed"
                        title=_strip_source(title, source) # need to define _strip_source or inline
                        key=re.sub(r"\W+", "", title.lower())[:70]
                        # simple dedup
                        if 'seen' not in st.session_state: st.session_state["seen"]=set()
                        if key in st.session_state["seen"]: continue
                        st.session_state["seen"].add(key)
                        try:
                            ts=parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                        except Exception: ts=0.0
                        link=(it.findtext("link") or "").strip()
                        news_items.append({"title": title, "source": source, "link": link, "ts": ts})
        except Exception as e:
            st.sidebar.error(f"News fetch error: {e}")
        if news_items:
            news_items.sort(key=lambda x:x["ts"],reverse=True)
            news_items=news_items[:10]
            nhash=hashlib.md5("|".join(i["title"] for i in news_items[:5]).encode()).hexdigest()
    else: st.sidebar.caption("News feed disabled")

    # AI Council
    try:
        groq_key = str(st.secrets.get("groq", {}).get("GROQ_API_KEY", "")).strip()
    except Exception:
        groq_key = ""
    groq_model=st.sidebar.selectbox("Groq Model:",["openai/gpt-oss-120b","openai/gpt-oss-20b"],index=0)
    if st.sidebar.button("Run AI Council") or "ai" not in st.session_state:
        with st.spinner("Sending to Groq..."):
            ai=run_council(m,groq_key,groq_model)
            st.session_state["ai"]=ai; st.session_state["ai_time"]=datetime.now(timezone.utc)
    else: ai=st.session_state.get("ai",{"error":"No AI verdict yet"})

    # Tabs
    tabs=st.tabs(["Dashboard","Option Chain","AI Council","Scorecard","Data Health"])

    # Tab 1: Dashboard
    with tabs[0]:
        st.subheader(f"{chosen_idx} Dashboard")
        c1,c2,c3=st.columns(3); c1.metric("ATM IV",f"{m['atm_iv']:.1f}%"); c2.metric("Gamma",f"{m['gex_regime']}"); c3.metric("DTE",m["dte"])
        c4,c5,c6=st.columns(3); c4.metric("PCR",f"{m['pcr']:.2f}"); c5.metric("Bias Score",f"{m['score']}/100",m["bias"]); c6.metric("Exp Move ±",f"{m['exp_move']:,.0f}")
        st.metric("Net GEX",f"{m['net_gex']:+,.0f}",f"Gross {m['gross_gex']:+,.0f}")
        st.metric("RR25",f"{m['rr_25']:+.2f}%",f"BF25 {m['bf_25']:+.2f}")
        st.metric("Zero Gamma",f"{m['zero_gamma']:,.0f}" if m["zero_gamma"] else "—",f"Crossover {m['crossover']:,.0f}" if m["crossover"] else "—")
        st.metric("Call Wall",f"{m['call_wall']:,.0f}"); st.metric("Put Wall",f"{m['put_wall']:,.0f}"); st.metric("Max Pain",f"{m['max_pain']:,.0f}")

    # Tab 2: Option Chain
    with tabs[1]:
        st.subheader(f"{chosen_idx} Option Chain")
        c1,c2=st.columns(2)
        with c1: st.dataframe(df[["Strike","CE_OI","PE_OI","CE_IV","PE_IV","CE_Gamma","PE_Gamma","CE_LTP","PE_LTP"]].head(20),hide_index=True)
        with c2: st.write("Strike distribution"); st.bar_chart(df.set_index("Strike")["CE_OI"].head(10))

    # Tab 3: AI Council
    with tabs[2]:
        st.subheader("Groq AI Council (5-Agent Prompt)")
        if st.button("Refresh AI Analysis"):
            ai=run_council(m,groq_key,groq_model); st.session_state["ai"]=ai; st.rerun()
        if "error" in ai: st.error(ai["error"])
        else:
            st.success("AI Signals:")
            ca,colb=st.columns(2)
            with ca: st.write("**Price Action:**",ai.get("price_action","-")); st.write("**Volatility:**",ai.get("volatility","-"))
            with colb: st.write("**Order Flow:**",ai.get("order_flow","-")); st.write("**Sentiment:**",ai.get("sentiment","-")); st.write("**Risk Officer:**",ai.get("risk_officer","-"))

    # Tab 4: Scorecard
    with tabs[3]:
        st.subheader("Signal Scorecard")
        if "error" not in ai:
            sig=build_signal(m,ai)
            c1,c2,c3=st.columns(3)
            with c1: st.metric("Signal",sig["signal"]); st.metric("Confidence",f"{sig['confidence']}%")
            with c2: st.metric("Entry",f"{sig['entry']:,.0f}"); st.metric("Target",f"{sig['target'] or '—':,.0f}" if sig['target'] else "—")
            with c3: st.metric("Stop",f"{sig['sl'] or '—':,.0f}"); st.metric("R:R",f"1:{sig['rr']:.2f}" if sig['rr'] else "n/a")
            st.info(f"Note: {sig['note'] or 'Signal validated.'}")
        else: st.warning(ai["error"])

    # Tab 5: Data Health
    with tabs[4]:
        st.subheader("Data Health & API Status")
        try:
            tok = str(st.secrets.get("general", {}).get("DHAN_ACCESS_TOKEN", ""))
        except Exception:
            tok = ""
