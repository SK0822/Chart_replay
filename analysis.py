"""
analysis.py — Technical Analysis Engine + Fundamentals
Detects trend, support/resistance, chart patterns.
Fetches fundamentals from Yahoo Finance.
"""

import pandas as pd
import numpy as np
from data import fetch_yahoo


# ── Fundamentals ──────────────────────────────────────────────
def get_fundamentals(symbol):
    """Fetch key fundamentals from Yahoo Finance."""
    try:
        from data import yf_ticker
        sym = symbol.upper()
        if not sym.endswith(".NS"):
            sym = sym + ".NS"
        t = yf_ticker(sym)
        info = t.info or {}

        def safe(key, default="N/A"):
            v = info.get(key)
            return v if v is not None else default

        def pct(key):
            v = info.get(key)
            return f"{round(v*100,1)}%" if v else "N/A"

        def cr(key):
            v = info.get(key)
            if v is None: return "N/A"
            if v >= 1e7:  return f"₹{round(v/1e7,2)} Cr"
            if v >= 1e5:  return f"₹{round(v/1e5,2)} L"
            return f"₹{round(v,2)}"

        return {
            "name":           safe("longName", symbol),
            "sector":         safe("sector"),
            "industry":       safe("industry"),
            "market_cap":     cr("marketCap"),
            "pe_ratio":       round(safe("trailingPE", 0) or 0, 2),
            "pb_ratio":       round(safe("priceToBook", 0) or 0, 2),
            "revenue":        cr("totalRevenue"),
            "profit_margin":  pct("profitMargins"),
            "roe":            pct("returnOnEquity"),
            "debt_equity":    round(safe("debtToEquity", 0) or 0, 2),
            "current_ratio":  round(safe("currentRatio", 0) or 0, 2),
            "dividend_yield": pct("dividendYield"),
            "52w_high":       round(safe("fiftyTwoWeekHigh", 0) or 0, 2),
            "52w_low":        round(safe("fiftyTwoWeekLow", 0) or 0, 2),
            "beta":           round(safe("beta", 0) or 0, 2),
            "avg_volume":     safe("averageVolume"),
            "description":    safe("longBusinessSummary", "")[:300] + "..." if safe("longBusinessSummary") else "N/A",
        }
    except Exception as e:
        return {"error": str(e)}


# ── Trend detection ───────────────────────────────────────────
def detect_trend(df, lookback=50):
    """
    Detect trend direction using swing highs/lows and EMAs.
    Returns: uptrend / downtrend / sideways + details
    """
    if len(df) < lookback:
        lookback = len(df)

    recent = df.tail(lookback).copy()
    close  = recent["close"]
    high   = recent["high"]
    low    = recent["low"]

    # EMA alignment
    ema20  = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50  = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if len(df) >= 200 else None
    last_close = float(close.iloc[-1])

    # Swing highs/lows — find local peaks and troughs
    def find_swings(series, window=5):
        highs, lows = [], []
        for i in range(window, len(series)-window):
            if series.iloc[i] == series.iloc[i-window:i+window+1].max():
                highs.append((i, float(series.iloc[i])))
            if series.iloc[i] == series.iloc[i-window:i+window+1].min():
                lows.append((i, float(series.iloc[i])))
        return highs, lows

    swing_highs, swing_lows = find_swings(close)

    # Higher highs and higher lows = uptrend
    hh = len(swing_highs) >= 2 and swing_highs[-1][1] > swing_highs[-2][1]
    hl = len(swing_lows)  >= 2 and swing_lows[-1][1]  > swing_lows[-2][1]
    lh = len(swing_highs) >= 2 and swing_highs[-1][1] < swing_highs[-2][1]
    ll = len(swing_lows)  >= 2 and swing_lows[-1][1]  < swing_lows[-2][1]

    # EMA trend
    ema_bull = last_close > ema20 > ema50
    ema_bear = last_close < ema20 < ema50

    # Score
    bull_score = sum([hh, hl, ema_bull,
                      last_close > ema20, last_close > ema50])
    bear_score = sum([lh, ll, ema_bear,
                      last_close < ema20, last_close < ema50])

    if bull_score >= 4:
        direction = "uptrend"
        color     = "#3fb950"
        emoji     = "↑"
    elif bear_score >= 4:
        direction = "downtrend"
        color     = "#f85149"
        emoji     = "↓"
    else:
        direction = "sideways"
        color     = "#d29922"
        emoji     = "→"

    # Price change
    price_change = round((last_close - float(close.iloc[0])) / float(close.iloc[0]) * 100, 2)

    return {
        "direction":    direction,
        "color":        color,
        "emoji":        emoji,
        "bull_score":   int(bull_score),
        "bear_score":   int(bear_score),
        "ema20":        round(float(ema20), 2),
        "ema50":        round(float(ema50), 2),
        "ema200":       round(float(ema200), 2) if ema200 else None,
        "price_change": float(price_change),
        "above_ema20":  bool(last_close > ema20),
        "above_ema50":  bool(last_close > ema50),
        "above_ema200": bool(last_close > ema200) if ema200 else None,
        "higher_highs": bool(hh),
        "higher_lows":  bool(hl),
    }


