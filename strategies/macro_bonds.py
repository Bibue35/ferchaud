"""
Macro & Fixed Income Strategies — tradeable via ETFs (no futures account needed)
Vault strategies: #82-90 (Fixed Income), #96-101 (Volatility), #102-106 (FX via ETFs)

All strategies return: {"signal": "buy"|"sell"|"hold", "score": float, "meta": dict}
ETFs used: TLT, SHY, IEF, LQD, HYG, TIP, AGG, VXX, SVXY, UUP, FXE, EWJ, GLD, DBC
"""

import math


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _mean(series):
    return sum(series) / len(series) if series else 0.0

def _std(series):
    if len(series) < 2:
        return 1e-9
    m = _mean(series)
    return math.sqrt(sum((x - m) ** 2 for x in series) / len(series))

def _zscore(series):
    if len(series) < 2:
        return 0.0
    return (series[-1] - _mean(series)) / _std(series)

def _rolling_mean(series, n):
    if len(series) < n:
        return _mean(series)
    return _mean(series[-n:])

def _rolling_std(series, n):
    if len(series) < n:
        return _std(series)
    return _std(series[-n:])


# ─────────────────────────────────────────────
# Vault #85 & #86 — Yield Curve Steepener / Flattener
# ─────────────────────────────────────────────

class YieldCurveStrategy:
    """
    Trades the 2s10s slope via TLT (long-duration) vs SHY (short-duration).
    Steepener: long TLT / short SHY when spread is historically compressed.
    Flattener:  long SHY / short TLT when spread is historically wide.
    Proxy: use (TLT_price_change - SHY_price_change) as spread proxy if yields unavailable.
    """

    def __init__(self, lookback: int = 60, z_entry: float = 1.5, z_exit: float = 0.3):
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit

    def generate_signal(self, tlt_prices: list, shy_prices: list,
                        current_position: str = "flat") -> dict:
        """
        tlt_prices / shy_prices: list of daily closes, most recent last.
        Returns steepener or flattener signal.
        """
        if len(tlt_prices) < self.lookback or len(shy_prices) < self.lookback:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        # Spread proxy: TLT return - SHY return (long-duration outperforms when curve steepens)
        tlt_ret = [(tlt_prices[i] - tlt_prices[i-1]) / tlt_prices[i-1]
                   for i in range(1, len(tlt_prices))]
        shy_ret = [(shy_prices[i] - shy_prices[i-1]) / shy_prices[i-1]
                   for i in range(1, len(shy_prices))]
        n = min(len(tlt_ret), len(shy_ret))
        spread = [tlt_ret[-n+i] - shy_ret[-n+i] for i in range(n)]

        z = _zscore(spread[-self.lookback:])

        # Steepener trade: spread is compressed (TLT underperformed) — expect mean reversion
        if z < -self.z_entry and current_position != "steepener":
            return {
                "signal": "buy",
                "score": min(abs(z) / 3.0, 1.0),
                "meta": {
                    "trade": "steepener",
                    "action": "long TLT / short SHY",
                    "z_score": round(z, 3),
                    "etfs": {"long": "TLT", "short": "SHY"}
                }
            }
        # Flattener trade: spread is wide (TLT outperformed) — expect Fed hikes / compression
        elif z > self.z_entry and current_position != "flattener":
            return {
                "signal": "sell",
                "score": min(z / 3.0, 1.0),
                "meta": {
                    "trade": "flattener",
                    "action": "long SHY / short TLT",
                    "z_score": round(z, 3),
                    "etfs": {"long": "SHY", "short": "TLT"}
                }
            }
        # Exit on mean reversion
        elif abs(z) < self.z_exit and current_position != "flat":
            return {
                "signal": "hold",
                "score": 0.0,
                "meta": {"trade": "exit", "z_score": round(z, 3)}
            }

        return {"signal": "hold", "score": 0.0, "meta": {"z_score": round(z, 3)}}



# ─────────────────────────────────────────────
# Vault #88 — Credit Spread Strategy
# ─────────────────────────────────────────────

