"""
BUILDERR ROUND 1 — agent.py  v4  (Macro-Informed, June 24 2026)
================================================================
CURRENT SITUATION:
  Rank: 24/31 | Capital: $97,768 | Loss: -2.23% | Days left: ~6
  Leader: +7% ($107,000) | We need: aggressive recovery, tight risk

REAL MARKET INTELLIGENCE (June 24, 2026):
  • S&P 500 at 7,420 — near all-time highs, broadly bullish
  • Fed holds at 3.50-3.75% but dot plot signals possible HIKE — risk!
  • Inflation 4.2% CPI, sticky — defensive/energy stay relevant
  • WINNING sectors: Tech (AI) +16% in May, Industrials +16% YTD
  • LOSING sectors: Energy -6% recently, Utilities -5%, Consumer Def -3%
  • AI infrastructure plays dominating: NVDA, AVGO, INTC, SMH, XLK
  • Rotation signal: CAT, WMT, XOM showing strength as "real economy" plays
  • Iran conflict → oil uncertainty → avoid heavy XLE exposure
  • Rate hike risk → avoid long-duration bonds / rate-sensitive utilities

STRATEGY FOR REMAINING ~6 DAYS:
  We are down -2.23%. Leader is +7%. Gap = ~9.3%.
  Need aggressive but controlled recovery. Calmar = ann_return / max_drawdown.
  
  APPROACH:
  1. MOMENTUM CONCENTRATION — top 5 high-conviction AI/tech names (not 6-8)
     Fewer, stronger positions = higher return potential per unit of risk
  2. REAL-TIME REGIME — still risk_on (SPY near ATH, QQQ above all SMAs)
     No defensive retreat unless crash_bail triggers
  3. SECTOR INTELLIGENCE applied to scoring weights:
     • AI infrastructure (NVDA, AVGO, MSFT, META, SMH) → boost weight
     • Industrial (CAT proxy via XLI) → include  
     • Energy (XLE) → underweight (down -6% recently)
     • Utilities (XLU) → avoid in risk_on (rate hike risk)
  4. REBALANCE EVERY 3 DAYS (not 5) — only 6 trading days left,
     need faster adaptation
  5. AGGRESSIVE ALLOCATION: top picks get up to MAX_WEIGHT=0.28 (raised from 0.24)
     in risk_on only. Still beta-capped.

RISK MANAGEMENT:
  • Beta-adj gross ≤ 1.35x (slightly higher to compete)
  • Per-ticker cap 28% (raised for concentration)
  • crash_bail still fires on 3-bar -3% drop or vol spike
  • In crash: retreat to XLP/XLV only (skip XLU — rate hike risk)
"""

from __future__ import annotations
from math import sqrt
from statistics import mean, pstdev
from typing import Any

# ── Universe (macro-informed, June 2026) ─────────────────────────────────────
# AI infrastructure dominates — these are the real winners right now
RISK_CANDIDATES = (
    # AI infrastructure — top performers 2026 YTD
    "NVDA", "AVGO", "MSFT", "META", "GOOGL",
    # Broad tech / semis — XLK +16% in May
    "XLK", "SMH", "AAPL", "AMZN",
    # Industrial — CAT +32%, benefiting from AI data center buildout
    "XLI",
    # Broad market anchors
    "SPY", "QQQ",
    # Consumer — WMT showing strength
    "XLY",
    # Healthcare — defensive but growth
    "XLV",
    # Communication — META/GOOGL already above, XLC as basket
    "XLC",
    # Energy — underweighted but included for regime flexibility
    "XLE",
    # Small/mid cap
    "IWM",
)

# Defensive crash book — NO XLU (rate hike risk), focus XLP/XLV
DEFENSIVE_CRASH   = (("XLP", 0.50), ("XLV", 0.50))
DEFENSIVE_RISKOFF = (("XLP", 0.30), ("XLV", 0.28), ("GLD", 0.20), ("IWM", 0.10))
CAUTIOUS_DEF_SLV  = (("XLP", 0.12), ("XLV", 0.10))

