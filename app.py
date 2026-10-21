# VIPUL BLOOMBERG PRO v13.3 - FIXED (with secrets guard)
import streamlit as st
import json, re, math, time, hashlib, numpy as np, pandas as pd, requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.parse

# -------------------------------------------------------
# 0. SECRETS GUARD (prevents raw KeyError)
# -------------------------------------------------------
try:
    _ = st.secrets["general"]["DHAN_CLIENT_ID"]
    _ = st.secrets["general"]["DHAN_ACCESS_TOKEN"]
    _ = st.secrets["groq"]["GROQ_API_KEY"]
except Exception:
    st.error(
        "⚠️ Missing Dhan/Groq/Toml secrets. "
        "Create `.streamlit/secrets.toml` next to this script (or set them in Streamlit Cloud Settings → Secrets).\n"
        "Required keys: DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, GROQ_API_KEY"
    )
    st.stop()

# -------------------------------------------------------
# 1. SESSION STATE INITIALIZATION LOOP
# -------------------------------------------------------
DEFAULT_STATE = {
    "index_name": "NIFTY 50", "noise_pct": 0.5, "gex_threshold": 0.25,
    "vol_surface_step": 50, "dte_filter": 30, "confluence_cutoff": 3,
    "ai_temperature": 0.7, "telegram_enabled": False, "data_update_ttl": 60,
    "regime_mode": "mixed", "vega_target": 0.0, "gamma_mode": "long",
    "slippage_pct": 0.1, "capital_alloc": 100000,
}
for key, default in DEFAULT_STATE.items():
    st.session_state.setdefault(key, default)

# -------------------------------------------------------
# 2. INDIAN INDEX MAPPINGS
# -------------------------------------------------------
INDEX_MAP = {
    "NIFTY 50": {"scrip": 13, "seg": "IDX_I", "step": 50},
    "NIFTY BANK": {"scrip": 25, "seg": "IDX_I", "step": 100},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "step": 50},
    "MIDCPNIFTY": {"scrip": 118, "seg": "IDX_I", "step": 25},
}