class CreditSpreadStrategy:
    """
    Trades credit spread compression/widening via LQD (IG) vs IEF (treasury).
    Or HYG (HY) vs IEF for high-yield spread.
    Wide spread → buy LQD (expect compression). Tight spread → sell / hedge.
    """

    def __init__(self, lookback: int = 60, z_entry: float = 1.5, z_exit: float = 0.4):
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit

    def generate_signal(self, lqd_prices: list, ief_prices: list,
                        current_position: str = "flat") -> dict:
        if len(lqd_prices) < self.lookback or len(ief_prices) < self.lookback:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        # Spread proxy: IEF return - LQD return (positive = credit spread widening)
        lqd_ret = [(lqd_prices[i]/lqd_prices[i-1] - 1) for i in range(1, len(lqd_prices))]
        ief_ret  = [(ief_prices[i]/ief_prices[i-1] - 1) for i in range(1, len(ief_prices))]
        n = min(len(lqd_ret), len(ief_ret))
        spread = [ief_ret[-n+i] - lqd_ret[-n+i] for i in range(n)]

        z = _zscore(spread[-self.lookback:])
        spread_pct = spread[-1] * 100

        if z > self.z_entry and current_position != "long":
            return {
                "signal": "buy",
                "score": min(z / 3.0, 1.0),
                "meta": {
                    "trade": "long_credit",
                    "action": "long LQD (credit spread wide, expect compression)",
                    "z_score": round(z, 3),
                    "spread_daily_bps": round(spread_pct * 100, 1)
                }
            }
        elif z < -self.z_entry and current_position == "long":
            return {
                "signal": "sell",
                "score": min(abs(z) / 3.0, 1.0),
                "meta": {"trade": "exit_credit", "z_score": round(z, 3)}
            }

        return {"signal": "hold", "score": 0.0, "meta": {"z_score": round(z, 3)}}



# ─────────────────────────────────────────────
# Vault #90 & #121 — TIPS / Inflation Arbitrage
# ─────────────────────────────────────────────

class TIPSInflationStrategy:
    """
    Vault #90: TIPS vs nominal Treasury arb (breakeven inflation trade).
    Vault #121: TIPS-Treasury pair trade.
    Trade via TIP (TIPS ETF) vs IEF (nominal Treasury ETF).
    Buy TIP when breakeven inflation is low (market underpricing inflation).
    Sell TIP / long IEF when breakeven is elevated.
    """

    def __init__(self, lookback: int = 90, z_entry: float = 1.5):
        self.lookback = lookback
        self.z_entry = z_entry

    def generate_signal(self, tip_prices: list, ief_prices: list,
                        cpi_yoy: float = None) -> dict:
        if len(tip_prices) < self.lookback or len(ief_prices) < self.lookback:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        # Breakeven proxy: TIP/IEF price ratio (higher = market pricing more inflation)
        ratio = [tip_prices[i] / ief_prices[i]
                 for i in range(len(tip_prices) - self.lookback, len(tip_prices))]
        z = _zscore(ratio)

        cpi_boost = 0.0
        if cpi_yoy is not None:
            # If actual CPI >> historical avg, boost long TIPS score
            cpi_boost = (cpi_yoy - 2.5) / 10.0

        if z < -self.z_entry:
            score = min(abs(z) / 3.0 + max(cpi_boost, 0), 1.0)
            return {
                "signal": "buy",
                "score": score,
                "meta": {
                    "trade": "long_tips",
                    "action": "long TIP / short IEF (breakeven underpriced)",
                    "z_score": round(z, 3),
                    "cpi_yoy": cpi_yoy
                }
            }
        elif z > self.z_entry:
            return {
                "signal": "sell",
                "score": min(z / 3.0, 1.0),
                "meta": {
                    "trade": "long_nominal",
                    "action": "long IEF / short TIP (breakeven overpriced)",
                    "z_score": round(z, 3)
                }
            }

        return {"signal": "hold", "score": 0.0, "meta": {"z_score": round(z, 3)}}



# ─────────────────────────────────────────────
# Vault #96 & #97 — Volatility Carry & VIX Mean Reversion
# ─────────────────────────────────────────────

