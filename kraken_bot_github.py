"""
Kraken SMA Crossover Bot — GitHub Actions ("one-shot") version
================================================================

This version does ONE check-and-act cycle, then exits. It's designed to
be triggered on a schedule by GitHub Actions (see
.github/workflows/trading-bot.yml), so you don't need any server, VPS,
or credit card — just a free GitHub account.

State (whether you're currently in a position) is saved to
bot_state.json, which the GitHub Actions workflow commits back to the
repo after every run so it's remembered next time.

Same strategy and safety notes as kraken_bot.py — read that file's
docstring for the full explanation of DRY_RUN, stop-loss, risk sizing,
and the "never enable withdrawal on your API key" warning.
"""

import os
import sys
import json
import logging

try:
    import ccxt
except ImportError:
    sys.exit("Missing dependency 'ccxt'. Install with: pip install ccxt")

# --------------------------------------------------------------------------
# Configuration (from environment variables / GitHub Secrets)
# --------------------------------------------------------------------------

API_KEY = os.environ.get("KRAKEN_API_KEY", "")
API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")

SYMBOL = os.environ.get("SYMBOL", "BTC/USD")
TIMEFRAME = os.environ.get("TIMEFRAME", "15m")
SHORT_MA = int(os.environ.get("SHORT_MA", "9"))
LONG_MA = int(os.environ.get("LONG_MA", "21"))
RISK_PER_TRADE_PCT = float(os.environ.get("RISK_PER_TRADE_PCT", "2"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "3"))
DRY_RUN = os.environ.get("DRY_RUN", "True").strip().lower() != "false"

STATE_FILE = "bot_state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kraken_bot")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"position": None, "entry_price": None, "amount": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def build_exchange():
    if not API_KEY or not API_SECRET:
        sys.exit("KRAKEN_API_KEY / KRAKEN_API_SECRET are not set (check GitHub Secrets).")
    return ccxt.kraken({"apiKey": API_KEY, "secret": API_SECRET, "enableRateLimit": True})


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def get_signal(closes):
    short_now, long_now = sma(closes, SHORT_MA), sma(closes, LONG_MA)
    short_prev, long_prev = sma(closes[:-1], SHORT_MA), sma(closes[:-1], LONG_MA)
    if None in (short_now, long_now, short_prev, long_prev):
        return None
    if short_prev <= long_prev and short_now > long_now:
        return "buy"
    if short_prev >= long_prev and short_now < long_now:
        return "sell"
    return None


def calc_position_size(balance_quote, price):
    return (balance_quote * (RISK_PER_TRADE_PCT / 100)) / price


def place_buy(exchange, price, state):
    base, quote = SYMBOL.split("/")
    balance_quote = exchange.fetch_balance().get("free", {}).get(quote, 0.0)
    amount = calc_position_size(balance_quote, price)
    if amount <= 0:
        log.warning("Calculated position size is 0 — skipping buy.")
        return state
    if DRY_RUN:
        log.info(f"[DRY RUN] Would BUY {amount:.6f} {base} at ~{price:.2f} {quote}")
    else:
        log.info(f"LIVE ORDER: {exchange.create_market_buy_order(SYMBOL, amount)}")
    state.update({"position": "long", "entry_price": price, "amount": amount})
    return state


def place_sell(exchange, price, state, reason="signal"):
    base, quote = SYMBOL.split("/")
    amount = state.get("amount")
    if not amount:
        log.warning("No recorded position amount — skipping sell.")
        return state
    if DRY_RUN:
        log.info(f"[DRY RUN] Would SELL {amount:.6f} {base} at ~{price:.2f} {quote} ({reason})")
    else:
        log.info(f"LIVE ORDER: {exchange.create_market_sell_order(SYMBOL, amount)}")
    state.update({"position": None, "entry_price": None, "amount": None})
    return state


def main():
    log.info(f"Symbol={SYMBOL} TF={TIMEFRAME} MA={SHORT_MA}/{LONG_MA} DRY_RUN={DRY_RUN}")
    exchange = build_exchange()
    state = load_state()

    limit = max(LONG_MA, SHORT_MA) + 5
    candles = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=limit)
    closes = [c[4] for c in candles]
    price = closes[-1]

    # Stop-loss check
    if state.get("position") == "long" and state.get("entry_price"):
        drop_pct = (state["entry_price"] - price) / state["entry_price"] * 100
        if drop_pct >= STOP_LOSS_PCT:
            log.warning(f"Stop-loss triggered: -{drop_pct:.2f}%")
            state = place_sell(exchange, price, state, reason="stop-loss")

    signal = get_signal(closes)
    if signal == "buy" and state.get("position") != "long":
        state = place_buy(exchange, price, state)
    elif signal == "sell" and state.get("position") == "long":
        state = place_sell(exchange, price, state, reason="signal")
    else:
        log.info(f"No action. Price={price:.2f} Position={state.get('position')}")

    save_state(state)


if __name__ == "__main__":
    main()
