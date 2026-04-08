"""
Ferchaud Strategy Vault — 151 strategies from academic research.
Each strategy includes: id, number (book ref), name, asset_class,
category (directional/neutral/income), description, signals, risk, status.

Pro tier: strategies 1-10 (equity strategies the bot actively uses).
Elite tier: all 151 strategies.
"""

# Status codes:
#   "live"     — actively running in the Ferchaud bot on Alpaca
#   "backtest" — implemented, backtesting only (pending live approval)
#   "equity_acct" — requires equity account (available via Alpaca)
#   "options_acct" — requires options trading approval
#   "futures_acct" — requires futures brokerage
#   "informational" — educational/institutional strategy, not Alpaca-tradeable

STRATEGIES = [
    # ─── PRO TIER: Equity Strategies (Bot-Implemented) ───────────────────────
    {
        "id": 1, "tier": "pro",
        "name": "Price Momentum",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Stocks that have outperformed over the past 2–12 months tend to continue "
            "outperforming over the next 1–3 months. The strategy ranks stocks by risk-adjusted "
            "cumulative return over a 12-month formation period (skipping the most recent month "
            "to avoid short-term reversal). Long the top decile, short the bottom decile in a "
            "dollar-neutral portfolio. Position weights inversely proportional to historical volatility."
        ),
        "signals": ["12-1 month cumulative return", "Risk-adjusted return (return/volatility)",
                    "Volume-weighted price trend", "52-week high proximity"],
        "entry": "Buy when stock ranks in top 20% by risk-adjusted momentum score.",
        "exit": "Sell when rank drops below median or stop-loss at −8% from entry.",
        "holding_period": "1–3 months (swing trading timeframe)",
        "risk": "Medium — momentum crashes occur during sharp market reversals.",
        "status": "live",
    },
    {
        "id": 2, "tier": "pro",
        "name": "Aggressive Breakout",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Identifies stocks breaking out of consolidation ranges on high relative volume. "
            "Combines price breakout above the N-day high with volume surge (>2× 20-day average), "
            "ATR-based volatility confirmation, and RSI momentum filter. Entry is triggered "
            "intraday on the breakout candle; stop-loss is placed below the consolidation low."
        ),
        "signals": ["N-day high breakout", "Volume ratio vs 20-day average",
                    "RSI(14) above 55", "ATR expansion", "Pre-market gap confirmation"],
        "entry": "Price closes above 20-day high with volume > 2× average and RSI > 55.",
        "exit": "Take profit at 2× ATR above entry; stop-loss at 1× ATR below entry.",
        "holding_period": "Intraday to 3 days",
        "risk": "Medium-High — false breakouts common in choppy markets.",
        "status": "live",
    },
    {
        "id": 3, "tier": "pro",
        "name": "Mean Reversion",
        "asset_class": "Equities", "category": "Neutral",
        "description": (
            "Stocks that have deviated significantly from their historical mean tend to revert. "
            "Identifies stocks 2+ standard deviations below their 20-day moving average with "
            "oversold RSI and declining volume. Pairs this with sector-relative analysis to "
            "ensure the deviation is idiosyncratic, not systematic. Long the oversold names, "
            "short the sector ETF as a hedge."
        ),
        "signals": ["Z-score vs 20-day MA (entry at z < −2)", "RSI(14) below 30",
                    "Bollinger Band lower breach", "Sector-relative strength"],
        "entry": "Stock falls >2σ below 20-day MA with RSI < 30 and sector ETF flat/up.",
        "exit": "Close when price returns to 20-day MA or RSI crosses back above 50.",
        "holding_period": "2–10 days",
        "risk": "Medium — can fail during sustained trends; always hedge with sector ETF.",
        "status": "live",
    },
    {
        "id": 4, "tier": "pro",
        "name": "Gap & Fill (Gap Short)",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Stocks that gap up on earnings or news frequently fade back toward the pre-gap level "
            "if the gap is unsupported by volume and is above the prior resistance zone. "
            "Strategy waits for the first 15-minute candle post-open to confirm direction, "
            "then shorts the gap-fill move with defined risk above the opening high."
        ),
        "signals": ["Gap % vs prior close (>3%)", "First 15-min candle direction",
                    "Relative volume at open", "Float size (prefer small/mid cap)",
                    "Options IV crush post-earnings"],
        "entry": "Short when price fails to hold opening high after 15-min candle confirms reversal.",
        "exit": "Cover at 50% gap fill or stop above the opening high + 0.5%.",
        "holding_period": "Intraday (same session)",
        "risk": "High — gap continuation is the primary risk; strict stop required.",
        "status": "live",
    },
    {
        "id": 5, "tier": "pro",
        "name": "Multi-Factor Alpha",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Combines multiple factor signals — momentum, value (P/B ratio), quality (ROE), "
            "low volatility, and earnings revision — into a composite score. Each factor is "
            "standardized (z-scored) cross-sectionally, then averaged. Portfolio is constructed "
            "by going long the top quintile and short the bottom quintile with Sharpe-optimal "
            "weighting (inverse-variance portfolio weights)."
        ),
        "signals": ["12-1M momentum z-score", "Book-to-price z-score",
                    "ROE z-score", "1/historical-volatility z-score",
                    "Earnings revision z-score (3-month)"],
        "entry": "Composite z-score > 1.5 for long; < −1.5 for short.",
        "exit": "Rebalance monthly; exit individual position if z-score crosses zero.",
        "holding_period": "1 month (monthly rebalance)",
        "risk": "Low-Medium — diversified across factors reduces idiosyncratic risk.",
        "status": "live",
    },
    {
        "id": 6, "tier": "pro",
        "name": "Statistical Arbitrage",
        "asset_class": "Equities", "category": "Neutral",
        "description": (
            "Identifies pairs or clusters of historically co-integrated stocks within the same "
            "sector. When the spread between two co-integrated stocks diverges beyond 2σ of "
            "its historical distribution, trade the convergence: long the cheaper stock, short "
            "the richer. Uses Johansen cointegration test to select pairs and Kalman filter "
            "for dynamic hedge ratio estimation."
        ),
        "signals": ["Cointegration test (Johansen)", "Spread z-score > 2σ",
                    "Kalman-filtered hedge ratio", "Half-life of mean reversion < 30 days"],
        "entry": "Spread z-score crosses +2σ (short the spread) or −2σ (long the spread).",
        "exit": "Unwind when spread reverts to ±0.5σ; stop at ±3σ.",
        "holding_period": "3–20 days",
        "risk": "Medium — correlation breakdown is the primary risk.",
        "status": "live",
    },
    {
        "id": 7, "tier": "pro",
        "name": "Market Making",
        "asset_class": "Equities", "category": "Neutral",
        "description": (
            "Captures bid-ask spread by simultaneously posting limit orders on both sides of "
            "the market. Uses order flow toxicity (VPIN) to detect informed trading and "
            "temporarily withdraw from the market. Inventory limits prevent directional exposure. "
            "Effective in liquid mid-cap stocks during regular market hours with low VIX."
        ),
        "signals": ["VPIN toxicity score (withdraw if > 0.7)", "Bid-ask spread > 0.05%",
                    "Inventory imbalance limit (±500 shares)", "VIX < 25 filter"],
        "entry": "Post limit buy at bid + 1 tick; limit sell at ask − 1 tick simultaneously.",
        "exit": "Fill on either side; immediately repost. Flatten inventory if VPIN spikes.",
        "holding_period": "Seconds to minutes",
        "risk": "Low-Medium in calm markets; high during news events.",
        "status": "live",
    },
    {
        "id": 8, "tier": "pro",
        "name": "Catalyst Event-Driven",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Trades around specific catalysts: earnings surprises, FDA approvals, M&A announcements, "
            "index inclusions. Combines fundamental analysis (earnings estimate vs. consensus), "
            "options market signals (unusual call/put activity, IV skew change), and social media "
            "sentiment (via Grok X-research) to predict the direction and magnitude of the price move."
        ),
        "signals": ["Earnings surprise vs. consensus", "Options unusual volume ratio",
                    "IV skew change (call/put IV difference)", "Social sentiment momentum",
                    "SEC filing scanner (8-K, S-1)"],
        "entry": "Position established 1–2 days before known catalyst; directional based on signals.",
        "exit": "Close 1 day after catalyst event; scale out on large intraday moves.",
        "holding_period": "1–3 days around event",
        "risk": "High — binary outcomes; position size capped at 2% of portfolio per trade.",
        "status": "live",
    },
    {
        "id": 9, "tier": "pro",
        "name": "Predictive Short (High Short Interest)",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "Targets stocks with high short interest ratio (> 20% of float), declining fundamentals, "
            "and technical distribution patterns. Uses the Altman Z-score to identify financially "
            "stressed companies, overlaid with insider selling signals and decelerating revenue growth. "
            "Short the stock with a trailing stop to manage squeeze risk."
        ),
        "signals": ["Short interest / float > 20%", "Altman Z-score < 1.8 (distress zone)",
                    "Insider net selling over past 90 days", "Revenue growth deceleration (3 quarters)",
                    "Technical: lower highs + lower lows pattern"],
        "entry": "All 5 signals align; enter short on a failed bounce below the 20-day MA.",
        "exit": "Cover at 15–20% profit target or if stock gaps up >5% on news.",
        "holding_period": "1–4 weeks",
        "risk": "Very High — short squeeze risk; hard borrows increase cost.",
        "status": "live",
    },
    {
        "id": 10, "tier": "pro",
        "name": "Micro-Cap Scalper",
        "asset_class": "Equities", "category": "Directional",
        "description": (
            "High-frequency scalping of micro-cap stocks during the first 30 minutes of trading "
            "when momentum is strongest. Identifies stocks gapping up with news catalysts and "
            "high relative volume. Enters on the first pullback after the opening spike and "
            "exits within minutes to capture 0.5–2% moves. Strict position sizing."
        ),
        "signals": ["Pre-market gap > 5%", "News catalyst confirmed", "Relative volume > 5×",
                    "L2 order book depth (bid support)", "VWAP as dynamic support"],
        "entry": "Buy on first pullback to VWAP with L2 showing strong bid support.",
        "exit": "Sell at high-of-day or if price drops below VWAP with momentum loss.",
        "holding_period": "1–30 minutes",
        "risk": "High — wide spreads and thin liquidity; max position size 1% portfolio.",
        "status": "live",
    },

    # ─── ELITE TIER ONLY: Options Strategies (58) ────────────────────────────
    {
        "id": 11, "tier": "elite",
        "name": "Covered Call",
        "asset_class": "Options", "category": "Income",
        "description": "Hold stock, sell OTM call. Earns premium income; caps upside. Effective in sideways to slightly bullish markets. Maximum profit = call premium + (strike − entry). Maximum loss = stock value − premium.",
        "signals": ["Stock in sideways trend", "IV rank > 50 (rich premium)", "30–45 DTE options"],
        "entry": "Sell call 1–2 strikes OTM with 30–45 days to expiration.", "exit": "Buy back at 50% profit or roll forward at expiration.",
        "holding_period": "30–45 days", "risk": "Low — capped upside is the cost.", "status": "options_acct",
    },
    {
        "id": 12, "tier": "elite",
        "name": "Covered Put",
        "asset_class": "Options", "category": "Income",
        "description": "Short stock + sell OTM put. Generates income from the put premium while maintaining short position. Bearish outlook. Maximum profit = premium + (entry − strike).",
        "signals": ["Stock in downtrend", "IV rank > 50", "Short borrow available"],
        "entry": "Sell put 1–2 strikes OTM (below current price) with 30 DTE.", "exit": "Buy back put at 50% profit.",
        "holding_period": "30 days", "risk": "High — unlimited loss if stock reverses sharply upward.", "status": "options_acct",
    },
    {
        "id": 13, "tier": "elite",
        "name": "Protective Put (Married Put)",
        "asset_class": "Options", "category": "Hedging",
        "description": "Long stock + long ATM/OTM put. Hedges downside risk while preserving upside. Acts as portfolio insurance. Cost = put premium (the 'insurance premium').",
        "signals": ["Long-term bullish on stock", "Near-term uncertainty (earnings, event)", "IV relatively low"],
        "entry": "Buy put at or just below current price with 60–90 DTE.", "exit": "Let expire worthless (if stock rises) or exercise (if stock falls).",
        "holding_period": "Until expiration", "risk": "Low — max loss = premium paid.", "status": "options_acct",
    },
    {
        "id": 14, "tier": "elite",
        "name": "Protective Call (Married Call)",
        "asset_class": "Options", "category": "Hedging",
        "description": "Short stock + long ATM/OTM call. Hedges upside risk on a short position. Bearish with defined upside risk. Maximum loss = call premium.",
        "signals": ["Short position in stock", "Near-term uncertainty", "IV relatively low"],
        "entry": "Buy call at or just above current price.", "exit": "Exercise if stock rises above strike.",
        "holding_period": "Until expiration", "risk": "Low — defined risk.", "status": "options_acct",
    },
    {
        "id": 15, "tier": "elite",
        "name": "Bull Call Spread",
        "asset_class": "Options", "category": "Directional",
        "description": "Buy near-ATM call, sell OTM call at higher strike. Net debit. Bullish outlook with defined risk/reward. Max profit = spread width − debit paid. Max loss = debit paid.",
        "signals": ["Moderately bullish", "IV rank low (cheap options)", "Clear resistance above"],
        "entry": "Buy lower call, sell upper call — same expiry, 30–45 DTE.", "exit": "Close at 50–75% of max profit.",
        "holding_period": "30–45 days", "risk": "Low — max loss = debit.", "status": "options_acct",
    },
    {
        "id": 16, "tier": "elite",
        "name": "Bull Put Spread",
        "asset_class": "Options", "category": "Income",
        "description": "Sell higher-strike put, buy lower-strike put. Net credit. Bullish to neutral. Profit if stock stays above the short strike. Max profit = credit received. Max loss = spread width − credit.",
        "signals": ["Neutral to bullish", "IV rank > 50 (rich premium)", "Strong support level"],
        "entry": "Sell put near current price, buy put further OTM — same expiry.", "exit": "Close at 50% of credit or let expire.",
        "holding_period": "30–45 days", "risk": "Medium — loss if stock falls through support.", "status": "options_acct",
    },
    {
        "id": 17, "tier": "elite",
        "name": "Bear Call Spread",
        "asset_class": "Options", "category": "Income",
        "description": "Sell lower-strike call, buy higher-strike call. Net credit. Bearish to neutral. Profit if stock stays below short strike. Max profit = credit. Max loss = spread width − credit.",
        "signals": ["Neutral to bearish", "IV rank > 50", "Clear resistance overhead"],
        "entry": "Sell call near current price, buy call further OTM.", "exit": "Close at 50% profit or let expire.",
        "holding_period": "30–45 days", "risk": "Medium.", "status": "options_acct",
    },
    {
        "id": 18, "tier": "elite",
        "name": "Bear Put Spread",
        "asset_class": "Options", "category": "Directional",
        "description": "Buy near-ATM put, sell lower-strike put. Net debit. Bearish. Max profit = spread width − debit. Max loss = debit paid.",
        "signals": ["Bearish outlook", "IV rank low", "Clear support below"],
        "entry": "Buy higher-strike put, sell lower-strike put.", "exit": "Close at 50–75% of max profit.",
        "holding_period": "30–45 days", "risk": "Low — defined risk.", "status": "options_acct",
    },
    {
        "id": 19, "tier": "elite",
        "name": "Long Synthetic Forward",
        "asset_class": "Options", "category": "Directional",
        "description": "Buy ATM call + sell ATM put at same strike. Mimics long stock/futures position with minimal upfront cost. Bullish. Unlimited profit and loss.",
        "signals": ["Strongly bullish", "Cost-efficient vs. buying stock outright"],
        "entry": "Buy call and sell put at same ATM strike, same expiry.", "exit": "Unwind before expiry or let underlying delivery occur.",
        "holding_period": "Until expiry", "risk": "High — unlimited downside.", "status": "options_acct",
    },
    {
        "id": 20, "tier": "elite",
        "name": "Short Synthetic Forward",
        "asset_class": "Options", "category": "Directional",
        "description": "Buy ATM put + sell ATM call at same strike. Mimics short stock/futures. Bearish. Unlimited loss on upside.",
        "signals": ["Strongly bearish"],
        "entry": "Buy put and sell call at same ATM strike.", "exit": "Unwind before expiry.",
        "holding_period": "Until expiry", "risk": "High — unlimited upside risk.", "status": "options_acct",
    },

    # Options continued (21–58) ─────────────────────────────────────────────
    {"id":21,"tier":"elite","name":"Long Straddle","asset_class":"Options","category":"Volatility","description":"Buy ATM call + ATM put, same strike/expiry. Profits from large moves in either direction. Best before major events (earnings, FDA). Max loss = total debit.","signals":["Low IV (cheap to enter)","Upcoming binary event","Stock near key level"],"entry":"Buy both ATM call and put, 1–2 weeks before event.","exit":"Sell one leg on big move; close other leg.","holding_period":"Days to weeks","risk":"Medium — needs large move to profit.","status":"options_acct"},
    {"id":22,"tier":"elite","name":"Long Strangle","asset_class":"Options","category":"Volatility","description":"Buy OTM call + OTM put. Cheaper than straddle but needs even larger move. Same logic — profit from big directional move.","signals":["Very low IV","High-conviction catalyst"],"entry":"Buy OTM call and OTM put, same expiry.","exit":"Close profitable leg on move.","holding_period":"Days","risk":"Medium.","status":"options_acct"},
    {"id":23,"tier":"elite","name":"Long Guts","asset_class":"Options","category":"Volatility","description":"Buy ITM call + ITM put. More expensive than straddle but has higher intrinsic value floor. Profits from very large moves.","signals":["Extreme event expected"],"entry":"Buy ITM call and ITM put.","exit":"On large move, sell profitable leg.","holding_period":"Days","risk":"Medium-High — high cost.","status":"options_acct"},
    {"id":24,"tier":"elite","name":"Short Straddle","asset_class":"Options","category":"Income","description":"Sell ATM call + ATM put. Collect maximum premium; profit from no-move or small move. Max profit = total premium. Unlimited risk in both directions.","signals":["High IV rank (>70)","Stock expected to stay flat","No upcoming events"],"entry":"Sell ATM call and put, 30–45 DTE.","exit":"Close at 25–50% of max profit.","holding_period":"30 days","risk":"Very High — unlimited loss.","status":"options_acct"},
    {"id":25,"tier":"elite","name":"Short Strangle","asset_class":"Options","category":"Income","description":"Sell OTM call + OTM put. Wider strikes than short straddle — higher probability but less premium. The 'wheel strategy' starts here.","signals":["High IV rank","Stock between strikes expected"],"entry":"Sell OTM call and OTM put, 30–45 DTE.","exit":"Close at 50% of credit.","holding_period":"30 days","risk":"High.","status":"options_acct"},
    {"id":26,"tier":"elite","name":"Short Guts","asset_class":"Options","category":"Income","description":"Sell ITM call + ITM put. Generates large credit but immediate intrinsic risk. Profit only if stock lands between strikes at expiry.","signals":["Very high IV"],"entry":"Sell ITM call and put.","exit":"Close early if ITM risk materializes.","holding_period":"Days","risk":"Very High.","status":"options_acct"},
    {"id":27,"tier":"elite","name":"Strap","asset_class":"Options","category":"Volatility","description":"Buy 2 ATM calls + 1 ATM put. Bullish volatility play — profits more from upside than downside. Good before bullish catalyst.","signals":["Bullish bias but uncertain direction"],"entry":"Buy 2× ATM call and 1× ATM put.","exit":"Sell on big up move.","holding_period":"Days","risk":"Medium.","status":"options_acct"},
    {"id":28,"tier":"elite","name":"Strip","asset_class":"Options","category":"Volatility","description":"Buy 1 ATM call + 2 ATM puts. Bearish volatility play — profits more from downside. Good before bearish catalyst.","signals":["Bearish bias but uncertain direction"],"entry":"Buy 1× ATM call and 2× ATM put.","exit":"Sell on big down move.","holding_period":"Days","risk":"Medium.","status":"options_acct"},
    {"id":29,"tier":"elite","name":"Call Ratio Backspread","asset_class":"Options","category":"Directional","description":"Sell 1 near-ATM call, buy 2 OTM calls. Net credit. Strongly bullish — profits from large upward move; small move produces small loss.","signals":["Strongly bullish"],"entry":"Sell 1 ATM call, buy 2 OTM calls.","exit":"Close on large upward move.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":30,"tier":"elite","name":"Put Ratio Backspread","asset_class":"Options","category":"Directional","description":"Sell 1 near-ATM put, buy 2 OTM puts. Net credit. Strongly bearish — profits from large downward move.","signals":["Strongly bearish"],"entry":"Sell 1 ATM put, buy 2 OTM puts.","exit":"Close on large downward move.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":31,"tier":"elite","name":"Long Call Butterfly","asset_class":"Options","category":"Income","description":"Buy 1 low-strike call, sell 2 mid-strike calls, buy 1 high-strike call. Profits if stock near mid-strike at expiry. Low cost, defined risk.","signals":["Stock expected near target price"],"entry":"3-leg call spread centered on target.","exit":"Close at 50% of max profit.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":32,"tier":"elite","name":"Long Put Butterfly","asset_class":"Options","category":"Income","description":"Same as call butterfly but using puts. Profits if stock near mid-strike at expiry.","signals":["Stock near target"],"entry":"Buy low put, sell 2 mid puts, buy high put.","exit":"50% profit close.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":33,"tier":"elite","name":"Short Call Butterfly","asset_class":"Options","category":"Volatility","description":"Sell 1 low-strike call, buy 2 mid-strike calls, sell 1 high-strike call. Profits from large move away from center. Volatility bet.","signals":["Expecting large move"],"entry":"Opposite of long butterfly.","exit":"Close if large move occurs.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":34,"tier":"elite","name":"Short Put Butterfly","asset_class":"Options","category":"Volatility","description":"Sell 1 low put, buy 2 mid puts, sell 1 high put. Volatility play, profits from large move.","signals":["Large move expected"],"entry":"Opposite leg structure of long put butterfly.","exit":"Close on large move.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":35,"tier":"elite","name":"Long Iron Butterfly","asset_class":"Options","category":"Volatility","description":"Buy OTM put, sell ATM put, sell ATM call, buy OTM call. Net debit. Profits from large move beyond wings.","signals":["Very large move expected"],"entry":"4-leg position centered at current price.","exit":"On large breakout.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":36,"tier":"elite","name":"Short Iron Butterfly","asset_class":"Options","category":"Income","description":"Opposite of long iron butterfly. Net credit. Profits from stock staying near center strike. Very popular income strategy.","signals":["High IV","Flat market expected"],"entry":"Sell ATM straddle + buy OTM wings.","exit":"Close at 50% of credit.","holding_period":"30–45 days","risk":"Medium.","status":"options_acct"},
    {"id":37,"tier":"elite","name":"Long Call Condor","asset_class":"Options","category":"Income","description":"4-leg call spread between 4 strikes. Profits if stock stays between inner strikes. Wider profit zone than butterfly.","signals":["Stock expected in range"],"entry":"Buy low call, sell lower-mid, sell upper-mid, buy high call.","exit":"50% profit.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":38,"tier":"elite","name":"Long Put Condor","asset_class":"Options","category":"Income","description":"Same as call condor using puts. Range-bound profit.","signals":["Range-bound market"],"entry":"4-leg put condor.","exit":"50% profit.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":39,"tier":"elite","name":"Long Iron Condor","asset_class":"Options","category":"Income","description":"Sell OTM call spread + sell OTM put spread simultaneously. Most popular options income strategy. Net credit. Profits if stock stays between inner strikes.","signals":["High IV rank (>50)","Flat market expected","No upcoming binary events"],"entry":"Sell call 1σ OTM, buy call 2σ OTM; sell put 1σ OTM, buy put 2σ OTM.","exit":"Close at 50% of credit; adjust if tested.","holding_period":"30–45 days","risk":"Medium.","status":"options_acct"},
    {"id":40,"tier":"elite","name":"Short Iron Condor","asset_class":"Options","category":"Volatility","description":"Opposite of long iron condor. Net debit. Profits from large move beyond the wings.","signals":["Very large move expected","Low IV (cheap to enter)"],"entry":"Buy call and put spreads outside current range.","exit":"On breakout move.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":41,"tier":"elite","name":"Collar","asset_class":"Options","category":"Hedging","description":"Long stock + long OTM put + short OTM call. Zero-cost hedge (call premium funds put). Caps both upside and downside. Often used by executives with large stock positions.","signals":["Long-term stock holding","Hedging need"],"entry":"Buy OTM put, sell OTM call at approximately equal premiums.","exit":"Rolls each expiry.","holding_period":"3–12 months","risk":"Very Low.","status":"options_acct"},
    {"id":42,"tier":"elite","name":"Calendar Call Spread","asset_class":"Options","category":"Neutral","description":"Buy longer-dated call, sell shorter-dated call at same strike. Profits from time decay differential and/or rising IV.","signals":["Flat near-term, bullish longer-term","IV expected to rise"],"entry":"Sell front-month ATM call, buy back-month ATM call.","exit":"Close when short expires.","holding_period":"Days to weeks","risk":"Low.","status":"options_acct"},
    {"id":43,"tier":"elite","name":"Calendar Put Spread","asset_class":"Options","category":"Neutral","description":"Buy longer-dated put, sell shorter-dated put at same strike. Same mechanics as calendar call spread but using puts.","signals":["Flat near-term, bearish longer-term"],"entry":"Sell front-month ATM put, buy back-month ATM put.","exit":"Close when short expires.","holding_period":"Days to weeks","risk":"Low.","status":"options_acct"},
    {"id":44,"tier":"elite","name":"Diagonal Call Spread","asset_class":"Options","category":"Directional","description":"Buy deep ITM long-dated call, sell OTM short-dated call. LEAPS + covered call hybrid. Bullish with income generation.","signals":["Bullish long-term","High IV on short-dated options"],"entry":"Buy 6–12 month deep ITM call; sell monthly OTM call.","exit":"Roll short call monthly.","holding_period":"Months","risk":"Medium.","status":"options_acct"},
    {"id":45,"tier":"elite","name":"Diagonal Put Spread","asset_class":"Options","category":"Directional","description":"Buy deep ITM long-dated put, sell OTM short-dated put. Bearish LEAPS strategy.","signals":["Bearish long-term"],"entry":"Buy 6–12 month deep ITM put; sell monthly OTM put.","exit":"Roll short put monthly.","holding_period":"Months","risk":"Medium.","status":"options_acct"},
    {"id":46,"tier":"elite","name":"Covered Short Straddle","asset_class":"Options","category":"Income","description":"Hold stock + sell ATM call + sell ATM put. Higher income than covered call alone. Bullish with income focus.","signals":["Bullish stock outlook","High IV"],"entry":"Hold stock; sell ATM straddle.","exit":"Buy back at 25–50% profit.","holding_period":"30 days","risk":"High — put side has full downside.","status":"options_acct"},
    {"id":47,"tier":"elite","name":"Covered Short Strangle","asset_class":"Options","category":"Income","description":"Hold stock + sell OTM call + sell OTM put. Lower premium than covered straddle but higher probability.","signals":["Bullish stock","IV > 30%"],"entry":"Hold stock; sell OTM strangle.","exit":"50% profit or roll.","holding_period":"30 days","risk":"High.","status":"options_acct"},
    {"id":48,"tier":"elite","name":"Bull Call Ladder","asset_class":"Options","category":"Directional","description":"Buy ATM call, sell OTM call, sell further OTM call. Reduces cost of bull call spread. Conservatively bullish — can lose if stock rises too much above highest strike.","signals":["Moderately bullish with upside cap"],"entry":"3-leg spread, all same expiry.","exit":"Close at 50% profit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":49,"tier":"elite","name":"Bear Put Ladder","asset_class":"Options","category":"Directional","description":"Buy ATM put, sell OTM put, sell further OTM put. Bearish with capped downside profit. Used when bull spread turns bearish mid-trade.","signals":["Moderately bearish"],"entry":"3-leg put spread.","exit":"Close at 50% profit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":50,"tier":"elite","name":"Bullish Short Seagull Spread","asset_class":"Options","category":"Directional","description":"Bull call spread + sell OTM put to finance it. Zero or low net cost. Bullish with downside risk from the sold put.","signals":["Bullish","IV elevated (costly spreads)"],"entry":"Buy call spread, sell OTM put for credit.","exit":"At expiry or 50% profit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":51,"tier":"elite","name":"Bearish Long Seagull Spread","asset_class":"Options","category":"Directional","description":"Bear put spread + sell OTM call to finance it. Zero or low net cost. Bearish with upside risk from sold call.","signals":["Bearish","IV elevated"],"entry":"Buy put spread, sell OTM call for credit.","exit":"At expiry or 50% profit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":52,"tier":"elite","name":"Long Box","asset_class":"Options","category":"Arbitrage","description":"Bull call spread + bear put spread at same strikes. Riskless arbitrage if mispriced. Value = present value of strike difference. Rarely profitable after commissions in modern markets.","signals":["Mispricing between call and put spreads"],"entry":"4-leg position at same strikes/expiry.","exit":"At expiry.","holding_period":"Until expiry","risk":"Very Low — locked-in value.","status":"options_acct"},
    {"id":53,"tier":"elite","name":"Ratio Call Spread","asset_class":"Options","category":"Neutral","description":"Buy 1 lower call, sell 2 higher calls. Net credit. Profits from moderate upward move; loses on large rally above upper strike.","signals":["Neutral to moderately bullish"],"entry":"Buy ATM call, sell 2× OTM calls.","exit":"Close at 50% of credit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":54,"tier":"elite","name":"Ratio Put Spread","asset_class":"Options","category":"Neutral","description":"Buy 1 higher put, sell 2 lower puts. Net credit. Profits from moderate downward move; loses on large decline below lower strike.","signals":["Neutral to moderately bearish"],"entry":"Buy ATM put, sell 2× OTM puts.","exit":"Close at 50% credit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":55,"tier":"elite","name":"Long Call Synthetic Straddle","asset_class":"Options","category":"Volatility","description":"Short stock + buy 2× ATM calls. Equivalent to long straddle. Neutral directional bet on large move.","signals":["Large move expected"],"entry":"Short stock, buy 2× ATM calls.","exit":"On large price move.","holding_period":"Days","risk":"Medium.","status":"options_acct"},
    {"id":56,"tier":"elite","name":"Long Put Synthetic Straddle","asset_class":"Options","category":"Volatility","description":"Long stock + buy 2× ATM puts. Equivalent to long straddle. Protects long stock while capturing downside from large move.","signals":["Large move expected; currently long stock"],"entry":"Hold stock, buy 2× ATM puts.","exit":"On large price move.","holding_period":"Days","risk":"Medium.","status":"options_acct"},
    {"id":57,"tier":"elite","name":"Short Call Synthetic Straddle","asset_class":"Options","category":"Income","description":"Long stock + sell 2× ATM calls. Income-generating. Profits if stock stays near strike. Equivalent to short straddle.","signals":["High IV","Flat market expected"],"entry":"Hold stock, sell 2× ATM calls.","exit":"Close at 50% profit.","holding_period":"30 days","risk":"High.","status":"options_acct"},
    {"id":58,"tier":"elite","name":"Short Put Synthetic Straddle","asset_class":"Options","category":"Income","description":"Short stock + sell 2× ATM puts. Income-generating. Profits if stock stays near strike.","signals":["High IV","Stock expected flat near expiry"],"entry":"Short stock, sell 2× ATM puts.","exit":"Close at 50% profit.","holding_period":"30 days","risk":"High.","status":"options_acct"},

    # ─── ELITE: Equities Advanced (59–79) ────────────────────────────────────
    {"id":59,"tier":"elite","name":"Earnings Momentum (SUE)","asset_class":"Equities","category":"Directional","description":"Stocks with positive earnings surprises (high SUE = Standardized Unexpected Earnings) continue to outperform over the following 1–3 months. SUE = (actual EPS − expected EPS) / std deviation of surprises. Long top SUE decile, short bottom decile.","signals":["Earnings surprise vs. consensus","SUE z-score above +1.5","Post-earnings drift confirmation"],"entry":"Enter within 2 days of earnings announcement if SUE > +1.5.","exit":"Hold 3 months or until next earnings.","holding_period":"1–3 months","risk":"Medium.","status":"equity_acct"},
    {"id":60,"tier":"elite","name":"Value (Book-to-Price)","asset_class":"Equities","category":"Directional","description":"High book-to-market stocks (value stocks) systematically outperform growth stocks over long horizons. Fama-French HML factor. Monthly rebalance — long top B/P quintile, short bottom B/P quintile. Works best in small-cap universe.","signals":["Book-to-price ratio (top vs. bottom quintile)","Market cap filter","Sector neutralization"],"entry":"Long top quintile, short bottom quintile — monthly rebalance.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low-Medium — multi-year drawdowns possible.","status":"equity_acct"},
    {"id":61,"tier":"elite","name":"Low Volatility Factor","asset_class":"Equities","category":"Directional","description":"Counterintuitively, low-volatility stocks outperform high-volatility stocks on risk-adjusted basis (volatility anomaly). Long the bottom decile by 6-month realized volatility, short the top decile. Works via the low-beta anomaly.","signals":["6-month realized volatility rank","Beta vs. market","Sharpe ratio filter"],"entry":"Long 30% lowest vol stocks, short 30% highest vol stocks — monthly rebalance.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low.","status":"equity_acct"},
    {"id":62,"tier":"elite","name":"Implied Volatility Signal","asset_class":"Equities","category":"Directional","description":"Stocks with rising call implied volatility over the prior month have higher future returns; stocks with rising put implied volatility have lower returns. Long stocks with top decile call IV increase, short stocks with top decile put IV increase.","signals":["1-month call IV change","1-month put IV change","Options volume filter"],"entry":"Long top decile call IV increase; short top decile put IV increase.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"equity_acct"},
    {"id":63,"tier":"elite","name":"Residual Momentum (Fama-French Adjusted)","asset_class":"Equities","category":"Directional","description":"Purified momentum — regress stock returns on Fama-French 3 factors (MKT, SMB, HML) over 36 months, then use the residuals as momentum signal. Removes market/size/value exposure, isolating idiosyncratic momentum.","signals":["FF3 regression residuals (12-1M)","R-squared of regression","Factor loading stability"],"entry":"Long top residual momentum decile; short bottom decile.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low-Medium.","status":"equity_acct"},
    {"id":64,"tier":"elite","name":"Pairs Trading (Two-Stock Stat Arb)","asset_class":"Equities","category":"Neutral","description":"Identify two co-integrated stocks (e.g., Coke vs. Pepsi). When the spread (price ratio) deviates beyond 2σ from historical mean, long the cheap stock and short the rich stock. Kalman filter updates the hedge ratio dynamically.","signals":["Johansen cointegration test","Spread z-score > ±2","Half-life of reversion < 30 days","Kalman-filtered hedge ratio"],"entry":"Long cheap / short rich when spread z-score crosses ±2.","exit":"Close at ±0.5σ convergence or stop at ±3σ.","holding_period":"3–20 days","risk":"Medium.","status":"live"},
    {"id":65,"tier":"elite","name":"Mean Reversion – Cluster (Sector Portfolio)","asset_class":"Equities","category":"Neutral","description":"Generalization of pairs trading to N-stock clusters (full sector). Demean returns within each sector cluster — long stocks below sector mean, short stocks above sector mean. Dollar-neutral within each cluster.","signals":["Sector cluster assignment","Within-cluster return z-score","Dollar-neutrality constraint"],"entry":"Long stocks > 1σ below cluster mean; short stocks > 1σ above.","exit":"Rebalance weekly.","holding_period":"1 week","risk":"Low-Medium.","status":"equity_acct"},
    {"id":66,"tier":"elite","name":"Single Moving Average","asset_class":"Equities","category":"Directional","description":"Buy when price crosses above the N-day moving average (SMA or EMA); sell when it crosses below. Trend-following on individual stocks. Common parameters: 50-day SMA or 200-day SMA (golden cross/death cross).","signals":["Price vs. N-day MA","Volume confirmation"],"entry":"Price closes above MA — go long; below — go short.","exit":"Opposite crossover.","holding_period":"Days to months","risk":"Medium — whipsaws in sideways markets.","status":"equity_acct"},
    {"id":67,"tier":"elite","name":"Dual Moving Average Crossover","asset_class":"Equities","category":"Directional","description":"Two MAs of different lengths (e.g., 10-day and 50-day). Buy when short MA crosses above long MA (golden cross); sell when short crosses below long (death cross). Includes a trailing stop (e.g., 2% below recent high).","signals":["Short MA vs. Long MA crossover","Trailing stop trigger"],"entry":"Short MA crosses above long MA — long; below — short.","exit":"Opposite crossover or trailing stop.","holding_period":"Weeks to months","risk":"Medium.","status":"equity_acct"},
    {"id":68,"tier":"elite","name":"Triple Moving Average","asset_class":"Equities","category":"Directional","description":"Three MAs (e.g., 3, 10, 21 days). Long when all three are in ascending order (short > medium > long); short when descending. Additional filter reduces whipsaws vs. dual MA.","signals":["3 MA alignment filter","All three in ascending/descending order"],"entry":"All three MAs aligned — take directional position.","exit":"Any two MAs cross against each other.","holding_period":"Weeks","risk":"Medium.","status":"equity_acct"},
    {"id":69,"tier":"elite","name":"Support & Resistance","asset_class":"Equities","category":"Directional","description":"Trade bounces off and breakouts through key support/resistance levels. Support = prior swing low; resistance = prior swing high. Long on bounce from support; short on rejection from resistance; momentum trade on breakout through either.","signals":["Historical swing highs/lows","Volume at key levels","Price action confirmation (e.g., hammer candle at support)"],"entry":"Bounce: enter at support with stop below; Breakout: enter on close above resistance.","exit":"Next resistance level (long) or support level (short).","holding_period":"Days to weeks","risk":"Medium.","status":"equity_acct"},
    {"id":70,"tier":"elite","name":"Donchian Channel","asset_class":"Equities","category":"Directional","description":"The Donchian Channel is bounded by the N-day high (ceiling) and N-day low (floor). Buy at the floor (expecting bounce); sell at the ceiling (expecting reversal). Or trade the breakout: long when price breaks ceiling, short when it breaks floor. Common parameter: 20-day channel.","signals":["20-day high/low channel","Volume on breakout","ATR for position sizing"],"entry":"Breakout: buy above ceiling or short below floor; Mean-reversion: fade at extremes.","exit":"Opposite channel boundary or stop.","holding_period":"Days to weeks","risk":"Medium.","status":"equity_acct"},
    {"id":71,"tier":"elite","name":"Event-Driven M&A","asset_class":"Equities","category":"Arbitrage","description":"When a merger/acquisition is announced, target stock trades below deal price (deal risk discount). Buy the target, short the acquirer (optional). Profit = convergence of target price to deal price at closing. Risk = deal failure.","signals":["Merger announcement","Deal spread (target price vs. offer)","Deal completion probability","Regulatory risk assessment"],"entry":"Long target at discount; optionally short acquirer if all-stock deal.","exit":"Deal close (capture full spread) or deal break (stop-loss).","holding_period":"Weeks to months","risk":"High on deal break.","status":"equity_acct"},
    {"id":72,"tier":"elite","name":"KNN Machine Learning","asset_class":"Equities","category":"Directional","description":"K-nearest-neighbors algorithm predicts next-T-day return based on historical 'similar' market states. Feature vector = normalized price/volume moving averages of varying lengths. Euclidean distance finds k most similar historical periods. Trade direction based on average realized return of those k neighbors.","signals":["KNN predicted return vs. threshold","k-neighbors similarity score","Feature vector: price/volume MAs"],"entry":"Long if KNN prediction > z1 threshold; short if < -z1.","exit":"Predicted return crosses opposite threshold.","holding_period":"T trading days","risk":"Medium — overfitting risk.","status":"equity_acct"},
    {"id":73,"tier":"elite","name":"Statistical Arbitrage – Optimization","asset_class":"Equities","category":"Neutral","description":"Mean-variance optimization of N-stock portfolio. Expected returns from signals (momentum, mean-reversion); covariance matrix from historical data. Sharpe-optimal weights via inverse-covariance formula. Dollar-neutral via Lagrange multiplier constraint.","signals":["Signal-based expected returns","Sample covariance matrix","Sharpe ratio maximization","Dollar-neutrality constraint"],"entry":"Rebalance to optimal weights weekly/monthly.","exit":"Rebalance.","holding_period":"1 week–1 month","risk":"Medium.","status":"live"},
    {"id":74,"tier":"elite","name":"Alpha Combos (Multi-Alpha)","asset_class":"Equities","category":"Neutral","description":"Combine hundreds of individual alpha signals (each weak individually) into a single diversified portfolio. Neutralize each alpha against sector/style factors; weight alphas by inverse variance. Inspired by WorldQuant and Two Sigma approaches.","signals":["Individual alpha signal returns","Cross-alpha covariance","Factor exposure (sector, size, value, momentum)"],"entry":"Systematic rebalance to alpha-weighted portfolio.","exit":"Rebalance.","holding_period":"Daily to weekly","risk":"Low (diversified).","status":"backtest"},

    # ─── ELITE: ETFs (75–80) ──────────────────────────────────────────────────
    {"id":75,"tier":"elite","name":"Sector Momentum Rotation","asset_class":"ETFs","category":"Directional","description":"Rank S&P 500 sector ETFs (XLK, XLF, XLV, etc.) by trailing 12-month return. Invest in top 3–5 sectors. Rebalance monthly. Exploits the momentum effect across sectors rather than individual stocks.","signals":["12M sector ETF return ranking","Relative strength vs. SPY","Volume trend"],"entry":"Long top 3 sectors by 12M return each month.","exit":"Monthly rebalance — replace sectors falling from top 3.","holding_period":"1 month","risk":"Medium.","status":"equity_acct"},
    {"id":76,"tier":"elite","name":"Dual Momentum Sector Rotation","asset_class":"ETFs","category":"Directional","description":"Extension of sector rotation: absolute momentum filter first (if sector return < T-bill rate, hold cash for that allocation), then relative momentum to rank remaining sectors. Gary Antonacci's Dual Momentum applied to sectors.","signals":["12M sector return vs. T-bill rate","Relative sector ranking","Cash allocation trigger"],"entry":"Long top sectors passing absolute momentum filter.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low-Medium.","status":"equity_acct"},
    {"id":77,"tier":"elite","name":"ETF Alpha Rotation","asset_class":"ETFs","category":"Directional","description":"Use factor-based signals (momentum, value, quality) to rotate among smart-beta ETFs (MTUM, VLUE, QUAL, USMV, etc.). Rank by factor composite score — overweight highest-scoring ETFs.","signals":["Factor ETF composite score","Momentum (3M, 6M, 12M)","Relative value vs. benchmark"],"entry":"Overweight top 2–3 factor ETFs; underweight bottom.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low.","status":"equity_acct"},
    {"id":78,"tier":"elite","name":"R-Squared Trend Filter","asset_class":"ETFs","category":"Directional","description":"R² of a linear regression of price vs. time measures how 'trendy' an ETF is. High R² (>0.75) = strong trend, use trend-following; low R² = range-bound, use mean-reversion. Dynamic strategy selection based on market regime.","signals":["R² of price-time regression (20-day window)","ADX indicator","Regime classification"],"entry":"High R²: follow trend; Low R²: fade extremes.","exit":"Regime change triggers strategy switch.","holding_period":"Days to weeks","risk":"Medium.","status":"equity_acct"},
    {"id":79,"tier":"elite","name":"ETF Mean Reversion","asset_class":"ETFs","category":"Neutral","description":"Liquid ETFs (SPY, QQQ, IWM) exhibit short-term mean reversion at the daily/weekly level. When ETF deviates >1.5σ from its 10-day mean, fade the move with a 3–5 day holding period.","signals":["Z-score vs. 10-day MA","RSI(2) extreme (<10 or >90)","VIX level filter"],"entry":"Long when z-score < −1.5 and RSI(2) < 10; short when z-score > +1.5.","exit":"Return to 10-day MA or after 5 days.","holding_period":"3–5 days","risk":"Medium.","status":"equity_acct"},
    {"id":80,"tier":"elite","name":"Leveraged ETF Decay Exploitation","asset_class":"ETFs","category":"Directional","description":"3× leveraged ETFs (TQQQ, SOXL, LABU) suffer from volatility decay in choppy markets. Short both bull and bear 3× ETF pairs (e.g., short TQQQ + short SQQQ) to harvest the decay. Requires precise delta hedging.","signals":["Implied volatility of underlying","Beta-slippage calculation","Net delta of paired position"],"entry":"Short equal dollar amounts of 3× bull and 3× bear ETFs on same underlying.","exit":"Rebalance to delta-neutral weekly; close if vol spikes dramatically.","holding_period":"1–4 weeks","risk":"High — vol spikes can overwhelm decay profit.","status":"equity_acct"},
    {"id":81,"tier":"elite","name":"Multi-Asset Trend Following","asset_class":"ETFs","category":"Directional","description":"Apply trend-following signals (MA crossover, breakout) across diversified ETF portfolio: stocks (SPY), bonds (TLT), gold (GLD), commodities (DJP), real estate (VNQ). Position based on trailing 10-month return. A Managed Futures-style portfolio in ETF form.","signals":["10-month trailing return sign","200-day MA position","Cross-asset correlation"],"entry":"Long ETF if trailing return positive; move to cash/bonds if negative.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Low (diversified).","status":"equity_acct"},

    # ─── ELITE: Fixed Income (82–90) ─────────────────────────────────────────
    {"id":82,"tier":"elite","name":"Bullet Strategy (Bond Maturity Concentration)","asset_class":"Fixed Income","category":"Directional","description":"Concentrate bond portfolio at a single target maturity point on the yield curve. Simple and transparent duration exposure. Outperforms when yield curve shifts in a parallel manner and the targeted maturity is well-chosen.","signals":["Yield curve level","Target maturity point","Duration target"],"entry":"Buy bonds concentrated at single maturity.","exit":"Hold to maturity or rebalance target maturity.","holding_period":"Months to years","risk":"Low-Medium.","status":"informational"},
    {"id":83,"tier":"elite","name":"Barbell Strategy","asset_class":"Fixed Income","category":"Neutral","description":"Split portfolio between very short-term and very long-term bonds. Avoids the middle of the curve. Duration-matched to benchmark but outperforms during yield curve flattening. Combines liquidity (short end) with yield (long end).","signals":["Yield curve shape (flat/steep)","Duration matching","Short-end vs. long-end yield spread"],"entry":"50% in 3-month T-bills, 50% in 30-year bonds.","exit":"Rebalance when curve shape changes.","holding_period":"Months","risk":"Medium.","status":"informational"},
    {"id":84,"tier":"elite","name":"Ladder Strategy","asset_class":"Fixed Income","category":"Neutral","description":"Distribute bonds evenly across maturities (1, 2, 3...10 years). As each bond matures, reinvest at the longest rung. Reduces reinvestment risk and interest rate risk. Popular retail strategy.","signals":["Even maturity distribution","Reinvestment yield","Average portfolio yield"],"entry":"Equal allocation across 10 annual maturity rungs.","exit":"Reinvest maturing proceeds at longest rung.","holding_period":"Perpetual","risk":"Low.","status":"informational"},
    {"id":85,"tier":"elite","name":"Yield Curve Steepener","asset_class":"Fixed Income","category":"Directional","description":"Long long-duration bonds (or bond ETFs like TLT), short short-duration bonds (SHY). Profits when yield curve steepens (long rates rise faster than short rates, or short rates fall faster). Common during early economic expansion.","signals":["2s10s yield spread","Fed funds rate trajectory","Economic cycle phase"],"entry":"Long TLT, short SHY (or equivalent).","exit":"Flatten when curve steepens to target or macro view changes.","holding_period":"Weeks to months","risk":"Medium.","status":"equity_acct"},
    {"id":86,"tier":"elite","name":"Yield Curve Flattener","asset_class":"Fixed Income","category":"Directional","description":"Opposite of steepener. Short long-duration, long short-duration. Profits when yield curve flattens. Common as Fed hikes rates in late expansion cycle.","signals":["Fed rate hike trajectory","2s10s spread compression","Inflation expectations"],"entry":"Short TLT, long SHY (or equivalent).","exit":"Flatten when curve flattens to target.","holding_period":"Weeks to months","risk":"Medium.","status":"equity_acct"},
    {"id":87,"tier":"elite","name":"Carry Trade (Bonds)","asset_class":"Fixed Income","category":"Income","description":"Borrow at the short end of the curve, lend at the long end. The carry is the positive yield spread. Works until the curve inverts. Often levered 3–10×. Classic bank and hedge fund strategy.","signals":["Yield curve slope (must be positive)","Rolldown return","Repo rate for financing"],"entry":"Long 10-year bond, funded via repo at overnight rate.","exit":"Unwind when curve inverts or financing cost rises.","holding_period":"Weeks to months","risk":"High — duration + leverage risk.","status":"informational"},
    {"id":88,"tier":"elite","name":"Credit Spread Trading","asset_class":"Fixed Income","category":"Directional","description":"Long investment-grade or high-yield corporate bonds, short equivalent Treasuries. Captures the credit spread (compensation for default risk). Spread widens in recessions, compresses in expansions.","signals":["Investment-grade credit spread vs. historical mean","High-yield OAS (option-adjusted spread)","Default rate trends","Economic cycle"],"entry":"Long IG bonds (LQD) or HY bonds (HYG) when spread > historical mean.","exit":"Spread compresses to fair value.","holding_period":"Months","risk":"Medium-High.","status":"equity_acct"},
    {"id":89,"tier":"elite","name":"Duration Mismatch (Duration Arbitrage)","asset_class":"Fixed Income","category":"Arbitrage","description":"Identify bonds with similar credit quality and coupon but different durations trading at incorrect relative yields. Long the cheap bond, short the rich bond. Pure rates play with credit exposure hedged.","signals":["Duration-adjusted yield comparison","Fitted yield curve","Z-spread vs. fair value"],"entry":"Long bond below fitted curve, short bond above fitted curve.","exit":"Convergence to fitted curve.","holding_period":"Weeks","risk":"Low.","status":"informational"},
    {"id":90,"tier":"elite","name":"Inflation-Linked Bond Arbitrage (TIPS)","asset_class":"Fixed Income","category":"Arbitrage","description":"Trade the spread between TIPS (inflation-linked) and nominal Treasuries. The break-even inflation rate = nominal yield − TIPS yield. Long TIPS when realized inflation likely exceeds break-even; short when inflation likely disappoints.","signals":["CPI trajectory vs. break-even inflation","Fed inflation targets","Commodity prices as leading indicator"],"entry":"Long TIPS (TIP ETF) when CPI > break-even; long nominal (IEF) when CPI < break-even.","exit":"Rebalance on macro shift.","holding_period":"Months","risk":"Low-Medium.","status":"equity_acct"},

    # ─── ELITE: Indexes (91–95) ───────────────────────────────────────────────
    {"id":91,"tier":"elite","name":"Cash-and-Carry Arbitrage (Index Futures)","asset_class":"Indexes","category":"Arbitrage","description":"Buy spot index (via ETF), sell equivalent index futures. Locks in the 'basis' (futures premium over fair value). Riskless if basis > financing cost. Requires precise execution and low transaction costs.","signals":["Basis (futures price − fair value)","Financing rate (repo/LIBOR)","Dividend yield of index"],"entry":"Long ETF + short futures when basis > financing cost.","exit":"At futures expiration (convergence).","holding_period":"To futures expiry","risk":"Very Low.","status":"futures_acct"},
    {"id":92,"tier":"elite","name":"Dispersion Trading (Index vs. Constituents)","asset_class":"Indexes","category":"Volatility","description":"Short index volatility (sell index straddle), long single-stock volatility (buy stock straddles). Exploits the tendency for correlation to be lower than implied by index options pricing. Profits when stocks move independently.","signals":["Implied correlation (index IV vs. constituent IV weighted average)","IV skew","Realized vs. implied correlation spread"],"entry":"Short index options, long weighted basket of single-stock options.","exit":"Close when implied correlation normalizes.","holding_period":"Days to weeks","risk":"High — correlation spikes kill this trade.","status":"options_acct"},
    {"id":93,"tier":"elite","name":"Intraday ETF-Index Arbitrage","asset_class":"Indexes","category":"Arbitrage","description":"Trade the intraday price discrepancy between an ETF (e.g., SPY) and its underlying index futures (ES). If SPY trades above its fair value vs. ES, short SPY and long ES. High-frequency execution required.","signals":["ETF NAV vs. market price","Futures fair value premium","Intraday volume profile"],"entry":"Deviation > transaction cost threshold.","exit":"Convergence to fair value (seconds to minutes).","holding_period":"Seconds to minutes","risk":"Low with proper execution.","status":"equity_acct"},
    {"id":94,"tier":"elite","name":"Index Inclusion Effect","asset_class":"Indexes","category":"Event-Driven","description":"When a stock is announced for addition to a major index (S&P 500, Russell 2000), passive funds must buy it. Price typically rises between announcement and addition date. Buy on announcement, sell on addition.","signals":["Index rebalance announcement","Estimated passive demand vs. float","Historical inclusion premium"],"entry":"Buy on rebalancing announcement date.","exit":"Sell on actual index addition date (or 1 day after).","holding_period":"Days to weeks","risk":"Medium — premium often fully priced by addition.","status":"equity_acct"},
    {"id":95,"tier":"elite","name":"Index Volatility Targeting","asset_class":"Indexes","category":"Neutral","description":"Scale index exposure based on realized volatility — reduce exposure when vol is high, increase when vol is low. Target a constant portfolio volatility (e.g., 10% annualized). The risk-parity approach applied to a single index.","signals":["20-day realized volatility","VIX level","Target volatility (10% annualized)"],"entry":"Adjust leverage to keep portfolio vol at target.","exit":"Daily rebalance.","holding_period":"Daily","risk":"Low.","status":"equity_acct"},

    # ─── ELITE: Volatility (96–101) ───────────────────────────────────────────
    {"id":96,"tier":"elite","name":"VIX Futures Basis Trading","asset_class":"Volatility","category":"Income","description":"VIX futures typically trade in contango (futures > spot VIX). Short the front-month VIX futures (or XIV/SVXY ETN) to capture the roll-down as futures converge toward lower spot VIX. Catastrophic risk: VIX spikes (Feb 2018 style).","signals":["VIX futures term structure slope","VIX contango (front vs. back month)","Contango threshold (typically >5%)"],"entry":"Short front-month VIX futures or long SVXY when contango > 5%.","exit":"Cover when contango flattens or spot VIX spikes > 30.","holding_period":"Days to weeks","risk":"Very High — tail risk.","status":"futures_acct"},
    {"id":97,"tier":"elite","name":"Volatility Carry (VXX vs. VXZ)","asset_class":"Volatility","category":"Income","description":"Short VXX (1M VIX futures) and long VXZ (5M VIX futures). The short end decays faster due to contango. Hedges some of the tail risk of a pure short-VIX position.","signals":["VXX vs. VXZ price ratio","Term structure slope","Realized vs. implied volatility"],"entry":"Short VXX, long VXZ in dollar-neutral ratio.","exit":"Close if VIX > 35 or ratio normalizes.","holding_period":"Weeks","risk":"High.","status":"equity_acct"},
    {"id":98,"tier":"elite","name":"Volatility Risk Premium","asset_class":"Volatility","category":"Income","description":"Implied volatility (VIX) consistently overestimates realized volatility. Sell options (straddles on SPX) to capture this premium. Monthly delta-hedged short straddle on SPY/SPX is the institutional standard.","signals":["VIX vs. 30-day realized vol spread","IV rank (entry when IV > realized by >5 vol points)","VIX term structure"],"entry":"Sell SPX/SPY ATM straddle when IV >> realized vol.","exit":"At 50% profit or delta-hedge throughout.","holding_period":"30 days","risk":"High — tail risk from market crashes.","status":"options_acct"},
    {"id":99,"tier":"elite","name":"Volatility Skew – Long Risk Reversal","asset_class":"Volatility","category":"Directional","description":"Buy OTM call, sell OTM put (same delta). Exploits the IV skew where puts are richly priced vs. calls. Net credit typically. Bullish directional bet with positive carry if put IV > call IV.","signals":["Put-call IV skew (25-delta)","Skew percentile rank","Directional bias"],"entry":"Buy 25-delta call, sell 25-delta put, 1 month out.","exit":"At expiry or on directional move.","holding_period":"30 days","risk":"High — short put has unlimited downside.","status":"options_acct"},
    {"id":100,"tier":"elite","name":"Variance Swap Trading","asset_class":"Volatility","category":"Neutral","description":"Enter a variance swap: receive realized variance, pay implied variance (or vice versa). Long variance swap profits from realized vol > strike (implied vol). Institutional-grade volatility trading without the path dependency of options.","signals":["Implied variance vs. fair strike","Term structure of variance","Historical realized variance distribution"],"entry":"Long variance swap when implied variance < expected realized.","exit":"At maturity.","holding_period":"1 month","risk":"High.","status":"informational"},
    {"id":101,"tier":"elite","name":"Gamma Scalping","asset_class":"Volatility","category":"Neutral","description":"Buy ATM straddle and delta-hedge continuously. Profit = actual stock movement (gamma profits) minus theta decay. Profitable when realized volatility > implied volatility. A pure long-volatility strategy.","signals":["Implied vol vs. realized vol forecast","Gamma/theta ratio","Intraday price movement"],"entry":"Buy ATM straddle; set up delta-neutral hedge.","exit":"Delta-hedge throughout; close at expiry.","holding_period":"Days to weeks","risk":"Medium — theta decay kills profit if vol disappoints.","status":"options_acct"},

    # ─── ELITE: FX (102–106) ──────────────────────────────────────────────────
    {"id":102,"tier":"elite","name":"FX Carry Trade","asset_class":"Foreign Exchange","category":"Income","description":"Borrow in low-interest-rate currency (e.g., JPY), invest in high-interest-rate currency (e.g., AUD). Profit = interest rate differential. Risk: sharp currency reversals ('carry unwinds') during risk-off periods.","signals":["Interest rate differential","Risk sentiment (VIX)","Current account balance","Historical carry return"],"entry":"Long high-yield currency, short low-yield currency.","exit":"Risk-off trigger (VIX > 25) or carry differential narrows.","holding_period":"Weeks to months","risk":"High — carry unwinds are violent.","status":"informational"},
    {"id":103,"tier":"elite","name":"FX Momentum","asset_class":"Foreign Exchange","category":"Directional","description":"Apply trend-following to FX pairs using moving average crossovers filtered by HP (Hodrick-Prescott) filter to remove noise. Buy currency pairs with positive 3-month momentum; sell negative momentum pairs.","signals":["3M FX return ranking","HP-filtered trend","Volatility-adjusted position sizing"],"entry":"Long top 3 FX pairs by 3M return; short bottom 3.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"informational"},
    {"id":104,"tier":"elite","name":"Dollar Carry Trade","asset_class":"Foreign Exchange","category":"Income","description":"Build a diversified carry portfolio: long high-yield EM currencies vs. short USD, balanced across many currency pairs. Diversification reduces single-currency crash risk while maintaining aggregate carry.","signals":["EM vs. USD interest rate differentials","EM political/economic risk","Commodity price trends"],"entry":"Diversified basket of long EM / short USD carry.","exit":"Risk metric trigger or quarterly rebalance.","holding_period":"Quarterly","risk":"High.","status":"informational"},
    {"id":105,"tier":"elite","name":"FX Momentum + Carry Combo","asset_class":"Foreign Exchange","category":"Directional","description":"Combine FX momentum and carry signals into a composite ranking. Allocate to currency pairs with both positive momentum AND positive carry. Reduces whipsaw trades from momentum alone.","signals":["Momentum z-score + carry z-score composite","Combined ranking"],"entry":"Long currencies with top composite scores; short bottom.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"informational"},
    {"id":106,"tier":"elite","name":"FX Triangular Arbitrage","asset_class":"Foreign Exchange","category":"Arbitrage","description":"Exploit mis-pricing among three currency pairs (e.g., USD/EUR, EUR/GBP, USD/GBP). If the implied cross rate differs from the actual rate by more than transaction costs, buy the cheap route and sell the expensive route. Requires millisecond execution.","signals":["Triangular arbitrage opportunity (EUR×GBP−USD/GBP vs. USD/EUR)","Bid-ask spread analysis","Execution speed"],"entry":"Automated: simultaneously execute all three legs when opportunity detected.","exit":"Immediate — arbitrage closes instantly.","holding_period":"Milliseconds","risk":"Very Low with proper execution.","status":"informational"},

    # ─── ELITE: Commodities (107–110) ─────────────────────────────────────────
    {"id":107,"tier":"elite","name":"Commodity Roll Yield","asset_class":"Commodities","category":"Income","description":"Long backwardated commodity futures (spot > futures = positive roll yield); short contango commodities (futures > spot = negative roll yield). Roll yield is a systematic, economically justified premium.","signals":["Futures term structure shape","Roll yield calculation","Basis (spot - futures)"],"entry":"Long backwardated commodities; short contango commodities.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"futures_acct"},
    {"id":108,"tier":"elite","name":"Commodity Momentum","asset_class":"Commodities","category":"Directional","description":"Apply 12-month momentum ranking to commodity futures (crude oil, gold, silver, copper, natural gas, grains). Long top quartile, short bottom quartile. Similar to equity momentum.","signals":["12M futures return ranking","Backwardation filter (long only if backwardated)"],"entry":"Long top momentum commodities.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"High — commodities volatile.","status":"futures_acct"},
    {"id":109,"tier":"elite","name":"Commodity Value (Basis)","asset_class":"Commodities","category":"Directional","description":"Low-basis (cheap) commodities tend to outperform high-basis commodities. The basis reflects supply/demand imbalances. Long cheap commodities (low price vs. moving average), short expensive ones.","signals":["5-year price average (value anchor)","Current price vs. historical average","Inventory levels"],"entry":"Long commodities trading below 5Y mean; short above.","exit":"Revert to mean.","holding_period":"Months","risk":"High.","status":"futures_acct"},
    {"id":110,"tier":"elite","name":"Energy Spark Spread","asset_class":"Commodities","category":"Arbitrage","description":"Trade the 'spark spread' = electricity price − (natural gas price × heat rate). Long electricity futures, short equivalent natural gas. Represents the gross profit margin of a gas-fired power plant.","signals":["Spark spread vs. historical mean","Natural gas inventory levels","Electricity demand forecasts"],"entry":"Long spark spread when below historical mean.","exit":"Spread mean-reverts.","holding_period":"Days to weeks","risk":"High.","status":"futures_acct"},

    # ─── ELITE: Futures (111–114) ─────────────────────────────────────────────
    {"id":111,"tier":"elite","name":"Managed Futures (CTA Trend)","asset_class":"Futures","category":"Directional","description":"Systematic trend-following across equity index futures, bond futures, FX futures, and commodity futures. Classic CTA strategy. Signal: price vs. trailing moving average (200-day). Provides crisis alpha — negatively correlated with equities during crashes.","signals":["200-day MA position","Breakout signals","Volatility-adjusted position sizing (ATR)"],"entry":"Long futures above 200-day MA; short below.","exit":"Opposite MA crossover.","holding_period":"Weeks to months","risk":"Medium — whipsaw in sideways markets.","status":"futures_acct"},
    {"id":112,"tier":"elite","name":"Inter-Market Spread (Calendar Spread)","asset_class":"Futures","category":"Arbitrage","description":"Long near-month futures, short deferred-month futures (or vice versa). Trade changes in the futures term structure shape. Calendar spreads have lower margin requirements and smoother P&L than outright futures.","signals":["Near vs. deferred contract spread","Seasonal patterns","Cost-of-carry relationship"],"entry":"Long near-month, short deferred when spread below fair value.","exit":"Spread convergence.","holding_period":"Days to weeks","risk":"Low-Medium.","status":"futures_acct"},
    {"id":113,"tier":"elite","name":"Inter-Commodity Spread (Crack Spread)","asset_class":"Futures","category":"Arbitrage","description":"Crude oil vs. refined products (gasoline, heating oil). The '3-2-1 crack spread' = 2× gasoline + 1× heating oil − 3× crude oil. Represents refining margin. Long crack spread when refining margins compressed; short when elevated.","signals":["3-2-1 crack spread vs. historical mean","Refinery utilization rates","Seasonal demand"],"entry":"Long when crack spread < historical mean.","exit":"Mean reversion.","holding_period":"Weeks","risk":"High.","status":"futures_acct"},
    {"id":114,"tier":"elite","name":"VIX-Equity Relationship","asset_class":"Futures","category":"Neutral","description":"Exploit the tight inverse relationship between VIX and SPX. When VIX mean-reverts to fair value, take the opposite position in SPX futures. High VIX → long equities (expect recovery); low VIX → hedge equities (expect vol expansion).","signals":["VIX vs. historical mean","VIX futures term structure","SPX technical level"],"entry":"Long SPX futures when VIX > 30 and mean-reverting.","exit":"VIX returns to 15–20 range.","holding_period":"Days to weeks","risk":"Medium.","status":"futures_acct"},

    # ─── ELITE: Structured/Convertibles/Tax/Misc (115–126) ───────────────────
    {"id":115,"tier":"elite","name":"CDO Tranche – Carry & Hedge","asset_class":"Structured Assets","category":"Income","description":"Hold a CDO equity tranche (high yield, first-loss) and hedge with a short position in the corresponding CDO index (CDX). Earns carry from the high tranche yield while limiting correlation risk via the index hedge.","signals":["CDO tranche yield vs. hedge cost","Correlation pricing","Default rate outlook"],"entry":"Long equity tranche, short CDX index in notional-equivalent amount.","exit":"Unwind when carry no longer exceeds hedge cost.","holding_period":"Months","risk":"Very High — correlation and default risk.","status":"informational"},
    {"id":116,"tier":"elite","name":"MBS Trading (Prepayment Model)","asset_class":"Structured Assets","category":"Income","description":"Mortgage-backed securities have prepayment risk — homeowners refinance when rates fall, creating negative convexity. Strategy: buy discount MBS (below par) where prepayment actually benefits the holder; hedge interest rate risk with Treasuries.","signals":["Prepayment speed (CPR)","OAS vs. benchmark","Refinancing rate threshold","Interest rate level"],"entry":"Buy discount MBS when OAS > Treasury spread + prepayment model fair value.","exit":"OAS compression or duration hedge adjustment.","holding_period":"Months to years","risk":"Medium.","status":"informational"},
    {"id":117,"tier":"elite","name":"Convertible Arbitrage","asset_class":"Convertibles","category":"Arbitrage","description":"Long convertible bond, short the underlying stock (delta-hedged). Profits from: (1) bond yield/coupon, (2) embedded optionality mispricing (cheap gamma), (3) credit spread carry. Classic hedge fund strategy.","signals":["Convertible bond theoretical value vs. market price","Delta of conversion option","Credit spread"],"entry":"Long convert, short stock in delta-equivalent shares.","exit":"Bond conversion, maturity, or fair-value convergence.","holding_period":"Months","risk":"Medium — credit event + short squeeze risk.","status":"informational"},
    {"id":118,"tier":"elite","name":"Convertible OAS Trading","asset_class":"Convertibles","category":"Directional","description":"Trade the option-adjusted spread of convertibles. When OAS is wide vs. comparable straight bonds, the market is underpricing the convert — buy it. When OAS tight, the convert is rich — sell or short.","signals":["OAS vs. straight bond spread","Equity vol of underlying","Credit rating trajectory"],"entry":"Long when OAS > straight bond spread by >100bps.","exit":"OAS normalizes.","holding_period":"Weeks to months","risk":"Medium.","status":"informational"},
    {"id":119,"tier":"elite","name":"Municipal Bond Tax Arbitrage","asset_class":"Tax Arbitrage","category":"Arbitrage","description":"Tax-exempt municipal bonds yield less than Treasuries nominally but may yield more on an after-tax basis for high-bracket investors. Strategy: compare muni yield / (1 − tax rate) vs. Treasury yield. Buy munis if after-tax yield > Treasury.","signals":["Muni yield vs. Treasury yield","Marginal tax rate","State tax exemption","Credit quality (use AAA munis only)"],"entry":"Long muni ETF (MUB) when after-tax yield spread positive.","exit":"Spread narrows to fair value.","holding_period":"Months","risk":"Low.","status":"equity_acct"},
    {"id":120,"tier":"elite","name":"Inflation Hedging – Swaps","asset_class":"Miscellaneous","category":"Hedging","description":"Enter inflation swap: pay fixed rate, receive realized CPI. Portfolio hedge against unexpected inflation. Useful for institutional investors with real liabilities. Breakeven analysis similar to TIPS.","signals":["Break-even inflation rate","CPI trajectory","5Y5Y forward inflation expectations"],"entry":"Long inflation when realized CPI expected to exceed swap breakeven.","exit":"Maturity or macro regime change.","holding_period":"Months to years","risk":"Low-Medium.","status":"informational"},
    {"id":121,"tier":"elite","name":"TIPS-Treasury Arbitrage","asset_class":"Miscellaneous","category":"Arbitrage","description":"Trade the relative value between TIPS and nominal Treasuries. Break-even inflation = nominal yield − TIPS yield. When realized inflation consistently exceeds break-even, long TIPS vs. short Treasuries.","signals":["Break-even inflation vs. Fed/market forecast","CPI reports","Commodity prices"],"entry":"Long TIP (TIPS ETF), short IEF (nominal Treasury ETF) when inflation expected to exceed break-even.","exit":"Break-even normalizes.","holding_period":"Months","risk":"Low.","status":"equity_acct"},
    {"id":122,"tier":"elite","name":"Distressed Debt – Buy and Hold","asset_class":"Distressed Assets","category":"Directional","description":"Buy bonds of financially distressed companies at deep discounts (30–60 cents on the dollar). Hold through restructuring. Recovery in bankruptcy often 40–80 cents. High information advantage required.","signals":["Trading at < 60 cents on dollar","Coverage ratio","Liquidation value analysis","Reorganization probability"],"entry":"Buy at steep discount when restructuring likely to yield > purchase price.","exit":"Post-restructuring at recovery value.","holding_period":"1–3 years","risk":"Very High — can go to zero.","status":"informational"},
    {"id":123,"tier":"elite","name":"Distressed Loan-to-Own","asset_class":"Distressed Assets","category":"Directional","description":"Buy senior secured debt of distressed company at a discount. If the company enters bankruptcy, debt converts to equity (creditor becomes owner). Strategy requires deep fundamental analysis and active participation in restructuring.","signals":["Enterprise value vs. debt outstanding","Asset coverage","Industry outlook","Management quality"],"entry":"Buy senior secured at discount when EV > senior debt.","exit":"Converted to equity post-restructuring; sell when public market stabilizes.","holding_period":"2–4 years","risk":"Very High.","status":"informational"},
    {"id":124,"tier":"elite","name":"Crypto ANN Strategy","asset_class":"Cryptocurrencies","category":"Directional","description":"Artificial neural network (ANN) trained on crypto price/volume data to predict next-day returns. Features: lagged returns, on-chain metrics, social sentiment, funding rates. Multi-layer perceptron with L2 regularization.","signals":["ANN predicted return","On-chain active addresses","Funding rate (perpetuals)","Social sentiment score"],"entry":"Long crypto when ANN predicts > +2% daily return.","exit":"ANN prediction turns negative or stop-loss at −5%.","holding_period":"Hours to days","risk":"Very High.","status":"informational"},
    {"id":125,"tier":"elite","name":"Crypto Sentiment – Naive Bayes","asset_class":"Cryptocurrencies","category":"Directional","description":"Naive Bayes Bernoulli classifier trained on crypto news/social media to classify sentiment as positive/negative. Trade direction based on aggregate sentiment score. Works best for high-social-media coins (BTC, ETH, Dogecoin).","signals":["Social sentiment score (positive/negative)","News sentiment","Reddit/Twitter volume surge","Funding rate"],"entry":"Long when sentiment score crosses positive threshold; short when negative.","exit":"Sentiment reverses or stop-loss.","holding_period":"Hours to days","risk":"Very High.","status":"informational"},
    {"id":126,"tier":"elite","name":"Global Macro – Economic Announcement Trading","asset_class":"Global Macro","category":"Directional","description":"Trade equity index futures, bonds, or FX immediately following key economic releases (NFP, CPI, GDP, Fed decision). Position based on surprise factor = (actual − consensus estimate) / standard deviation of historical surprises.","signals":["Economic surprise index","Consensus vs. actual release","Market impact score","Intraday liquidity"],"entry":"Buy (sell) upon positive (negative) economic surprise in relevant asset.","exit":"Within same session — position held minutes to hours.","holding_period":"Minutes to hours","risk":"High — initial print sometimes revised.","status":"equity_acct"},
    # ─── ELITE: More Options Variants (127–140) ──────────────────────────────
    {"id":127,"tier":"elite","name":"Bear Call Ladder","asset_class":"Options","category":"Directional","description":"Sell near-ATM call, buy OTM call, buy further OTM call. Arises when a bear call spread turns bullish mid-trade. Profits from large upward move above highest strike. A defensive adjustment strategy.","signals":["Bear call spread gone wrong","Strong upside momentum","Earnings catalyst"],"entry":"Adjust a bear call spread by buying another OTM call.","exit":"Large move above highest strike.","holding_period":"Days to weeks","risk":"Low.","status":"options_acct"},
    {"id":128,"tier":"elite","name":"Bear Put Ladder","asset_class":"Options","category":"Directional","description":"Buy ATM put, sell OTM put, sell further OTM put. Conservative bearish. Reduces cost of bear put spread but caps profit at lower strikes. Loses on extreme downward moves below lowest strike.","signals":["Moderately bearish"],"entry":"3-leg put spread.","exit":"50% profit close.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":129,"tier":"elite","name":"Modified Call Butterfly","asset_class":"Options","category":"Income","description":"Variant of the long call butterfly where strike spacing is unequal — one wing is wider than the other. Adjusts the profit zone to be asymmetric, useful when directional bias exists within a range-bound view.","signals":["Slight directional bias with range-bound outlook"],"entry":"Asymmetric 3-leg call spread.","exit":"50% profit.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":130,"tier":"elite","name":"Modified Put Butterfly","asset_class":"Options","category":"Income","description":"Same as modified call butterfly but using puts. Asymmetric profit zone based on slight directional lean.","signals":["Slight directional bias"],"entry":"Asymmetric 3-leg put spread.","exit":"50% profit.","holding_period":"30 days","risk":"Low.","status":"options_acct"},
    {"id":131,"tier":"elite","name":"Short Call Condor","asset_class":"Options","category":"Volatility","description":"Opposite of long call condor. Net debit. Buy inner strikes, sell outer strikes. Profits from move outside the inner strikes. Volatility bet with low capital requirement.","signals":["Large move expected, direction uncertain"],"entry":"Buy inner call spread, sell outer wings.","exit":"On large move outside inner strikes.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":132,"tier":"elite","name":"Short Put Condor","asset_class":"Options","category":"Volatility","description":"Opposite of long put condor. Same mechanics as short call condor using puts. Profits from a large move in either direction.","signals":["Large move expected"],"entry":"Buy inner put spread, sell outer put wings.","exit":"On large move.","holding_period":"Days","risk":"Low.","status":"options_acct"},
    {"id":133,"tier":"elite","name":"Short Iron Condor (Reverse Iron Condor)","asset_class":"Options","category":"Volatility","description":"Buy OTM call and put spreads simultaneously. Net debit. Profits from stock moving significantly in either direction. Opposite of the popular income-focused iron condor.","signals":["Very large move expected","Low IV (cheap wings)","Binary event"],"entry":"Buy OTM call spread + buy OTM put spread, same expiry.","exit":"On significant breakout in either direction.","holding_period":"Days","risk":"Low — debit-based.","status":"options_acct"},
    {"id":134,"tier":"elite","name":"Bull Call Ladder (Extended)","asset_class":"Options","category":"Directional","description":"Buy near-ATM call, sell OTM call, sell further OTM call. Adjusts the bull call spread to reduce cost but adds risk above the highest sold strike. Used when strong upside move expected to stall at a specific level.","signals":["Bullish to a target level","High IV making spreads expensive"],"entry":"3-leg call spread.","exit":"At target level.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":135,"tier":"elite","name":"Bearish Short Seagull Spread","asset_class":"Options","category":"Directional","description":"Bear put spread financed by selling an OTM call. Zero or low net cost. Bearish with capped upside risk from the sold call.","signals":["Bearish with resistance above"],"entry":"Buy put spread, sell OTM call.","exit":"At expiry.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":136,"tier":"elite","name":"Bullish Short Seagull Spread","asset_class":"Options","category":"Directional","description":"Bull put spread financed by selling an OTM put. Net credit structure. Bullish with defined risk.","signals":["Bullish with support below"],"entry":"Buy call spread, sell OTM put.","exit":"At expiry.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":137,"tier":"elite","name":"Long Box Spread","asset_class":"Options","category":"Arbitrage","description":"Combination of bull call spread and bear put spread at same strikes and expiry. Value at expiry = spread width. Used to lock in risk-free rate when mispriced. Profitable only when market misprices the synthetic loan rate.","signals":["Box value vs. present value of spread width","Implied repo rate vs. risk-free rate"],"entry":"When box value < PV(spread width) × discount factor.","exit":"At expiry.","holding_period":"Until expiry","risk":"Very Low.","status":"options_acct"},
    {"id":138,"tier":"elite","name":"Ladder Spread (Call)","asset_class":"Options","category":"Income","description":"Buy 1 call at K1, sell 1 call at K2, sell 1 call at K3. Reduces maximum profit but also reduces cost. Best when stock expected to stay between K2 and K3.","signals":["Stock near K2/K3 range"],"entry":"3-leg call ladder.","exit":"50% of max profit.","holding_period":"30 days","risk":"Medium.","status":"options_acct"},
    {"id":139,"tier":"elite","name":"Collar with LEAPS","asset_class":"Options","category":"Hedging","description":"Long stock + long long-dated put (LEAPS 1–2 years out) + sell monthly calls. The LEAPS put provides long-term downside protection; monthly call income offsets the put cost. Cost-efficient long-term hedge.","signals":["Long-term stock holding needing hedge","LEAPS put IV relatively low"],"entry":"Buy annual LEAPS put + sell monthly OTM calls.","exit":"Roll short calls monthly; roll LEAPS put annually.","holding_period":"1–2 years","risk":"Low.","status":"options_acct"},
    {"id":140,"tier":"elite","name":"Volatility Risk Premium with Gamma Hedge","asset_class":"Volatility","category":"Income","description":"Sell straddle to collect volatility premium; continuously delta-hedge with underlying futures/ETF. Also hedge gamma by buying cheap OTM options. A more sophisticated version of the basic short straddle.","signals":["Implied vol premium over realized","Gamma/theta ratio","OTM options cheapness"],"entry":"Sell ATM straddle; buy OTM wings for gamma hedge.","exit":"50% profit or at expiry.","holding_period":"30 days","risk":"Medium — tail risk reduced by gamma hedge.","status":"options_acct"},
    # ─── ELITE: Real Estate, Infrastructure, Cash, Global Macro (141–151) ─────
    {"id":141,"tier":"elite","name":"REIT Momentum","asset_class":"Real Estate","category":"Directional","description":"Apply momentum factor to Real Estate Investment Trusts (REITs). Rank REITs by trailing 12-month price return. Long top quartile, short bottom. REITs have distinct factor exposures (rate sensitivity, property sector).","signals":["12M REIT return ranking","Interest rate trend (rising rates hurt REITs)","Occupancy rate trends"],"entry":"Long top quartile REITs; short bottom quartile.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"equity_acct"},
    {"id":142,"tier":"elite","name":"REIT Carry (Cap Rate vs. Bond Yield)","asset_class":"Real Estate","category":"Income","description":"Compare REIT dividend yield (cap rate proxy) to 10-year Treasury yield. When the spread is wide (REITs cheap vs. bonds), overweight REITs; when narrow, underweight. Classic real estate valuation metric.","signals":["REIT yield vs. 10Y Treasury spread","Historical mean of spread","Rate of change of Treasury yields"],"entry":"Long REITs (VNQ) when yield spread > historical average.","exit":"Spread normalizes.","holding_period":"Months","risk":"Medium.","status":"equity_acct"},
    {"id":143,"tier":"elite","name":"Infrastructure Investment","asset_class":"Real Estate","category":"Income","description":"Infrastructure assets (utilities, airports, toll roads) have bond-like cash flows but equity upside. Long infrastructure ETFs (IFRA, PAVE) when yield spread vs. bonds is attractive. Defensive, low-beta income strategy.","signals":["Infrastructure ETF yield vs. bond yield","Dividend growth rate","Regulatory environment"],"entry":"Long infrastructure ETFs when dividend yield > 10Y Treasury + 2%.","exit":"Yield spread compresses.","holding_period":"Months to years","risk":"Low.","status":"equity_acct"},
    {"id":144,"tier":"elite","name":"Global Macro – Fundamental Momentum","asset_class":"Global Macro","category":"Directional","description":"Rank countries by composite macro score (GDP growth momentum, inflation trajectory, current account, PMI trend). Long the top 3 country equity ETFs; short the bottom 3. A cross-country equity momentum strategy.","signals":["GDP growth surprise","PMI trend","Current account balance","Currency momentum"],"entry":"Long top countries by composite macro score.","exit":"Quarterly rebalance.","holding_period":"3 months","risk":"Medium.","status":"equity_acct"},
    {"id":145,"tier":"elite","name":"Global Macro – Inflation Hedge","asset_class":"Global Macro","category":"Hedging","description":"Build a portfolio designed to protect against unexpected inflation: long commodities (GLD, DJP), long TIPS (TIP), long commodity-producer equities (XLE), long real estate (VNQ). Rebalance when inflation expectations shift.","signals":["Break-even inflation vs. CPI","Commodity price momentum","Fed inflation language"],"entry":"Allocate to inflation-sensitive assets when CPI > 3% or rising rapidly.","exit":"Inflation expectations normalize.","holding_period":"Months","risk":"Low-Medium.","status":"equity_acct"},
    {"id":146,"tier":"elite","name":"Cross-Border Tax Arbitrage","asset_class":"Tax Arbitrage","category":"Arbitrage","description":"Exploit differences in dividend withholding tax treaties between countries. Borrow shares from a tax-exempt counterparty before the ex-dividend date; return post-dividend. The lender receives dividend equivalent payments net of withholding tax.","signals":["Dividend size","Withholding tax differential","Stock borrow cost"],"entry":"Institutional strategy — requires tax-treaty access.","exit":"Post ex-dividend return of shares.","holding_period":"Days","risk":"Low (regulatory risk).","status":"informational"},
    {"id":147,"tier":"elite","name":"Weather Risk – Demand Hedging","asset_class":"Miscellaneous","category":"Hedging","description":"Energy companies hedge demand uncertainty using weather derivatives (degree-day futures/options). Long HDD (Heating Degree Day) futures in winter when gas demand correlated to temperature. CME Group lists weather futures on 18 cities.","signals":["Temperature forecast vs. normal","HDD/CDD futures term structure","Energy demand forecast"],"entry":"Long HDD futures when forecasted temperature below normal.","exit":"At settlement or when temperature forecast normalizes.","holding_period":"Days to weeks","risk":"Medium.","status":"futures_acct"},
    {"id":148,"tier":"elite","name":"Merger Arbitrage (Risk Arbitrage)","asset_class":"Equities","category":"Arbitrage","description":"After M&A announcement, target stock trades below offer price. Buy target at discount; optionally short acquirer in all-stock deals. Profit = closing of the deal spread at completion. Requires deal probability analysis.","signals":["Deal spread (offer price − current price)","Regulatory risk score","Financing condition","Competing bid probability"],"entry":"Long target stock immediately after deal announcement.","exit":"Deal closure (spread zeroes) or deal break (stop-loss).","holding_period":"1–6 months","risk":"High on deal break — stock may fall 20–40%.","status":"equity_acct"},
    {"id":149,"tier":"elite","name":"Distressed Risk Puzzle (Anomaly)","asset_class":"Distressed Assets","category":"Directional","description":"Contrary to theory, high-distress stocks (high Altman Z-score failure probability) tend to underperform low-distress stocks. Long low-distress, short high-distress in a factor portfolio. Requires distress probability ranking.","signals":["Altman Z-score","Ohlson O-score","Bankruptcy probability model","Momentum filter"],"entry":"Long bottom distress decile; short top distress decile.","exit":"Monthly rebalance.","holding_period":"1 month","risk":"Medium.","status":"equity_acct"},
    {"id":150,"tier":"elite","name":"Liquidity Management (Short-End)","asset_class":"Cash","category":"Income","description":"Active management of cash holdings: instead of leaving cash in zero-yield accounts, ladder it across overnight repo, T-bills (SHV ETF), and money market funds. Earn incremental yield while maintaining liquidity. Used by corporate treasurers and hedge funds.","signals":["Fed funds rate","T-bill yield curve","Money market fund yields","Liquidity needs forecast"],"entry":"Deploy cash into highest-yielding short-duration instruments within liquidity constraints.","exit":"Rollover as instruments mature.","holding_period":"Overnight to 3 months","risk":"Very Low.","status":"equity_acct"},
    {"id":151,"tier":"elite","name":"Alpha + Beta Separation (Portable Alpha)","asset_class":"Equities","category":"Neutral","description":"Separate market exposure (beta) from active returns (alpha). Gain beta cheaply via S&P 500 futures; deploy freed-up capital into an uncorrelated alpha strategy (e.g., long/short equity). Achieves benchmark return + excess return with efficient capital use.","signals":["Beta cost (futures carry)","Alpha strategy Sharpe ratio","Correlation between alpha and beta"],"entry":"Long index futures for beta; run alpha portfolio on freed cash.","exit":"Ongoing — monitor correlation to ensure true separation.","holding_period":"Ongoing","risk":"Medium — alpha strategy can fail.","status":"backtest"},
]

# Build lookup dicts for fast access
STRATEGIES_BY_ID = {s["id"]: s for s in STRATEGIES}
PRO_STRATEGIES = [s for s in STRATEGIES if s["tier"] in ("pro",)]
ELITE_STRATEGIES = STRATEGIES  # All 126 (plus the 10 pro = 136 shown to elite)

STATUS_LABELS = {
    "live": ("Live", "#22c55e"),
    "backtest": ("In Backtest", "#f59e0b"),
    "equity_acct": ("Equity Account", "#6366f1"),
    "options_acct": ("Options Account", "#8b5cf6"),
    "futures_acct": ("Futures Account", "#ec4899"),
    "informational": ("Institutional", "#64748b"),
}

ASSET_CLASS_ICONS = {
    "Equities": "📈",
    "Options": "⚙️",
    "ETFs": "🔄",
    "Fixed Income": "🏦",
    "Indexes": "📊",
    "Volatility": "〰️",
    "Foreign Exchange": "💱",
    "Commodities": "🛢️",
    "Futures": "📉",
    "Structured Assets": "🏗️",
    "Convertibles": "🔀",
    "Tax Arbitrage": "⚖️",
    "Miscellaneous": "🔬",
    "Distressed Assets": "⚠️",
    "Cryptocurrencies": "₿",
    "Global Macro": "🌍",
}
