"""
db.py — SQLite session history + activity logs for Chart Replay
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta

import charges

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_history.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT NOT NULL,
        timeframe   TEXT NOT NULL,
        from_date   TEXT NOT NULL,
        to_date     TEXT NOT NULL,
        chart_type  TEXT DEFAULT 'candlestick',
        candles     INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id    INTEGER,
        symbol        TEXT NOT NULL,
        strategy      TEXT DEFAULT 'swing',
        entry_price   REAL NOT NULL,
        sl            REAL,
        target        REAL,
        qty           INTEGER DEFAULT 1,
        exit_price    REAL,
        result        TEXT,
        pl_rs         REAL,
        pl_pct        REAL,
        entry_date    TEXT,
        exit_date     TEXT,
        notes         TEXT,
        entry_score   INTEGER,
        score_verdict TEXT,
        created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES sessions(id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS app_logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        event      TEXT NOT NULL,
        details    TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS backtests (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT NOT NULL,
        from_date    TEXT,
        to_date      TEXT,
        conditions   TEXT,
        sl_pct       REAL,
        target_pct   REAL,
        total_trades INTEGER,
        win_rate     REAL,
        total_pl     REAL,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Wallet transaction ledger. Balance is derived as SUM(amount): deposits are
    # positive, opening a trade blocks (negative) its entry cost, closing a trade
    # returns entry cost + gross P&L − total charges (positive).
    c.execute("""
    CREATE TABLE IF NOT EXISTS wallet_txns (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        type       TEXT NOT NULL,          -- DEPOSIT | WITHDRAW | TRADE_OPEN | TRADE_CLOSE
        amount     REAL NOT NULL,          -- signed
        trade_id   INTEGER,
        symbol     TEXT,
        note       TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Migrations — add columns if they don't exist (for existing DBs)
    trade_cols = [
        ("status",        "TEXT DEFAULT 'CLOSED'"),
        ("side",          "TEXT DEFAULT 'long'"),
        ("brokerage",     "REAL DEFAULT 0"),
        ("stt",           "REAL DEFAULT 0"),
        ("stamp",         "REAL DEFAULT 0"),
        ("exchange_txn",  "REAL DEFAULT 0"),
        ("sebi",          "REAL DEFAULT 0"),
        ("ipft",          "REAL DEFAULT 0"),
        ("dp",            "REAL DEFAULT 0"),
        ("gst",           "REAL DEFAULT 0"),
        ("total_charges", "REAL DEFAULT 0"),
        ("gross_pl",      "REAL DEFAULT 0"),
        ("net_pl",        "REAL DEFAULT 0"),
    ]
    for col, decl in trade_cols:
        try:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} {decl}")
            conn.commit()
        except:
            pass  # column already exists

    try:
        c.execute("ALTER TABLE sessions ADD COLUMN candles INTEGER DEFAULT 0")
        conn.commit()
    except:
        pass  # column already exists

    try:
        c.execute("""CREATE TABLE IF NOT EXISTS app_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
    except:
        pass

    try:
        c.execute("""CREATE TABLE IF NOT EXISTS backtests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            from_date TEXT, to_date TEXT, conditions TEXT,
            sl_pct REAL, target_pct REAL,
            total_trades INTEGER, win_rate REAL, total_pl REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
    except:
        pass

    conn.commit()
    conn.close()
    purge_old_logs()


# ── App logs ──────────────────────────────────────────────────
def log_event(event, details=None):
    """Log an app activity event."""
    conn = get_db()
    conn.execute(
        "INSERT INTO app_logs (event, details) VALUES (?, ?)",
        (event, json.dumps(details) if details else None)
    )
    conn.commit()
    conn.close()


def purge_old_logs():
    """Keep only last 7 days of logs."""
    conn = get_db()
    # Use SQLite's own date arithmetic — avoids timezone issues
    conn.execute("DELETE FROM app_logs WHERE created_at < datetime('now', '-7 days')")
    conn.commit()
    conn.close()


def get_logs(limit=100):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM app_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    return result


def get_log_count():
    """Debug — count logs in DB."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM app_logs").fetchone()['c']
    conn.close()
    return count


# ── Backtests ─────────────────────────────────────────────────
def save_backtest(symbol, from_date, to_date, conditions,
                  sl_pct, target_pct, total_trades, win_rate, total_pl):
    conn = get_db()
    conn.execute("""
        INSERT INTO backtests
        (symbol, from_date, to_date, conditions, sl_pct, target_pct,
         total_trades, win_rate, total_pl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, from_date, to_date,
          json.dumps(conditions) if conditions else None,
          sl_pct, target_pct, total_trades, win_rate, total_pl))
    conn.commit()
    conn.close()


