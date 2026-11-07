import math
import random
import unittest

from backend.strategy import generate_signal


def make_candles(seed, direction):
    rng = random.Random(seed)
    closes = [100.0]

    for i in range(100):
        if direction == "BUY":
            drift = 0.15 if i > 55 else -0.08
        else:
            drift = -0.15 if i > 55 else 0.08

        closes.append(closes[-1] + drift + rng.gauss(0, 0.35))

    return [
        {
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 10000,
        }
        for price in closes
    ]


class StrategyTests(unittest.TestCase):

    def setUp(self):
        self.buy_candles = make_candles(682, "BUY")
        self.sell_candles = make_candles(114, "SELL")

    def test_insufficient_candles(self):
        result = generate_signal([], 100000, 0)
        self.assertEqual(result["signal"], "HOLD")

    def test_buy_signal(self):
        result = generate_signal(self.buy_candles, 100000, 0)
        print("BUY TEST:", result)

        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["quantity"], 0)
        self.assertLess(result["stop_loss"], result["entry"])
        self.assertLess(result["entry"], result["target"])
        self.assertFalse(result["broker_order_sent"])

    def test_sell_signal(self):
        result = generate_signal(self.sell_candles, 100000, 0)
        print("SELL TEST:", result)

        self.assertEqual(result["signal"], "SELL")
        self.assertGreater(result["quantity"], 0)
        self.assertLess(result["target"], result["entry"])
        self.assertLess(result["entry"], result["stop_loss"])
        self.assertFalse(result["broker_order_sent"])

    def test_maximum_combined_risk(self):
        result = generate_signal(self.buy_candles, 100000, 2500)
        self.assertEqual(result["signal"], "HOLD")

    def test_zero_equity(self):
        result = generate_signal(self.buy_candles, 0, 0)
        self.assertEqual(result["signal"], "HOLD")

    def test_non_finite_equity(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(equity=value):
                result = generate_signal(self.buy_candles, value, 0)
                self.assertEqual(result["signal"], "HOLD")

    def test_invalid_open_risk(self):
        for value in (math.nan, math.inf, -math.inf, -1):
            with self.subTest(open_risk=value):
                result = generate_signal(self.buy_candles, 100000, value)
                self.assertEqual(result["signal"], "HOLD")

    def test_non_finite_ohlc(self):
        for field in ("open", "high", "low", "close"):
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(field=field, value=value):
                    candles = [dict(c) for c in self.buy_candles]
                    candles[-1][field] = value
                    result = generate_signal(candles, 100000, 0)
                    self.assertEqual(result["signal"], "HOLD")

    def test_invalid_candle_relationships(self):
        candles = [dict(c) for c in self.buy_candles]
        candles[-1]["high"] = 90
        candles[-1]["low"] = 100

        result = generate_signal(candles, 100000, 0)
        self.assertEqual(result["signal"], "HOLD")

    def test_missing_open_price(self):
        candles = [dict(c) for c in self.buy_candles]
        del candles[-1]["open"]

        result = generate_signal(candles, 100000, 0)
        self.assertEqual(result["signal"], "HOLD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
