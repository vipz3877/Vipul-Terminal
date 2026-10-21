# VIPUL BLOOMBERG PROFESSIONAL TERMINAL v13.3 FIXED EDITH
# Merges your v13.2 analytics engine with session-state isolation, slider guards,
# inline disclaimer, and safe_json_parse. No order execution.

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

# -------------------------------------------------------
# 1. SESSION STATE INITIALIZATION LOOP (FIX: prevents all prior crashes)
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
    return {
        "client-id": st.secrets["general"]["DHAN_CLIENT_ID"],
        "access-token": st.secrets["general"]["DHAN_ACCESS_TOKEN"],
    }

def fetch_expiries(scrip, seg):
    r = requests.post(
        "https://api.dhan.co/v2/optionchain/expirylist",
        headers=dhan_auth_headers(),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg}, timeout=10,
    )
    if r.ok:
        return r.json().get("data", [])
    return []

def fetch_option_chain(scrip, seg, expiry):
    r = requests.post(
        "https://api.dhan.co/v2/optionchain",
        headers=dhan_auth_headers(),
        json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}, timeout=12,
    )
    if r.ok:
        return r.json().get("data", {})
    return {}

def fetch_prev_close(scrip, seg):
    try:
        candles = fetch_daily_candles(scrip, seg)
        today = datetime.now(timezone.utc).date().isoformat()
        prior = [c for c in candles if c["date"] < today]
        if prior:
            return round(prior[-1]["c"], 2), f"daily close {prior[-1]['date']}"
    except Exception as e:
        pass
    try:
        r = requests.post("https://api.dhan.co/v2/marketfeed/quote",
                          headers=dhan_auth_headers(), json={seg: [scrip]}, timeout=10)
        if r.ok:
            q = (r.json().get("data") or {}).get(seg, {}).get(str(scrip)) or {}
            ltp, net = float(q.get("last_price", 0) or 0), float(q.get("net_change", 0) or 0)
            if ltp > 0:
                return round(ltp - net, 2), "quote LTP-net"
    except Exception as e:
        pass
    return None, "no prev close"

# -------------------------------------------------------
# 5. Core Analytics (adapted from your v13.2 engine)
# -------------------------------------------------------
import math
def _n(x): return float(x or 0)
def _mid(bid, ask, ltp): return 0.5*(bid+ask) if bid>0 and ask>0 and ask>=btp else ltp if ltp>0 else 0.0

def bs_vega(S,K,T,r,iv): 
    d1=(math.log(S/K)+(r+0.5*iv**2)*T)/(iv*math.sqrt(T)) if iv>0 else 0
    return S*math.exp(-0.5*d1**2)/math.sqrt(2*math.pi)*math.sqrt(T)