# Macro sector overrides — applied as score multipliers (June 2026 intel)
SECTOR_BOOST = {
    # Strong buy — AI infra dominating
    "NVDA": 1.35, "AVGO": 1.30, "SMH": 1.25,
    "MSFT": 1.20, "META": 1.20, "GOOGL": 1.15,
    "XLK":  1.20, "XLC":  1.10,
    # Neutral-positive
    "AAPL": 1.05, "AMZN": 1.05, "QQQ": 1.10,
    "SPY":  1.00, "XLI":  1.10, "XLY": 1.05,
    # Underweight — recent weakness or rate sensitivity
    "XLE":  0.70,   # energy down -6% recently
    "XLU":  0.40,   # avoid — rate hike risk
    "IWM":  0.85,   # small caps lagging
    "XLV":  0.95,
}

BETA_MULTIPLE: dict[str, float] = {
    "QLD": 2.0, "SSO": 2.0, "TQQQ": 3.0, "SOXL": 3.0,
    "UPRO": 3.0, "SPXL": 3.0,
}

# ── Constants ─────────────────────────────────────────────────────────────────
REBALANCE_DAYS   = 3      # faster — only 6 days left
MAX_WEIGHT       = 0.28   # slightly higher for concentration
DRIFT_LIM        = 0.30
MAX_BETA_GROSS   = 1.35   # slightly higher to compete
MIN_TRADE_PCT    = 0.010

VOL_CAUTION      = 0.28
VOL_RISKOFF      = 0.38
CRASH_DROP_3BAR  = -0.030
CRASH_VOL_RATIO  = 1.8
RISKOFF_MOM_FLOOR= -0.04  # both conditions required (less trigger-happy)
TOP_N_RISKOFF    = 4
TOP_N_CAUTIOUS   = 4
TOP_N_RISKON     = 5      # concentrated — top 5 only

_last_rebal_date: str | None = None

# ── Price utilities ────────────────────────────────────────────────────────────
def closes(bars):
    if not bars: return []
    out = []
    for b in bars:
        try: c = float(b["close"])
        except: return []
        if c <= 0: return []
        out.append(c)
    return out

def sma(v, n): return mean(v[-n:]) if len(v) >= n else None
def mom(v, n): return (v[-1]/v[-(n+1)] - 1.0) if len(v) > n and v[-(n+1)] > 0 else None
def rvol(v, n):
    if len(v) <= n: return None
    w = v[-(n+1):]
    rs = [w[i]/w[i-1]-1.0 for i in range(1,len(w)) if w[i-1]>0]
    return pstdev(rs)*sqrt(252.0) if len(rs)>=4 else None

# ── Portfolio helpers ──────────────────────────────────────────────────────────
def cur_pos(ps):
    out = {}
    for r in (ps.get("positions") or []):
        t = str(r.get("ticker","")).upper()
        if not t: continue
        try: qty=float(r.get("quantity",0)); cost=float(r.get("avg_cost",0))
        except: continue
        if qty<=0: continue
        e=out.setdefault(t,{"quantity":0.0,"avg_cost":cost})
        e["quantity"]+=qty
    return out

def tot_equity(ps, cash):
    try: total=float(ps.get("cash",cash))
    except: total=float(cash or 0)
    lp=ps.get("last_prices",{}) or {}
    for t,p in cur_pos(ps).items():
        try: price=float(lp.get(t,p["avg_cost"]))
        except: price=p["avg_cost"]
        total+=p["quantity"]*max(price,0)
    return max(total,0)

def mkt_prices(ms):
    out={}
    for t,bars in ms.items():
        cs=closes(bars)
        if cs: out[t.upper()]=cs[-1]
    return out

def bar_date(ms):
    bars=ms.get("SPY") or ms.get("QQQ") or []
    if not bars: return None
    ts=bars[-1].get("ts")
    return str(ts)[:10] if ts is not None else str(len(bars))