def get_backtests(limit=20):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM backtests ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Sessions ──────────────────────────────────────────────────
def create_session(symbol, timeframe, from_date, to_date,
                   chart_type='candlestick', candles=0):
    conn = get_db()
    c = conn.execute("""
        INSERT INTO sessions (symbol, timeframe, from_date, to_date,
                              chart_type, candles)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (symbol, timeframe, from_date, to_date, chart_type, candles))
    conn.commit()
    sid = c.lastrowid
    conn.close()
    return sid


def get_sessions(limit=50):
    conn = get_db()
    rows = conn.execute("""
        SELECT s.*,
               COUNT(t.id) as trade_count,
               SUM(CASE WHEN t.result='WIN'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN t.result='LOSS' THEN 1 ELSE 0 END) as losses,
               ROUND(SUM(t.pl_rs), 2) as total_pl
        FROM sessions s
        LEFT JOIN trades t ON t.session_id = s.id
        GROUP BY s.id
        ORDER BY s.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Trades ────────────────────────────────────────────────────
def save_trade(session_id, trade):
    conn = get_db()
    conn.execute("""
        INSERT INTO trades
        (session_id, symbol, strategy, entry_price, sl, target, qty,
         exit_price, result, pl_rs, pl_pct, entry_date, exit_date,
         notes, entry_score, score_verdict)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        trade.get('symbol', ''),
        trade.get('strategy', 'swing'),
        trade.get('entry', 0),
        trade.get('sl', 0),
        trade.get('tgt', 0),
        trade.get('qty', 1),
        trade.get('exitPrice'),
        trade.get('result'),
        trade.get('pl'),
        trade.get('plPct'),
        trade.get('date', ''),
        trade.get('exitDate', ''),
        trade.get('notes', ''),
        trade.get('score'),
        trade.get('scoreVerdict', ''),
    ))
    conn.commit()
    conn.close()


def get_trades(session_id=None, symbol=None, limit=200):
    conn   = get_db()
    query  = "SELECT * FROM trades WHERE 1=1"
    params = []
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_overall_stats():
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
            ROUND(AVG(CASE WHEN result='WIN'  THEN pl_rs END), 2) as avg_win,
            ROUND(AVG(CASE WHEN result='LOSS' THEN pl_rs END), 2) as avg_loss,
            ROUND(SUM(pl_rs), 2) as total_pl,
            ROUND(AVG(entry_score), 1) as avg_score
        FROM trades
        WHERE result IS NOT NULL
    """).fetchone()
    conn.close()
    d = dict(row)
    total = d['total_trades'] or 0
    wins  = d['wins'] or 0
    d['win_rate'] = round(wins / total * 100, 1) if total > 0 else 0
    return d


def get_monthly_pl():
    conn = get_db()
    rows = conn.execute("""
        SELECT substr(created_at, 1, 7) as month,
               ROUND(SUM(pl_rs), 2) as pl,
               COUNT(*) as trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE result IS NOT NULL
        GROUP BY month
        ORDER BY month ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_symbol_stats():
    conn = get_db()
    rows = conn.execute("""
        SELECT symbol,
               COUNT(*) as trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pl_rs), 2) as total_pl,
               ROUND(AVG(entry_score), 1) as avg_score
        FROM trades
        WHERE result IS NOT NULL
        GROUP BY symbol
        ORDER BY trades DESC
        LIMIT 20
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Dashboard stats ───────────────────────────────────────────
def get_dashboard_stats():
    conn = get_db()

    # Trading performance
    perf = dict(conn.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
            ROUND(SUM(pl_rs), 2) as total_pl,
            ROUND(AVG(CASE WHEN result='WIN'  THEN pl_rs END), 2) as avg_win,
            ROUND(AVG(CASE WHEN result='LOSS' THEN pl_rs END), 2) as avg_loss,
            ROUND(AVG(entry_score), 1) as avg_score,
            MAX(pl_rs) as best_trade,
            MIN(pl_rs) as worst_trade
        FROM trades WHERE result IS NOT NULL
    """).fetchone())
    total = perf['total_trades'] or 0
    perf['win_rate'] = round((perf['wins'] or 0) / total * 100, 1) if total > 0 else 0

    # Win streak
    trades = conn.execute(
        "SELECT result FROM trades WHERE result IN ('WIN','LOSS') ORDER BY created_at ASC"
    ).fetchall()
    cur_streak = best_streak = 0
    for t in trades:
        if t['result'] == 'WIN':
            cur_streak += 1
            best_streak = max(best_streak, cur_streak)
        else:
            cur_streak = 0
    perf['best_streak'] = best_streak
    perf['cur_streak']  = cur_streak

    # App usage
    usage = dict(conn.execute("""
        SELECT
            COUNT(*) as total_sessions,
            SUM(candles) as total_candles,
            COUNT(DISTINCT symbol) as unique_symbols
        FROM sessions
    """).fetchone())

    # Most replayed symbol
    top_sym = conn.execute("""
        SELECT symbol, COUNT(*) as cnt FROM sessions
        GROUP BY symbol ORDER BY cnt DESC LIMIT 1
    """).fetchone()
    usage['top_symbol'] = dict(top_sym) if top_sym else None

    # Backtest count
    bt_count = conn.execute("SELECT COUNT(*) as cnt FROM backtests").fetchone()
    usage['backtests_run'] = bt_count['cnt'] if bt_count else 0

    # Sessions per day (last 14 days)
    daily = conn.execute("""
        SELECT substr(created_at,1,10) as day, COUNT(*) as cnt
        FROM sessions
        WHERE created_at >= date('now','-14 days')
        GROUP BY day ORDER BY day ASC
    """).fetchall()
    usage['daily_sessions'] = [dict(r) for r in daily]

    # Strategy breakdown
    strategies = conn.execute("""
        SELECT strategy,
               COUNT(*) as trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pl_rs),2) as pl
        FROM trades WHERE result IS NOT NULL
        GROUP BY strategy
    """).fetchall()
    perf['strategies'] = [dict(r) for r in strategies]

    # Monthly P&L
    monthly = conn.execute("""
        SELECT substr(created_at,1,7) as month,
               ROUND(SUM(pl_rs),2) as pl,
               COUNT(*) as trades
        FROM trades WHERE result IS NOT NULL
        GROUP BY month ORDER BY month ASC
    """).fetchall()
    perf['monthly'] = [dict(r) for r in monthly]

    # Equity curve
    eq = []
    running = 0
    for t in conn.execute(
        "SELECT pl_rs, created_at FROM trades WHERE result IS NOT NULL ORDER BY created_at ASC"
    ).fetchall():
        running += (t['pl_rs'] or 0)
        eq.append({"date": t['created_at'][:10], "pl": round(running, 2)})
    perf['equity'] = eq

    # Symbol stats
    sym_stats = conn.execute("""
        SELECT symbol,
               COUNT(*) as trades,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pl_rs),2) as pl
        FROM trades WHERE result IS NOT NULL
        GROUP BY symbol ORDER BY trades DESC LIMIT 8
    """).fetchall()
    perf['symbols'] = [dict(r) for r in sym_stats]

    conn.close()
    return {"performance": perf, "usage": usage}


# ── Wallet ────────────────────────────────────────────────────
def get_balance():
    """Available balance = sum of all ledger entries."""
    conn = get_db()
    row = conn.execute("SELECT COALESCE(SUM(amount),0) AS bal FROM wallet_txns").fetchone()
    conn.close()
    return round(row["bal"] or 0.0, 2)


def get_locked():
    """Capital actually blocked by open trades (margin), from their open ledger entries."""
    conn = get_db()
    row = conn.execute("""
        SELECT COALESCE(-SUM(w.amount),0) AS locked
        FROM wallet_txns w JOIN trades t ON t.id = w.trade_id
        WHERE w.type='TRADE_OPEN' AND t.status='OPEN'
    """).fetchone()
    conn.close()
    return round(row["locked"] or 0.0, 2)


def wallet_deposit(amount, note="Deposit"):
    amount = round(float(amount), 2)
    if amount <= 0:
        return {"error": "Amount must be positive"}
    conn = get_db()
    conn.execute(
        "INSERT INTO wallet_txns (type, amount, note) VALUES ('DEPOSIT', ?, ?)",
        (amount, note))
    conn.commit()
    conn.close()
    return {"balance": get_balance()}


def get_wallet_summary():
    conn = get_db()
    dep = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS v FROM wallet_txns WHERE type='DEPOSIT'").fetchone()["v"]
    closed = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(net_pl),0)        AS net,
               COALESCE(SUM(gross_pl),0)      AS gross,
               COALESCE(SUM(total_charges),0) AS charges
        FROM trades WHERE status='CLOSED' AND total_charges > 0
    """).fetchone()
    open_n = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'").fetchone()["n"]
    conn.close()
    return {
        "balance":       get_balance(),
        "locked":        get_locked(),
        "deposited":     round(dep or 0.0, 2),
        "net_realized":  round(closed["net"] or 0.0, 2),
        "gross_realized":round(closed["gross"] or 0.0, 2),
        "total_charges": round(closed["charges"] or 0.0, 2),
        "closed_trades": closed["n"] or 0,
        "open_trades":   open_n or 0,
    }


