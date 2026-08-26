"""
app.py — Chart Replay App
Run: python app.py
Open: http://localhost:5001
"""

from flask import Flask, render_template, request, jsonify
import os, json
from datetime import datetime

from data import fetch_yahoo, build_candles, fetch_nifty_context, calc_supertrend, calc_pivot_points
from backtest import run_backtest
from analysis import get_ta_summary, get_fundamentals
from db import init_db as init_replay_db, create_session, save_trade,                get_sessions, get_trades, get_overall_stats, get_monthly_pl,                get_symbol_stats, log_event, get_logs, save_backtest,                get_backtests, get_dashboard_stats,                wallet_deposit, get_wallet_summary, get_ledger,                open_trade, close_trade, get_charges_report,                get_open_positions, cancel_trade

import json
import numpy as np
import warnings
warnings.filterwarnings('ignore')

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.bool_):    return bool(obj)
        if isinstance(obj, np.integer):  return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray):  return obj.tolist()
        return super().default(obj)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

app.json_encoder = NumpyEncoder
init_replay_db()


@app.route("/")
def index():
    return render_template("help.html")


@app.route("/replay")
def replay():
    symbol = request.args.get("symbol", "RELIANCE")
    return render_template("index.html", symbol=symbol.upper())


@app.route("/api/candles")
def api_candles():
    symbol     = request.args.get("symbol",     "RELIANCE").upper()
    from_date  = request.args.get("from_date",  "")
    to_date    = request.args.get("to_date",    "")
    interval   = request.args.get("interval",   "1D")
    chart_type = request.args.get("chart_type", "candlestick")

    if not from_date or not to_date:
        return jsonify({"error": "from_date and to_date required"}), 400

    df, err = fetch_yahoo(symbol, from_date, to_date, interval)
    if err:
        return jsonify({"error": err}), 404

    candles = build_candles(df, chart_type)
    if not candles:
        return jsonify({"error": f"No candles for {symbol}"}), 404

    # Nifty context — only for daily timeframe (fast enough)
    nifty = {}
    if interval == "1D":
        nifty = fetch_nifty_context(from_date, to_date)

    return jsonify({
        "symbol":     symbol,
        "interval":   interval,
        "chart_type": chart_type,
        "from":       from_date,
        "to":         to_date,
        "total":      len(candles),
        "candles":    candles,
        "nifty":      nifty,
    })


@app.route("/backtest")
def backtest():
    return render_template("backtest.html")


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data       = request.json
    symbol     = data.get("symbol", "RELIANCE").upper()
    from_date  = data.get("from_date", "")
    to_date    = data.get("to_date", "")
    conditions = data.get("conditions", [])
    sl_pct     = float(data.get("sl_pct",     1.5))
    target_pct = float(data.get("target_pct", 3.75))
    max_hold   = int(data.get("max_hold",     10))
    capital    = float(data.get("capital",    50000))

    if not from_date or not to_date:
        return jsonify({"error": "from_date and to_date required"}), 400
    if not conditions:
        return jsonify({"error": "Add at least one entry condition"}), 400

    result = run_backtest(symbol, from_date, to_date,
                          conditions, sl_pct, target_pct,
                          max_hold, capital)
    # Save backtest to DB and log
    if result and not result.get("error") and result.get("total_trades",0) > 0:
        s = result.get("stats", {})
        save_backtest(symbol, from_date, to_date, conditions,
                      sl_pct, target_pct,
                      s.get("total_trades",0),
                      s.get("win_rate",0),
                      s.get("total_pl",0))
        log_event("backtest_run", {
            "symbol": symbol, "from": from_date, "to": to_date,
            "trades": s.get("total_trades",0), "win_rate": s.get("win_rate",0)
        })
    return jsonify(result)


# ── Dashboard ─────────────────────────────────────────────────
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(get_dashboard_stats())


@app.route("/api/backtests")
def api_backtests():
    return jsonify(get_backtests())


@app.route("/api/logs")
def api_logs():
    logs = get_logs(100)
    return jsonify(logs)