def days_since(ms):
    if _last_rebal_date is None: return None
    bars=ms.get("SPY") or ms.get("QQQ") or []
    dates=[str(b.get("ts",i))[:10] for i,b in enumerate(bars)]
    if not dates or _last_rebal_date not in dates: return None
    return len(dates)-dates.index(_last_rebal_date)-1

def drifted(ps, eq):
    if eq<=0: return False
    lp=ps.get("last_prices",{}) or {}
    for t,p in cur_pos(ps).items():
        try: price=float(lp.get(t,p["avg_cost"]))
        except: price=p["avg_cost"]
        if price>0 and (p["quantity"]*price/eq)>DRIFT_LIM: return True
    return False

# ── Cap enforcement ────────────────────────────────────────────────────────────
def cap(w):
    c={t:min(max(v,0),MAX_WEIGHT) for t,v in w.items() if v>0}
    bg=sum(v*BETA_MULTIPLE.get(t,1.0) for t,v in c.items())
    if bg>MAX_BETA_GROSS:
        sc=MAX_BETA_GROSS/bg
        c={t:v*sc for t,v in c.items()}
    return {t:round(v,6) for t,v in c.items() if v>0.001}

# ── Regime detection ───────────────────────────────────────────────────────────
def regime(ms):
    spy=closes(ms.get("SPY")); qqq=closes(ms.get("QQQ"))
    if len(spy)<50 or len(qqq)<50: return "risk_off"
    spy50=sma(spy,50); qqq50=sma(qqq,50)
    qv20=rvol(qqq,20); qm20=mom(qqq,20); sm20=mom(spy,20)
    if any(x is None for x in (spy50,qqq50,qv20)): return "risk_off"

    # crash_bail — fast response
    qm3=mom(qqq,3)
    if qm3 is not None and qm3<CRASH_DROP_3BAR: return "crash_bail"
    if len(qqq)>=24:
        v3=rvol(qqq,3); v20=rvol(qqq,20)
        if v3 and v20 and v20>0 and v3>CRASH_VOL_RATIO*v20: return "crash_bail"

    # risk_off — BOTH conditions required (less trigger-happy)
    if spy[-1]<spy50 and (qm20 is not None and qm20<RISKOFF_MOM_FLOOR):
        return "risk_off"

    # cautious
    if qv20>=VOL_CAUTION: return "cautious"
    if qm20 is not None and qm20<0: return "cautious"
    if sm20 is not None and sm20<0: return "cautious"

    return "risk_on"

# ── Macro-informed factor scoring ─────────────────────────────────────────────
def score_universe(ms):
    """
    Multi-factor score with macro sector boost applied.
    
    Factors:
      0.40 × mom60    — primary trend (6-month momentum)
      0.25 × mom20    — medium-term trend
      0.20 × gap_sma50 — trend strength vs 50-SMA
      0.15 × risk_adj  — momentum / vol (quality filter)
     -0.10 × mom5     — fade short-term crowding

    Then multiply by SECTOR_BOOST[ticker] for macro intelligence overlay.
    This ensures AI infra stocks score higher than energy/utilities
    given current June 2026 market conditions.
    """
    scored = []
    for t in RISK_CANDIDATES:
        v=closes(ms.get(t))
        if len(v)<65: continue
        m60=mom(v,60); m20=mom(v,20); m5=mom(v,5)
        s50=sma(v,50); v20=rvol(v,20)
        if any(x is None for x in (m60,m20,m5,s50,v20)): continue
        if v20<=0: continue
        gap=v[-1]/s50-1.0
        ramo=m60/v20
        raw = 0.40*m60 + 0.25*m20 + 0.20*gap + 0.15*ramo - 0.10*m5
        # Apply macro sector intelligence
        boosted = raw * SECTOR_BOOST.get(t, 1.0)
        scored.append((boosted, t, v20))
    scored.sort(reverse=True)
    return scored