def get_ledger(limit=300):
    """Ledger newest-first, each row annotated with the running balance."""
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM wallet_txns ORDER BY id ASC").fetchall()]
    conn.close()
    running = 0.0
    for r in rows:
        running += r["amount"]
        r["balance"] = round(running, 2)
    rows.reverse()
    return rows[:limit]


def open_trade(session_id, symbol, side, segment, entry, sl, tgt, qty,
               entry_date, notes="", score=None, score_verdict=""):
    """Open a position: gate on balance, insert OPEN trade, block entry capital."""
    entry = float(entry); qty = int(qty)
    cost     = round(entry * qty, 2)
    margin   = charges.margin_required(segment, entry, qty)   # intraday blocks less than full
    leverage = charges.leverage_for(segment)
    bal      = get_balance()
    if margin > bal:
        note = " margin" if leverage > 1 else ""
        return {"error": f"Insufficient balance. Need ₹{margin:,.2f}{note}, available ₹{bal:,.2f}."}

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO trades
        (session_id, symbol, strategy, side, entry_price, sl, target, qty,
         entry_date, notes, entry_score, score_verdict, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
    """, (session_id, (symbol or "").upper(), segment, (side or "long").lower(),
          entry, sl, tgt, qty, entry_date, notes, score, score_verdict))
    tid = cur.lastrowid
    lev_note = f" · {leverage:g}x margin" if leverage > 1 else ""
    conn.execute("""
        INSERT INTO wallet_txns (type, amount, trade_id, symbol, note)
        VALUES ('TRADE_OPEN', ?, ?, ?, ?)
    """, (-margin, tid, (symbol or "").upper(),
          f"Open {(side or 'long').lower()} {qty}@₹{entry}{lev_note}"))
    conn.commit()
    conn.close()
    return {"trade_id": tid, "balance": get_balance(), "locked": get_locked(),
            "cost": cost, "margin": margin, "leverage": leverage}


def close_trade(trade_id, exit_price, exit_date):
    """Close a position: compute charges, settle wallet, persist the breakdown."""
    exit_price = float(exit_price)
    conn = get_db()
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or row["status"] != "OPEN":
        conn.close()
        return {"error": "Trade not open"}

    entry   = row["entry_price"]
    qty     = row["qty"]
    side    = row["side"] or "long"
    segment = row["strategy"] or "swing"

    gross, ch, net = charges.settle(segment, side, entry, exit_price, qty)
    entry_value = round(entry * qty, 2)
    pl_pct = round(gross / entry_value * 100, 2) if entry_value else 0.0
    result = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"

    conn.execute("""
        UPDATE trades SET
            exit_price=?, exit_date=?, result=?, status='CLOSED',
            brokerage=?, stt=?, stamp=?, exchange_txn=?, sebi=?, ipft=?, dp=?, gst=?,
            total_charges=?, gross_pl=?, net_pl=?, pl_rs=?, pl_pct=?
        WHERE id=?
    """, (exit_price, exit_date, result,
          ch["brokerage"], ch["stt"], ch["stamp"], ch["exchange_txn"], ch["sebi"],
          ch["ipft"], ch["dp"], ch["gst"], ch["total_charges"], gross, net, net, pl_pct,
          trade_id))

    # Return exactly what was blocked at open (margin) + net P&L (gross − charges).
    blk = conn.execute(
        "SELECT COALESCE(-SUM(amount),0) AS blocked FROM wallet_txns WHERE trade_id=? AND type='TRADE_OPEN'",
        (trade_id,)).fetchone()
    blocked = blk["blocked"] if blk and blk["blocked"] else entry_value
    credit  = round(blocked + net, 2)
    conn.execute("""
        INSERT INTO wallet_txns (type, amount, trade_id, symbol, note)
        VALUES ('TRADE_CLOSE', ?, ?, ?, ?)
    """, (credit, trade_id, row["symbol"],
          f"Close {side} {qty}@₹{exit_price} · net {net:+.2f}"))
    conn.commit()
    conn.close()
    return {
        "balance": get_balance(), "locked": get_locked(),
        "gross_pl": gross, "net_pl": net, "charges": ch, "result": result,
    }


def get_open_positions():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("""
        SELECT t.id, t.symbol, t.strategy AS segment, t.side, t.entry_price, t.qty,
               t.sl, t.target, t.entry_date, (t.entry_price*t.qty) AS cost,
               COALESCE((SELECT -SUM(w.amount) FROM wallet_txns w
                         WHERE w.trade_id=t.id AND w.type='TRADE_OPEN'),
                        t.entry_price*t.qty) AS blocked
        FROM trades t WHERE t.status='OPEN' ORDER BY t.id DESC
    """).fetchall()]
    conn.close()
    return rows


def cancel_trade(trade_id):
    """Cancel an open position and release its blocked capital (no charges)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or row["status"] != "OPEN":
        conn.close()
        return {"error": "Trade not open"}
    blk = conn.execute(
        "SELECT COALESCE(-SUM(amount),0) AS blocked FROM wallet_txns WHERE trade_id=? AND type='TRADE_OPEN'",
        (trade_id,)).fetchone()
    refund = round(blk["blocked"] if blk and blk["blocked"] else row["entry_price"]*row["qty"], 2)
    conn.execute("UPDATE trades SET status='CANCELLED' WHERE id=?", (trade_id,))
    conn.execute("""
        INSERT INTO wallet_txns (type, amount, trade_id, symbol, note)
        VALUES ('TRADE_CANCEL', ?, ?, ?, 'Cancelled — capital released')
    """, (refund, trade_id, row["symbol"]))
    conn.commit()
    conn.close()
    return {"balance": get_balance(), "locked": get_locked()}


def get_charges_report():
    """Per-trade charge breakdown + monthly aggregates for the wallet report."""
    conn = get_db()
    # total_charges > 0 keeps only trades priced by the new charge engine and
    # excludes pre-wallet rows (which the migration defaults to CLOSED / 0 charges
    # with legacy locale-format exit dates).
    per_trade = [dict(r) for r in conn.execute("""
        SELECT id, symbol, strategy AS segment, side, entry_price, exit_price, qty,
               gross_pl, brokerage, stt, stamp, exchange_txn, sebi, ipft, dp, gst,
               total_charges, net_pl, result, entry_date, exit_date
        FROM trades
        WHERE status='CLOSED' AND total_charges > 0
        ORDER BY exit_date DESC, id DESC
        LIMIT 500
    """).fetchall()]
    monthly = [dict(r) for r in conn.execute("""
        SELECT substr(exit_date,1,7) AS month,
               COUNT(*)                       AS trades,
               ROUND(SUM(total_charges),2)    AS charges,
               ROUND(SUM(brokerage),2)        AS brokerage,
               ROUND(SUM(stt),2)              AS stt,
               ROUND(SUM(gst),2)              AS gst,
               ROUND(SUM(gross_pl),2)         AS gross_pl,
               ROUND(SUM(net_pl),2)           AS net_pl
        FROM trades
        WHERE status='CLOSED' AND total_charges > 0 AND exit_date IS NOT NULL AND exit_date != ''
        GROUP BY month ORDER BY month ASC
    """).fetchall()]
    conn.close()
    return {"trades": per_trade, "monthly": monthly}