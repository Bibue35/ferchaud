"""
Options Catalyst Strategy
━━━━━━━━━━━━━━━━━━━━━━━━
Buys short-dated calls/puts on high-conviction catalyst setups.
Options can 5-20x on a 5-10% underlying move.
"""
import time
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from config import CONFIG
from strategies.base import BaseStrategy
from utils.logger import get_logger

log = get_logger("strategy.options_catalyst")

_H = {"APCA-API-KEY-ID": CONFIG.api_key, "APCA-API-SECRET-KEY": CONFIG.secret_key}
_TRADE = "https://paper-api.alpaca.markets/v2"
_OPT   = "https://paper-api.alpaca.markets/v2/options"

OTM_PCT       = 0.03
MIN_DTE       = 5
MAX_DTE       = 21
MAX_SPEND     = 1500
MAX_POSITIONS = 8


class OptionsCatalystStrategy(BaseStrategy):
    """Buys calls/puts when insider buying + scanner signal + news align."""

    name = "Options Catalyst"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._open_opts: Dict[str, dict] = {}   # symbol -> {contract, side, entry}
        self._insider_cache: Dict[str, float] = {}
        self._insider_ts: float = 0.0
        self._last_edgar_check: float = 0.0
        self._edgar_cooldown = 600  # refresh every 10 min
        self._last_rank_ts: float = 0.0
        self._rank_cache: list = []  # cached candidates

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            return

        # Exit positions that have doubled or hit -50%
        self._manage_exits()

        if len(self._open_opts) >= MAX_POSITIONS:
            return

        # Refresh insider data every 10 min
        self._refresh_insider_buys()

        # Score and trade
        candidates = self._rank_candidates()
        for sym, direction, score in candidates[:3]:
            if sym in self._open_opts:
                continue
            self._enter_option(sym, direction, score)
            time.sleep(0.5)

    # ── Insider buying (SEC EDGAR Form 4) ─────────────────────────────────────

    def _refresh_insider_buys(self) -> None:
        now = time.time()
        if now - self._last_edgar_check < self._edgar_cooldown:
            return
        self._last_edgar_check = now
        try:
            # EDGAR RSS feed for Form 4 (insider transactions)
            url = "https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom&startdt={}&enddt={}&forms=4"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
            r = requests.get(
                "https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom"
                f"&startdt={yesterday}&enddt={today}",
                headers={"User-Agent": "QuantBot research@example.com"},
                timeout=10,
            )
            if r.status_code != 200:
                return

            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            counts: Dict[str, int] = {}
            for hit in hits[:200]:
                src = hit.get("_source", {})
                tickers = src.get("period_of_report", "")
                # Extract ticker from entity name or display_names
                display = src.get("display_names", [])
                for d in display:
                    name = d.get("name", "") if isinstance(d, dict) else str(d)
                    if any(sym in name.upper() for sym in CONFIG.stock_universe):
                        for sym in CONFIG.stock_universe:
                            if sym in name.upper():
                                counts[sym] = counts.get(sym, 0) + 1

            self._insider_cache = {s: float(c) for s, c in counts.items()}
            if counts:
                top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]
                log.info("Insider Form 4 activity: %s",
                         ", ".join(f"{s}({c})" for s, c in top))
        except Exception as e:
            log.debug("EDGAR fetch error: %s", e)

    # ── Candidate ranking ─────────────────────────────────────────────────────

    def _rank_candidates(self) -> List[tuple]:
        """Return (symbol, direction, score) sorted by score desc. Cached 5 min."""
        import time
        now = time.time()
        if now - self._last_rank_ts < 300:  # 5-min cache
            return self._rank_cache
        self._last_rank_ts = now

        # Prioritize symbols with insider activity; fall back to full list
        insider_syms = sorted(self._insider_cache, key=lambda s: self._insider_cache[s], reverse=True)[:8]
        all_syms = list(dict.fromkeys(insider_syms + self.get_symbols()))[:15]

        results = []
        for sym in all_syms:
            try:
                score, direction = self._score_symbol(sym)
                if score >= 2.0 and direction:
                    results.append((sym, direction, score))
            except Exception:
                pass
        results.sort(key=lambda x: x[2], reverse=True)
        self._rank_cache = results
        return results

    def _score_symbol(self, sym: str):
        bars = self.feed.get_bars(sym, "1D", limit=10)
        if bars.empty or len(bars) < 5:
            return 0.0, None

        price = float(bars["close"].iloc[-1])
        prev  = float(bars["close"].iloc[-2])
        daily_chg = (price - prev) / prev
        vol_ratio = (float(bars["volume"].iloc[-1]) /
                     float(bars["volume"].iloc[:-1].mean() + 1))

        score = 0.0
        direction = None

        # Strong move today
        if abs(daily_chg) > 0.04:
            score += 2.0
            direction = "call" if daily_chg > 0 else "put"

        # Volume spike (smart money entering)
        if vol_ratio > 2.5:
            score += 1.5

        # Insider buying signal
        if self._insider_cache.get(sym, 0) >= 2:
            score += 2.0
            direction = direction or "call"

        # 5-day momentum
        mom5 = (price - float(bars["close"].iloc[-6])) / float(bars["close"].iloc[-6]) \
               if len(bars) > 5 else 0
        if abs(mom5) > 0.08:
            score += 1.0
            if direction is None:
                direction = "call" if mom5 > 0 else "put"

        return score, direction

    # ── Options order logic ───────────────────────────────────────────────────

    def _enter_option(self, sym: str, option_type: str, score: float) -> None:
        try:
            price = self._get_underlying_price(sym)
            if not price:
                return

            contract = self._find_contract(sym, option_type, price)
            if not contract:
                log.debug("No suitable contract for %s %s", sym, option_type)
                return

            contract_symbol = contract["symbol"]
            ask = float(contract.get("ask_price") or contract.get("close_price") or 0)
            if ask <= 0:
                return

            qty = min(int(MAX_SPEND / (ask * 100)), 10)
            if qty < 1:
                return

            log.info("OPTIONS %s %s %s | underlying=$%.2f ask=$%.2f qty=%d score=%.1f",
                     option_type.upper(), sym, contract_symbol, price, ask, qty, score)

            payload = {
                "symbol": contract_symbol,
                "qty": str(qty),
                "side": "buy",
                "type": "limit",
                "limit_price": str(round(ask * 1.02, 2)),
                "time_in_force": "day",
            }
            r = requests.post(f"{_TRADE}/orders", headers=_H, json=payload, timeout=10)
            if r.status_code in (200, 201):
                oid = r.json().get("id")
                self._open_opts[sym] = {
                    "contract": contract_symbol,
                    "type": option_type,
                    "entry_ask": ask,
                    "qty": qty,
                    "order_id": oid,
                    "ts": time.time(),
                }
                log.info("Options order placed: %s x%d [%s]", contract_symbol, qty, oid)
            else:
                log.warning("Options order failed: %s", r.text[:200])
        except Exception as e:
            log.error("_enter_option(%s): %s", sym, e)

    def _find_contract(self, sym: str, option_type: str, price: float) -> Optional[dict]:
        """Find the most liquid near-the-money contract in MIN_DTE to MAX_DTE range."""
        try:
            today = datetime.now(timezone.utc)
            exp_min = (today + timedelta(days=MIN_DTE)).strftime("%Y-%m-%d")
            exp_max = (today + timedelta(days=MAX_DTE)).strftime("%Y-%m-%d")

            r = requests.get(
                f"{_OPT}/contracts",
                headers=_H,
                params={
                    "underlying_symbols": sym,
                    "type": option_type,
                    "status": "active",
                    "expiration_date_gte": exp_min,
                    "expiration_date_lte": exp_max,
                    "limit": 20,
                },
                timeout=10,
            )
            if r.status_code != 200:
                return None

            contracts = r.json().get("option_contracts", [])
            if not contracts:
                return None

            # Target strike: 2-4% OTM
            if option_type == "call":
                target_strike = price * (1 + OTM_PCT)
                contracts = [c for c in contracts if float(c["strike_price"]) >= price * 0.99]
            else:
                target_strike = price * (1 - OTM_PCT)
                contracts = [c for c in contracts if float(c["strike_price"]) <= price * 1.01]

            if not contracts:
                return None

            # Pick closest to target strike
            contracts.sort(key=lambda c: abs(float(c["strike_price"]) - target_strike))
            return contracts[0]
        except Exception as e:
            log.debug("_find_contract(%s): %s", sym, e)
            return None

    def _get_underlying_price(self, sym: str) -> Optional[float]:
        try:
            r = requests.get(
                f"{_TRADE}/stocks/{sym}/trades/latest",
                headers=_H, timeout=5,
            )
            if r.status_code == 200:
                return float(r.json()["trade"]["p"])
        except Exception:
            pass
        return None

    # ── Exit management ───────────────────────────────────────────────────────

    def _manage_exits(self) -> None:
        for sym, info in list(self._open_opts.items()):
            try:
                contract = info["contract"]
                entry_ask = info["entry_ask"]

                # Get current contract price
                r = requests.get(
                    f"{_OPT}/contracts/{contract}",
                    headers=_H, timeout=5,
                )
                if r.status_code != 200:
                    continue

                data = r.json()
                cur_price = float(data.get("last_price") or data.get("close_price") or 0)
                if cur_price <= 0:
                    continue

                pnl_pct = (cur_price - entry_ask) / entry_ask
                age_hours = (time.time() - info["ts"]) / 3600

                # Exit rules: +100% profit, -50% stop, or 18h old
                if pnl_pct >= 1.0:
                    log.info("OPTIONS EXIT (profit +%.0f%%) %s", pnl_pct * 100, contract)
                    self._close_option(sym, info)
                elif pnl_pct <= -0.50:
                    log.info("OPTIONS EXIT (stop -50%%) %s", contract)
                    self._close_option(sym, info)
                elif age_hours > 18:
                    log.info("OPTIONS EXIT (time) %s", contract)
                    self._close_option(sym, info)
            except Exception:
                pass

    def _close_option(self, sym: str, info: dict) -> None:
        try:
            contract = info["contract"]
            qty = info["qty"]
            payload = {
                "symbol": contract,
                "qty": str(qty),
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
            }
            r = requests.post(f"{_TRADE}/orders", headers=_H, json=payload, timeout=10)
            if r.status_code in (200, 201):
                log.info("Options closed: %s x%d", contract, qty)
                self._open_opts.pop(sym, None)
        except Exception as e:
            log.error("_close_option(%s): %s", sym, e)