class VolatilityCarryStrategy:
    """
    Vault #96: Volatility risk premium — sell expensive implied vol, collect carry.
    Vault #97: VIX mean reversion — buy SVXY when VIX spikes, short VXX.
    ETFs: VXX (long vol), SVXY (short vol), VIXY
    Note: inverse vol ETFs have decay — only for short-term mean reversion.
    """

    SPIKE_THRESHOLD = 25.0   # VIX level considered "elevated"
    EXTREME_THRESHOLD = 35.0  # VIX level considered "panic"
    MEAN_VIX = 18.0

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def generate_signal(self, vix_levels: list, vxx_prices: list = None) -> dict:
        if len(vix_levels) < self.lookback:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        current_vix = vix_levels[-1]
        hist = vix_levels[-self.lookback:]
        z = _zscore(hist)
        roll_mean = _rolling_mean(hist, self.lookback)

        # Extreme spike — strong mean reversion signal (short VXX / long SVXY)
        if current_vix > self.EXTREME_THRESHOLD and z > 2.0:
            return {
                "signal": "sell",  # sell vol = sell VXX
                "score": min((current_vix - self.MEAN_VIX) / 30.0, 1.0),
                "meta": {
                    "trade": "short_vol_extreme",
                    "action": "long SVXY / short VXX (panic VIX spike)",
                    "vix": current_vix,
                    "z_score": round(z, 3),
                    "etfs": {"long": "SVXY", "short": "VXX"},
                    "hold_days": "3-7"
                }
            }
        # Moderate spike — carry trade entry
        elif current_vix > self.SPIKE_THRESHOLD and z > 1.0:
            return {
                "signal": "sell",
                "score": min((current_vix - self.SPIKE_THRESHOLD) / 20.0, 0.7),
                "meta": {
                    "trade": "vol_carry",
                    "action": "short VXX (vol risk premium carry)",
                    "vix": current_vix,
                    "z_score": round(z, 3),
                    "etfs": {"short": "VXX"}
                }
            }
        # VIX low — stay out of vol trades (contango decay favors longs now)
        elif current_vix < 14.0:
            return {
                "signal": "hold",
                "score": 0.0,
                "meta": {"reason": "VIX too low for carry", "vix": current_vix}
            }

        return {"signal": "hold", "score": 0.0, "meta": {"vix": current_vix, "z_score": round(z, 3)}}



# ─────────────────────────────────────────────
# Vault #102-106 — FX Strategies via Currency ETFs
# ─────────────────────────────────────────────