# -------------------------------------------------------
# 3. safe_json_parse() - never crashes on bad LLM output
# -------------------------------------------------------
def safe_json_parse(output):
    if not output: return None
    try: return json.loads(output)
    except: pass
    m = re.search(r'```json\s*(\{.*\})```', output, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    m = re.search(r'\{[^}]+\}', output)
    if m:
        try: return json.loads(m.group())
        except: pass
    return None

# -------------------------------------------------------
# 4. Dhan API Helpers
# -------------------------------------------------------
def dhan_auth_headers():
    return {
        "client-id": st.secrets["general"]["DHAN_CLIENT_ID"],
        "access-token": st.secrets["general"]["DHAN_ACCESS_TOKEN"],
    }

def get_expiries(scrip, seg):
    r = requests.post("https://api.dhan.co/v2/optionchain/expirylist",
                      headers=dhan_auth_headers(), json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10)
    if r.ok: return r.json().get("data", [])
    return []

def fetch_option_chain(scrip, seg, expiry):
    r = requests.post("https://api.dhan.co/v2/optionchain",
                      headers=dhan_auth_headers(), json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12)
    if r.ok: return r.json().get("data", {})
    return {}

def fetch_daily_candles(scrip, seg):
    try:
        r = requests.post("https://api.dhan.co/v2/charts/historical",
                          headers=dhan_auth_headers(),
                          json={"securityId": str(scrip), "exchangeSegment": seg, "instrument": "INDEX",
                                "expiryCode": 0, "fromDate": (datetime.now().date() - timedelta(days=35)).isoformat(),
                                "toDate": (datetime.now().date()).isoformat()}, timeout=12)
        if r.ok:
            d = r.json()
            return [{"date": (datetime.fromtimestamp(float(c[0]), tz=timezone.utc).date()).isoformat(),
                     "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])} for c in d.get("candles", [])]
    except: pass
    return []

# -------------------------------------------------------
# 5. Core Analytics
# -------------------------------------------------------
def _n(x): return float(x or 0)
def _mid(b, a, l): return 0.5*(b+a) if b>0 and a>0 and a>=l else l if l>0 else 0.0

def compute_metrics(df, spot, expiry_str, step):
    df = df.copy(); df["CE_OI_Chg"]=df["CE_OI"]-df["CE_PrevOI"]; df["PE_OI_Chg"]=df["PE_OI"]-df["PE_PrevOI"]
    pcr = df["PE_OI"].sum()/df["CE_OI"].sum() if df["CE_OI"].sum()>0 else 1.0
    dce, dpe = float(df["CE_OI_Chg"].sum()), float(df["PE_OI_Chg"].sum())
    atm_idx = (df["Strike"]-spot).abs().idxmin()
    ce_iv, pe_iv = float(df.loc[atm_idx,"CE_IV"]), float(df.loc[atm_idx,"PE_IV"])
    ivs=[x for x in (ce_iv,pe_iv) if x>0]; atm_iv=sum(ivs)/len(ivs) if ivs else 12.0
    iv_gap=abs(ce_iv-pe_iv) if (ce_iv>0 and pe_iv>0) else 0.0
    dte = max(1, (datetime.strptime(str(expiry_str)[:10],"%Y-%m-%d").date()-datetime.now().date()).days)
    t=max(dte,1)/365.0
    exp_move=spot*(atm_iv/100)*math.sqrt(t)
    disc=math.exp(0.065*t)
    ce_near=df[(df["CE_IV"]>0) & (df["Strike"]>=spot-4*step) & (df["Strike"]<=spot+4*step)]
    est=[]; [est.append(float(r.Strike)+disc*(_mid(r.CE_Bid,r.CE_Ask,r.CE_LTP)-_mid(r.PE_Bid,r.PE_Ask,r.PE_LTP))) for _,r in ce_near.iterrows() if _mid(r.CE_Bid,r.CE_Ask,r.CE_LTP)>1 and _mid(r.PE_Bid,r.PE_Ask,r.PE_LTP)>1]
    f_syn=round(sum(sorted(est)[:3])/len(sorted(est)[:3]),2) if est else None
    iv_c=iv_p=atm_iv; ok_c=ok_p=False
    ce=df[(df["CE_IV"]>0) & (df["CE_Delta"].abs()>0) & (df["Strike"]>=spot-2*step) & (df["Strike"]<=spot+12*step)]
    pe=df[(df["PE_IV"]>0) & (df["PE_Delta"].abs()>0) & (df["Strike"]<=spot+2*step) & (df["Strike"]>=spot-12*step)]
    if not ce.empty: iv_c,ok_c=float(ce.loc[(ce["CE_Delta"].abs()-0.25).abs().idxmin(),"CE_IV"]),True
    if not pe.empty: iv_p,ok_p=float(pe.loc[(pe["PE_Delta"].abs()-0.25).abs().idxmin(),"PE_IV"]),True
    rr=round(iv_c-iv_p,2); bf=round(0.5*(iv_c+iv_p)-atm_iv,2)
    out=df.copy(); out["CE_GEX"]=out["CE_Gamma"].abs()*out["CE_OI"]*spot**2*0.01; out["PE_GEX"]=-out["PE_Gamma"].abs()*out["PE_OI"]*spot**2*0.01; out["Net_GEX"]=out["CE_GEX"]+out["PE_GEX"]
    gross=abs(out["CE_GEX"].sum())+abs(out["PE_GEX"].sum()); pct=out["Net_GEX"].sum()/gross*100 if gross>0 else 0
    reg="POSITIVE" if pct>5 else "NEGATIVE" if pct<-5 else "NEUTRAL"
    t2=max(t,1e-5); sq=math.sqrt(t2); K=out["Strike"].to_numpy(float); ce_oi=out["CE_OI"].to_numpy(float); pe_oi=out["PE_OI"].to_numpy(float)
    ce_iv_n=out["CE_IV"].to_numpy(float)/100.0; pe_iv_n=out["PE_IV"].to_numpy(float)/100.0
    ok=~np.isnan(ce_iv_n)&~np.isnan(pe_iv_n)
    if ok.sum()>=5:
        Kk,Kc,Kp=K[ok],ce_iv_n[ok],pe_iv_n[ok]; cei=ce_oi[ok]; pei=pe_oi[ok]
        def gextot(S):
            d1=(math.log(S/Kc)+0.5*Kc**2*t2)/(Kc*sq); g=np.exp(-0.5*d1**2)/math.sqrt(2*math.pi)/(S*Kc*sq)
            d1p=(math.log(S/Kp)+0.5*Kp**2*t2)/(Kp*sq); gp=np.exp(-0.5*d1p**2)/math.sqrt(2*math.pi)/(S*Kp*sq)
            return np.sum(g*pei)*S*S*0.01-np.sum(g*cei)*S*S*0.01
        gg=round(gextot(spot),2)
    else: gg=0.0
    k,g=out["Strike"].to_numpy(float),out["Net_GEX"].to_numpy(float); xov=None
    for i in range(len(g)-1):
        a,b=g[i],g[i+1]; cand=None
        if a==0 and b!=0: cand=k[i]
        elif a*b<0: alpha=abs(a)/(abs(a)+abs(b)); cand=k[i]+alpha*(k[i+1]-k[i])
        if cand is not None: xov=cand if xov is None else min(xov,cand,key=lambda v:abs(v-spot))
    xov=round(float(xov),1) if xov else None
    strikes=df["Strike"].values; ceo=df["CE_OI"].values; peo=df["PE_OI"].values
    pain=[(ceo*np.maximum(0,k-strikes)).sum()+(peo*np.maximum(0,strikes-k)).sum() for k in strikes]
    mp=strikes[np.argmin(pain)]
    score=50; score+=25 if dce<-15000 and dpe>10000 else -25 if dpe<-15000 and dce>10000 else 0
    score+=8 if pcr>1.2 else -8 if pcr<0.8 else 0
    thr=max(8.0,0.0004*spot); score+=5 if (f_syn is not None and f_syn-spot>thr and rr>-0.5) else -5 if (f_syn is not None and f_syn-spot<-thr and rr<-1.5) else 0
    score=max(0,min(100,score)); label="BULLISH" if score>55 else "BEARISH" if score<45 else "NEUTRAL"
    near=df[(df["Strike"]>=spot-8*step) & (df["Strike"]<=spot+8*step)].copy()
    ce_tot=near["CE_OI"].sum(); pe_tot=near["PE_OI"].sum(); ce_noise=ce_tot*0.5/100; pe_noise=pe_tot*0.5/100
    ce_chg=near["CE_OI_Chg"]; pe_chg=near["PE_OI_Chg"]
    ce_exit=ce_chg[ce_chg<-ce_noise].sum(); pe_exit=pe_chg[pe_chg<-pe_noise].sum()
    ce_build=ce_chg[ce_chg>ce_noise].sum(); pe_build=pe_chg[pe_chg>pe_noise].sum()
    bull=pe_build/pe_tot*100 if pe_tot>0 else 0; bear=ce_build/ce_tot*100 if ce_tot>0 else 0
    exh_ready=False; exh_buyer_score=0; exh_seller_score=0
    if not near.empty:
        ce_max=near.loc[near["CE_OI_Chg"].idxmax(),"CE_OI_Chg"]; pe_max=near.loc[near["PE_OI_Chg"].idxmax(),"PE_OI_Chg"]
        exh_ready=True; exh_buyer_score=round(pe_max/pe_tot*100,1) if pe_tot>0 else 0; exh_seller_score=round(ce_max/ce_tot*100,1) if ce_tot>0 else 0
    return {"spot":spot,"step":step,"pcr":pcr,"dce":dce,"dpe":dpe,"atm_iv":atm_iv,"iv_gap":iv_gap,
            "dte":dte,"t":t,"exp_move":round(exp_move,2),"lower_1sigma":round(spot-exp_move,2),"upper_1sigma":round(spot+exp_move,2),
            "syn_fut":f_syn,"fair_fwd":round(spot*math.exp((0.065-0.013)*t),2),"carry":round(spot*math.exp(0.065*t)-spot,2),
            "excess_basis":round((f_syn or 0)-spot*math.exp(0.065*t),2),"surface_ok":f_syn is not None,
            "iv_25c":round(iv_c,2),"iv_25p":round(iv_p,2),"rr_25":round(rr,2),"bf_25":round(bf,2),
            "net_gex":round(out["Net_GEX"].sum(),1),"gross_gex":round(gross,1),"gex_regime":reg,
            "regime_stable":len({reg})==1,"crossover":xov,"zero_gamma":round(float(gg),1) if gg else None,
            "total_gex_at_spot":gg,"net_vex":round(float(near["Net_VEX"].sum()) if "Net_VEX" in near.columns else 0,1),
            "net_cex":round(float(near["Net_CEX"].sum()) if "Net_CEX" in near.columns else 0,1),
            "status":f"{reg} GAMMA","engine_signal":f"Bull force {bull:.1f} vs Bear {bear:.1f}",
            "score":score,"bias":label,"call_wall":float(df.loc[df["CE_OI"].idxmax(),"Strike"]),
            "put_wall":float(df.loc[df["PE_OI"].idxmax(),"Strike"]),"max_pain":mp,"ce_trap":None,"pe_trap":None}

# -------------------------------------------------------
# 6. Groq AI Council
# -------------------------------------------------------
def run_council(m, api_key, model):
    if Groq is None: return {"error":"groq not installed"}
    if not api_key: return {"error":"no Groq key"}
    try:
        from groq import Groq as _Groq
        client=_Groq(api_key=api_key)
        prompt=f"""You are a council of 5 options agents. Answer JSON ONLY with exactly these 5 keys: price_action, order_flow, volatility, sentiment, risk_officer.
Spot {m['spot']:.2f} | PCR {m['pcr']:.3f} | Bias {m['bias']} | IV {m['atm_iv']:.1f}% | DTE {m['dte']}d | Regime {m['gex_regime']}
- price_action: 1 sentence
- order_flow: 1 sentence
- volatility: 1 sentence
- sentiment: 1 sentence
- risk_officer: 1 sentence
Output must be valid JSON with those 5 keys. No markdown, no prose."""
        candidates=[model]+[x for x in ["openai/gpt-oss-120b","openai/gpt-oss-20b"] if x!=model]
        last_err=""
        for mdl in candidates:
            try:
                resp=client.chat.completions.create(model=mdl,messages=[{"role":"user","content":prompt}],temperature=0.1,max_tokens=500,response_format={"type":"json_object"})
                txt=(resp.choices[0].message.content or "").strip().replace("```json","").replace("```","").strip()
                return json.loads(txt)
            except Exception as e:
                last_err=f"{mdl}: {e}"
                if any(t in str(e).lower() for t in ("model_not_found","404","429","rate limit","503")): continue
                break
        return {"error":f"Groq failed: {last_err}"}
    except Exception as e: return {"error":str(e)}

# -------------------------------------------------------
# 7. Signal Build
# -------------------------------------------------------
def build_signal(m, ai):
    sig=str(ai.get("signal","WAIT")).upper(); 
    if sig not in ("BUY","SELL","WAIT"): sig="WAIT"
    try: conf=int(float(ai.get("confidence",0)))
    except: conf=0
    direction={"BUY":1,"SELL":-1}.get(sig,0)
    checks=[]; add=lambda n,v,d:checks.append({"name":n,"v":v,"detail":d})
    add("PCR",1 if m["pcr"]>1.2 else -1 if m["pcr"]<0.8 else 0,f"{m['pcr']:.2f}")
    add("Bias",1 if m["score"]>55 else -1 if m["score"]<45 else 0,f"{m['score']}/100")
    add("GEX regime",1 if "POSITIVE" in m["gex_regime"] else -1 if "NEGATIVE" in m["gex_regime"] else 0,m["gex_regime"])
    lean=sum(c["v"] for c in checks if c.get("counted",True)) * direction
    net=lean; entry=m["spot"]; res=m["struct"]["res"]; sup=m["struct"]["sup"]
    note=""; sig_out,target,sl,rr="WAIT",None,None,None
    if sig!="WAIT" and net<2: sig_out="WAIT"; note=f"signal rejected: agreement {net:+d} below required +2."
    if sig_out!="WAIT":
        if res is None or sup is None: sig_out="WAIT"; note+=" rejected: no usable OI wall."
        else:
            sig_out=sig; target=res["level"] if sig=="BUY" else sup["level"]; sl=sup["level"]-35 if sig=="BUY" else res["level"]+35
            risk=abs(entry-sl); reward=abs(target-entry); rr=reward/risk if risk>0 else None
            if rr is None or rr<1.0: sig_out="WAIT"; note+=f" rejected: R:R {rr:.2f} below 1:1." if rr else " rejected: R:R n/a."
    return {"signal":sig_out,"ai_signal":sig,"entry":entry,"target":target,"sl":sl,"rr":rr,
            "confidence":conf,"note":note,"checks":checks,"net":net,"lean":lean,"direction":direction}

# -------------------------------------------------------
# 8. DISCLAIMER
# -------------------------------------------------------
DISCLAIMER_HTML="""
<div style='position:fixed;bottom:0;left:0;width:100%;background:#fff3e0;padding:1rem;border-top:2px solid #ff9800;
z-index:999;font-size:0.8rem;text-align:center;'>
<b>Disclaimer:</b> Option positioning data reflects real-time Greek/volatility metrics and AI-derived confluence scores.
These are <u>not verified dealer recommendations</u>. Use for analytics only. Market conditions change rapidly.
</div>
"""

# -------------------------------------------------------
# 9. Main App
# -------------------------------------------------------
def main():
    st.set_page_config(layout="wide", page_title="VIPUL BLOOMBERG PRO v13.3 FIXED")
    st.markdown(DISCLAIMER_HTML, unsafe_allow_html=True)

    idx_opts=["NIFTY 50","NIFTY BANK","FINNIFTY","MIDCPNIFTY"]
    default_i=idx_opts.index(st.session_state["index_name"])
    chosen_idx=st.sidebar.selectbox("Choose NSE Index:",idx_opts,index=default_i,help="Select underlying index")
    st.session_state["index_name"]=chosen_idx
    idx=INDEX_MAP[chosen_idx]; scrip,step,seg=idx["scrip"],idx["step"],idx["seg"]

    st.sidebar.header("Configuration")
    st.session_state["noise_pct"]=st.sidebar.slider("Noise %:",0.1,2.0,value=st.session_state["noise_pct"])
    st.session_state["gex_threshold"]=st.sidebar.slider("GEX Threshold:",0.1,1.0,value=st.session_state["gex_threshold"])
    st.session_state["vol_surface_step"]=st.sidebar.slider("IV Surface Step:",10,200,value=st.session_state["vol_surface_step"])
    st.session_state["dte_filter"]=st.sidebar.slider("DTE Filter:",7,180,value=st.session_state["dte_filter"])
    st.session_state["confluence_cutoff"]=st.sidebar.slider("Confluence Cutoff:",1,10,value=st.session_state["confluence_cutoff"])
    st.session_state["ai_temperature"]=st.sidebar.slider("AI Temperature:",0.1,1.5,value=st.session_state["ai_temperature"])
    st.session_state["vega_target"]=st.sidebar.slider("Vega Target:",0.0,100.0,value=st.session_state["vega_target"])
    st.session_state["slippage_pct"]=st.sidebar.slider("Slippage %:",0.01,5.0,value=st.session_state["slippage_pct"])
    st.session_state["capital_alloc"]=st.sidebar.number_input("Capital Allocation (₹):",min_value=10000,
        max_value=100000000,value=st.session_state["capital_alloc"])

    queries = ['Nifty OR Sensex OR "Dalal Street"', "India stocks FII DII", 'RBI OR "US Fed" OR crude OR rupee']
    if "BANK" in chosen_idx.upper(): queries.insert(0,'"Bank Nifty" OR "banking stocks"')

    with st.spinner("Fetching option chain from Dhan..."):
        exp=get_expiries(scrip,seg) or []; expiry=st.selectbox("Expiry Date:",exp) if exp else "N/A"
    spot,df=_fetch_chain_raw(scrip,seg,expiry) or (0,pd.DataFrame())
    if spot==0 or df.empty: st.error("Could not fetch option chain. Check Dhan credentials."); st.stop()

    m=compute_metrics(df,spot,expiry,step)

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
                        src_el=it.find("source"); source=(src_el.text or "").strip() if src_el is not None and src_el.text else "feed"
                        key=re.sub(r"\W+", "", title.lower())[:70]
                        if 'seen' not in st.session_state: st.session_state["seen"]=set()
                        if key in st.session_state["seen"]: continue
                        st.session_state["seen"].add(key)
                        try: ts=parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                        except Exception: ts=0.0
                        link=(it.findtext("link") or "").strip()
                        news_items.append({"title": title, "source": source, "link": link, "ts": ts})
        except Exception as e: st.sidebar.error(f"News fetch error: {e}")
        if news_items:
            news_items.sort(key=lambda x:x["ts"],reverse=True); news_items=news_items[:10]
            nhash=hashlib.md5("|".join(i["title"] for i in news_items[:5]).encode()).hexdigest()
    else: st.sidebar.caption("News feed disabled")

    groq_key=st.secrets["groq"]["GROQ_API_KEY"]; groq_model=st.sidebar.selectbox("Groq Model:",["openai/gpt-oss-120b","openai/gpt-oss-20b"],index=0)
    if st.sidebar.button("Run AI Council") or "ai" not in st.session_state:
        with st.spinner("Sending to Groq..."): ai=run_council(m,groq_key,groq_model); st.session_state["ai"]=ai; st.session_state["ai_time"]=datetime.now(timezone.utc)
    else: ai=st.session_state.get("ai",{"error":"No AI verdict yet"})

    tabs=st.tabs(["Dashboard","Option Chain","AI Council","Scorecard","Data Health"])

    with tabs[0]:
        st.subheader(f"{chosen_idx} Dashboard")
        c1,c2,c3=st.columns(3); c1.metric("ATM IV",f"{m['atm_iv']:.1f}%"); c2.metric("Gamma",f"{m['gex_regime']}"); c3.metric("DTE",m["dte"])
        c4,c5,c6=st.columns(3); c4.metric("PCR",f"{m['pcr']:.2f}"); c5.metric("Bias Score",f"{m['score']}/100",m["bias"]); c6.metric("Exp Move ±",f"{m['exp_move']:,.0f}")
        st.metric("Net GEX",f"{m['net_gex']:+,.0f}",f"Gross {m['gross_gex']:+,.0f}"); st.metric("RR25",f"{m['rr_25']:+.2f}%",f"BF25 {m['bf_25']:+.2f}")
        st.metric("Zero Gamma",f"{m['zero_gamma']:,.0f}" if m["zero_gamma"] else "—",f"Crossover {m['crossover']:,.0f}" if m["crossover"] else "—")
        st.metric("Call Wall",f"{m['call_wall']:,.0f}"); st.metric("Put Wall",f"{m['put_wall']:,.0f}"); st.metric("Max Pain",f"{m['max_pain']:,.0f}")

    with tabs[1]:
        st.subheader(f"{chosen_idx} Option Chain")
        c1,c2=st.columns(2); [c.dataframe(df[[c for c in["Strike","CE_OI","PE_OI","CE_IV","PE_IV","CE_Gamma","PE_Gamma","CE_LTP","PE_LTP"]].head(20)],hide_index=True) for c in [c1,c2]]
        with c2: st.bar_chart(df.set_index("Strike")["CE_OI"].head(10))

    with tabs[2]:
        st.subheader("Groq AI Council (5-Agent Prompt)")
        if st.button("Refresh AI Analysis"): ai=run_council(m,groq_key,groq_model); st.session_state["ai"]=ai; st.rerun()
        if "error" in ai: st.error(ai["error"])
        else:
            st.success("AI Signals:"); ca,colb=st.columns(2)
            with ca: st.write("**Price Action:**",ai.get("price_action","-")); st.write("**Volatility:**",ai.get("volatility","-"))
            with colb: st.write("**Order Flow:**",ai.get("order_flow","-")); st.write("**Sentiment:**",ai.get("sentiment","-")); st.write("**Risk Officer:**",ai.get("risk_officer","-"))

    with tabs[3]:
        st.subheader("Signal Scorecard")
        if "error" not in ai:
            sig=build_signal(m,ai); c1,c2,c3=st.columns(3)
            with c1: st.metric("Signal",sig["signal"]); st.metric("Confidence",f"{sig['confidence']}%")
            with c2: st.metric("Entry",f"{sig['entry']:,.0f}"); st.metric("Target",f"{sig['target'] or '—':,.0f}" if sig['target'] else "—")
            with c3: st.metric("Stop",f"{sig['sl'] or '—':,.0f}"); st.metric("R:R",f"1:{sig['rr']:.2f}" if sig['rr'] else "n/a")
            st.info(f"Note: {sig['note'] or 'Signal validated.'}")
        else: st.warning(ai["error"])

    with tabs[4]:
        st.subheader("Data Health & API Status")
        tok=st.secrets["general"]["DHAN_ACCESS_TOKEN"]; ok="OK" if tok and tok.startswith("eyJ") else "FAIL"
        st.write(f"Dhan token: {ok}"); st.write(f"Strikes loaded: {len(df)} | Spot: {spot:,.2f} | DTE: {m['dte']}d")
        st.write(f"ATM IV: {m['atm_iv']:.1f}% | PCR: {m['pcr']:.2f} | Regime: {m['gex_regime']}")
        st.caption(f"Last refreshed: {t_ist.strftime('%H:%M:%S')} IST")

# -------------------------------------------------------
# 10. Run
# -------------------------------------------------------
if __name__=="__main__": main()
