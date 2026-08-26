"""
data.py — Data fetching and indicator calculation for Chart Replay
Uses Yahoo Finance — no login, no API key, free historical data.
"""

import os
import warnings
import pandas as pd
import numpy as np
from patterns import detect_patterns, patterns_to_list

try:
    import certifi
except ImportError:
    certifi = None


# ── SSL-tolerant Yahoo Finance session ────────────────────────
# On locked-down / corporate Windows networks, Yahoo's TLS chain fails with
# "unable to get local issuer certificate" (curl error 60). We verify against
# the certifi CA bundle by default, and transparently retry WITHOUT
# verification only if the secure attempt fails with a certificate/TLS error
# (covers corporate SSL-inspection proxies whose root CA isn't in any bundle).
# Set CHARTREPLAY_INSECURE_SSL=1 to force the no-verify path directly.
_FORCE_INSECURE = os.environ.get("CHARTREPLAY_INSECURE_SSL", "").lower() in ("1", "true", "yes")


def _yf_session(verify):
    """Build a curl_cffi session with the given verify setting."""
    from curl_cffi import requests as _creq
    sess = _creq.Session(impersonate="chrome")
    if verify is False:
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass
        warnings.filterwarnings("ignore")
    sess.verify = verify
    return sess


def _is_ssl_error(text):
    s = str(text).lower()
    return "certificate" in s or "ssl" in s or "issuer" in s or "curl: (60)" in s


def _verify_default():
    return certifi.where() if certifi else True


# Remember whether TLS verification works so we don't probe on every call.
# None = unknown, "secure" = certifi works, "insecure" = must skip verification.
_ssl_mode = None


class _SSLLogCatcher(__import__("logging").Handler):
    """yfinance reports fetch failures via its logger (not exceptions), so we
    watch its log records to tell a TLS failure apart from an empty result."""
    def __init__(self):
        super().__init__()
        self.ssl = False

    def emit(self, record):
        try:
            if _is_ssl_error(record.getMessage()):
                self.ssl = True
        except Exception:
            pass


def _secure_call(fn):
    """Run fn() under the certifi session while sniffing yfinance's log for TLS
    errors. Returns (result, ssl_failed). Non-TLS exceptions propagate."""
    import logging
    yflog = logging.getLogger("yfinance")
    cap = _SSLLogCatcher()
    yflog.addHandler(cap)
    try:
        result = fn()
    except Exception as e:
        yflog.removeHandler(cap)
        if _is_ssl_error(e):
            return None, True
        raise
    yflog.removeHandler(cap)
    return result, cap.ssl


def yf_download(tickers, **kwargs):
    """yf.download that verifies with certifi, then transparently retries WITHOUT
    verification if the secure attempt comes back empty or with a TLS error.

    On SSL-inspection networks curl_cffi's verified session often returns an empty
    frame (logged as "possibly delisted") rather than raising a cert error, so we
    can't rely on catching an exception — we retry insecure whenever secure yields
    no data, and once the insecure retry succeeds we remember it for the session."""
    import yfinance as yf
    global _ssl_mode

    if _FORCE_INSECURE or _ssl_mode == "insecure":
        return yf.download(tickers, session=_yf_session(False), **kwargs)

    df, _ = _secure_call(
        lambda: yf.download(tickers, session=_yf_session(_verify_default()), **kwargs))
    if df is not None and not df.empty:
        _ssl_mode = "secure"
        return df

    # Secure returned nothing — retry without verification.
    df2 = yf.download(tickers, session=_yf_session(False), **kwargs)
    if df2 is not None and not df2.empty:
        _ssl_mode = "insecure"          # this network needs no-verify; skip secure next time
    return df2


def yf_ticker(symbol):
    """yf.Ticker with certifi verification, retrying without verification when the
    secure probe returns no info (empty or TLS failure)."""
    import yfinance as yf
    global _ssl_mode

    if _FORCE_INSECURE or _ssl_mode == "insecure":
        return yf.Ticker(symbol, session=_yf_session(False))

    def _probe():
        t = yf.Ticker(symbol, session=_yf_session(_verify_default()))
        _ = t.info  # trigger the network fetch so a TLS failure is observable
        return t

    t, _ = _secure_call(_probe)
    if t is not None and getattr(t, "info", None):
        _ssl_mode = "secure"
        return t

    # Secure probe returned nothing usable — fall back to no-verify.
    ti = yf.Ticker(symbol, session=_yf_session(False))
    try:
        if ti.info:
            _ssl_mode = "insecure"
    except Exception:
        pass
    return ti


