"""
charges.py — Groww equity charge engine.

Pure functions (no I/O) that compute realistic Indian retail trading costs for a
completed round-trip trade (entry + exit), split by segment (delivery / intraday)
and side (long / short). All rates are named constants — edit them here if Groww
revises its schedule.

Rate reference (Groww equity, as configured):
  Brokerage : lower of ₹20 or 0.1% per executed order, minimum ₹5 (both legs)
  STT       : intraday 0.025% on SELL leg;  delivery 0.1% on BUY + SELL
  Stamp duty: intraday 0.003% on BUY leg;   delivery 0.015% on BUY leg
  Exchange  : NSE 0.00297% on both legs
  SEBI      : 0.0001% on both legs
  IPFT      : NSE 0.0001% on both legs
  DP charge : delivery ₹20 on SELL (₹3.5 depository + ₹16.5 Groww); ₹0 if qty < 100
  GST       : 18% of (brokerage + exchange + SEBI + IPFT + DP)
"""

# ── Rate constants ────────────────────────────────────────────
BROKERAGE_PCT = 0.001      # 0.1% per executed order
BROKERAGE_MAX = 20.0       # ₹20 cap per order
BROKERAGE_MIN = 5.0        # ₹5 floor per order

STT_INTRADAY_SELL = 0.00025    # 0.025% on sell leg (intraday)
STT_DELIVERY      = 0.001      # 0.1% on buy + sell legs (delivery)

STAMP_INTRADAY = 0.00003       # 0.003% on buy leg (intraday)
STAMP_DELIVERY = 0.00015       # 0.015% on buy leg (delivery)

EXCHANGE_NSE = 0.0000297       # 0.00297% on both legs
SEBI_RATE    = 0.000001        # 0.0001% on both legs
IPFT_NSE     = 0.000001        # 0.0001% on both legs

DP_DELIVERY_SELL = 20.0        # ₹3.5 depository + ₹16.5 Groww, on delivery sell
DP_MIN_QTY       = 100         # DP charge waived if qty < 100

GST_RATE = 0.18                # 18% on brokerage + exchange + SEBI + IPFT + DP

# Intraday (MIS) buying power. Only this fraction of the position value is blocked
# as margin — e.g. 5x means a ₹230 share needs ₹46 (₹230/5). Delivery = full payment.
# Tweak this one number to change intraday margin (e.g. 5.5 → ~₹42 for a ₹230 share).
INTRADAY_LEVERAGE = 5.0


def _norm_segment(segment):
    """Map UI strategy names to a canonical segment."""
    s = (segment or "").lower()
    return "delivery" if s in ("delivery", "swing") else "intraday"


def _brokerage(order_value):
    """Groww brokerage for a single executed order."""
    if order_value <= 0:
        return 0.0
    return round(max(BROKERAGE_MIN, min(BROKERAGE_MAX, BROKERAGE_PCT * order_value)), 2)


def margin_required(segment, entry, qty):
    """Capital blocked to open a position: full value for delivery, a leveraged
    fraction for intraday."""
    cost = entry * qty
    if _norm_segment(segment) == "intraday":
        return round(cost / INTRADAY_LEVERAGE, 2)
    return round(cost, 2)


def leverage_for(segment):
    return INTRADAY_LEVERAGE if _norm_segment(segment) == "intraday" else 1.0


def gross_pl(side, entry, exit_price, qty):
    """Gross P&L before charges. Long profits when price rises, short when it falls."""
    if (side or "long").lower() == "short":
        return round((entry - exit_price) * qty, 2)
    return round((exit_price - entry) * qty, 2)


def compute_charges(segment, side, entry, exit_price, qty):
    """
    Compute every charge line item for a round-trip trade.

    Returns a dict with individual line items (all rounded to 2 dp) plus
    total_charges. total_charges is the sum of the rounded line items so it
    always reconciles with what the UI displays.
    """
    segment = _norm_segment(segment)
    side    = (side or "long").lower()
    qty     = int(qty)

    # Which leg is the BUY and which is the SELL.
    # Long : buy at entry, sell at exit.  Short: sell at entry, buy (cover) at exit.
    if side == "short":
        sell_value = entry * qty
        buy_value  = exit_price * qty
    else:
        buy_value  = entry * qty
        sell_value = exit_price * qty

    turnover = buy_value + sell_value

    brokerage = _brokerage(buy_value) + _brokerage(sell_value)

    if segment == "delivery":
        stt   = STT_DELIVERY * turnover
        stamp = STAMP_DELIVERY * buy_value
        dp    = DP_DELIVERY_SELL if qty >= DP_MIN_QTY else 0.0
    else:  # intraday
        stt   = STT_INTRADAY_SELL * sell_value
        stamp = STAMP_INTRADAY * buy_value
        dp    = 0.0

    exchange = EXCHANGE_NSE * turnover
    sebi     = SEBI_RATE * turnover
    ipft     = IPFT_NSE * turnover
    gst      = GST_RATE * (brokerage + exchange + sebi + ipft + dp)

    line_items = {
        "brokerage":    round(brokerage, 2),
        "stt":          round(stt, 2),
        "stamp":        round(stamp, 2),
        "exchange_txn": round(exchange, 2),
        "sebi":         round(sebi, 2),
        "ipft":         round(ipft, 2),
        "dp":           round(dp, 2),
        "gst":          round(gst, 2),
    }
    line_items["total_charges"] = round(sum(line_items.values()), 2)
    return line_items


def settle(segment, side, entry, exit_price, qty):
    """Convenience: returns (gross_pl, charges_dict, net_pl) for a round-trip."""
    g  = gross_pl(side, entry, exit_price, qty)
    ch = compute_charges(segment, side, entry, exit_price, qty)
    net = round(g - ch["total_charges"], 2)
    return g, ch, net