# ── Support/Resistance ────────────────────────────────────────
def find_sr_levels(df, n_levels=5, lookback=100):
    """
    Find key support and resistance levels.
    Uses price clustering — levels where price bounced multiple times.
    """
    if len(df) < 20:
        return [], []

    recent = df.tail(lookback)
    highs  = recent["high"].values
    lows   = recent["low"].values
    closes = recent["close"].values
    last   = float(closes[-1])

    # Collect all significant price levels
    levels = []
    for i in range(2, len(recent)-2):
        h = highs[i]
        l = lows[i]
        # Local high
        if h >= highs[i-1] and h >= highs[i-2] and h >= highs[i+1] and h >= highs[i+2]:
            levels.append(('resistance', float(h)))
        # Local low
        if l <= lows[i-1] and l <= lows[i-2] and l <= lows[i+1] and l <= lows[i+2]:
            levels.append(('support', float(l)))

    if not levels:
        return [], []

    # Cluster nearby levels — merge levels within 0.5% of each other
    threshold = last * 0.005
    clustered = []
    for typ, price in levels:
        merged = False
        for cl in clustered:
            if abs(cl['price'] - price) < threshold:
                cl['count'] += 1
                cl['price'] = round(float((cl['price'] + price) / 2), 2)
                merged = True
                break
        if not merged:
            clustered.append({'price': round(float(price), 2), 'count': 1, 'type': str(typ)})

    # Sort by strength (count) then split into support/resistance
    clustered.sort(key=lambda x: x['count'], reverse=True)

    support    = sorted([l for l in clustered if l['price'] < last],
                        key=lambda x: x['price'], reverse=True)[:n_levels]
    resistance = sorted([l for l in clustered if l['price'] > last],
                        key=lambda x: x['price'])[:n_levels]

    return support, resistance