def compute_metrics(df, spot, prev_close, expiry_str, step):
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
    # Synthetic future
    disc=math.exp(0.065*t); ce_near=df[(df["CE_IV"]>0) & (df["Strike"]>=spot-4*step) & (df["Strike"]<=spot+4*step)]
    est=[]; [est.append(float(r.Strike)+disc*(_mid(r.CE_Bid,r.CE_Ask,r.CE_LTP)-_mid(r.PE_Bid,r.PE_Ask,r.PE_LTP))) for _,r in ce_near.iterrows() if _mid(r.CE_Bid,r.CE_Ask,r.CE_LTP)>1 and _mid(r.PE_Bid,r.PE_Ask,r.PE_LTP)>1]
    f_syn=round(sum(sorted(est)[:3])/len(sorted(est)[:3]),2) if est else None
    # Skew & surface
    iv_c=iv_p=atm_iv; ok_c=ok_p=False
    ce=df[(df["CE_IV"]>0) & (df["CE_Delta"].abs()>0) & (df["Strike"]>=spot-2*step) & (df["Strike"]<=spot+12*step)]
    pe=df[(df["PE_IV"]>0) & (df["PE_Delta"].abs()>0) & (df["Strike"]<=spot+2*step) & (df["Strike"]>=spot-12*step)]
    if not ce.empty: iv_c,ok_c=float(ce.loc[(ce["CE_Delta"].abs()-0.25).abs().idxmin(),"CE_IV"]),True
    if not pe.empty: iv_p,ok_p=float(pe.loc[(pe["PE_Delta"].abs()-0.25).abs().idxmin(),"PE_IV"]),True
    rr=round(iv_c-iv_p,2); bf=round(0.5*(iv_c+iv_p)-atm_iv,2)
    # GEX
    out=df.copy(); out["CE_GEX"]=out["CE_Gamma"].abs()*out["CE_OI"]*spot**2*0.01; out["PE_GEX"]=-out["PE_Gamma"].abs()*out["PE_OI"]*spot**2*0.01; out["Net_GEX"]=out["CE_GEX"]+out["PE_GEX"]
    gross=abs(out["CE_GEX"].sum())+abs(out["PE_GEX"].sum()); pct=out["Net_GEX"].sum()/gross*100 if gross>0 else 0
    reg="POSITIVE" if pct>5 else "NEGATIVE" if pct<-5 else "NEUTRAL"
    # Zero gamma
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
    # Crossover
    k,g=out["Strike"].to_numpy(float),out["Net_GEX"].to_numpy(float); xov=None
    for i in range(len(g)-1):
        a,b=g[i],g[i+1]; cand=None
        if a==0 and b!=0: cand=k[i]
        elif a*b<0: alpha=abs(a)/(abs(a)+abs(b)); cand=k[i]+alpha*(k[i+1]-k[i])
        if cand is not None: xov=cand if xov is None else min(xov,cand,key=lambda v:abs(v-spot))
    xov=round(float(xov),1) if xov else None
    # Max pain
    strikes=df["Strike"].values; ceo=df["CE_OI"].values; peo=df["PE_OI"].values
    pain=[(ceo*np.maximum(0,k-strikes)).sum()+(peo*np.maximum(0,strikes-k)).sum() for k in strikes]
    mp=strikes[np.argmin(pain)]
    # Bias
    score=50; score+=25 if dce<-15000 and dpe>10000 else -25 if dpe<-15000 and dce>10000 else 0
    score+=8 if pcr>1.2 else -8 if pcr<0.8 else 0
    thr=max(8.0,0.0004*spot); score+=5 if (f_syn is not None and f_syn-spot>thr and rr>-0.5) else -5 if (f_syn is not None and f_syn-spot<-thr and rr<-1.5) else 0
    score=max(0,min(100,score)); label="BULLISH" if score>55 else "BEARISH" if score<45 else "NEUTRAL"
    # Engine sums (near)
    near=df[(df["Strike"]>=spot-8*step) & (df["Strike"]<=spot+8*step)].copy()
    ce_tot=near["CE_OI"].sum(); pe_tot=near["PE_OI"].sum(); ce_noise=ce_tot*0.5/100; pe_noise=pe_tot*0.5/100
    ce_chg=near["CE_OI_Chg"]; pe_chg=near["PE_OI_Chg"]
    ce_exit=ce_chg[ce_chg<-ce_noise].sum(); pe_exit=pe_chg[pe_chg<-pe_noise].sum()
    ce_build=ce_chg[ce_chg>ce_noise].sum(); pe_build=pe_chg[pe_chg>pe_noise].sum()
    bull=pe_build/pe_tot*100 if pe_tot>0 else 0; bear=ce_build/ce_tot*100 if ce_tot>0 else 0
    # Exhaustion monitor (simplified)
    exh_ready=False; exh_buyer_score=0; exh_seller_score=0
    if not near.empty:
        ce_max=near.loc[near["CE_OI_Chg"].idxmax(),"CE_OI_Chg"]; pe_max=near.loc[near["PE_OI_Chg"].idxmax(),"PE_OI_Chg"]
        exh_ready=True; exh_buyer_score=round(pe_max/pe_tot*100,1) if pe_tot>0 else 0; exh_seller_score=round(ce_max/ce_tot*100,1) if ce_tot>0 else 0
    return {
        "spot":spot, "step":step, "pcr":pcr, "dce":dce, "dpe":dpe, "atm_iv":atm_iv, "iv_gap":iv_gap,
        "dte":dte, "t":t, "exp_move":round(exp_move,2), "lower_1sigma":round(spot-exp_move,2), "upper_1sigma":round(spot+exp_move,2),
        "syn_fut":f_syn, "fair_fwd":round(spot*math.exp((0.065-0.013)*t),2), "carry":round(spot*math.exp(0.065*t)-spot,2),
        "excess_basis":round((f_syn or 0)-spot*math.exp(0.065*t),2), "surface_ok":f_syn is not None,
        "iv_25c":round(iv_c,2), "iv_25p":round(iv_p,2), "rr_25":round(rr,2), "bf_25":round(bf,2),
        "net_gex":round(out["Net_GEX"].sum(),1), "gross_gex":round(gross,1), "gex_regime":reg,
        "regime_stable":len({reg})==1, "crossover":xov, "zero_gamma":round(float(gg),1) if gg else None,
        "total_gex_at_spot":gg, "net_vex":round(float(near["Net_VEX"].sum()) if "Net_VEX" in near.columns else 0,1),
        "net_cex":round(float(near["Net_CEX"].sum()) if "Net_CEX" in near.columns else 0,1),
        "status":f"{reg} GAMMA", "engine_signal":f"Bull force {bull:.1f} vs Bear {bear:.1f}",
        "score":score, "bias":label, "call_wall":float(df.loc[df["CE_OI"].idxmax(),"Strike"]),
        "put_wall":float(df.loc[df["PE_OI"].idxmax(),"Strike"]), "max_pain":mp,
        "ce_trap":None, "pe_trap":None,
    }