# ── Yahoo Finance fetch ───────────────────────────────────────
def fetch_yahoo(symbol, from_date, to_date, interval):
    """
    Fetch OHLCV data from Yahoo Finance.
    NSE stocks: symbol = RELIANCE → fetched as RELIANCE.NS
    """
    try:
        import yfinance as yf

        interval_map = {
            "1D":    "1d",
            "1H":    "1h",
            "15min": "15m",
            "5min":  "5m",
            "1min":  "1m",
        }

        # Yahoo Finance actual retention limits
        retention_days = {"1min":30,"5min":60,"15min":60,"1H":730,"1D":3650}
        from datetime import datetime as dt2, timedelta

        def parse_date(d):
            for fmt in ["%Y-%m-%d","%m/%d/%Y","%d/%m/%Y","%Y/%m/%d"]:
                try: return dt2.strptime(d, fmt)
                except: pass
            return dt2.now()

        limit     = retention_days.get(interval, 60)
        from_dt   = parse_date(from_date)
        to_dt     = parse_date(to_date)
        # Normalize from_date to YYYY-MM-DD for yfinance
        from_date = from_dt.strftime("%Y-%m-%d")
        cutoff_dt = dt2.now() - timedelta(days=limit)
        if from_dt < cutoff_dt:
            return None, (
                f"Date range too old for {interval} data. "
                f"Yahoo Finance only keeps {interval} data for the last {limit} days. "
                f"Your From date must be after {cutoff_dt.strftime('%Y-%m-%d')}. "
                f"Switch to 1D timeframe for older historical data."
            )

        # Yahoo serves intraday data only in a limited window PER REQUEST
        # (1m ≈ 8 days). A wider span returns an empty frame, so clamp the
        # start forward to a fetchable window (keeps the requested end date).
        max_span_days = {"1min":7,"5min":60,"15min":60,"1H":730,"1D":3650}
        span_limit    = max_span_days.get(interval, 60)
        if (to_dt - from_dt).days > span_limit:
            from_dt   = to_dt - timedelta(days=span_limit)
            from_date = from_dt.strftime("%Y-%m-%d")

        yf_interval = interval_map.get(interval, "1d")

        # Auto-add .NS for NSE stocks
        yf_sym = symbol.upper()
        if not yf_sym.endswith(".NS") and not yf_sym.endswith(".BO"):
            yf_sym = yf_sym + ".NS"

        df = yf_download(
            yf_sym,
            start       = from_date,
            end         = to_date,
            interval    = yf_interval,
            progress    = False,
            auto_adjust = True,
            multi_level_index = False,  # flatten MultiIndex columns
        )

        if df is None or df.empty:
            return None, (
                f"Couldn't find data for '{symbol}'. This usually means the symbol is "
                f"misspelled — use the exact NSE ticker (e.g. RELIANCE, HDFCBANK, INFY). "
                f"If the symbol is correct, try a different date range."
            )

        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={
            date_col: "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })

        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        df[["open","high","low","close","volume"]] = \
            df[["open","high","low","close","volume"]].astype(float)

        return df, None

    except ImportError:
        return None, "yfinance not installed. Run: pip install yfinance"
    except Exception as e:
        return None, str(e)


# ── Heikin Ashi calculation ───────────────────────────────────
def calc_heikin_ashi(df):
    """Calculate Heikin Ashi candles from regular OHLCV."""
    ha = df.copy()
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4

    ha_open = [0.0] * len(df)
    ha_open[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i-1] + ha["ha_close"].iloc[i-1]) / 2

    ha["ha_open"]  = ha_open
    ha["ha_high"]  = df[["high","open","close"]].max(axis=1)
    ha["ha_low"]   = df[["low","open","close"]].min(axis=1)

    # Apply HA values
    ha["open"]  = ha["ha_open"]
    ha["high"]  = ha["ha_high"]
    ha["low"]   = ha["ha_low"]
    ha["close"] = ha["ha_close"]

    return ha