class FXCarryStrategy:
    """
    Vault #102: FX Carry Trade — long high-yield currencies, short low-yield.
    Vault #103: FX Momentum — trend-follow currency ETFs.
    Vault #104: FX Mean Reversion — fade large FX moves.
    ETFs: UUP (USD bull), FXE (Euro), FXY (Yen), FXA (AUD), EWJ (Japan)
    """

    def __init__(self, momentum_window: int = 60, reversion_window: int = 20,
                 reversion_z: float = 1.8):
        self.momentum_window = momentum_window
        self.reversion_window = reversion_window
        self.reversion_z = reversion_z

    def carry_signal(self, high_yield_fx_prices: list, low_yield_fx_prices: list) -> dict:
        """Long high-yield currency ETF / short low-yield (e.g., AUD vs JPY)."""
        if len(high_yield_fx_prices) < 20 or len(low_yield_fx_prices) < 20:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        hi_ret = (high_yield_fx_prices[-1] / high_yield_fx_prices[-20]) - 1
        lo_ret = (low_yield_fx_prices[-1] / low_yield_fx_prices[-20]) - 1
        carry_spread = hi_ret - lo_ret

        if carry_spread > 0.01:
            return {
                "signal": "buy",
                "score": min(carry_spread * 20, 1.0),
                "meta": {"trade": "fx_carry", "carry_spread_20d": round(carry_spread, 4)}
            }
        return {"signal": "hold", "score": 0.0, "meta": {"carry_spread_20d": round(carry_spread, 4)}}

    def momentum_signal(self, fx_prices: list, ticker: str = "") -> dict:
        """Trend-follow a currency ETF over momentum_window days."""
        if len(fx_prices) < self.momentum_window:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        ret_long = (fx_prices[-1] / fx_prices[-self.momentum_window]) - 1
        ret_short = (fx_prices[-1] / fx_prices[-min(20, len(fx_prices))]) - 1

        if ret_long > 0.02 and ret_short > 0:
            return {
                "signal": "buy",
                "score": min(abs(ret_long) * 10, 1.0),
                "meta": {"trade": "fx_momentum", "ticker": ticker,
                         "return_60d": round(ret_long, 4)}
            }
        elif ret_long < -0.02 and ret_short < 0:
            return {
                "signal": "sell",
                "score": min(abs(ret_long) * 10, 1.0),
                "meta": {"trade": "fx_momentum_short", "ticker": ticker,
                         "return_60d": round(ret_long, 4)}
            }
        return {"signal": "hold", "score": 0.0, "meta": {"return_60d": round(ret_long, 4)}}

    def mean_reversion_signal(self, fx_prices: list, ticker: str = "") -> dict:
        """Fade extreme FX moves — buy after sharp drop, sell after sharp rally."""
        if len(fx_prices) < self.reversion_window:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        window = fx_prices[-self.reversion_window:]
        z = _zscore(window)

        if z < -self.reversion_z:
            return {
                "signal": "buy",
                "score": min(abs(z) / 4.0, 1.0),
                "meta": {"trade": "fx_mean_rev_long", "ticker": ticker, "z_score": round(z, 3)}
            }
        elif z > self.reversion_z:
            return {
                "signal": "sell",
                "score": min(z / 4.0, 1.0),
                "meta": {"trade": "fx_mean_rev_short", "ticker": ticker, "z_score": round(z, 3)}
            }
        return {"signal": "hold", "score": 0.0, "meta": {"z_score": round(z, 3)}}



# ─────────────────────────────────────────────
# Vault #82 & #83 — Bond Momentum & Duration Timing
# ─────────────────────────────────────────────

class BondMomentumStrategy:
    """
    Vault #82: Fixed income momentum — rotate across duration buckets.
    Vault #83: Duration timing — extend/reduce duration based on rate trend.
    ETFs: SHY (1-3yr), IEF (7-10yr), TLT (20+yr), AGG (total bond market)
    """

    DURATION_ETFS = ["SHY", "IEF", "TLT"]  # short → long duration

    def __init__(self, momentum_window: int = 60, top_n: int = 1):
        self.momentum_window = momentum_window
        self.top_n = top_n

    def rank_bonds(self, price_series: dict) -> dict:
        """
        price_series: {"SHY": [prices], "IEF": [prices], "TLT": [prices]}
        Returns ranked ETFs by momentum score.
        """
        scores = {}
        for ticker, prices in price_series.items():
            if len(prices) < self.momentum_window:
                scores[ticker] = 0.0
                continue
            ret = (prices[-1] / prices[-self.momentum_window]) - 1
            vol = _rolling_std(
                [(prices[i]/prices[i-1]-1) for i in range(1, len(prices))],
                min(20, len(prices)-1)
            )
            scores[ticker] = ret / max(vol, 1e-6)  # risk-adjusted momentum

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top = ranked[:self.top_n]

        signal = "buy" if top[0][1] > 0 else "hold"
        return {
            "signal": signal,
            "score": min(abs(top[0][1]) / 2.0, 1.0) if signal == "buy" else 0.0,
            "meta": {
                "trade": "bond_momentum",
                "ranked_etfs": [{"ticker": t, "score": round(s, 4)} for t, s in ranked],
                "top_pick": top[0][0] if signal == "buy" else None,
                "action": f"long {top[0][0]}" if signal == "buy" else "hold cash"
            }
        }

    def duration_timing_signal(self, rate_trend_prices: list, proxy_etf: str = "TLT") -> dict:
        """
        Simple duration timing: if rates trending down (TLT up), go long duration.
        If rates trending up (TLT down), reduce to short duration (SHY) or cash.
        """
        if len(rate_trend_prices) < 60:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        ma20  = _rolling_mean(rate_trend_prices, 20)
        ma60  = _rolling_mean(rate_trend_prices, 60)
        price = rate_trend_prices[-1]

        if price > ma20 > ma60:  # uptrend → rates falling → extend duration
            return {
                "signal": "buy",
                "score": min((price - ma60) / ma60 * 50, 1.0),
                "meta": {"trade": "long_duration", "action": f"long {proxy_etf}",
                         "ma20": round(ma20, 2), "ma60": round(ma60, 2)}
            }
        elif price < ma20 < ma60:  # downtrend → rates rising → shorten duration
            return {
                "signal": "sell",
                "score": min((ma60 - price) / ma60 * 50, 1.0),
                "meta": {"trade": "short_duration", "action": "long SHY / exit TLT",
                         "ma20": round(ma20, 2), "ma60": round(ma60, 2)}
            }
        return {"signal": "hold", "score": 0.0,
                "meta": {"ma20": round(ma20, 2), "ma60": round(ma60, 2)}}