@app.route("/api/logs/test")
def api_logs_test():
    """Test route — log a test event and return count."""
    from db import get_log_count
    log_event("test_event", {"msg": "dashboard test"})
    return jsonify({"count": get_log_count(), "logs": get_logs(5)})


@app.route("/api/log", methods=["POST"])
def api_log():
    d = request.json
    log_event(d.get("event",""), d.get("details"))
    return jsonify({"ok": True})


# ── SuperTrend + Pivot endpoint ───────────────────────────────
@app.route("/api/supertrend")
def api_supertrend():
    symbol     = request.args.get("symbol", "RELIANCE").upper()
    from_date  = request.args.get("from_date", "")
    to_date    = request.args.get("to_date", "")
    interval   = request.args.get("interval", "5min")
    atr_period = int(request.args.get("atr_period", 7))
    multiplier = float(request.args.get("multiplier", 3.0))

    df, err = fetch_yahoo(symbol, from_date, to_date, interval)
    if err or df is None or df.empty:
        return jsonify({"error": err or "No data"})

    # Calculate SuperTrend
    st = calc_supertrend(df, atr_period, multiplier)

    # Build two clean series — no nulls, no shared points
    up_series   = []
    down_series = []

    for i in range(len(df)):
        ts    = int(df["date"].iloc[i].timestamp())
        trend = int(st["trend"].iloc[i])
        val   = round(float(st["supertrend"].iloc[i]), 2)
        if trend == 1:
            up_series.append({"time": ts, "value": val})
        else:
            down_series.append({"time": ts, "value": val})

    # Calculate Pivot Points
    pivots = calc_pivot_points(df)

    # Build pivot lines per date
    pivot_lines = []
    df2 = df.copy()
    df2["date_only"] = df2["date"].dt.date

    for date_str, pv in pivots.items():
        # Get all candle timestamps for this date
        day_candles = df2[df2["date_only"].astype(str) == date_str]
        if day_candles.empty:
            continue
        start_ts = int(day_candles["date"].iloc[0].timestamp())
        end_ts   = int(day_candles["date"].iloc[-1].timestamp())
        pivot_lines.append({
            "date":     date_str,
            "start_ts": start_ts,
            "end_ts":   end_ts,
            "pp":  pv["pp"],
            "r1":  pv["r1"],
            "s1":  pv["s1"],
            "r2":  pv["r2"],
            "s2":  pv["s2"],
        })

    return jsonify({
        "up_series":   up_series,
        "down_series": down_series,
        "pivot_lines": pivot_lines,
        "atr_period":  atr_period,
        "multiplier":  multiplier,
    })


# ── Chart data for analysis page (lightweight — no ticks) ────
@app.route("/api/chart-data")
def api_chart_data():
    symbol    = request.args.get("symbol", "RELIANCE").upper()
    from_date = request.args.get("from_date", "")
    to_date   = request.args.get("to_date", "")
    interval  = request.args.get("interval", "1D")

    if not from_date or not to_date:
        from datetime import datetime, timedelta
        to_date   = datetime.now().strftime("%Y-%m-%d")
        days      = 60 if interval in ["1min","5min","15min","1H"] else 365
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    df, err = fetch_yahoo(symbol, from_date, to_date, interval)
    if err or df is None or df.empty:
        return jsonify({"error": err or "No data"})

    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time":  int(row["date"].timestamp()),
            "open":  round(float(row["open"]),  2),
            "high":  round(float(row["high"]),  2),
            "low":   round(float(row["low"]),   2),
            "close": round(float(row["close"]), 2),
        })
    return jsonify({"candles": candles, "total": len(candles)})


# ── Analysis ──────────────────────────────────────────────────
@app.route("/analysis")
def analysis():
    symbol = request.args.get("symbol", "RELIANCE")
    return render_template("analysis.html", symbol=symbol.upper())


@app.route("/api/ta")
def api_ta():
    symbol    = request.args.get("symbol",    "RELIANCE").upper()
    timeframe = request.args.get("timeframe", "1D")
    from_date = request.args.get("from_date", None)
    to_date   = request.args.get("to_date",   None)
    result    = get_ta_summary(symbol, timeframe, from_date=from_date, to_date=to_date)
    if result and not result.get("error"):
        log_event("analysis_run", {"symbol": symbol, "timeframe": timeframe})
    return jsonify(result)


