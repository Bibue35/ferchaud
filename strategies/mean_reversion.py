"""
Mean Reversion Strategy (v2 — Regime + VPIN aware)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Signals:
  • Long  when price < lower BB AND RSI < 30
  • Short when price > upper BB AND RSI > 70
  • Exit  when price reverts to mid-band

Enhancements over v1:
  • Position sizes scaled by regime detector (crisis = 30%)
  • Entries blocked when VPIN detects toxic flow
  • ATR stop/target dynamically scaled by vol-target leverage
  • Bayesian Kelly feeds into the risk engine
"""
from typing import List

from config import CONFIG
from data.indicators import bollinger_bands, rsi, atr
from strategies.base import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    BB_PERIOD = 20
    BB_STD = 1.8              # Tighter bands = more signals (was 2.0)
    RSI_PERIOD = 14
    RSI_OVERSOLD = 35         # More triggers (was 30)
    RSI_OVERBOUGHT = 65       # More triggers (was 70)
    ATR_PERIOD = 14
    ATR_STOP_MULT = 1.5       # Tighter stop (was 2.0)
    ATR_TARGET_MULT = 2.5     # Faster target (was 3.0)
    BARS_REQUIRED = 40        # Less data needed (was 60)

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe + CONFIG.crypto_universe

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk engine halted — skipping mean reversion")
            return

        for symbol in self.get_symbols():
            try:
                self._process(symbol)
            except Exception as exc:
                self.log.error("Error processing %s: %s", symbol, exc)

    def _process(self, symbol: str) -> None:
        df = self.feed.get_bars(symbol, timeframe="15Min", limit=self.BARS_REQUIRED)
        if df.empty or len(df) < self.BARS_REQUIRED:
            return

        close = df["close"]
        high = df["high"]
        low = df["low"]

        upper, mid, lower = bollinger_bands(close, self.BB_PERIOD, self.BB_STD)
        rsi_vals = rsi(close, self.RSI_PERIOD)
        atr_vals = atr(high, low, close, self.ATR_PERIOD)

        last_close = close.iloc[-1]
        last_upper = upper.iloc[-1]
        last_mid = mid.iloc[-1]
        last_lower = lower.iloc[-1]
        last_rsi = rsi_vals.iloc[-1]
        last_atr = atr_vals.iloc[-1]

        has_position = self.portfolio.has_position(symbol)
        pos_side = self.portfolio.position_side(symbol)

        # ── Exit logic ────────────────────────────────────────────────────────
        if has_position:
            if pos_side == "long" and last_close >= last_mid:
                self.executor.exit_position(symbol, reason="MR: price reverted to mid-band")
                return
            if pos_side == "short" and last_close <= last_mid:
                self.executor.exit_position(symbol, reason="MR: price reverted to mid-band")
                return

        # ── Entry logic ───────────────────────────────────────────────────────
        if has_position:
            return

        # VPIN + regime gate
        if self.should_skip_entry(symbol):
            return

        # Scale by regime
        scale = self.regime_scale

        # Long signal
        if last_close < last_lower and last_rsi < self.RSI_OVERSOLD:
            stop, target = self.risk.atr_stops(
                last_close, last_atr, "buy", self.ATR_STOP_MULT, self.ATR_TARGET_MULT
            )
            stop_distance = abs(last_close - stop)
            # Confidence: how far below band + how oversold RSI is
            bb_dev = (last_lower - last_close) / (last_upper - last_lower + 1e-9)
            rsi_dev = (self.RSI_OVERSOLD - last_rsi) / self.RSI_OVERSOLD
            confidence = float(min(0.5 + bb_dev * 1.5 + rsi_dev * 0.5, 1.0))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, last_close, stop_distance,
                regime_scale=scale, confidence=confidence,
            )
            if qty >= 1:
                self.log.info(
                    "LONG %s | price=%.2f  BB_low=%.2f  RSI=%.1f  "
                    "regime=%s(×%.1f)  conf=%.0f%%",
                    symbol, last_close, last_lower, last_rsi,
                    self.regime.state_name if self.regime else "N/A",
                    scale, confidence * 100,
                )
                self.executor.enter_long(symbol, qty, stop_price=stop,
                                         take_profit=target, strategy_tag="MR",
                                         confidence=confidence)

        # Short signal
        elif last_close > last_upper and last_rsi > self.RSI_OVERBOUGHT:
            stop, target = self.risk.atr_stops(
                last_close, last_atr, "sell", self.ATR_STOP_MULT, self.ATR_TARGET_MULT
            )
            stop_distance = abs(last_close - stop)
            # Confidence: how far above band + how overbought RSI is
            bb_dev = (last_close - last_upper) / (last_upper - last_lower + 1e-9)
            rsi_dev = (last_rsi - self.RSI_OVERBOUGHT) / (100 - self.RSI_OVERBOUGHT)
            confidence = float(min(0.5 + bb_dev * 1.5 + rsi_dev * 0.5, 1.0))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, last_close, stop_distance,
                regime_scale=scale, confidence=confidence,
            )
            if qty >= 1:
                self.log.info(
                    "SHORT %s | price=%.2f  BB_high=%.2f  RSI=%.1f  "
                    "regime=%s(×%.1f)  conf=%.0f%%",
                    symbol, last_close, last_upper, last_rsi,
                    self.regime.state_name if self.regime else "N/A",
                    scale, confidence * 100,
                )
                self.executor.enter_short(symbol, qty, stop_price=stop,
                                          take_profit=target, strategy_tag="MR",
                                          confidence=confidence)
