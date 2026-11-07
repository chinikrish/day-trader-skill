
import math
from typing import Any


# AlgoPilot India — experimental paper strategy
FAST_EMA = 9
SLOW_EMA = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

RISK_PER_TRADE = 0.005
MAX_TOTAL_OPEN_RISK = 0.025
REWARD_RISK_RATIO = 2.0


def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    result = [sum(values[:period]) / period]

    for price in values[period:]:
        result.append(
            (price - result[-1]) * multiplier + result[-1]
        )

    return result


def calculate_rsi(
    closes: list[float],
    period: int = RSI_PERIOD
) -> float | None:
    if len(closes) <= period:
        return None

    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(
    candles: list[dict[str, Any]],
    period: int = ATR_PERIOD
) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous_close = float(candles[i - 1]["close"])

        high = float(current["high"])
        low = float(current["low"])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )
        true_ranges.append(true_range)

    atr = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        atr = ((atr * (period - 1)) + tr) / period

    return atr


def generate_signal(
    candles: list[dict[str, Any]],
    equity: float,
    open_risk: float = 0.0
) -> dict[str, Any]:
    """
    Generates a signal from completed candles.

    Does not place orders.
    Does not connect to a broker.
    Does not execute trades.
    """

    try:
        equity = float(equity)
        open_risk = float(open_risk)
    except (TypeError, ValueError, OverflowError):
        return {"signal": "HOLD", "reason": "Invalid risk inputs"}

    if not math.isfinite(equity) or equity <= 0:
        return {"signal": "HOLD", "reason": "Invalid paper equity"}

    if not math.isfinite(open_risk) or open_risk < 0:
        return {"signal": "HOLD", "reason": "Invalid open risk"}

    if open_risk >= equity * MAX_TOTAL_OPEN_RISK:
        return {"signal": "HOLD", "reason": "Maximum combined risk reached"}

    if len(candles) < 50:
        return {
            "signal": "HOLD",
            "reason": "At least 50 candles are required"
        }

    try:
        opens = [float(c["open"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]

        if any(
            not all(math.isfinite(x) and x > 0 for x in (o, h, l, c))
            or h < l
            or max(o, c) > h
            or min(o, c) < l
            for o, h, l, c in zip(opens, highs, lows, closes)
        ):
            return {"signal": "HOLD", "reason": "Invalid candle prices"}

    except (KeyError, TypeError, ValueError):
        return {"signal": "HOLD", "reason": "Invalid candle format"}

    fast = ema(closes, FAST_EMA)
    slow = ema(closes, SLOW_EMA)
    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    if not fast or not slow or rsi is None or atr is None:
        return {"signal": "HOLD", "reason": "Insufficient indicator data"}

    # Align EMA arrays to their original candle positions.
    previous_fast = fast[-2]
    current_fast = fast[-1]
    previous_slow = slow[-2]
    current_slow = slow[-1]

    crossed_up = (
        previous_fast <= previous_slow
        and current_fast > current_slow
    )

    crossed_down = (
        previous_fast >= previous_slow
        and current_fast < current_slow
    )

    entry = closes[-1]
    stop_distance = atr * 1.5

    if stop_distance <= 0:
        return {"signal": "HOLD", "reason": "Invalid ATR"}

    side = None

    if crossed_up and 50 < rsi < 70:
        side = "BUY"
    elif crossed_down and 30 < rsi < 50:
        side = "SELL"

    if side is None:
        return {
            "signal": "HOLD",
            "reason": "No confirmed EMA crossover",
            "indicators": {
                "ema_9": round(current_fast, 4),
                "ema_21": round(current_slow, 4),
                "rsi_14": round(rsi, 2),
                "atr_14": round(atr, 4)
            }
        }

    risk_budget = min(
        equity * RISK_PER_TRADE,
        (equity * MAX_TOTAL_OPEN_RISK) - open_risk
    )

    quantity = int(risk_budget / stop_distance)

    if quantity < 1:
        return {
            "signal": "HOLD",
            "reason": "Risk budget too small for one unit"
        }

    if side == "BUY":
        stop_loss = entry - stop_distance
        target = entry + (stop_distance * REWARD_RISK_RATIO)
    else:
        stop_loss = entry + stop_distance
        target = entry - (stop_distance * REWARD_RISK_RATIO)

    return {
        "signal": side,
        "entry": round(entry, 4),
        "stop_loss": round(stop_loss, 4),
        "target": round(target, 4),
        "quantity": quantity,
        "estimated_risk": round(quantity * stop_distance, 2),
        "reward_risk_ratio": REWARD_RISK_RATIO,
        "indicators": {
            "ema_9": round(current_fast, 4),
            "ema_21": round(current_slow, 4),
            "rsi_14": round(rsi, 2),
            "atr_14": round(atr, 4)
        },
        "mode": "PAPER_ONLY",
        "broker_order_sent": False
    }