@app.route("/api/fundamentals")
def api_fundamentals():
    symbol = request.args.get("symbol", "RELIANCE").upper()
    result = get_fundamentals(symbol)
    return jsonify(result)


# ── Session History ────────────────────────────────────────────
@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_sessions())


@app.route("/api/session/create", methods=["POST"])
def api_create_session():
    d = request.json
    sid = create_session(
        d.get("symbol",""), d.get("timeframe","1D"),
        d.get("from_date",""), d.get("to_date",""),
        d.get("chart_type","candlestick"),
        d.get("candles", 0)
    )
    log_event("replay_started", {
        "symbol": d.get("symbol"), "timeframe": d.get("timeframe"),
        "candles": d.get("candles",0)
    })
    return jsonify({"session_id": sid})


@app.route("/api/session/save-trade", methods=["POST"])
def api_save_trade():
    d = request.json
    save_trade(d.get("session_id"), d.get("trade", {}))
    return jsonify({"ok": True})


@app.route("/api/history/stats")
def api_history_stats():
    return jsonify({
        "overall":  get_overall_stats(),
        "monthly":  get_monthly_pl(),
        "symbols":  get_symbol_stats(),
        "sessions": get_sessions(20),
    })


@app.route("/api/history/trades")
def api_history_trades():
    symbol = request.args.get("symbol")
    return jsonify(get_trades(symbol=symbol))


# ── Wallet ──────────────────────────────────────────────────────
@app.route("/wallet")
def wallet():
    return render_template("wallet.html")


@app.route("/api/wallet")
def api_wallet():
    return jsonify({
        "summary": get_wallet_summary(),
        "ledger":  get_ledger(),
        "open":    get_open_positions(),
    })


@app.route("/api/wallet/cancel-trade", methods=["POST"])
def api_cancel_trade():
    d = request.json or {}
    res = cancel_trade(d.get("trade_id"))
    if res.get("error"):
        return jsonify(res), 400
    return jsonify(res)


@app.route("/api/wallet/deposit", methods=["POST"])
def api_wallet_deposit():
    d = request.json or {}
    try:
        amt = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid amount"}), 400
    res = wallet_deposit(amt)
    if res.get("error"):
        return jsonify(res), 400
    log_event("wallet_deposit", {"amount": amt})
    return jsonify({"ok": True, "summary": get_wallet_summary()})


@app.route("/api/wallet/open-trade", methods=["POST"])
def api_open_trade():
    d = request.json or {}
    res = open_trade(
        d.get("session_id"), d.get("symbol", ""), d.get("side", "long"),
        d.get("segment", "swing"), d.get("entry"), d.get("sl"), d.get("tgt"),
        d.get("qty", 1), d.get("entry_date", ""), d.get("notes", ""),
        d.get("score"), d.get("score_verdict", ""))
    if res.get("error"):
        return jsonify(res), 400
    log_event("trade_entered", {
        "symbol": d.get("symbol"), "side": d.get("side"), "segment": d.get("segment"),
        "entry": d.get("entry"), "qty": d.get("qty"),
    })
    return jsonify(res)


@app.route("/api/wallet/close-trade", methods=["POST"])
def api_close_trade():
    d = request.json or {}
    res = close_trade(d.get("trade_id"), d.get("exit"), d.get("exit_date", ""))
    if res.get("error"):
        return jsonify(res), 400
    log_event("trade_closed", {
        "trade_id": d.get("trade_id"), "net_pl": res.get("net_pl"),
        "charges": res.get("charges", {}).get("total_charges"),
    })
    return jsonify(res)


@app.route("/api/wallet/charges")
def api_wallet_charges():
    return jsonify(get_charges_report())


if __name__ == "__main__":
    print("\n" + "="*52)
    print("  Chart Replay App")
    print("="*52)
    print("  Help page : http://localhost:5001")
    print("  Replay    : http://localhost:5001/replay")
    print("  Stop      : Ctrl+C")
    print("="*52 + "\n")
    app.run(debug=True, port=5001, use_reloader=False)