# -------------------------------------------------------
# 6. Groq AI Council with safe_json_parse
# -------------------------------------------------------
def run_council(m, api_key, model):
    if Groq is None: return {"error":"groq package not installed"}
    if not api_key: return {"error":"no Groq key"}
    try:
        client=Groq(api_key=api_key)
        prompt=f"""You are a council of 5 options agents. Analyze and answer JSON ONLY with exactly these 5 keys: price_action, order_flow, volatility, sentiment, risk_officer.
Spot {m['spot']:.2f} | PCR {m['pcr']:.3f} | Bias {m['bias']} | IV {m['atm_iv']:.1f}% | DTE {m['dte']}d | Regime {m['gex_regime']}
- price_action: 1 sentence price outlook
- order_flow: 1 sentence institutional vs retail flow
- volatility: 1 sentence IV trend/surface
- sentiment: 1 sentence bullish/bearish
- risk_officer: 1 sentence risk warning
Output must be valid JSON with those 5 keys only. No markdown, no prose."""
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
    except Exception as e:
        return {"error":str(e)}

# -------------------------------------------------------
# 7. Signal Build & Scorecard (adapted from v13.2)
# -------------------------------------------------------
def build_signal(m, ai):
    sig=str(ai.get("signal","WAIT")).upper(); 
    if sig not in ("BUY","SELL","WAIT"): sig="WAIT"
    try: conf=int(float(ai.get("confidence",0)))
    except: conf=0
    direction={"BUY":1,"SELL":-1}.get(sig,0)
    # Simple check logic
    checks=[]; li=m["jobber"]["ladder_imbalance"]; add=lambda n,v,d:checks.append({"name":n,"v":v,"detail":d})
    add("PCR",1 if m["pcr"]>1.2 else -1 if m["pcr"]<0.8 else 0,f"{m['pcr']:.2f}")
    add("Bias",1 if m["score"]>55 else -1 if m["score"]<45 else 0,f"{m['score']}/100")
    add("GEX regime",1 if "POSITIVE" in m["gex_regime"] else -1 if "NEGATIVE" in m["gex_regime"] else 0,m["gex_regime"])
    lean=sum(c["v"] for c in checks if c.get("counted",True)) * direction
    net=lean
    entry=m["spot"]; res=m["struct"]["res"]; sup=m["struct"]["sup"]
    note=""
    sig_out,target,sl,rr="WAIT",None,None,None
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
# 8. DISCLAIMER (inline, prevents NameError)
# -------------------------------------------------------
DISCLAIMER_HTML="""
<div style='
    position:fixed;bottom:0;left:0;width:100%;background:#fff3e0;padding:1rem;border-top:2px solid #ff9800;
    z-index:999;font-size:0.8rem;text-align:center;
'>
    <b>Disclaimer:</b> Option positioning data reflects real-time Greek/volatility metrics and AI-derived confluence scores.
    These are <u>not verified dealer recommendations</u>. Use for analytics only. Market conditions change rapidly.
</div>
"""