# ── Chart pattern detection ───────────────────────────────────
def detect_patterns(df, lookback=60):
    """
    Detect major chart patterns.
    Returns list of detected patterns with confidence score.
    """
    if len(df) < 30:
        return []

    recent = df.tail(lookback)
    close  = recent["close"].values
    high   = recent["high"].values
    low    = recent["low"].values
    n      = len(close)
    patterns = []

    # ── Double Top ──────────────────────────────────────────
    if n >= 20:
        # Find two highs roughly equal with a trough between
        h1_idx = np.argmax(high[:n//2])
        h2_idx = n//2 + np.argmax(high[n//2:])
        h1, h2 = high[h1_idx], high[h2_idx]
        trough = np.min(close[h1_idx:h2_idx]) if h2_idx > h1_idx else 0

        if (abs(h1-h2)/max(h1,h2) < 0.03 and  # peaks within 3%
                trough < min(h1,h2)*0.97 and   # trough lower
                close[-1] < trough):            # price broke neckline
            confidence = 85 if abs(h1-h2)/max(h1,h2) < 0.01 else 65
            patterns.append({
                "name":        "Double Top",
                "type":        "bearish",
                "confidence":  int(confidence),
                "description": "Two peaks at similar levels — bearish reversal signal.",
                "color":       "#f85149",
            })

    # ── Double Bottom ───────────────────────────────────────
    if n >= 20:
        l1_idx = np.argmin(low[:n//2])
        l2_idx = n//2 + np.argmin(low[n//2:])
        l1, l2 = low[l1_idx], low[l2_idx]
        peak = np.max(close[l1_idx:l2_idx]) if l2_idx > l1_idx else 0

        if (abs(l1-l2)/max(l1,l2) < 0.03 and
                peak > max(l1,l2)*1.03 and
                close[-1] > peak):
            confidence = 85 if abs(l1-l2)/max(l1,l2) < 0.01 else 65
            patterns.append({
                "name":        "Double Bottom",
                "type":        "bullish",
                "confidence":  confidence,
                "description": "Two troughs at similar levels — bullish reversal signal.",
                "color":       "#3fb950",
            })

    # ── Head and Shoulders ──────────────────────────────────
    if n >= 30:
        third = n // 3
        left_h  = np.max(high[:third])
        head_h  = np.max(high[third:2*third])
        right_h = np.max(high[2*third:])
        neckline= np.mean([np.min(low[:third]), np.min(low[third:2*third])])

        if (head_h > left_h * 1.02 and
                head_h > right_h * 1.02 and
                abs(left_h - right_h) / max(left_h, right_h) < 0.05 and
                close[-1] < neckline):
            patterns.append({
                "name":        "Head & Shoulders",
                "type":        "bearish",
                "confidence":  75,
                "description": "Classic bearish reversal — head higher than shoulders, price broke neckline.",
                "color":       "#f85149",
            })

    # ── Inverse Head and Shoulders ──────────────────────────
    if n >= 30:
        third  = n // 3
        left_l = np.min(low[:third])
        head_l = np.min(low[third:2*third])
        right_l= np.min(low[2*third:])
        neckline= np.mean([np.max(high[:third]), np.max(high[third:2*third])])

        if (head_l < left_l * 0.98 and
                head_l < right_l * 0.98 and
                abs(left_l - right_l) / max(left_l, right_l) < 0.05 and
                close[-1] > neckline):
            patterns.append({
                "name":        "Inverse H&S",
                "type":        "bullish",
                "confidence":  75,
                "description": "Classic bullish reversal — head lower than shoulders, price broke neckline.",
                "color":       "#3fb950",
            })

    # ── Ascending Triangle ──────────────────────────────────
    if n >= 20:
        resistance_lvl = np.max(high[-20:])
        low_trend = np.polyfit(range(20), low[-20:], 1)
        if (low_trend[0] > 0 and  # rising lows
                np.std(high[-20:]) / resistance_lvl < 0.01):  # flat highs
            patterns.append({
                "name":        "Ascending Triangle",
                "type":        "bullish",
                "confidence":  70,
                "description": "Flat resistance + rising support — bullish breakout expected.",
                "color":       "#3fb950",
            })

    # ── Descending Triangle ─────────────────────────────────
    if n >= 20:
        support_lvl = np.min(low[-20:])
        high_trend  = np.polyfit(range(20), high[-20:], 1)
        if (high_trend[0] < 0 and
                np.std(low[-20:]) / support_lvl < 0.01):
            patterns.append({
                "name":        "Descending Triangle",
                "type":        "bearish",
                "confidence":  70,
                "description": "Flat support + falling resistance — bearish breakdown expected.",
                "color":       "#f85149",
            })

    # ── Flag and Pole ───────────────────────────────────────
    if n >= 15:
        # Strong move in first half, consolidation in second
        first_half_move = abs(close[n//2] - close[0]) / close[0]
        second_half_std = np.std(close[n//2:]) / np.mean(close[n//2:])
        if first_half_move > 0.05 and second_half_std < 0.01:
            typ = "bullish" if close[n//2] > close[0] else "bearish"
            patterns.append({
                "name":        "Flag Pattern",
                "type":        typ,
                "confidence":  65,
                "description": f"Strong {'upward' if typ=='bullish' else 'downward'} move followed by tight consolidation — continuation expected.",
                "color":       "#3fb950" if typ == "bullish" else "#f85149",
            })

    return patterns


# ── Full TA summary ───────────────────────────────────────────
def get_ta_summary(symbol, timeframe="1D", lookback_days=365,
                   from_date=None, to_date=None):
    """Complete technical analysis for a symbol."""
    from datetime import datetime, timedelta

    def norm_date(d):
        """Normalize date to YYYY-MM-DD regardless of input format."""
        if not d: return None
        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]:
            try: return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
            except: pass
        return d  # return as-is if can't parse

    from_date = norm_date(from_date)
    to_date   = norm_date(to_date)

    # Auto date range based on timeframe if not provided
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if not from_date:
        days = 60 if timeframe in ["1min","5min","15min","1H"] else lookback_days
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Extend to_date by 1 day — Yahoo end is exclusive
    to_dt_obj  = datetime.strptime(to_date, "%Y-%m-%d")
    to_date    = (to_dt_obj + timedelta(days=1)).strftime("%Y-%m-%d")

    # Always extend FROM date BACKWARDS for indicator warmup
    # EMA200=200 candles, RSI=14, MACD=26 — use 2 years for daily
    warmup_days = 730 if timeframe == "1D" else 90
    from_dt_obj = datetime.strptime(from_date, "%Y-%m-%d")
    warmup_from = (from_dt_obj - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    # Only use warmup if it doesn't exceed Yahoo limits
    from_date   = warmup_from

    df, err = fetch_yahoo(symbol, from_date, to_date, timeframe)
    if err or df is None or df.empty:
        return {"error": err or "No data"}



    trend    = detect_trend(df)
    support, resistance = find_sr_levels(df)
    patterns = detect_patterns(df)

    # Overall verdict
    bull = (trend["direction"] == "uptrend") + len([p for p in patterns if p["type"]=="bullish"])
    bear = (trend["direction"] == "downtrend") + len([p for p in patterns if p["type"]=="bearish"])

    if bull > bear:
        verdict = {"label": "Bullish 📈", "color": "#3fb950"}
    elif bear > bull:
        verdict = {"label": "Bearish 📉", "color": "#f85149"}
    else:
        verdict = {"label": "Neutral ↔", "color": "#d29922"}

    last_close = round(float(df["close"].iloc[-1]), 2)
    rsi_val = None
    try:
        delta  = df["close"].diff()
        gain   = delta.clip(lower=0)
        loss   = (-delta.clip(upper=0))
        avg_g  = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_l  = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs     = avg_g / avg_l.replace(0, float('nan'))
        rsi_val = round(float(100 - (100 / (1 + rs)).iloc[-1]), 1)
    except:
        pass

    # Ensure all S/R values are plain Python
    support    = [{"price": float(s["price"]), "count": int(s["count"]), "type": str(s["type"])} for s in support]
    resistance = [{"price": float(r["price"]), "count": int(r["count"]), "type": str(r["type"])} for r in resistance]

    return {
        "symbol":       str(symbol),
        "timeframe":    str(timeframe),
        "last_close":   float(last_close),
        "rsi":          float(rsi_val) if rsi_val else None,
        "trend":        trend,
        "support":      support,
        "resistance":   resistance,
        "patterns":     patterns,
        "verdict":      verdict,
        "candle_count": int(len(df)),
    }