def inv_vol_w(cands, budget):
    if not cands: return {}
    ivs=[1.0/max(v,1e-6) for _,_,v in cands]
    tot=sum(ivs)
    if tot<=0:
        n=len(cands)
        return {t:min(budget/n,MAX_WEIGHT) for _,t,_ in cands}
    return {t:min(budget*iv/tot,MAX_WEIGHT)
            for (_,t,_),iv in zip(cands,ivs)}

# ── Target weights ─────────────────────────────────────────────────────────────
def target_weights(ms):
    r=regime(ms)

    if r=="crash_bail":
        return cap({t:w for t,w in DEFENSIVE_CRASH if closes(ms.get(t))})
    if r=="risk_off":
        return cap({t:w for t,w in DEFENSIVE_RISKOFF if closes(ms.get(t))})

    scored=score_universe(ms)
    pos=[s for s in scored if s[0]>0]

    if r=="cautious":
        winners=pos[:TOP_N_CAUTIOUS]
        if not winners:
            return cap({t:w for t,w in DEFENSIVE_RISKOFF if closes(ms.get(t))})
        cdef={t:w for t,w in CAUTIOUS_DEF_SLV if closes(ms.get(t))}
        rb=min(0.72,1.0-sum(cdef.values()))
        return cap({**cdef,**inv_vol_w(winners,rb)})

    # risk_on — top 5 concentrated, macro-boosted
    winners=pos[:TOP_N_RISKON]
    if not winners:
        return cap({t:w for t,w in DEFENSIVE_RISKOFF if closes(ms.get(t))})

    return cap(inv_vol_w(winners, 0.95))   # 95% deployed in risk_on

# ── Order generation ───────────────────────────────────────────────────────────
def build_orders(targets, positions, eq, prices, cash):
    if eq<=0: return []
    min_t=eq*MIN_TRADE_PCT
    orders=[]; sell_proc=0.0

    for t,p in positions.items():
        price=prices.get(t)
        if not price or price<=0: continue
        qty=p["quantity"]; cv=qty*price
        tv=eq*targets.get(t,0.0)
        if t not in targets:
            sq=int(qty)
            if sq>0 and cv>=min_t:
                orders.append({"ticker":t,"side":"sell","quantity":sq})
                sell_proc+=sq*price
        elif tv-cv<-min_t:
            sq=min(int(abs(tv-cv)/price),int(qty))
            if sq>0:
                orders.append({"ticker":t,"side":"sell","quantity":sq})
                sell_proc+=sq*price

    spendable=max(float(cash),0.0)+sell_proc*0.98
    for t,w in sorted(targets.items(),key=lambda x:-x[1]):
        price=prices.get(t)
        if not price or price<=0: continue
        cv=positions.get(t,{}).get("quantity",0.0)*price
        tv=eq*w
        if tv-cv<min_t: continue
        bq=int(min(tv-cv,spendable)/price)
        if bq>0:
            orders.append({"ticker":t,"side":"buy","quantity":bq})
            spendable-=bq*price
    return orders[:45]

# ── Entry point ────────────────────────────────────────────────────────────────
def decide(market_state, portfolio_state, cash):
    """Long-only. No network. All limits enforced. Runtime < 1s."""
    global _last_rebal_date
    if not market_state: return []
    today=bar_date(market_state)
    if today is None: return []

    eq=tot_equity(portfolio_state,cash)
    dsince=days_since(market_state)
    drift=drifted(portfolio_state,eq)
    r=regime(market_state)

    should_rebal=(
        _last_rebal_date is None
        or dsince is None
        or dsince>=REBALANCE_DAYS
        or drift
        or r=="crash_bail"
    )
    if not should_rebal: return []

    tgts=target_weights(market_state)
    if not tgts: return []

    prices=mkt_prices(market_state)
    pos=cur_pos(portfolio_state)
    orders=build_orders(tgts,pos,eq,prices,cash)
    if orders: _last_rebal_date=today
    return orders