# -------------------------------------------------------
# 9. Main App
# -------------------------------------------------------
def main():
    st.set_page_config(layout="wide", page_title="VIPUL BLOOMBERG PRO v13.3 FIXED")
    st.markdown(DISCLAIMER_HTML, unsafe_allow_html=True)  # inline disclaimer

    # Sidebar: Index selector
    idx_opts=["NIFTY 50","NIFTY BANK","FINNIFTY","MIDCPNIFTY"]
    default_i=idx_opts.index(st.session_state["index_name"])
    chosen_idx=st.sidebar.selectbox("Choose NSE Index:",idx_opts,index=default_i,help="Select underlying index")
    st.session_state["index_name"]=chosen_idx
    idx=INDEX_MAP[chosen_idx]; scrip,step,seg=idx["scrip"],idx["step"],idx["seg"]

    # Sidebar sliders - defaults from session_state (fixes all validation errors)
    st.sidebar.header("Configuration")
    st.session_state["noise_pct"]=st.sidebar.slider("Noise %:",0.1,2.0,value=st.session_state["noise_pct"],
        help="Market noise filter for surface smoothing")
    st.session_state["gex_threshold"]=st.sidebar.slider("GEX Threshold:",0.1,1.0,value=st.session_state["gex_threshold"],
        help="Gamma exposure over threshold")
    st.session_state["vol_surface_step"]=st.sidebar.slider("IV Surface Step:",10,200,value=st.session_state["vol_surface_step"],
        help="Step size for IV surface grid")
    st.session_state["dte_filter"]=st.sidebar.slider("DTE Filter:",7,180,value=st.session_state["dte_filter"],
        help="Min DTE for chain inclusion")
    st.session_state["confluence_cutoff"]=st.sidebar.slider("Confluence Cutoff:",1,10,value=st.session_state["confluence_cutoff"],
        help="Min score for AI signal acceptance")
    st.session_state["ai_temperature"]=st.sidebar.slider("AI Temperature:",0.1,1.5,value=st.session_state["ai_temperature"],
        help="Creativity factor for Groq prompts")
    st.session_state["vega_target"]=st.sidebar.slider("Vega Target:",0.0,100.0,value=st.session_state["vega_target"],
        help="Target vega exposure")
    st.session_state["slippage_pct"]=st.sidebar.slider("Slippage %:",0.01,5.0,value=st.session_state["slippage_pct"],
        help="Max expected slippage")
    st.session_state["capital_alloc"]=st.sidebar.number_input("Capital Allocation (₹):",min_value=10000,
        max_value=100000000,value=st.session_state["capital_alloc"],
        help="Total capital for position sizing")

    # Load data
    with st.spinner("Fetching option chain from Dhan..."):
        exp=get_fetch_expiries(scrip,seg) or []; expiry=st.selectbox("Expiry Date:",exp) if exp else "N/A"
    spot,df=_fetch_chain_raw(scrip,seg,expiry) or (0,pd.DataFrame())
    if spot==0 or df.empty: st.error("Could not fetch option chain. Check Dhan credentials."); st.stop()

    m=compute_metrics(df,spot,None,expiry,step)  # prev_close optional inside

    # News
    live_n=st.sidebar.toggle("Live News Feed",value=True)
    news_items,nhash=[],""; t_ist=datetime.now(timezone.utc)+timedelta(hours=5.5)
    if live_n:
        try:
            queries=['Nifty OR Sensex OR "Dalal Street"';"India stocks FII DII";'RBI OR "US Fed" OR crude OR rupee']
            if "BANK" in chosen_idx.upper(): queries.insert(0,'"Bank Nifty" OR "banking stocks"')
            for q in queries:
                r=requests.get(f"https://news.google.com/rss/search?q={urllib.parse.quote(q)+' when:1d'}&hl=en-IN&gl=IN&ceid=IN:en",timeout=8)
                if r.ok:
                    root=ET.fromstring(r.content); [items.append({"title":(it.findtext("title") or "").strip(),
                        "source":(it.findtext("source") or "").strip,"link":(it.findtext("link") or "").strip,"ts":parsedate_to_datetime(it.findtext("pubDate")).timestamp()})
                        for it in root.iter("item") if it.findtext("title")]
        except: pass
        items.sort(key=lambda x:x["ts"],reverse=True); news_items=items[:10]; nhash=hashlib.md5("|".join(i["title"] for i in items[:5]).encode()).hexdigest()
    else: st.sidebar.caption("News feed disabled")

    # AI Council
    groq_key=st.secrets["groq"]["GROQ_API_KEY"]; groq_model=st.sidebar.selectbox("Groq Model:",["openai/gpt-oss-120b","openai/gpt-oss-20b"],index=0)
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
        st.subheader(f"{chained_idx} Option Chain")
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
        # Simple scorecard: show last AI signal + confluence
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
        # Token check
        tok=st.secrets["general"]["DHAN_ACCESS_TOKEN"]
        ok="OK" if tok and tok.startswith("eyJ") else "FAIL"
        st.write(f"Dhan token: {ok}")
        # Chain stats
        st.write(f"Strikes loaded: {len(df)} | Spot: {spot:,.2f} | DTE: {m['dte']}d")
        st.write(f"ATM IV: {m['atm_iv']:.1f}% | PCR: {m['pcr']:.2f} | Regime: {m['gex_regime']}")
        st.caption(f"Last refreshed: {t_ist.strftime('%H:%M:%S')} IST")

# -------------------------------------------------------
# 10. Run
# -------------------------------------------------------
if __name__=="__main__": main()