# ── Indicators ────────────────────────────────────────────────
def calc_indicators(df):
    """Calculate all indicators from real OHLCV (not HA)."""
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    result = {}

    # EMAs
    result["ema20"]  = close.ewm(span=20,  adjust=False).mean()
    result["ema50"]  = close.ewm(span=50,  adjust=False).mean()
    result["ema200"] = close.ewm(span=200, adjust=False).mean()

    # SMAs
    result["sma20"] = close.rolling(20).mean()
    result["sma50"] = close.rolling(50).mean()

    # Bollinger Bands
    sma20  = close.rolling(20).mean()
    std20  = close.rolling(20).std()
    result["bb_upper2"]  = sma20 + 2 * std20
    result["bb_middle"]  = sma20
    result["bb_lower2"]  = sma20 - 2 * std20
    result["bb_upper3"]  = sma20 + 3 * std20
    result["bb_lower3"]  = sma20 - 3 * std20

    # VWAP (resets daily)
    typical = (high + low + close) / 3
    df2     = df.copy()
    df2["tp"]      = typical * volume
    df2["vol_cum"] = df2.groupby(df2["date"].dt.date)["volume"].cumsum()
    df2["tp_cum"]  = df2.groupby(df2["date"].dt.date)["tp"].cumsum()
    result["vwap"] = df2["tp_cum"] / df2["vol_cum"]

    # RSI — Wilder's smoothing (matches TradingView exactly)
    delta  = close.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta.clip(upper=0))
    avg_g  = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_l  = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    result["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    result["macd"]        = macd
    result["macd_signal"] = sig
    result["macd_hist"]   = macd - sig

    # Volume avg for spike detection
    result["vol_avg"] = volume.rolling(10).mean()

    return result


def calc_supertrend(df, atr_period=7, multiplier=3.0):
    """
    Calculate SuperTrend indicator.
    Returns DataFrame with columns: supertrend, trend
    trend = 1 (bullish/green), trend = -1 (bearish/red)
    """
    high   = df["high"]
    low    = df["low"]
    close  = df["close"]
    hl2    = (high + low) / 2

    # ATR using Wilder's smoothing (same as Pine Script atr())
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()

    # Basic bands
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    # Final bands — ratcheted (never move against trend)
    upper = upper_basic.copy()
    lower = lower_basic.copy()
    trend = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        # Lower band (support in uptrend) — only moves up
        if lower_basic.iloc[i] > lower.iloc[i-1] or close.iloc[i-1] < lower.iloc[i-1]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i-1]

        # Upper band (resistance in downtrend) — only moves down
        if upper_basic.iloc[i] < upper.iloc[i-1] or close.iloc[i-1] > upper.iloc[i-1]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i-1]

        # Trend direction
        if trend.iloc[i-1] == -1 and close.iloc[i] > upper.iloc[i-1]:
            trend.iloc[i] = 1
        elif trend.iloc[i-1] == 1 and close.iloc[i] < lower.iloc[i-1]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i-1]

    # SuperTrend line value
    supertrend = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        supertrend.iloc[i] = lower.iloc[i] if trend.iloc[i] == 1 else upper.iloc[i]

    return pd.DataFrame({
        "supertrend": supertrend.round(2),
        "trend":      trend,
        "st_upper":   upper.round(2),
        "st_lower":   lower.round(2),
    })


