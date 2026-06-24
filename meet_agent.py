import math
import statistics

# ■■ Configuration ■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
LOOKBACKS    = [3, 7, 14]       # Fast: 3-tick, 7-tick, 14-tick momentum
SKIP_DAYS    = 0
TOP_N        = 6                # Hold top 6 longs and top 6 shorts
MAX_POS      = 0.16             # Max 16% of the account per asset (keeps us safely under the 30% concentration cap)
REBAL_DAYS   = 15               # Rebalance every 15 runs to prevent rank-churn

_state = {
    'day_count': 0,
    'last_rebal_day': -999,
    'peak_equity': 0,
    'high_water_marks': {},
    'low_water_marks': {},
    'entry_prices': {},
    'sold_cooldown': {},  # {ticker: iterations_remaining} to prevent wash trades
    'circuit_breaker': 0,
}

def compute_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains = []
    losses = []
    window = prices[-(period + 1):]
    for i in range(1, len(window)):
        delta = window[i] - window[i-1]
        if delta > 0:
            gains.append(delta)
        elif delta < 0:
            losses.append(-delta)
    avg_gain = sum(gains) / len(gains) if gains else 0
    avg_loss = sum(losses) / len(losses) if losses else 0.001
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    return 100 - (100 / (1 + rs))

def compute_bollinger(prices, period=20, std_mult=2.0):
    if len(prices) < period: return 0, 0, 0, 0.5
    window = prices[-period:]
    sma = sum(window) / period
    std = statistics.stdev(window) if len(window) > 1 else 0
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    current = prices[-1]
    pct_b = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return upper, sma, lower, pct_b

def compute_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal: return 0, 0, 0
    def ema(data, period):
        alpha = 2.0 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(alpha * data[i] + (1 - alpha) * result[i-1])
        return result
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], histogram