# ─────────────────────────────────────────────
# Vault #105 & #106 — Currency Crash & Safe Haven
# ─────────────────────────────────────────────

class SafeHavenStrategy:
    """
    Vault #105: Safe haven rotation — fly to quality (USD, gold, treasuries) in risk-off.
    Vault #106: Currency crash detection — short EM FX proxies on stress signals.
    ETFs: GLD (gold), UUP (USD), TLT (treasuries), EEM (EM equity — inverse signal)
    """

    def __init__(self, lookback: int = 20, stress_z: float = 1.5):
        self.lookback = lookback
        self.stress_z = stress_z

    def risk_regime_signal(self, spy_prices: list, vix_levels: list,
                           eem_prices: list = None) -> dict:
        """
        Detect risk-off regime → rotate into GLD + TLT + UUP.
        Detect risk-on regime → reduce safe haven allocation.
        """
        if len(spy_prices) < self.lookback or len(vix_levels) < self.lookback:
            return {"signal": "hold", "score": 0.0, "meta": {"reason": "insufficient data"}}

        spy_ret_5d = (spy_prices[-1] / spy_prices[-min(5, len(spy_prices))]) - 1
        vix_now = vix_levels[-1]
        vix_z = _zscore(vix_levels[-self.lookback:])

        # Risk-off conditions
        risk_off_score = 0.0
        if spy_ret_5d < -0.03:
            risk_off_score += 0.4
        if vix_now > 25:
            risk_off_score += 0.3
        if vix_z > self.stress_z:
            risk_off_score += 0.3

        if eem_prices and len(eem_prices) >= 5:
            eem_ret = (eem_prices[-1] / eem_prices[-5]) - 1
            if eem_ret < -0.04:
                risk_off_score += 0.2

        risk_off_score = min(risk_off_score, 1.0)

        if risk_off_score > 0.5:
            return {
                "signal": "buy",
                "score": risk_off_score,
                "meta": {
                    "trade": "safe_haven_rotation",
                    "action": "long GLD + TLT + UUP (risk-off)",
                    "etfs": ["GLD", "TLT", "UUP"],
                    "spy_5d_ret": round(spy_ret_5d, 4),
                    "vix": vix_now,
                    "vix_z": round(vix_z, 3)
                }
            }
        elif risk_off_score < 0.2:
            return {
                "signal": "sell",  # risk-on — reduce safe havens
                "score": 1.0 - risk_off_score,
                "meta": {
                    "trade": "risk_on",
                    "action": "reduce GLD/TLT — rotate to equities",
                    "risk_off_score": round(risk_off_score, 3)
                }
            }

        return {"signal": "hold", "score": 0.0,
                "meta": {"risk_off_score": round(risk_off_score, 3)}}


# ─────────────────────────────────────────────
# Convenience: get all strategy instances
# ─────────────────────────────────────────────

def get_all_strategies():
    return {
        "yield_curve":     YieldCurveStrategy(),
        "credit_spread":   CreditSpreadStrategy(),
        "tips_inflation":  TIPSInflationStrategy(),
        "vol_carry":       VolatilityCarryStrategy(),
        "fx_carry":        FXCarryStrategy(),
        "bond_momentum":   BondMomentumStrategy(),
        "safe_haven":      SafeHavenStrategy(),
    }