def calc_pivot_points(df, pivot_type="traditional"):
    """
    Calculate daily Traditional Pivot Points (R1, S1 only).
    For intraday charts — uses previous day's OHLC.
    Returns dict keyed by date string: {pp, r1, s1}
    """
    import yfinance as yf

    pivots = {}

    # Get unique trading dates from df
    df2 = df.copy()
    df2["date_only"] = df2["date"].dt.date
    trading_dates = sorted(df2["date_only"].unique())

    if len(trading_dates) < 2:
        return pivots

    # Build a day-level OHLC from the intraday data
    daily = df2.groupby("date_only").agg(
        open  = ("open",  "first"),
        high  = ("high",  "max"),
        low   = ("low",   "min"),
        close = ("close", "last"),
    ).reset_index()
    daily = daily.sort_values("date_only").reset_index(drop=True)

    # For each trading date, pivot = previous day's OHLC
    for i in range(1, len(daily)):
        prev = daily.iloc[i-1]
        curr_date = daily.iloc[i]["date_only"]

        ph = float(prev["high"])
        pl = float(prev["low"])
        pc = float(prev["close"])

        if pivot_type == "traditional":
            pp = round((ph + pl + pc) / 3, 2)
            r1 = round(2 * pp - pl, 2)
            s1 = round(2 * pp - ph, 2)
            r2 = round(pp + (ph - pl), 2)
            s2 = round(pp - (ph - pl), 2)
        else:
            pp = round((ph + pl + pc) / 3, 2)
            r1 = round(2 * pp - pl, 2)
            s1 = round(2 * pp - ph, 2)
            r2 = round(pp + (ph - pl), 2)
            s2 = round(pp - (ph - pl), 2)

        pivots[str(curr_date)] = {
            "pp": pp, "r1": r1, "s1": s1, "r2": r2, "s2": s2
        }

    return pivots


# ── Build candle payload ──────────────────────────────────────
def build_candles(df, chart_type="candlestick"):
    """
    Build full candle payload with all indicators pre-calculated.
    Browser receives everything — reveals candles one at a time.
    """
    # Calculate indicators on REAL prices always
    inds = calc_indicators(df)

    # For HA chart type — use HA candle prices for display
    display_df = calc_heikin_ashi(df) if chart_type == "heikin_ashi" else df

    # Detect candle patterns on real prices
    pat_dict  = detect_patterns(df)
    timestamps = [int(d.timestamp()) for d in df["date"]]
    patterns  = patterns_to_list(pat_dict, timestamps)
    pat_map   = {p["time"]: p for p in patterns}

    volume = df["volume"]
    vol_avg= inds["vol_avg"]

    def safe(v):
        try:
            f = float(v)
            return None if (f != f) else round(f, 4)
        except:
            return None

    candles = []
    for i, row in display_df.iterrows():
        d   = df["date"].iloc[i]   # always use real date
        ts  = int(d.timestamp())
        v   = float(volume.iloc[i])
        avg = safe(vol_avg.iloc[i])
        is_spike = avg and v > avg * 2
        is_up    = float(display_df["close"].iloc[i]) >= float(display_df["open"].iloc[i])
        vol_col  = "#F59E0B" if is_spike else ("#26a69a" if is_up else "#ef5350")

        candle = {
            "time":         ts,
            "open":         round(float(display_df["open"].iloc[i]),  2),
            "high":         round(float(display_df["high"].iloc[i]),  2),
            "low":          round(float(display_df["low"].iloc[i]),   2),
            "close":        round(float(display_df["close"].iloc[i]), 2),
            # Real prices for paper trade
            "real_open":    round(float(df["open"].iloc[i]),  2),
            "real_high":    round(float(df["high"].iloc[i]),  2),
            "real_low":     round(float(df["low"].iloc[i]),   2),
            "real_close":   round(float(df["close"].iloc[i]), 2),
            "volume":       v,
            "vol_color":    vol_col,
            "vol_spike":    is_spike,
            # Indicators (calculated on real prices)
            "ema20":        safe(inds["ema20"].iloc[i]),
            "ema50":        safe(inds["ema50"].iloc[i]),
            "ema200":       safe(inds["ema200"].iloc[i]),
            "sma20":        safe(inds["sma20"].iloc[i]),
            "sma50":        safe(inds["sma50"].iloc[i]),
            "bb_upper2":    safe(inds["bb_upper2"].iloc[i]),
            "bb_middle":    safe(inds["bb_middle"].iloc[i]),
            "bb_lower2":    safe(inds["bb_lower2"].iloc[i]),
            "bb_upper3":    safe(inds["bb_upper3"].iloc[i]),
            "bb_lower3":    safe(inds["bb_lower3"].iloc[i]),
            "vwap":         safe(inds["vwap"].iloc[i]),
            "rsi":          safe(inds["rsi"].iloc[i]),
            "macd":         safe(inds["macd"].iloc[i]),
            "macd_signal":  safe(inds["macd_signal"].iloc[i]),
            "macd_hist":    safe(inds["macd_hist"].iloc[i]),
            # Pattern
            "pattern":      pat_map.get(ts),
        }
        candle['entry_score'] = calc_entry_score(candle)
        candles.append(candle)

    # Add tick simulation
    candles = add_ticks_to_candles(candles, n_ticks=60)
    return candles


