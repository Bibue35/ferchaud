"""
Momentum / Trend-Following Strategy (v2 — Regime + VPIN aware)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry requires ALL to align:
  • EMA(9) crosses EMA(21) in signal direction
  • MACD histogram confirms direction
  • Price on correct side of EMA(50) (trend filter)
  • ADX > 25 (only trade trending markets)
  • Volume > 20-period average (volume confirmation)

v2 enhancements:
  • Regime gating: in CRISIS regime, only take shorts (trend-follow the crash)
  • VPIN filter: skip entries when informed flow detected
  • Position sizes dynamically adjusted by vol-target leverage
"""
from typing import List

import numpy as np

from config import CONFIG
from data.indicators import ema, macd, atr, adx
from strategies.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    EMA_FAST = 9
    EMA_SLOW = 21
    EMA_TREND = 50
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    ADX_PERIOD = 14
    ADX_THRESHOLD = 20        # Lower bar for "trending" (was 25)
    VOL_PERIOD = 20
    ATR_PERIOD = 14
    ATR_STOP_MULT = 1.2       # Tighter stop (was 1.5)
    ATR_TARGET_MULT = 2.0     # Faster target (was 2.5)
    BARS_REQUIRED = 60        # Less data needed (was 120)

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe + CONFIG.crypto_universe

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk engine halted — skipping momentum")
            return

        for symbol in self.get_symbols():
            try:
                self._process(symbol)
            except Exception as exc:
                self.log.error("Error processing %s: %s", symbol, exc)

    def _process(self, symbol: str) -> None:
        df = self.feed.get_bars(symbol, timeframe="1H", limit=self.BARS_REQUIRED)
        if df.empty or len(df) < self.BARS_REQUIRED:
            return

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        fast_ema = ema(close, self.EMA_FAST)
        slow_ema = ema(close, self.EMA_SLOW)
        trend_ema = ema(close, self.EMA_TREND)
        macd_line, signal_line, histogram = macd(
            close, self.MACD_FAST, self.MACD_SLOW, self.MACD_SIGNAL
        )
        adx_vals = adx(high, low, close, self.ADX_PERIOD)
        atr_vals = atr(high, low, close, self.ATR_PERIOD)
        avg_volume = volume.rolling(self.VOL_PERIOD).mean()

        cur_fast = fast_ema.iloc[-1]
        cur_slow = slow_ema.iloc[-1]
        prev_fast = fast_ema.iloc[-2]
        prev_slow = slow_ema.iloc[-2]
        cur_trend = trend_ema.iloc[-1]
        cur_hist = histogram.iloc[-1]
        cur_adx = adx_vals.iloc[-1]
        cur_price = close.iloc[-1]
        cur_vol = volume.iloc[-1]
        avg_vol = avg_volume.iloc[-1]
        cur_atr = atr_vals.iloc[-1]

        trending = cur_adx > self.ADX_THRESHOLD
        volume_ok = cur_vol > avg_vol
        scale = self.regime_scale

        has_position = self.portfolio.has_position(symbol)
        pos_side = self.portfolio.position_side(symbol)

        # ── Exit logic ─────────────────────────────────────────────────────────
        if has_position:
            if pos_side == "long":
                if (prev_fast >= prev_slow and cur_fast < cur_slow) or cur_hist < 0:
                    self.executor.exit_position(symbol, "MOM: bearish crossover")
                    return
            elif pos_side == "short":
                if (prev_fast <= prev_slow and cur_fast > cur_slow) or cur_hist > 0:
                    self.executor.exit_position(symbol, "MOM: bullish crossover")
                    return
            return

        if not trending or not volume_ok:
            return

        # VPIN + regime gate
        if self.should_skip_entry(symbol):
            return

        # In CRISIS regime, only allow shorts (trend-follow the crash)
        from core.regime import CRISIS
        in_crisis = self.regime and self.regime.current_state == CRISIS

        # Learned parameters (fall back to class defaults)
        stop_mult   = self.learn_param("stop_atr_mult",   self.ATR_STOP_MULT)
        target_mult = self.learn_param("target_atr_mult", self.ATR_TARGET_MULT)

        # ── Long signal ────────────────────────────────────────────────────────
        bullish_cross = prev_fast < prev_slow and cur_fast > cur_slow
        if bullish_cross and cur_hist > 0 and cur_price > cur_trend and not in_crisis:
            stop, target = self.risk.atr_stops(cur_price, cur_atr, "buy",
                                               stop_mult, target_mult)
            stop_dist = abs(cur_price - stop)
            # Confidence: ADX strength + how far above trend + MACD histogram size
            adx_conf = min((cur_adx - self.ADX_THRESHOLD) / 40.0, 0.3)
            trend_conf = min((cur_price - cur_trend) / (cur_atr + 1e-9) * 0.05, 0.15)
            hist_conf = min(abs(cur_hist) / (cur_price * 0.001 + 1e-9), 0.05)
            confidence = float(min(0.5 + adx_conf + trend_conf + hist_conf, 1.0))

            signals = {
                "ema_cross_up": 1,
                "macd_pos":     1 if cur_hist > 0 else 0,
                "above_trend":  1 if cur_price > cur_trend else 0,
                "adx_strong":   1 if cur_adx > self.ADX_THRESHOLD * 1.5 else 0,
                "vol_confirm":  1 if volume_ok else 0,
            }
            decision = self.learn_before_entry(signals, confidence)
            if decision["veto"]:
                return
            confidence = decision["confidence"]
            size_mult = decision["size_mult"]

            qty = self.risk.risk_based_size(
                self.portfolio.equity, cur_price, stop_dist,
                regime_scale=scale, confidence=confidence,
            ) * size_mult
            if qty >= 1:
                self.log.info(
                    "LONG %s | price=%.2f  EMA9/21  ADX=%.1f  "
                    "regime=%s(×%.1f)  conf=%.0f%% sm=%.2f",
                    symbol, cur_price, cur_adx,
                    self.regime.state_name if self.regime else "N/A",
                    scale, confidence * 100, size_mult,
                )
                self.executor.enter_long(
                    symbol, qty, stop_price=stop, take_profit=target,
                    strategy_tag=self.name, confidence=confidence,
                    signals=signals, atr=cur_atr,
                )

        # ── Short signal ───────────────────────────────────────────────────────
        bearish_cross = prev_fast > prev_slow and cur_fast < cur_slow
        if bearish_cross and cur_hist < 0 and cur_price < cur_trend:
            stop, target = self.risk.atr_stops(cur_price, cur_atr, "sell",
                                               stop_mult, target_mult)
            stop_dist = abs(cur_price - stop)
            adx_conf = min((cur_adx - self.ADX_THRESHOLD) / 40.0, 0.3)
            trend_conf = min((cur_trend - cur_price) / (cur_atr + 1e-9) * 0.05, 0.15)
            hist_conf = min(abs(cur_hist) / (cur_price * 0.001 + 1e-9), 0.05)
            confidence = float(min(0.5 + adx_conf + trend_conf + hist_conf, 1.0))

            signals = {
                "ema_cross_dn": 1,
                "macd_neg":     1 if cur_hist < 0 else 0,
                "below_trend":  1 if cur_price < cur_trend else 0,
                "adx_strong":   1 if cur_adx > self.ADX_THRESHOLD * 1.5 else 0,
                "vol_confirm":  1 if volume_ok else 0,
            }
            decision = self.learn_before_entry(signals, confidence)
            if decision["veto"]:
                return
            confidence = decision["confidence"]
            size_mult = decision["size_mult"]

            qty = self.risk.risk_based_size(
                self.portfolio.equity, cur_price, stop_dist,
                regime_scale=scale, confidence=confidence,
            ) * size_mult
            if qty >= 1:
                self.log.info(
                    "SHORT %s | price=%.2f  EMA9/21  ADX=%.1f  "
                    "regime=%s(×%.1f)  conf=%.0f%% sm=%.2f",
                    symbol, cur_price, cur_adx,
                    self.regime.state_name if self.regime else "N/A",
                    scale, confidence * 100, size_mult,
                )
                self.executor.enter_short(
                    symbol, qty, stop_price=stop, take_profit=target,
                    strategy_tag=self.name, confidence=confidence,
                    signals=signals, atr=cur_atr,
                )
