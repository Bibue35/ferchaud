"""
ETF Rotation Strategies — Vault Strategies #75–81, #95, #97, #141–145
Covers:
  #75  Sector Momentum Rotation
  #76  Dual Momentum Sector Rotation (Gary Antonacci)
  #77  ETF Alpha Rotation (factor ETFs)
  #78  R-Squared Trend Filter
  #79  ETF Mean Reversion
  #81  Multi-Asset Trend Following
  #95  Index Volatility Targeting
  #97  Volatility Carry (VXX/SVXY)
  #141 REIT Momentum
  #142 REIT Carry
  #144 Global Macro Fundamental Momentum
  #145 Global Macro Inflation Hedge

All tradeable via Alpaca (equities/ETFs — no futures account needed).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


# ── Universe definitions ──────────────────────────────────────────────────────

SECTOR_ETFS = {
    "XLK": "Technology", "XLF": "Financials", "XLV": "Health Care",
    "XLE": "Energy",     "XLI": "Industrials","XLY": "Consumer Disc",
    "XLP": "Consumer Staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication",
}

FACTOR_ETFS = {
    "MTUM": "Momentum",  "VLUE": "Value",  "QUAL": "Quality",
    "USMV": "Low Vol",   "SIZE": "Size",   "DIVI": "Dividend",
}

MULTI_ASSET_ETFS = {
    "SPY": "US Equity",  "TLT": "Long Bond", "GLD": "Gold",
    "DJP": "Commodities","VNQ": "Real Estate","EEM": "EM Equity",
}

REIT_ETFS = ["VNQ", "IYR", "XLRE", "RWX", "KBWY", "SRVR"]
INFLATION_HEDGE = ["GLD", "DJP", "XLE", "VNQ", "TIP"]
VOL_ETFS = {"VXX": "short_vol_signal", "SVXY": "long"}


class SectorRotationStrategy:
    """
    Vault #75/76: Momentum-based sector ETF rotation.
    Ranks sectors by trailing return; invests in top N.
    Dual momentum variant: also applies absolute momentum filter (vs. cash/bonds).
    """
    name        = "SectorRotation"
    description = "Monthly sector ETF rotation by momentum (Vault #75/76)."
    top_n       = 3           # Hold top N sectors
    formation_months = 12     # Look-back for momentum ranking
    absolute_momentum = True  # Vault #76: switch to SHY if sector underperforms T-bills

    def rank_sectors(self, price_history: dict[str, pd.Series],
                     benchmark_return: float = 0.0) -> dict:
        """
        price_history: {ticker: pd.Series of monthly close prices}
        benchmark_return: T-bill / risk-free return over same period
        Returns ranked dict with buy/hold signals.
        """
        scores = {}
        for ticker, prices in price_history.items():
            if len(prices) < 2:
                continue
            total_return = float(prices.iloc[-1] / prices.iloc[0] - 1)
            scores[ticker] = total_return

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        signals = {}
        for i, (ticker, ret) in enumerate(ranked):
            if i < self.top_n:
                # Absolute momentum filter (Vault #76)
                if self.absolute_momentum and ret < benchmark_return:
                    signals[ticker] = {"signal": "cash", "rank": i + 1, "return": round(ret, 4)}
                else:
                    signals[ticker] = {"signal": "buy", "rank": i + 1, "return": round(ret, 4)}
            else:
                signals[ticker] = {"signal": "hold", "rank": i + 1, "return": round(ret, 4)}

        return signals


class ETFMeanReversionStrategy:
    """
    Vault #79: Short-term ETF mean reversion.
    RSI(2) < 10 → buy; RSI(2) > 90 → sell.
    Works on SPY, QQQ, IWM, GLD, TLT.
    """
    name        = "ETFMeanReversion"
    description = "Short-term ETF mean reversion using RSI(2) and z-score (Vault #79)."
    max_positions     = 5
    position_size_pct = 0.10

    @staticmethod
    def _rsi(series: pd.Series, period: int = 2) -> float:
        delta  = series.diff()
        gain   = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss   = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs     = gain / loss.replace(0, np.nan)
        rsi    = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0

    def generate_signal(self, ticker: str, prices: pd.Series,
                        vix_level: float = 20.0) -> dict:
        if len(prices) < 15:
            return {"signal": "hold", "score": 0.0}

        rsi = self._rsi(prices, 2)
        # Z-score vs 10-day MA
        ma10  = prices.rolling(10).mean().iloc[-1]
        std10 = prices.rolling(10).std().iloc[-1]
        price = float(prices.iloc[-1])
        z     = float((price - ma10) / std10) if std10 > 0 else 0.0

        # Skip if VIX is very high (trending not mean-reverting)
        if vix_level > 35:
            return {"signal": "hold", "score": 0.0, "reason": "high_vix"}

        if rsi < 10 and z < -1.5:
            return {"signal": "buy",  "score": min(1.0, (10 - rsi) / 10 + abs(z) * 0.1),
                    "meta": {"rsi2": round(rsi, 1), "z_score": round(z, 2)}}
        elif rsi > 90 and z > 1.5:
            return {"signal": "sell", "score": -min(1.0, (rsi - 90) / 10 + z * 0.1),
                    "meta": {"rsi2": round(rsi, 1), "z_score": round(z, 2)}}

        return {"signal": "hold", "score": round(z * 0.05, 4),
                "meta": {"rsi2": round(rsi, 1), "z_score": round(z, 2)}}


class MultiAssetTrendStrategy:
    """
    Vault #81: Trend-following across multi-asset ETF portfolio.
    Long ETF if trailing 10-month return is positive; move to cash if negative.
    Provides crisis alpha by going to cash/bonds in downtrends.
    """
    name        = "MultiAssetTrend"
    description = "10-month momentum across SPY/TLT/GLD/DJP/VNQ (Vault #81)."
    universe    = list(MULTI_ASSET_ETFS.keys())
    position_size_pct = 1.0 / len(universe)

    def generate_signals(self, price_history: dict[str, pd.Series],
                         lookback_months: int = 10) -> dict[str, str]:
        """Returns {ticker: "buy"|"cash"} for each ETF in the universe."""
        signals = {}
        for ticker, prices in price_history.items():
            if len(prices) < 2:
                signals[ticker] = "cash"
                continue
            ret = float(prices.iloc[-1] / prices.iloc[0] - 1)
            signals[ticker] = "buy" if ret > 0 else "cash"
        return signals


class VolatilityTargetingStrategy:
    """
    Vault #95: Index Volatility Targeting.
    Scales equity ETF exposure to maintain a constant annualized vol target.
    Lever up in low-vol regimes; reduce in high-vol regimes.
    """
    name        = "VolatilityTargeting"
    description = "Scale SPY exposure to maintain 10% annualized vol target (Vault #95)."
    vol_target  = 0.10    # 10% annualized portfolio vol
    max_leverage = 2.0
    min_leverage = 0.0

    def compute_leverage(self, returns: pd.Series, window: int = 20) -> float:
        """Returns leverage multiplier [min_leverage, max_leverage]."""
        if len(returns) < window:
            return 1.0
        realized_vol = float(returns.tail(window).std() * np.sqrt(252))
        if realized_vol <= 0:
            return self.max_leverage
        leverage = self.vol_target / realized_vol
        return round(max(self.min_leverage, min(self.max_leverage, leverage)), 3)


class REITStrategy:
    """
    Vault #141/142: REIT Momentum + Carry (dividend yield vs. Treasury yield).
    Long REITs when momentum positive AND yield spread attractive.
    """
    name        = "REITStrategy"
    description = "REIT momentum + yield spread carry (Vault #141/142)."
    position_size_pct = 0.08

    def generate_signal(self, reit_prices: pd.Series, reit_div_yield: float,
                        treasury_10y: float, formation_months: int = 12) -> dict:
        """
        reit_div_yield: annual dividend yield of REIT (e.g., 0.045 = 4.5%)
        treasury_10y: 10-year Treasury yield (e.g., 0.04 = 4%)
        """
        if len(reit_prices) < 2:
            return {"signal": "hold", "score": 0.0}

        # Momentum signal (#141)
        mom_return = float(reit_prices.iloc[-1] / reit_prices.iloc[0] - 1)
        mom_signal = 1 if mom_return > 0 else -1

        # Yield spread signal (#142): REIT yield > Treasury + 1% = attractive
        spread = reit_div_yield - treasury_10y
        yield_signal = 1 if spread > 0.01 else (-1 if spread < -0.01 else 0)

        composite = 0.6 * mom_signal + 0.4 * yield_signal

        signal = "hold"
        if composite > 0.4:   signal = "buy"
        elif composite < -0.4: signal = "sell"

        return {
            "signal": signal,
            "score":  round(composite, 4),
            "meta": {
                "momentum_12m":  round(mom_return, 4),
                "yield_spread":  round(spread, 4),
                "div_yield":     round(reit_div_yield, 4),
                "treasury_10y":  round(treasury_10y, 4),
            }
        }


class MacroInflationHedgeStrategy:
    """
    Vault #145: Global Macro Inflation Hedge.
    Allocates to GLD, DJP, XLE, VNQ, TIP when inflation rising.
    """
    name        = "MacroInflationHedge"
    description = "Inflation-sensitive ETF basket — GLD, DJP, XLE, VNQ, TIP (Vault #145)."
    universe    = INFLATION_HEDGE
    position_size_pct = 1.0 / len(universe)

    def generate_signal(self, cpi_yoy: float, cpi_3m_change: float,
                        breakeven_inflation: float, prices: dict[str, pd.Series]) -> dict:
        """
        cpi_yoy: CPI year-over-year % (e.g., 3.5)
        cpi_3m_change: 3-month acceleration (e.g., +0.8)
        breakeven_inflation: 5Y TIPS break-even rate
        """
        # Enter inflation hedge when CPI > 3% and rising, or CPI > breakeven
        inflation_regime = (cpi_yoy > 3.0 and cpi_3m_change > 0) or (cpi_yoy > breakeven_inflation + 0.5)
        score = min(1.0, cpi_yoy / 5.0) if inflation_regime else -0.3

        signals = {}
        for ticker in self.universe:
            if ticker in prices:
                p = prices[ticker]
                mom = float(p.iloc[-1] / p.iloc[0] - 1) if len(p) >= 2 else 0
                # Enter asset if inflation regime AND asset has positive momentum
                if inflation_regime and mom > 0:
                    signals[ticker] = {"signal": "buy", "score": score}
                elif not inflation_regime:
                    signals[ticker] = {"signal": "sell", "score": -0.3}
                else:
                    signals[ticker] = {"signal": "hold", "score": 0.0}

        return {
            "regime": "inflation" if inflation_regime else "normal",
            "composite_score": round(score, 4),
            "asset_signals": signals,
        }