# ── Nifty context ─────────────────────────────────────────────
def fetch_nifty_context(from_date, to_date):
    """Fetch Nifty 50 daily performance for context bar."""
    try:
        df = yf_download("^NSEI", start=from_date, end=to_date,
                         interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        df["pct"]  = df["Close"].pct_change() * 100
        result = {}
        for _, row in df.iterrows():
            ts = int(row["Date"].timestamp())
            result[ts] = round(float(row["pct"]), 3) if not pd.isna(row["pct"]) else 0.0
        return result
    except:
        return {}


# ── Tick simulation ───────────────────────────────────────────
def generate_ticks(candle, n_ticks=60):
    """
    Generate n_ticks synthetic price ticks for a candle.
    Constrained random walk: starts at open, ends at close,
    must touch both high and low somewhere in the middle.
    Returns list of {time_offset, price} dicts.
    """
    import random
    o = candle["real_open"]
    h = candle["real_high"]
    l = candle["real_low"]
    c = candle["real_close"]

    # Decide direction — did price go to high first or low first?
    # Use close vs open to make an educated guess
    go_high_first = c >= o

    ticks = [o]
    n = n_ticks

    # Split into 3 phases:
    # Phase 1 (0 to 33%): move toward first extreme
    # Phase 2 (33% to 66%): move toward second extreme
    # Phase 3 (66% to 100%): converge toward close

    p1_end = n // 3
    p2_end = (n * 2) // 3

    first_extreme  = h if go_high_first else l
    second_extreme = l if go_high_first else h

    def walk_toward(current, target, steps, noise=0.3):
        """Random walk biased toward target."""
        result = []
        val = current
        rng = abs(target - current)
        if rng == 0 or steps == 0:
            return [current] * steps
        step_size = rng / steps
        for _ in range(steps):
            bias = (target - val) / max(abs(target - val), 0.01)
            noise_val = random.uniform(-noise, noise) * rng * 0.1
            val = val + bias * step_size + noise_val
            # Clamp within OHLC
            val = max(l, min(h, val))
            result.append(round(val, 2))
        return result

    # Phase 1: open → first extreme
    phase1 = walk_toward(o, first_extreme, p1_end)
    if phase1: phase1[-1] = first_extreme  # ensure we touch it

    # Phase 2: first extreme → second extreme
    start2 = phase1[-1] if phase1 else o
    phase2 = walk_toward(start2, second_extreme, p2_end - p1_end)
    if phase2: phase2[-1] = second_extreme  # ensure we touch it

    # Phase 3: second extreme → close
    start3 = phase2[-1] if phase2 else second_extreme
    phase3 = walk_toward(start3, c, n - p2_end)
    if phase3: phase3[-1] = c  # ensure we end at close

    all_prices = [o] + phase1 + phase2 + phase3

    # Build tick objects
    result = []
    for i, price in enumerate(all_prices[:n]):
        result.append({
            "i":     i,
            "price": round(float(price), 2),
        })

    return result


def add_ticks_to_candles(candles, n_ticks=60):
    """Add tick simulation data to each candle."""
    for c in candles:
        c["ticks"] = generate_ticks(c, n_ticks)
    return candles


# ── Entry quality score ───────────────────────────────────────
def calc_entry_score(candle):
    """
    Score a trade entry 0-100 based on indicator alignment.
    Returns {score, breakdown, verdict}
    """
    score     = 0
    breakdown = []

    def chk(condition, label, pts, good_msg, bad_msg):
        nonlocal score
        if condition:
            score += pts
            breakdown.append({"label": label, "pts": pts, "pass": True,  "msg": good_msg})
        else:
            breakdown.append({"label": label, "pts": 0,   "pass": False, "msg": bad_msg})

    c   = candle.get("real_close", 0)
    o   = candle.get("real_open",  0)
    rsi = candle.get("rsi")
    macd= candle.get("macd")
    msig= candle.get("macd_signal")
    e20 = candle.get("ema20")
    e50 = candle.get("ema50")
    e200= candle.get("ema200")
    vol = candle.get("volume", 0)
    bbu = candle.get("bb_upper2")
    bbl = candle.get("bb_lower2")

    # RSI — 15 pts
    if rsi is not None:
        chk(30 <= rsi <= 60, "RSI", 15,
            f"RSI {rsi:.1f} — healthy range (30-60)",
            f"RSI {rsi:.1f} — {'overbought >60' if rsi>60 else 'oversold <30'}")
    else:
        breakdown.append({"label":"RSI","pts":0,"pass":False,"msg":"RSI not available"})

    # Price above EMA 20 — 15 pts
    if e20 is not None:
        chk(c > e20, "Price vs EMA 20", 15,
            f"Price ₹{c} above EMA20 ₹{e20:.2f} ✓",
            f"Price ₹{c} below EMA20 ₹{e20:.2f} ✗")
    else:
        breakdown.append({"label":"Price vs EMA 20","pts":0,"pass":False,"msg":"EMA 20 not available"})

    # Price above EMA 50 — 15 pts
    if e50 is not None:
        chk(c > e50, "Price vs EMA 50", 15,
            f"Price ₹{c} above EMA50 ₹{e50:.2f} ✓",
            f"Price ₹{c} below EMA50 ₹{e50:.2f} ✗")
    else:
        breakdown.append({"label":"Price vs EMA 50","pts":0,"pass":False,"msg":"EMA 50 not available"})

    # MACD above signal — 15 pts
    if macd is not None and msig is not None:
        chk(macd > msig, "MACD", 15,
            f"MACD {macd:.3f} above signal {msig:.3f} ✓",
            f"MACD {macd:.3f} below signal {msig:.3f} ✗")
    else:
        breakdown.append({"label":"MACD","pts":0,"pass":False,"msg":"MACD not available"})

    # Volume above average — 15 pts (vol_spike already in candle)
    vol_spike = candle.get("vol_spike", False)
    chk(vol_spike, "Volume spike", 15,
        "Volume 2x+ above average — institutional activity ✓",
        "Volume below 2x average — weak conviction")

    # Bollinger Band — not overbought — 10 pts
    if bbu is not None and bbl is not None:
        chk(c < bbu, "Bollinger Band", 10,
            f"Price below upper BB — room to move up ✓",
            f"Price above upper BB — overbought ✗")
    else:
        breakdown.append({"label":"Bollinger Band","pts":0,"pass":False,"msg":"BB not available"})

    # Candle is green — 10 pts
    chk(c > o, "Candle color", 10,
        "Green candle — bullish close ✓",
        "Red candle — bearish close ✗")

    # Price above EMA 200 — 5 pts (long term trend)
    if e200 is not None:
        chk(c > e200, "Long term trend (EMA 200)", 5,
            f"Price above EMA200 — bull market ✓",
            f"Price below EMA200 — bear market ✗")
    else:
        breakdown.append({"label":"Long term trend (EMA 200)","pts":0,"pass":False,"msg":"EMA 200 not available"})

    # Verdict
    if score >= 80:
        verdict = {"label": "Excellent setup 🔥", "color": "#3fb950"}
    elif score >= 60:
        verdict = {"label": "Good setup ✓",       "color": "#58a6ff"}
    elif score >= 40:
        verdict = {"label": "Average setup ⚠",    "color": "#d29922"}
    else:
        verdict = {"label": "Weak setup ✗",        "color": "#f85149"}

    return {
        "score":     score,
        "max":       100,
        "breakdown": breakdown,
        "verdict":   verdict,
    }
