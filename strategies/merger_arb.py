"""
Event-Driven Strategies — Vault Strategies #71, #94, #126, #148
Covers:
  #71  Event-Driven M&A (pre-announcement)
  #94  Index Inclusion Effect
  #126 Economic Announcement Trading
  #148 Merger Arbitrage (Risk Arbitrage — post-announcement)

These strategies trade around specific, known catalyst events.
All executable via Alpaca standard equity account.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional


class MergerArbitrageStrategy:
    """
    Vault #148: Risk Arbitrage — post-announcement deal spread.
    Buy target stock at discount to deal price; capture spread at closing.

    Also covers Vault #71: pre-announcement patterns (unusual options activity,
    above-average volume, management meetings, activist filings).
    """
    name        = "MergerArbitrage"
    description = "Post-announcement deal spread capture and pre-announcement signals (Vault #71/#148)."
    max_positions     = 5    # concentration risk in deal-specific outcomes
    position_size_pct = 0.04  # smaller positions — binary outcome risk

    # Deal completion probability tiers → position sizing
    PROB_TIERS = {
        "high":   (0.90, 1.0),    # 90%+ → full allocation
        "medium": (0.75, 0.90),   # 75–90% → 60% allocation
        "low":    (0.60, 0.75),   # 60–75% → 30% allocation
    }

    @staticmethod
    def deal_spread(current_price: float, offer_price: float) -> float:
        """Gross spread = (offer - current) / current."""
        if current_price <= 0:
            return 0.0
        return (offer_price - current_price) / current_price

    @staticmethod
    def annualized_spread(gross_spread: float, days_to_close: int) -> float:
        """Annualized return = gross_spread × (365 / days_to_close)."""
        if days_to_close <= 0:
            return 0.0
        return gross_spread * (365 / days_to_close)

    def generate_signal(self, current_price: float, offer_price: float,
                        deal_completion_prob: float, days_to_close: int,
                        min_annualized_return: float = 0.08) -> dict:
        """
        Generate long signal for merger arb position.

        deal_completion_prob: estimated probability deal closes (0–1)
        days_to_close: estimated days until deal closes
        min_annualized_return: minimum annualized return required to enter (8%)
        """
        gross   = self.deal_spread(current_price, offer_price)
        ann_ret = self.annualized_spread(gross, days_to_close)

        # Expected return = prob × gross_spread − (1−prob) × deal_break_loss
        # Deal break typically −20% from current price
        deal_break_loss = -0.20
        expected = deal_completion_prob * gross + (1 - deal_completion_prob) * deal_break_loss
        ev_ratio = expected / abs(deal_break_loss) if deal_break_loss != 0 else 0

        enter = (ann_ret >= min_annualized_return and
                 deal_completion_prob >= 0.65 and
                 expected > 0)

        # Position sizing based on deal probability
        if deal_completion_prob >= 0.90:
            size_mult = 1.0
        elif deal_completion_prob >= 0.75:
            size_mult = 0.60
        else:
            size_mult = 0.30

        return {
            "signal":        "buy" if enter else "hold",
            "score":         round(float(expected), 4),
            "gross_spread":  round(gross * 100, 2),
            "ann_return":    round(ann_ret * 100, 2),
            "expected_val":  round(float(expected), 4),
            "ev_ratio":      round(float(ev_ratio), 3),
            "size_multiplier": size_mult,
            "days_to_close": days_to_close,
        }

    # ── Pre-announcement signals (Vault #71) ─────────────────────────────────

    @staticmethod
    def pre_announcement_score(unusual_options_vol: bool,
                               above_avg_volume: bool,
                               activist_filing: bool,
                               recent_management_meeting: bool,
                               sector_consolidation: bool) -> dict:
        """
        Screen for stocks showing pre-announcement M&A signals.
        Returns score 0–5 (each signal = 1 point).
        Score ≥ 3 → watchlist; ≥ 4 → speculative long.
        """
        signals = {
            "unusual_options_activity":    unusual_options_vol,
            "above_average_volume":        above_avg_volume,
            "activist_13D_filing":         activist_filing,
            "management_investor_meeting": recent_management_meeting,
            "sector_consolidation_trend":  sector_consolidation,
        }
        score = sum(1 for v in signals.values() if v)

        signal = "hold"
        if score >= 4:   signal = "buy"   # speculative long
        elif score >= 3: signal = "watch"

        return {"signal": signal, "pre_score": score, "signals": signals}


class IndexInclusionStrategy:
    """
    Vault #94: Index Inclusion Effect.
    When a stock is announced for S&P 500 / Russell 2000 addition,
    passive fund buying creates predictable price appreciation.
    Buy on announcement; sell on actual inclusion date.
    """
    name        = "IndexInclusion"
    description = "Trade the passive rebalancing price impact of index inclusions (Vault #94)."
    position_size_pct = 0.03    # small positions — short holding window
    max_positions = 8

    # Historical average inclusion premium by index
    HISTORICAL_PREMIUMS = {
        "SP500":     0.035,   # ~3.5% average announcement-to-inclusion premium
        "RUSSELL2000": 0.025, # ~2.5%
        "NASDAQ100": 0.020,
    }

    def generate_signal(self, ticker: str, index_name: str,
                        announcement_date: datetime,
                        inclusion_date: datetime,
                        estimated_passive_demand_pct: float = 0.0,
                        current_price: float = 0.0,
                        price_before_announcement: float = 0.0) -> dict:
        """
        estimated_passive_demand_pct: estimated buying as % of average daily volume
        """
        days_to_inclusion = (inclusion_date - datetime.now()).days
        days_since_ann    = (datetime.now() - announcement_date).days

        # Historical premium already priced in since announcement?
        hist_premium = self.HISTORICAL_PREMIUMS.get(index_name, 0.02)
        if price_before_announcement > 0 and current_price > 0:
            premium_already_priced = (current_price - price_before_announcement) / price_before_announcement
        else:
            premium_already_priced = 0.0

        remaining_upside = hist_premium - premium_already_priced

        # Only enter if meaningful upside remains and time horizon reasonable
        enter = (remaining_upside > 0.005 and
                 0 < days_to_inclusion <= 30 and
                 days_since_ann <= 3)  # Enter within 3 days of announcement

        score = remaining_upside + estimated_passive_demand_pct * 0.01

        return {
            "signal":                "buy" if enter else "hold",
            "score":                 round(score, 4),
            "remaining_upside":      round(remaining_upside * 100, 2),
            "premium_priced":        round(premium_already_priced * 100, 2),
            "days_to_inclusion":     days_to_inclusion,
            "days_since_ann":        days_since_ann,
            "hist_premium":          round(hist_premium * 100, 2),
        }


class EconomicAnnouncementStrategy:
    """
    Vault #126: Economic Announcement Trading.
    Trade SPY/QQQ/TLT in the minutes/hours following key economic releases
    (NFP, CPI, GDP, FOMC) when surprise direction is clear.
    """
    name        = "EconomicAnnouncement"
    description = "Directional trade on economic surprise factor post-release (Vault #126)."
    position_size_pct = 0.03   # very small — high vol around releases
    hold_hours        = 4      # close same session

    # Asset reactions to positive economic surprise
    SURPRISE_IMPACT = {
        "NFP":       {"SPY": +1, "TLT": -1, "GLD": -0.5, "UUP": +0.5},
        "CPI_high":  {"SPY": -1, "TLT": -1, "GLD": +1,   "UUP": +1},
        "CPI_low":   {"SPY": +1, "TLT": +1, "GLD": -0.5, "UUP": -0.5},
        "GDP":       {"SPY": +1, "TLT": -0.5,"GLD": -0.5,"UUP": +0.5},
        "FOMC_hike": {"SPY": -1, "TLT": -1,  "GLD": -0.5,"UUP": +1},
        "FOMC_cut":  {"SPY": +1, "TLT": +1,  "GLD": +0.5,"UUP": -1},
    }

    def generate_signals(self, release_type: str,
                         actual: float, consensus: float,
                         historical_std: float,
                         vix_level: float = 20.0) -> dict:
        """
        release_type: one of NFP, CPI_high, CPI_low, GDP, FOMC_hike, FOMC_cut
        actual: actual release value
        consensus: consensus estimate
        historical_std: std of past surprises (for z-score)
        vix_level: current VIX (avoid trading if > 40)
        """
        if historical_std <= 0 or vix_level > 40:
            return {"signals": {}, "surprise_z": 0.0, "tradeable": False}

        surprise_z = (actual - consensus) / historical_std
        impacts    = self.SURPRISE_IMPACT.get(release_type, {})

        # Only trade if surprise is meaningful (|z| > 1.0)
        if abs(surprise_z) < 1.0:
            return {"signals": {}, "surprise_z": round(surprise_z, 3), "tradeable": False}

        signals = {}
        for ticker, direction in impacts.items():
            # Direction × sign of surprise
            effective_dir = direction * np.sign(surprise_z)
            score = effective_dir * min(abs(surprise_z) / 3, 1.0)
            if effective_dir > 0:
                signals[ticker] = {"signal": "buy",  "score": round(score, 4)}
            elif effective_dir < 0:
                signals[ticker] = {"signal": "sell", "score": round(score, 4)}
            else:
                signals[ticker] = {"signal": "hold", "score": 0.0}

        return {
            "release":    release_type,
            "surprise_z": round(surprise_z, 3),
            "tradeable":  True,
            "signals":    signals,
        }