def decide(market_state: dict, portfolio_state: dict, cash: float, tradeable_equity: float = None) -> list:
    orders = []
    _state['day_count'] += 1
    
    # Active universe is dynamically extracted from market_state keys
    active_universe = list(market_state.keys())
    
    # Determine risk regime (Risk OFF if SPY is below its 200-day moving average)
    spy_bars = market_state.get('SPY', [])
    spy_closes = [float(b['close']) for b in spy_bars]
    is_risk_off = False
    if len(spy_closes) >= 200:
        spy_sma200 = sum(spy_closes[-200:]) / 200
        if spy_closes[-1] < spy_sma200 * 0.98: # 2% buffer to avoid whipsaw
            is_risk_off = True

    # Use Mean Reversion if risk-off (hunting deep crashes), else Momentum
    strategy = 'mean_reversion' if is_risk_off else 'momentum'

    prices = portfolio_state.get('last_prices', {})
    positions = portfolio_state.get('positions', [])
    
    pos_map = {p['ticker']: p for p in positions}

    def closes(ticker, n=None):
        bars = market_state.get(ticker, [])
        c = [float(b['close']) for b in bars]
        return c[-n:] if n else c

    def compute_volatility(ticker, n=14):
        bars = market_state.get(ticker, [])
        if len(bars) < 2: return 0.05 # Default 5%
        c = [float(b['close']) for b in bars[-n:]]
        pct_changes = [(c[i] - c[i-1])/c[i-1] for i in range(1, len(c))]
        if not pct_changes: return 0.05
        return statistics.stdev(pct_changes) if len(pct_changes) > 1 else 0.05

    current_equity = cash + sum(p['quantity'] * prices.get(p['ticker'], 0) for p in positions)

    # 2. Global Trailing Stop Loss & Take Profit
    for p in positions:
        t = p['ticker']
        curr_price = prices.get(t, 0)
        entry_price = _state['entry_prices'].get(t, curr_price)
        qty = p['quantity']
        
        if curr_price > 0:
            if qty > 0: # LONG POSITION
                if t not in _state['high_water_marks'] or curr_price > _state['high_water_marks'][t]:
                    _state['high_water_marks'][t] = curr_price
                
                current_vol = compute_volatility(t)
                stop_drop = max(0.03, min(0.15, current_vol * 2.5))
                stop_threshold = 1.0 - stop_drop
                
                tp_gain = max(0.015, min(0.05, current_vol * 1.5))
                take_profit_threshold = 1.0 + tp_gain 
                
                if curr_price > entry_price * take_profit_threshold:
                    orders.append({'ticker': t, 'side': 'sell', 'quantity': qty})
                    if t in _state['high_water_marks']: del _state['high_water_marks'][t]
                    if t in _state['entry_prices']: del _state['entry_prices'][t]
                    _state['sold_cooldown'][t] = 15
                    continue
                    
                if curr_price < _state['high_water_marks'][t] * stop_threshold:
                    orders.append({'ticker': t, 'side': 'sell', 'quantity': qty})
                    if t in _state['high_water_marks']: del _state['high_water_marks'][t]
                    if t in _state['entry_prices']: del _state['entry_prices'][t]
                    _state['sold_cooldown'][t] = 15
                    continue

    # Decrement cooldowns
    expired = [k for k, v in _state['sold_cooldown'].items() if v <= 0]
    for k in expired:
        del _state['sold_cooldown'][k]
    for k in list(_state['sold_cooldown'].keys()):
        _state['sold_cooldown'][k] -= 1

    # 3. PORTFOLIO CIRCUIT BREAKER (Challenge Safe Mode)
    peak_equity = _state.get('peak_equity', current_equity)
    if current_equity > peak_equity:
        _state['peak_equity'] = current_equity
        peak_equity = current_equity

    if current_equity < peak_equity * 0.95: # 5% drawdown triggers full portfolio liquidation
        if _state.get('circuit_breaker', 0) <= 0:
            _state['circuit_breaker'] = 15 # Hide in cash
            for p in positions:
                t = p['ticker']
                qty = p['quantity']
                side = 'sell' if qty > 0 else 'buy_to_cover'
                if not any(o['ticker'] == t for o in orders):
                    orders.append({'ticker': t, 'side': side, 'quantity': abs(qty)})
                    if t in _state['high_water_marks']: del _state['high_water_marks'][t]
                    if t in _state['entry_prices']: del _state['entry_prices'][t]
            return orders

    dynamic_max_pos = MAX_POS
    if _state.get('circuit_breaker', 0) > 0:
        _state['circuit_breaker'] -= 1
        dynamic_max_pos = MAX_POS * 0.10 # Reinvest a tiny fraction to catch falling knives!

    # Only rebalance every REBAL_DAYS
    if _state['day_count'] - _state['last_rebal_day'] < REBAL_DAYS:
        return orders

    target_allocs = {}

    if strategy == 'momentum':
        scores_long = {}
        for ticker in active_universe:
            if ticker in _state['sold_cooldown']: continue
            c = closes(ticker)
            if len(c) > LOOKBACKS[-1] + SKIP_DAYS:
                recent = c[-SKIP_DAYS-1]
                ret1 = (recent / c[-LOOKBACKS[0] - SKIP_DAYS]) - 1
                ret2 = (recent / c[-LOOKBACKS[1] - SKIP_DAYS]) - 1
                ret3 = (recent / c[-LOOKBACKS[2] - SKIP_DAYS]) - 1
                mom_score = (ret1 * 0.4) + (ret2 * 0.3) + (ret3 * 0.3)
                
                composite = mom_score * 10
                rsi = compute_rsi(c)
                if rsi < 30: composite -= 1.0
                elif rsi < 50: composite += 1.5
                elif rsi < 70: composite += 0.5
                else: composite -= 2.0
                
                _, _, macd_hist = compute_macd(c)
                if macd_hist > 0: composite += 1.5
                else: composite -= 0.5
                
                _, _, _, pct_b = compute_bollinger(c)
                if pct_b < 0.2: composite += 2.0
                elif pct_b < 0.5: composite += 0.5
                elif pct_b > 0.9: composite -= 1.0

                if composite > 0:
                    scores_long[ticker] = composite
                    
        top_longs = sorted(scores_long.keys(), key=lambda k: scores_long[k], reverse=True)[:TOP_N]
        for t in top_longs:
            target_allocs[t] = dynamic_max_pos

    elif strategy == 'mean_reversion':
        for p in positions:
            t = p['ticker']
            rsi = compute_rsi(closes(t))
            if rsi > 70:
                if not any(o['ticker'] == t for o in orders):
                    orders.append({'ticker': t, 'side': 'sell', 'quantity': p['quantity']})
                
        rsi_scores = {}
        for ticker in active_universe:
            if ticker in pos_map: continue
            rsi = compute_rsi(closes(ticker))
            if rsi < 25:  # Oversold dips
                rsi_scores[ticker] = rsi
                
        best_dips = sorted(rsi_scores.keys(), key=lambda k: rsi_scores[k])[:TOP_N]
        for ticker in best_dips:
            target_allocs[ticker] = dynamic_max_pos

    # Execute target allocations
    for t in list(pos_map.keys()):
        if t not in target_allocs and strategy == 'momentum':
            qty = pos_map[t]['quantity']
            if qty > 0 and not any(o['ticker'] == t for o in orders):
                orders.append({'ticker': t, 'side': 'sell', 'quantity': qty})
                
    # Target Value Matching for Execution
    if tradeable_equity is None:
        tradeable_equity = current_equity # Use full account for the challenge
    
    for ticker, base_target_pct in target_allocs.items():
        if ticker not in pos_map:
            price = prices.get(ticker, 0)
            if price > 0:
                vol = compute_volatility(ticker)
                dynamic_risk = min(0.25, abs(base_target_pct) * (0.02 / max(0.001, vol)))
                target_pct = dynamic_risk if base_target_pct > 0 else -dynamic_risk
                
                target_val = tradeable_equity * abs(target_pct)
                target_val = min(target_val, cash)
                
                if target_val < 100: continue 
                
                # Use int division for share quantity in challenge to be safe, though fractional might be supported
                qty = int(target_val / price)
                if qty > 0:
                    if target_pct > 0:
                        orders.append({'ticker': ticker, 'side': 'buy', 'quantity': qty})
                        cash -= (qty * price)
                        _state['entry_prices'][ticker] = price

    if orders:
        _state['last_rebal_day'] = _state['day_count']

    return orders
