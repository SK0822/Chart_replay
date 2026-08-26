"""
backtest.py — Strategy Builder & Backtesting Engine
Fetches historical data via Yahoo Finance, evaluates
user-defined conditions day by day, simulates trades.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ── Fetch data from Yahoo Finance ─────────────────────────────
def fetch_data(symbol, from_date, to_date):
    try:
        from data import yf_download
        sym = symbol.upper()
        if not sym.endswith(".NS") and not sym.endswith(".BO"):
            sym = sym + ".NS"
        df = yf_download(sym, start=from_date, end=to_date,
                         interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None, f"No data for {symbol}. Check symbol name."
        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.rename(columns={"Date":"date","Open":"open","High":"high",
                                 "Low":"low","Close":"close","Volume":"volume"})
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        df[["open","high","low","close","volume"]] = \
            df[["open","high","low","close","volume"]].astype(float)
        return df, None
    except ImportError:
        return None, "yfinance not installed. Run: pip install yfinance"
    except Exception as e:
        return None, str(e)


# ── Calculate all indicators ───────────────────────────────────
def calc_all(df):
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    ind = {}
    ind["ema20"]  = close.ewm(span=20,  adjust=False).mean()
    ind["ema50"]  = close.ewm(span=50,  adjust=False).mean()
    ind["ema200"] = close.ewm(span=200, adjust=False).mean()
    ind["sma20"]  = close.rolling(20).mean()
    ind["sma50"]  = close.rolling(50).mean()

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    ind["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    ind["macd"]   = ema12 - ema26
    ind["signal"] = ind["macd"].ewm(span=9, adjust=False).mean()
    ind["hist"]   = ind["macd"] - ind["signal"]

    # Bollinger Bands
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    ind["bb_upper"] = sma20 + 2 * std20
    ind["bb_lower"] = sma20 - 2 * std20

    # ADX
    tr    = pd.concat([high - low,
                       (high - close.shift()).abs(),
                       (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    dmp   = high.diff().clip(lower=0)
    dmm   = (-low.diff()).clip(lower=0)
    dip   = 100 * dmp.rolling(14).mean() / atr14
    dim   = 100 * dmm.rolling(14).mean() / atr14
    dx    = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    ind["adx"] = dx.rolling(14).mean()

    # Volume avg
    ind["vol_avg10"] = volume.rolling(10).mean()

    # 52W high (rolling 252 days)
    ind["high_52w"] = high.rolling(252, min_periods=1).max()

    return ind


# ── Evaluate a single condition at index i ────────────────────
def eval_condition(cond, df, ind, i):
    """
    cond = {
      "indicator": "rsi" | "macd" | "price_vs_ema" | "ema_cross" |
                   "volume" | "bb" | "adx" | "52w_high" |
                   "candle_color" | "consecutive_closes",
      "operator":  depends on indicator,
      "value":     number or string
    }
    """
    if i < 1:
        return False

    indicator = cond.get("indicator", "")
    operator  = cond.get("operator",  "")
    value     = cond.get("value",     0)

    try:
        # RSI
        if indicator == "rsi":
            rsi = float(ind["rsi"].iloc[i])
            if pd.isna(rsi): return False
            val = float(value)
            if operator == "less_than":    return rsi < val
            if operator == "greater_than": return rsi > val
            if operator == "between":
                v2 = float(cond.get("value2", val))
                return val <= rsi <= v2

        # MACD crossed above signal
        elif indicator == "macd":
            m_now  = float(ind["macd"].iloc[i])
            s_now  = float(ind["signal"].iloc[i])
            m_prev = float(ind["macd"].iloc[i-1])
            s_prev = float(ind["signal"].iloc[i-1])
            if any(pd.isna(x) for x in [m_now,s_now,m_prev,s_prev]):
                return False
            if operator == "crossed_above": return m_prev < s_prev and m_now >= s_now
            if operator == "crossed_below": return m_prev > s_prev and m_now <= s_now
            if operator == "above":         return m_now > s_now
            if operator == "below":         return m_now < s_now

        # Price vs EMA
        elif indicator == "price_vs_ema":
            price = float(df["close"].iloc[i])
            ema_key = f"ema{int(value)}"
            ema_val = float(ind.get(ema_key, ind["ema50"]).iloc[i])
            if pd.isna(ema_val): return False
            if operator == "above": return price > ema_val
            if operator == "below": return price < ema_val

        # EMA crossover
        elif indicator == "ema_cross":
            fast = int(cond.get("fast", 20))
            slow = int(cond.get("slow", 50))
            fast_now  = float(ind.get(f"ema{fast}", ind["ema20"]).iloc[i])
            slow_now  = float(ind.get(f"ema{slow}", ind["ema50"]).iloc[i])
            fast_prev = float(ind.get(f"ema{fast}", ind["ema20"]).iloc[i-1])
            slow_prev = float(ind.get(f"ema{slow}", ind["ema50"]).iloc[i-1])
            if any(pd.isna(x) for x in [fast_now,slow_now,fast_prev,slow_prev]):
                return False
            if operator == "crossed_above": return fast_prev < slow_prev and fast_now >= slow_now
            if operator == "crossed_below": return fast_prev > slow_prev and fast_now <= slow_now
            if operator == "above":         return fast_now > slow_now

        # Volume
        elif indicator == "volume":
            vol     = float(df["volume"].iloc[i])
            avg_vol = float(ind["vol_avg10"].iloc[i])
            if pd.isna(avg_vol) or avg_vol == 0: return False
            mult = float(value)
            if operator == "greater_than": return vol > avg_vol * mult
            if operator == "less_than":    return vol < avg_vol * mult

        # Bollinger Band
        elif indicator == "bb":
            price = float(df["close"].iloc[i])
            upper = float(ind["bb_upper"].iloc[i])
            lower = float(ind["bb_lower"].iloc[i])
            if pd.isna(upper) or pd.isna(lower): return False
            if operator == "above_upper": return price > upper
            if operator == "below_lower": return price < lower
            if operator == "inside":      return lower <= price <= upper

        # ADX
        elif indicator == "adx":
            adx = float(ind["adx"].iloc[i])
            if pd.isna(adx): return False
            val = float(value)
            if operator == "greater_than": return adx > val
            if operator == "less_than":    return adx < val

        # 52W High proximity
        elif indicator == "52w_high":
            price    = float(df["close"].iloc[i])
            high_52w = float(ind["high_52w"].iloc[i])
            if pd.isna(high_52w) or high_52w == 0: return False
            pct  = price / high_52w * 100
            val  = float(value)  # e.g. 95 means within 5%
            if operator == "within_pct": return pct >= val

        # Candle color
        elif indicator == "candle_color":
            close_val = float(df["close"].iloc[i])
            open_val  = float(df["open"].iloc[i])
            if operator == "green": return close_val > open_val
            if operator == "red":   return close_val < open_val

        # Consecutive higher closes
        elif indicator == "consecutive_closes":
            n = int(value)
            for j in range(i, max(i-n, 0), -1):
                if float(df["close"].iloc[j]) <= float(df["close"].iloc[j-1]):
                    return False
            return True

    except Exception:
        return False

    return False


# ── Helper: session time ──────────────────────────────────────
def get_session(dt):
    """Return trading session name based on time."""
    try:
        hour = dt.hour
        if hour < 10:   return "Pre-market"
        if hour < 11:   return "Morning (9:15-11:00)"
        if hour < 13:   return "Mid-session (11:00-13:00)"
        if hour < 14:   return "Afternoon (13:00-14:00)"
        return "Late session (14:00-15:30)"
    except:
        return "Daily"


# ── Helper: condition details at signal candle ─────────────────
def build_condition_details(conditions, df, ind, i):
    """
    For each condition, evaluate it at candle i and return
    whether it passed and the actual values.
    """
    details = []
    for cond in conditions:
        indicator = cond.get("indicator", "")
        operator  = cond.get("operator",  "")
        value     = cond.get("value",     0)
        passed    = eval_condition(cond, df, ind, i)

        # Build human-readable description
        try:
            if indicator == "rsi":
                actual = round(float(ind["rsi"].iloc[i]), 1)
                desc   = f"RSI {actual} {operator.replace('_',' ')} {value}"
            elif indicator == "macd":
                m = round(float(ind["macd"].iloc[i]), 3)
                s = round(float(ind["signal"].iloc[i]), 3)
                desc = f"MACD {m} {operator.replace('_',' ')} Signal {s}"
            elif indicator == "price_vs_ema":
                price = round(float(df["close"].iloc[i]), 2)
                ema   = round(float(ind.get(f"ema{int(value)}", ind["ema50"]).iloc[i]), 2)
                desc  = f"Price ₹{price} {operator} EMA{int(value)} ₹{ema}"
            elif indicator == "volume":
                vol = float(df["volume"].iloc[i])
                avg = float(ind["vol_avg10"].iloc[i])
                mult = round(vol/avg, 1) if avg else 0
                desc = f"Volume {mult}x average (need {value}x)"
            elif indicator == "adx":
                adx = round(float(ind["adx"].iloc[i]), 1)
                desc = f"ADX {adx} {operator.replace('_',' ')} {value}"
            elif indicator == "candle_color":
                c = float(df["close"].iloc[i])
                o = float(df["open"].iloc[i])
                desc = f"Candle {'green' if c>o else 'red'} (need {operator})"
            else:
                desc = f"{indicator} {operator} {value}"
        except:
            desc = f"{indicator} {operator} {value}"

        details.append({
            "indicator": indicator,
            "desc":      desc,
            "passed":    bool(passed),
        })
    return details


# ── Run backtest ───────────────────────────────────────────────
def run_backtest(symbol, from_date, to_date, conditions,
                 sl_pct=1.5, target_pct=3.75,
                 max_hold_days=10, capital=50000):
    """
    Main backtest function.
    Returns dict with all results or error string.
    """
    # Fetch data — extend back 300 days for indicator warmup
    ext_from = (datetime.strptime(from_date, "%Y-%m-%d") -
                timedelta(days=300)).strftime("%Y-%m-%d")
    df, err = fetch_data(symbol, ext_from, to_date)
    if err:
        return {"error": err}

    if len(df) < 30:
        return {"error": "Not enough data. Try a longer date range."}

    # Calculate all indicators
    ind = calc_all(df)

    # Find the start index corresponding to from_date
    start_idx = df[df["date"] >= pd.Timestamp(from_date)].index.min()
    if pd.isna(start_idx):
        return {"error": "No data in the selected date range."}

    trades       = []
    in_trade     = False
    entry_price  = 0
    entry_date   = None
    entry_idx    = None
    sl_price     = 0
    target_price = 0

    for i in range(start_idx, len(df)):
        row = df.iloc[i]

        if in_trade:
            # Check exit conditions
            days_held = i - entry_idx
            high = float(row["high"])
            low  = float(row["low"])
            close= float(row["close"])
            date_str = row["date"].strftime("%Y-%m-%d")

            if high >= target_price:
                pl_pct   = round((target_price - entry_price) / entry_price * 100, 2)
                pl_rs    = round((target_price - entry_price) * int(capital / entry_price), 2)
                qty      = int(capital / entry_price)
                # Build condition details at entry
                cond_details = build_condition_details(conditions, df, ind, entry_idx - 1)
                trades.append({
                    "entry_date":   entry_date,
                    "exit_date":    date_str,
                    "entry_price":  round(entry_price, 2),
                    "exit_price":   round(target_price, 2),
                    "result":       "WIN",
                    "pl_pct":       pl_pct,
                    "pl_rs":        pl_rs,
                    "days_held":    days_held,
                    "exit_reason":  "Target Hit",
                    "exit_detail":  f"Price reached target ₹{round(target_price,2)} on day {days_held}",
                    "conditions":   cond_details,
                    "entry_rsi":    round(float(ind['rsi'].iloc[entry_idx-1]), 1) if not np.isnan(ind['rsi'].iloc[entry_idx-1]) else None,
                    "entry_ema20":  round(float(ind['ema20'].iloc[entry_idx-1]), 2),
                    "entry_ema50":  round(float(ind['ema50'].iloc[entry_idx-1]), 2),
                    "session":      get_session(df['date'].iloc[entry_idx-1]),
                })
                in_trade = False

            elif low <= sl_price:
                pl_pct   = round((sl_price - entry_price) / entry_price * 100, 2)
                pl_rs    = round((sl_price - entry_price) * int(capital / entry_price), 2)
                cond_details = build_condition_details(conditions, df, ind, entry_idx - 1)
                trades.append({
                    "entry_date":   entry_date,
                    "exit_date":    date_str,
                    "entry_price":  round(entry_price, 2),
                    "exit_price":   round(sl_price, 2),
                    "result":       "LOSS",
                    "pl_pct":       pl_pct,
                    "pl_rs":        pl_rs,
                    "days_held":    days_held,
                    "exit_reason":  "Stop Loss Hit",
                    "exit_detail":  f"Price dropped to SL ₹{round(sl_price,2)} on day {days_held} — loss of {abs(pl_pct)}%",
                    "conditions":   cond_details,
                    "entry_rsi":    round(float(ind['rsi'].iloc[entry_idx-1]), 1) if not np.isnan(ind['rsi'].iloc[entry_idx-1]) else None,
                    "entry_ema20":  round(float(ind['ema20'].iloc[entry_idx-1]), 2),
                    "entry_ema50":  round(float(ind['ema50'].iloc[entry_idx-1]), 2),
                    "session":      get_session(df['date'].iloc[entry_idx-1]),
                })
                in_trade = False

            elif days_held >= max_hold_days:
                pl_pct   = round((close - entry_price) / entry_price * 100, 2)
                pl_rs    = round((close - entry_price) * int(capital / entry_price), 2)
                result   = "WIN" if pl_rs > 0 else "LOSS" if pl_rs < 0 else "FLAT"
                cond_details = build_condition_details(conditions, df, ind, entry_idx - 1)
                trades.append({
                    "entry_date":   entry_date,
                    "exit_date":    date_str,
                    "entry_price":  round(entry_price, 2),
                    "exit_price":   round(close, 2),
                    "result":       result,
                    "pl_pct":       pl_pct,
                    "pl_rs":        pl_rs,
                    "days_held":    days_held,
                    "exit_reason":  "Max Hold Days",
                    "exit_detail":  f"Held {days_held} days — neither SL nor target hit. Exited at close ₹{round(close,2)}",
                    "conditions":   cond_details,
                    "entry_rsi":    round(float(ind['rsi'].iloc[entry_idx-1]), 1) if not np.isnan(ind['rsi'].iloc[entry_idx-1]) else None,
                    "entry_ema20":  round(float(ind['ema20'].iloc[entry_idx-1]), 2),
                    "entry_ema50":  round(float(ind['ema50'].iloc[entry_idx-1]), 2),
                    "session":      get_session(df['date'].iloc[entry_idx-1]),
                })
                in_trade = False

        else:
            # Check entry conditions — signal fires today, entry tomorrow
            if i + 1 >= len(df):
                continue

            all_true = all(eval_condition(c, df, ind, i) for c in conditions)
            if all_true:
                next_row    = df.iloc[i + 1]
                entry_price = float(next_row["open"])
                entry_date  = next_row["date"].strftime("%Y-%m-%d")
                entry_idx   = i + 1
                sl_price    = entry_price * (1 - sl_pct / 100)
                target_price= entry_price * (1 + target_pct / 100)
                in_trade    = True

    if not trades:
        return {
            "error": None,
            "symbol":      symbol,
            "from_date":   from_date,
            "to_date":     to_date,
            "total_trades": 0,
            "message": "No signals fired with these conditions in this date range. Try relaxing conditions.",
            "trades": [],
            "stats": {},
            "equity_curve": [],
            "monthly_pl": {},
        }

    # Calculate stats
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total_pl   = round(sum(t["pl_rs"] for t in trades), 2)
    avg_win    = round(sum(t["pl_rs"] for t in wins)   / len(wins),   2) if wins   else 0
    avg_loss   = round(sum(t["pl_rs"] for t in losses) / len(losses), 2) if losses else 0
    win_rate   = round(len(wins) / len(trades) * 100, 1)
    loss_rate  = round(1 - win_rate / 100, 3)
    expectancy = round((win_rate/100 * avg_win) + (loss_rate * avg_loss), 2)

    # Max drawdown
    running = 0
    peak    = 0
    max_dd  = 0
    for t in trades:
        running += t["pl_rs"]
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd
    max_dd = round(max_dd, 2)

    # Equity curve
    running = 0
    equity_curve = []
    for t in trades:
        running += t["pl_rs"]
        equity_curve.append({"date": t["exit_date"], "pl": round(running, 2)})

    # Monthly P&L
    monthly_pl = {}
    for t in trades:
        month = t["exit_date"][:7]
        monthly_pl[month] = round(monthly_pl.get(month, 0) + t["pl_rs"], 2)

    best  = max(trades, key=lambda x: x["pl_rs"])
    worst = min(trades, key=lambda x: x["pl_rs"])

    return {
        "error":        None,
        "symbol":       symbol,
        "from_date":    from_date,
        "to_date":      to_date,
        "total_trades": len(trades),
        "message":      None,
        "trades":       trades,
        "stats": {
            "total_trades": len(trades),
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     win_rate,
            "total_pl":     total_pl,
            "avg_win":      avg_win,
            "avg_loss":     avg_loss,
            "expectancy":   expectancy,
            "max_drawdown": max_dd,
            "best_trade":   best["pl_rs"],
            "worst_trade":  worst["pl_rs"],
            "best_date":    best["entry_date"],
            "worst_date":   worst["entry_date"],
        },
        "equity_curve": equity_curve,
        "monthly_pl":   monthly_pl,
    }
