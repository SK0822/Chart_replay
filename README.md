# ChartReplay

**Practice paper trading on historical NSE data — candle by candle, like a match replay.**

ChartReplay lets you go back in time and step through real historical price movement as if it were happening live. Read the chart, draw your levels, log paper trades with stop-loss and target, and see whether the setup actually worked — with realistic Indian brokerage and charges applied to every trade.

Built with Flask + vanilla JavaScript and [lightweight-charts](https://github.com/tradingview/lightweight-charts). No build step, no API keys — historical data comes free from Yahoo Finance.

> ⚠️ **Paper trading only.** Educational tool for practicing on historical data. Not investment advice. Backtest results do not predict future performance. It places no real orders and is not investment advice.


---

## Features

- **Candle-by-candle replay** — play, pause, and step through history at adjustable speed; you only ever see the past, never the future.
- **Chart types** — Candlestick, Heikin Ashi, Line, Area, Bar.
- **Indicators** — EMA 20/50/200, SMA 20/50, Bollinger Bands (2σ + 3σ), VWAP, Volume, RSI, MACD, SuperTrend.
- **Drawing tools** — horizontal line, support, resistance, and trendline — draggable directly on the chart.
- **Candlestick patterns** — Doji, Hammer, Engulfing, Inside Bar, Morning/Evening Star, and more.
- **Paper trades** with automatic WIN / LOSS detection (judged on **net** P&L, after charges).
- **Intraday short (sell-side)** — sell first, buy to cover; profit when price falls *(intraday only)*.
- **Wallet** — deposit funds, capital is enforced on every trade, with a persistent balance and full transaction ledger.
- **Realistic Groww charges** — brokerage, STT, exchange transaction, SEBI, IPFT, stamp duty, GST, and DP charges; your balance is always net of costs.
- **Intraday margin / leverage** — MIS positions block only the margin (5×), not the full position value.
- **Tax & charges report** — per-trade breakdown grouped by date, with monthly totals.
- **Strategy backtesting** — define rule-based conditions and simulate them across historical data.
- **Technical analysis + fundamentals** — trend, support/resistance, patterns, and Yahoo fundamentals.
- Bookmarks, jump-to-date, strict mode, sound effects, keyboard-driven playback, and CSV export.

---

## Setup

Requires **Python 3.9+**.

```bash
# 1. Clone
git clone https://github.com/<your-username>/ChartReplay.git
cd ChartReplay

# 2. (Recommended) create a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python app.py
```

Then open your browser:

| Page | URL |
|------|-----|
| Landing / how-it-works | http://localhost:5001 |
| Replay (main screen) | http://localhost:5001/replay |
| Backtest | http://localhost:5001/backtest |
| Analysis | http://localhost:5001/analysis |
| Dashboard | http://localhost:5001/dashboard |
| History | http://localhost:5001/history |
| Wallet | http://localhost:5001/wallet |

The SQLite database (`session_history.db`) is created automatically on first run.

---

## Usage

1. **Load data** — on the Replay page, type an NSE symbol (e.g. `RELIANCE`, `HDFCBANK`, `INFY`), pick a timeframe and date range, and click **Load Data**.
2. **Fund your wallet** — deposit a starting balance on the Wallet page (capital is enforced on trades).
3. **Replay** — press **Space** to play; use the speed control and keyboard to step through candles.
4. **Trade** — when you spot a setup, open the trade modal (**T**), set entry / stop-loss / target / qty, choose Buy or Sell, and enter. The wallet blocks the required capital (or margin for intraday).
5. **Review** — closed trades settle net of charges; check the Wallet ledger, charges report, and History/Dashboard for your performance.

### Wallet & charges
- Opening a **delivery** position blocks its full cost (`entry × qty`); opening an **intraday** position blocks only the margin (position ÷ 5). Closing returns the blocked capital **± P&L − charges**.
- Trades larger than your available balance are rejected.
- **Swing** = equity delivery (buy-side only). **Intraday** supports **Buy or Sell**.
- Charge rates live as named constants in [`charges.py`](charges.py) — edit them if Groww revises its schedule.

---

## Keyboard Shortcuts

| Key   | Action            |
|-------|-------------------|
| Space | Play / Pause      |
| →     | Step forward      |
| ←     | Step back         |
| B     | Bookmark candle   |
| T     | Open trade modal  |
| S     | Toggle strict     |
| R     | Reset replay      |
| Esc   | Close modal       |

---

## Project structure

```
ChartReplay/
├── app.py            # Flask entry point + all routes
├── data.py           # Yahoo Finance fetch + indicator calculation
├── db.py             # SQLite: session history, activity log, wallet + trades
├── charges.py        # Groww equity charge engine (brokerage/STT/GST/margin)
├── analysis.py       # Technical analysis + fundamentals engine
├── backtest.py       # Strategy builder & backtesting engine
├── patterns.py       # Candlestick pattern detection
├── requirements.txt
├── templates/        # Jinja2 pages (replay, backtest, analysis, wallet, ...)
└── static/
    └── app.css       # Design system (tokens + all page styles)
```

---

## Troubleshooting

- **"Unable to fetch stock data" / SSL certificate errors** — the app verifies Yahoo's TLS with the `certifi` bundle and automatically retries without verification on corporate SSL-inspection networks. To force the no-verify path, set the environment variable `CHARTREPLAY_INSECURE_SSL=1` before running `python app.py`.
- **Intraday data (1min / 5min)** is only available for a short recent window from Yahoo Finance; the app auto-adjusts your date range to fit.
- **Symbol not found** — type the symbol exactly as shown on NSE (e.g. `RELIANCE`, not `RELAINCE`). The app automatically appends the `.NS` suffix for Yahoo Finance.

---

## Tech stack

- **Backend:** Python, Flask, SQLite
- **Data:** yfinance, pandas, numpy, certifi
- **Frontend:** vanilla JavaScript, Jinja2 templates, [lightweight-charts](https://github.com/tradingview/lightweight-charts)

---

## Disclaimer

ChartReplay is for **education and practice only**. It executes no real trades, connects to no broker, and provides no financial advice. Historical data is sourced from Yahoo Finance and may be delayed, incomplete, or inaccurate. Always do your own research before trading real capital.
