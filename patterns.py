"""
patterns.py — Candle pattern detection for chart replay
Detects 8 common patterns and returns labels for each candle.
"""

import pandas as pd
import numpy as np


def detect_patterns(df):
    """
    Detect candle patterns for every candle in the dataframe.
    Returns a dict: {index: {"name": str, "type": "bullish"/"bearish"/"neutral"}}
    """
    patterns = {}
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    for i in range(2, n):
        body     = abs(c[i] - o[i])
        rng      = h[i] - l[i]
        upper_w  = h[i] - max(c[i], o[i])
        lower_w  = min(c[i], o[i]) - l[i]
        is_bull  = c[i] > o[i]
        is_bear  = c[i] < o[i]
        prev_body= abs(c[i-1] - o[i-1])
        prev_rng = h[i-1] - l[i-1]

        if rng == 0:
            continue

        body_pct    = body / rng
        upper_pct   = upper_w / rng if rng > 0 else 0
        lower_pct   = lower_w / rng if rng > 0 else 0

        found = None

        # 1. Doji — body very small relative to range
        if body_pct < 0.1 and rng > 0:
            found = {"name": "Doji", "type": "neutral"}

        # 2. Hammer — small body at top, long lower wick, bullish context
        elif (lower_pct >= 0.6 and body_pct < 0.3 and
              upper_pct < 0.1 and c[i-1] < o[i-1]):
            found = {"name": "Hammer", "type": "bullish"}

        # 3. Hanging Man — same shape as hammer but in uptrend (bearish)
        elif (lower_pct >= 0.6 and body_pct < 0.3 and
              upper_pct < 0.1 and c[i-1] > o[i-1]):
            found = {"name": "Hanging Man", "type": "bearish"}

        # 4. Shooting Star — small body at bottom, long upper wick
        elif (upper_pct >= 0.6 and body_pct < 0.3 and
              lower_pct < 0.1 and c[i-1] > o[i-1]):
            found = {"name": "Shooting Star", "type": "bearish"}

        # 5. Bullish Engulfing — green candle engulfs previous red
        elif (is_bull and c[i-1] < o[i-1] and
              o[i] <= c[i-1] and c[i] >= o[i-1] and
              body > prev_body * 1.0):
            found = {"name": "Bullish Engulfing", "type": "bullish"}

        # 6. Bearish Engulfing — red candle engulfs previous green
        elif (is_bear and c[i-1] > o[i-1] and
              o[i] >= c[i-1] and c[i] <= o[i-1] and
              body > prev_body * 1.0):
            found = {"name": "Bearish Engulfing", "type": "bearish"}

        # 7. Inside Bar — current candle fully inside previous candle
        elif (h[i] < h[i-1] and l[i] > l[i-1]):
            found = {"name": "Inside Bar", "type": "neutral"}

        # 8. Morning Star — 3 candle pattern (bearish, doji/small, bullish)
        elif (i >= 2 and
              c[i-2] < o[i-2] and                          # day 1 bearish
              abs(c[i-1]-o[i-1]) < abs(c[i-2]-o[i-2])*0.3 and  # day 2 small
              c[i] > o[i] and                               # day 3 bullish
              c[i] > (o[i-2] + c[i-2]) / 2):               # closes above midpoint
            found = {"name": "Morning Star", "type": "bullish"}

        # 9. Evening Star — 3 candle pattern (bullish, doji/small, bearish)
        elif (i >= 2 and
              c[i-2] > o[i-2] and
              abs(c[i-1]-o[i-1]) < abs(c[i-2]-o[i-2])*0.3 and
              c[i] < o[i] and
              c[i] < (o[i-2] + c[i-2]) / 2):
            found = {"name": "Evening Star", "type": "bearish"}

        if found:
            patterns[i] = found

    return patterns


def patterns_to_list(patterns, timestamps):
    """
    Convert patterns dict to list format for JSON serialization.
    Returns list of {time, name, type} dicts.
    """
    result = []
    for idx, pat in patterns.items():
        if idx < len(timestamps):
            result.append({
                "time": int(timestamps[idx]),
                "name": pat["name"],
                "type": pat["type"],
            })
    return result
