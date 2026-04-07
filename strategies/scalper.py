"""
Aggressive Scalper Strategy
━━━━━━━━━━━━━━━━━━━━━━━━━━
Ultra-fast mean-reversion on short timeframes.
Trades 1-5 minute dislocations for quick 0.1-0.5% profit targets.

Signals:
  • RSI(5) extremes: < 20 buy, > 80 sell (on 5-min bars)
  • Price touches lower/upper Bollinger Band (10-period, 1.5 std)
  • VWAP reversion: buy below VWAP, sell above VWAP
  • Multiple signal confluence = higher confidence

Key features:
  • Tight stops (0.5× ATR) and tight targets (1× ATR) 
  • High win rate targeting (70%+) with small gains
  • Exits quickly — never holds longer than needed
  • Trades ALL symbols for maximum surface area
"""
from typing import List
import numpy as np
from config import CONFIG
from data.indicators import ema, rsi, atr, bollinger_bands, vwap
from strategies.base import BaseStrategy


class ScalperStrategy(BaseStrategy):
    name = "scalper"

    RSI_PERIOD = 5
    RSI_OVERSOLD = 20
    RSI_OVERBOUGHT = 80
    BB_PERIOD = 10
    BB_STD = 1.5
    ATR_PERIOD = 10
    ATR_STOP_MULT = 0.5    # Tight stop
    ATR_TARGET_MULT = 1.0  # Quick target
    BARS_REQUIRED = 30

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe + CONFIG.crypto_universe

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            return
        for symbol in self.get_symbols():
            try:
                self._process(symbol)
            except Exception as exc:
                self.log.error("Scalp error %s: %s", symbol, exc)

    def _process(self, symbol: str) -> None:
        # Use 5-minute bars for scalping
        df = self.feed.get_bars(symbol, timeframe="5Min", limit=self.BARS_REQUIRED)
        if df.empty or len(df) < self.BARS_REQUIRED:
            return

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi_vals = rsi(close, self.RSI_PERIOD)
        upper, mid, lower = bollinger_bands(close, self.BB_PERIOD, self.BB_STD)
        atr_vals = atr(high, low, close, self.ATR_PERIOD)
        vwap_vals = vwap(high, low, close, volume)

        last_close = close.iloc[-1]
        last_rsi = rsi_vals.iloc[-1]
        last_upper = upper.iloc[-1]
        last_lower = lower.iloc[-1]
        last_mid = mid.iloc[-1]
        last_atr = atr_vals.iloc[-1]
        last_vwap = vwap_vals.iloc[-1] if not vwap_vals.empty else last_close

        has_pos = self.portfolio.has_position(symbol)
        pos_side = self.portfolio.position_side(symbol)

        # Quick exit — scalps should close fast
        if has_pos:
            if pos_side == "long" and (last_close >= last_mid or last_rsi > 60):
                self.executor.exit_position(symbol, reason="SCALP: target/mid hit")
            elif pos_side == "short" and (last_close <= last_mid or last_rsi < 40):
                self.executor.exit_position(symbol, reason="SCALP: target/mid hit")
            return

        if self.should_skip_entry(symbol):
            return

        scale = self.regime_scale

        # Count confluence signals
        buy_signals = 0
        sell_signals = 0

        # RSI extreme
        if last_rsi < self.RSI_OVERSOLD:
            buy_signals += 1
        elif last_rsi > self.RSI_OVERBOUGHT:
            sell_signals += 1

        # Bollinger touch
        if last_close <= last_lower:
            buy_signals += 1
        elif last_close >= last_upper:
            sell_signals += 1

        # VWAP reversion
        if last_close < last_vwap * 0.998:  # 0.2% below VWAP
            buy_signals += 1
        elif last_close > last_vwap * 1.002:  # 0.2% above VWAP
            sell_signals += 1

        # Need at least 2 signals for entry
        if buy_signals >= 2:
            stop, target = self.risk.atr_stops(
                last_close, last_atr, "buy", self.ATR_STOP_MULT, self.ATR_TARGET_MULT
            )
            stop_dist = abs(last_close - stop)
            # Confidence scales with signal count: 2 signals=0.6, 3 signals=0.85
            confidence = float(min(0.4 + buy_signals * 0.15, 0.90))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, last_close, stop_dist,
                regime_scale=scale, confidence=confidence,
            )
            if qty >= 1:
                self.log.info(
                    "SCALP LONG %s | price=%.2f RSI=%.0f signals=%d conf=%.0f%%",
                    symbol, last_close, last_rsi, buy_signals, confidence * 100,
                )
                self.executor.enter_long(symbol, qty, stop_price=stop,
                                        take_profit=target, strategy_tag="SCALP",
                                        confidence=confidence)

        elif sell_signals >= 2:
            stop, target = self.risk.atr_stops(
                last_close, last_atr, "sell", self.ATR_STOP_MULT, self.ATR_TARGET_MULT
            )
            stop_dist = abs(last_close - stop)
            confidence = float(min(0.4 + sell_signals * 0.15, 0.90))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, last_close, stop_dist,
                regime_scale=scale, confidence=confidence,
            )
            if qty >= 1:
                self.log.info(
                    "SCALP SHORT %s | price=%.2f RSI=%.0f signals=%d conf=%.0f%%",
                    symbol, last_close, last_rsi, sell_signals, confidence * 100,
                )
                self.executor.enter_short(symbol, qty, stop_price=stop,
                                         take_profit=target, strategy_tag="SCALP",
                                         confidence=confidence)
