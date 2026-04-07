#!/usr/bin/env python3
"""
QuantBot Terminal Dashboard — Linear-inspired dark trading terminal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Virtual $5K portfolio tracking, equity curve, trade history, P&L.
Design inspired by Linear.app + Coinbase + Kraken design systems.
"""
import os, sys, time, json
from datetime import datetime, timedelta, timezone

import dash
from dash import html, dcc, dash_table
from dash.dependencies import Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONFIG
from core.broker import Broker
from utils.logger import get_logger

log = get_logger("dashboard")
DASH_PORT = int(os.environ.get("DASH_PORT", 8050))
VIRTUAL_START = CONFIG.virtual_cap  # $5,000

# ── Linear-inspired color system ──────────────────────────────────────────────
BG_BASE = "#08090a"          # Marketing background
BG_PANEL = "#0f1011"         # Panel background
BG_ELEVATED = "#191a1b"      # Elevated surfaces / cards
BG_INPUT = "#1e1f20"         # Input fields
BORDER = "rgba(255,255,255,0.06)"
BORDER_HOVER = "rgba(255,255,255,0.12)"
TEXT_PRIMARY = "#f7f8f8"     # Primary text
TEXT_SECONDARY = "#8a8f98"   # Secondary text
TEXT_MUTED = "#5c5f66"       # Muted text
ACCENT = "#5e6ad2"           # Brand indigo
ACCENT_HOVER = "#828fff"     # Hover state
GREEN = "#2da44e"            # Profit / bullish
GREEN_BG = "rgba(45,164,78,0.12)"
RED = "#e5534b"              # Loss / bearish
RED_BG = "rgba(229,83,75,0.12)"
YELLOW = "#d4a72c"           # Warning / neutral
YELLOW_BG = "rgba(212,167,44,0.12)"
FONT = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
MONO = "'SF Mono', 'Fira Code', 'Cascadia Code', monospace"


# ── Broker connection ─────────────────────────────────────────────────────────
try:
    broker = Broker()
except Exception as e:
    log.error("Broker connection failed: %s", e)
    broker = None


def safe_account():
    try:
        return broker.get_account() if broker else {}
    except Exception:
        return {}


def safe_positions():
    try:
        return broker.get_positions() if broker else {}
    except Exception:
        return {}


def safe_orders():
    try:
        return broker.list_open_orders() if broker else []
    except Exception:
        return []


def get_portfolio_history():
    """Fetch Alpaca equity history, normalize to virtual $5K start."""
    try:
        hist = broker._api.get_portfolio_history(period="1M", timeframe="1D")
        if not hist or not hist.equity:
            return [], []
        equities = [float(e) for e in hist.equity if e]
        timestamps = [datetime.fromtimestamp(t, tz=timezone.utc) for t in hist.timestamp[:len(equities)]]
        if not equities:
            return [], []
        base = equities[0]
        virtual = [VIRTUAL_START * (e / base) for e in equities]
        return timestamps, virtual
    except Exception as e:
        log.debug("Portfolio history error: %s", e)
        return [], []


def get_all_trades():
    """Fetch recent closed orders from Alpaca."""
    try:
        from datetime import date
        after = (date.today() - timedelta(days=30)).isoformat()
        orders = broker._api.list_orders(status="closed", limit=200, after=after,
                                         direction="desc", nested=True)
        trades = []
        for o in orders:
            if o.filled_qty and float(o.filled_qty) > 0:
                trades.append({
                    "time": o.filled_at or o.submitted_at or "",
                    "symbol": o.symbol,
                    "side": o.side,
                    "qty": float(o.filled_qty),
                    "price": float(o.filled_avg_price) if o.filled_avg_price else 0,
                    "notional": float(o.filled_qty) * (float(o.filled_avg_price) if o.filled_avg_price else 0),
                })
        return trades
    except Exception as e:
        log.debug("Trades fetch error: %s", e)
        return []


def get_x_research():
    """Get trending tickers from X research module."""
    try:
        from data.x_research import x_researcher
        return x_researcher.trending_details
    except Exception:
        return {}


def get_scanner_data():
    """Get full market scanner hot stocks."""
    try:
        from data.full_market_scanner import scanner
        return scanner.hot_details
    except Exception:
        return {}


# ── Chart builder ─────────────────────────────────────────────────────────────
def equity_chart(times, values):
    fig = go.Figure()
    if not times or not values:
        fig.add_annotation(text="Awaiting data...", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(size=16, color=TEXT_MUTED))
    else:
        color = GREEN if values[-1] >= VIRTUAL_START else RED
        fill_color = GREEN_BG if values[-1] >= VIRTUAL_START else RED_BG
        fig.add_trace(go.Scatter(
            x=times, y=values, mode="lines",
            line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=fill_color,
            hovertemplate="$%{y:,.2f}<extra>%{x|%b %d}</extra>"
        ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=280,
        xaxis=dict(showgrid=False, zeroline=False, color=TEXT_MUTED,
                   tickfont=dict(size=11, family=MONO)),
        yaxis=dict(showgrid=True, gridcolor=BORDER, zeroline=False,
                   color=TEXT_MUTED, tickprefix="$",
                   tickfont=dict(size=11, family=MONO)),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=BG_ELEVATED, bordercolor=BORDER,
                        font=dict(family=FONT, size=12)),
    )
    return fig


# ── CSS ───────────────────────────────────────────────────────────────────────
CUSTOM_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
* {{ box-sizing: border-box; }}
body {{
    background: {BG_BASE}; color: {TEXT_PRIMARY}; font-family: {FONT};
    margin: 0; padding: 0; -webkit-font-smoothing: antialiased;
}}
.card {{
    background: {BG_PANEL}; border: 1px solid {BORDER};
    border-radius: 12px; padding: 20px; margin-bottom: 12px;
    transition: border-color 0.15s ease;
}}
.card:hover {{ border-color: {BORDER_HOVER}; }}
.card-title {{
    font-size: 12px; font-weight: 500; color: {TEXT_MUTED};
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;
}}
.metric-value {{
    font-size: 28px; font-weight: 600; letter-spacing: -0.5px;
    font-family: {MONO};
}}
.metric-label {{
    font-size: 11px; color: {TEXT_MUTED}; text-transform: uppercase;
    letter-spacing: 0.5px; margin-top: 2px;
}}
.metric-change {{
    font-size: 13px; font-weight: 500; font-family: {MONO};
}}
.green {{ color: {GREEN}; }}
.red {{ color: {RED}; }}
.yellow {{ color: {YELLOW}; }}
.muted {{ color: {TEXT_MUTED}; }}
.badge {{
    display: inline-block; padding: 2px 8px; border-radius: 6px;
    font-size: 11px; font-weight: 500; font-family: {MONO};
}}
.badge-green {{ background: {GREEN_BG}; color: {GREEN}; }}
.badge-red {{ background: {RED_BG}; color: {RED}; }}
.badge-yellow {{ background: {YELLOW_BG}; color: {YELLOW}; }}
.badge-accent {{ background: rgba(94,106,210,0.12); color: {ACCENT}; }}
"""

TABLE_CSS = f"""
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner {{
    background: transparent !important;
}}
.dash-table-container .dash-spreadsheet {{
    background: transparent !important; border: none !important;
}}
.dash-table-container td, .dash-table-container th {{
    border: none !important;
    border-bottom: 1px solid {BORDER} !important;
    font-family: {MONO} !important; font-size: 12px !important;
    padding: 8px 12px !important;
}}
.dash-table-container th {{
    color: {TEXT_MUTED} !important; font-weight: 500 !important;
    text-transform: uppercase !important; letter-spacing: 0.5px !important;
    font-size: 10px !important;
}}
.dash-table-container td {{ color: {TEXT_SECONDARY} !important; }}
.dash-table-container tr:hover td {{ background: {BG_ELEVATED} !important; }}
"""


# ── Helper components ─────────────────────────────────────────────────────────
def metric_card(title, value, change=None, change_pct=None, label=None):
    """Single metric with optional change indicator."""
    change_color = "green" if (change and change >= 0) else "red" if change else "muted"
    children = [
        html.Div(title, className="card-title"),
        html.Div(value, className="metric-value"),
    ]
    if change is not None:
        sign = "+" if change >= 0 else ""
        pct_str = f" ({sign}{change_pct:.1f}%)" if change_pct is not None else ""
        children.append(html.Div(f"{sign}${change:,.2f}{pct_str}",
                                 className=f"metric-change {change_color}"))
    if label:
        children.append(html.Div(label, className="metric-label"))
    return html.Div(children, className="card")


def pos_row(symbol, pos):
    """Position row for the positions panel."""
    pl = pos.get("unrealized_pl", 0)
    pl_pct = pos.get("unrealized_plpc", 0) * 100
    color = "green" if pl >= 0 else "red"
    badge = "badge-green" if pl >= 0 else "badge-red"
    return html.Div([
        html.Div([
            html.Span(symbol, style={"fontWeight": "600", "fontSize": "13px"}),
            html.Span(f" {pos.get('qty', 0):.4g} shares",
                      style={"color": TEXT_MUTED, "fontSize": "11px", "marginLeft": "8px"}),
        ]),
        html.Div([
            html.Span(f"${pos.get('market_value', 0):,.2f}",
                      style={"fontFamily": MONO, "fontSize": "13px", "marginRight": "8px"}),
            html.Span(f"{'+'if pl>=0 else ''}{pl_pct:.1f}%", className=f"badge {badge}"),
        ]),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "padding": "8px 0",
              "borderBottom": f"1px solid {BORDER}"})


def strat_badge(name, active=True):
    """Strategy indicator badge."""
    color_map = {
        "aggressive_breakout": ACCENT,
        "momentum": "#7c3aed",
        "scalper": "#f59e0b",
        "mean_reversion": "#06b6d4",
        "catalyst": "#ec4899",
        "gap_short": RED,
        "predictive_short": "#f97316",
        "market_making": "#8b5cf6",
        "stat_arb": "#14b8a6",
        "multi_factor": "#6366f1",
    }
    c = color_map.get(name, TEXT_MUTED)
    return html.Div([
        html.Div(style={"width": "6px", "height": "6px", "borderRadius": "50%",
                         "background": c if active else TEXT_MUTED, "marginRight": "8px",
                         "boxShadow": f"0 0 6px {c}" if active else "none"}),
        html.Span(name.replace("_", " ").title(),
                   style={"fontSize": "12px", "fontWeight": "500",
                           "color": TEXT_PRIMARY if active else TEXT_MUTED}),
    ], style={"display": "flex", "alignItems": "center", "padding": "4px 0"})


# ══════════════════════════════════════════════════════════════════════════════
#  Dash App
# ═════���════════════════════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    external_stylesheets=[],
    title="QuantBot Terminal",
    update_title=None,
)
# Inject custom CSS via index_string
app.index_string = f'''<!DOCTYPE html>
<html><head>{{%metas%}}{{%title%}}{{%favicon%}}{{%css%}}
<style>{CUSTOM_CSS}{TABLE_CSS}</style>
</head><body>{{%app_entry%}}<footer>{{%config%}}{{%scripts%}}{{%renderer%}}</footer></body></html>'''

app.layout = html.Div([
    # Auto-refresh
    dcc.Interval(id="tick", interval=15_000),

    # ── Header ────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.Div([
                html.Span("QB", style={"fontWeight": "700", "fontSize": "16px",
                                         "color": ACCENT, "marginRight": "8px",
                                         "fontFamily": MONO}),
                html.Span("QuantBot Terminal", style={"fontWeight": "600",
                                                        "fontSize": "15px"}),
            ], style={"display": "flex", "alignItems": "center"}),
            html.Div([
                html.Span(id="header-time", style={"color": TEXT_MUTED, "fontSize": "12px",
                                                     "fontFamily": MONO, "marginRight": "16px"}),
                html.Span(id="header-status", className="badge badge-accent"),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                   "alignItems": "center", "maxWidth": "1400px", "margin": "0 auto"}),
    ], style={"padding": "12px 24px", "borderBottom": f"1px solid {BORDER}",
              "background": BG_PANEL}),

    # ── Main content ──────────────────────────────────────────────────────
    html.Div([
        # ── Top metrics row ───────────────────────────────────────────────
        html.Div(id="metrics-row", style={
            "display": "grid", "gridTemplateColumns": "repeat(4, 1fr)",
            "gap": "12px", "marginBottom": "16px",
        }),

        # ── Two-column layout ─────────────────────────────────────────────
        html.Div([
            # Left: Equity chart + Trade history
            html.Div([
                html.Div([
                    html.Div("Equity Curve", className="card-title"),
                    dcc.Graph(id="equity-chart", config={"displayModeBar": False}),
                ], className="card"),

                html.Div([
                    html.Div([
                        html.Span("Recent Trades", className="card-title"),
                        html.Span(id="trade-count",
                                  style={"color": TEXT_MUTED, "fontSize": "11px",
                                          "marginLeft": "8px", "fontFamily": MONO}),
                    ], style={"display": "flex", "alignItems": "center",
                              "marginBottom": "12px"}),
                    html.Div(id="trades-table"),
                ], className="card"),
            ], style={"flex": "1", "minWidth": "0"}),

            # Right sidebar: Positions + Strategies + X Research
            html.Div([
                html.Div([
                    html.Div("Open Positions", className="card-title"),
                    html.Div(id="positions-list"),
                ], className="card"),

                html.Div([
                    html.Div("Strategies", className="card-title"),
                    html.Div(id="strategies-list"),
                ], className="card"),

                html.Div([
                    html.Div("Market Scanner (ALL Stocks)", className="card-title"),
                    html.Div(id="scanner-list"),
                ], className="card"),

                html.Div([
                    html.Div("X Research / Trending", className="card-title"),
                    html.Div(id="x-research-list"),
                ], className="card"),

                html.Div([
                    html.Div("Open Orders", className="card-title"),
                    html.Div(id="orders-list"),
                ], className="card"),
            ], style={"width": "340px", "flexShrink": "0"}),

        ], style={"display": "flex", "gap": "16px"}),

    ], style={"maxWidth": "1400px", "margin": "0 auto",
              "padding": "16px 24px"}),

], style={"minHeight": "100vh", "background": BG_BASE})


# ══════════════════════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    [Output("header-time", "children"),
     Output("header-status", "children"),
     Output("metrics-row", "children"),
     Output("equity-chart", "figure"),
     Output("trade-count", "children"),
     Output("trades-table", "children"),
     Output("positions-list", "children"),
     Output("strategies-list", "children"),
     Output("x-research-list", "children"),
     Output("scanner-list", "children"),
     Output("orders-list", "children")],
    [Input("tick", "n_intervals")],
)
def update_dashboard(_):
    now = datetime.now(timezone.utc)
    time_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    acct = safe_account()
    positions = safe_positions()
    orders = safe_orders()

    equity = acct.get("equity", 0)
    cash = acct.get("cash", 0)
    total_deployed = sum(abs(p.get("market_value", 0)) for p in positions.values())
    total_pl = sum(p.get("unrealized_pl", 0) for p in positions.values())

    # Virtual balance: normalize to $5K start
    if equity > 0:
        base_equity = acct.get("last_equity", equity)
        if base_equity > 0:
            daily_return_pct = ((equity - base_equity) / base_equity) * 100
        else:
            daily_return_pct = 0
        # Virtual PnL based on deployed capital only
        virtual_balance = VIRTUAL_START + total_pl
        virtual_change = total_pl
        virtual_pct = (total_pl / VIRTUAL_START * 100) if VIRTUAL_START else 0
    else:
        virtual_balance = VIRTUAL_START
        virtual_change = 0
        virtual_pct = 0
        daily_return_pct = 0

    # Status badge
    is_running = total_deployed > 0 or len(orders) > 0
    status = "LIVE" if is_running else "IDLE"

    # ── Metrics cards ─────────────────────────────────────────────────────
    cap_used_pct = (total_deployed / VIRTUAL_START * 100) if VIRTUAL_START else 0
    metrics = [
        metric_card("Virtual Balance",
                    f"${virtual_balance:,.2f}",
                    change=virtual_change, change_pct=virtual_pct),
        metric_card("Deployed Capital",
                    f"${total_deployed:,.2f}",
                    label=f"{cap_used_pct:.0f}% of ${VIRTUAL_START:,.0f} cap"),
        metric_card("Open Positions",
                    str(len(positions)),
                    label=f"{len(orders)} pending orders"),
        metric_card("Unrealized P&L",
                    f"${'+'if total_pl>=0 else ''}{total_pl:,.2f}",
                    change=total_pl, change_pct=virtual_pct),
    ]

    # ── Equity chart ──────────────────────────────────────────────────────
    times, values = get_portfolio_history()
    chart = equity_chart(times, values)

    # ── Trades table ──────────────────────────────────────────────────────
    trades = get_all_trades()
    trade_count_str = f"({len(trades)} fills, 30d)"

    if trades:
        trade_rows = []
        for t in trades[:50]:
            ts = str(t.get("time", ""))[:16].replace("T", " ")
            side = t.get("side", "")
            side_badge = "badge-green" if side == "buy" else "badge-red"
            trade_rows.append(html.Div([
                html.Span(ts, style={"width": "120px", "color": TEXT_MUTED,
                                      "fontSize": "11px", "fontFamily": MONO}),
                html.Span(t["symbol"], style={"width": "80px", "fontWeight": "600",
                                                "fontSize": "12px"}),
                html.Span(side.upper(), className=f"badge {side_badge}",
                           style={"width": "50px", "textAlign": "center"}),
                html.Span(f"{t['qty']:.4g}", style={"width": "70px",
                           "fontFamily": MONO, "fontSize": "12px", "color": TEXT_SECONDARY}),
                html.Span(f"${t['price']:,.2f}", style={"width": "90px",
                           "fontFamily": MONO, "fontSize": "12px", "color": TEXT_SECONDARY}),
                html.Span(f"${t['notional']:,.2f}", style={"width": "90px",
                           "fontFamily": MONO, "fontSize": "12px",
                           "color": TEXT_PRIMARY, "fontWeight": "500"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "8px",
                      "padding": "6px 0", "borderBottom": f"1px solid {BORDER}"}))

        # Header
        header = html.Div([
            html.Span("Time", style={"width": "120px"}),
            html.Span("Symbol", style={"width": "80px"}),
            html.Span("Side", style={"width": "50px"}),
            html.Span("Qty", style={"width": "70px"}),
            html.Span("Price", style={"width": "90px"}),
            html.Span("Value", style={"width": "90px"}),
        ], style={"display": "flex", "gap": "8px", "padding": "4px 0",
                  "borderBottom": f"1px solid {BORDER}", "marginBottom": "4px",
                  "fontSize": "10px", "fontWeight": "500", "color": TEXT_MUTED,
                  "textTransform": "uppercase", "letterSpacing": "0.5px"})
        trades_table = html.Div([header] + trade_rows,
                                 style={"maxHeight": "400px", "overflowY": "auto"})
    else:
        trades_table = html.Div("No trades yet — bot will start trading soon",
                                 style={"color": TEXT_MUTED, "fontSize": "13px",
                                         "padding": "20px 0"})

    # ── Positions list ────────────────────────────────────────────────────
    if positions:
        pos_list = [pos_row(sym, p) for sym, p in sorted(
            positions.items(), key=lambda x: abs(x[1].get("market_value", 0)), reverse=True)]
    else:
        pos_list = [html.Div("No open positions", style={"color": TEXT_MUTED,
                              "fontSize": "13px", "padding": "12px 0"})]

    # ── Strategies ────────────────────────────────────────────────────────
    strat_names = ["aggressive_breakout", "momentum", "scalper", "mean_reversion",
                   "catalyst", "gap_short", "predictive_short", "stat_arb", "multi_factor"]
    strat_list = [strat_badge(s, active=True) for s in strat_names]

    # ── X Research ────────────────────────────────────────────────────────
    x_data = get_x_research()
    if x_data:
        x_items = []
        for sym, info in sorted(x_data.items(), key=lambda x: x[1].get("score", 0), reverse=True)[:8]:
            sent = info.get("sentiment", "neutral")
            badge_cls = "badge-green" if sent == "bullish" else "badge-red" if sent == "bearish" else "badge-yellow"
            x_items.append(html.Div([
                html.Div([
                    html.Span(sym, style={"fontWeight": "600", "fontSize": "13px"}),
                    html.Span(f" score {info.get('score', 0)}",
                              style={"color": TEXT_MUTED, "fontSize": "11px", "marginLeft": "6px"}),
                ]),
                html.Span(sent[:4].upper(), className=f"badge {badge_cls}"),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "center", "padding": "6px 0",
                      "borderBottom": f"1px solid {BORDER}"}))
        if x_items:
            x_research = x_items
        else:
            x_research = [html.Div("Scanning...", style={"color": TEXT_MUTED, "fontSize": "12px"})]
    else:
        x_research = [html.Div(
            "Set XAI_API_KEY in .env to enable Grok X research",
            style={"color": TEXT_MUTED, "fontSize": "12px", "padding": "8px 0"}
        )]

    # ── Orders list ───────────────────────────────────────────────────────
    if orders:
        order_items = []
        for o in orders[:10]:
            side = o.get("side", "buy")
            badge_cls = "badge-green" if side == "buy" else "badge-red"
            order_items.append(html.Div([
                html.Div([
                    html.Span(o.get("symbol", ""), style={"fontWeight": "600", "fontSize": "12px"}),
                    html.Span(f" {o.get('qty', '')}",
                              style={"color": TEXT_MUTED, "fontSize": "11px", "marginLeft": "4px"}),
                ]),
                html.Span(side.upper(), className=f"badge {badge_cls}"),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "center", "padding": "6px 0",
                      "borderBottom": f"1px solid {BORDER}"}))
        orders_list = order_items
    else:
        orders_list = [html.Div("No pending orders", style={"color": TEXT_MUTED,
                                 "fontSize": "12px", "padding": "8px 0"})]

    # ── Scanner data ────────────────────────────────────────────────────
    scan_data = get_scanner_data()
    if scan_data:
        scan_items = []
        for sym, info in sorted(scan_data.items(),
                                key=lambda x: x[1].get("score", 0), reverse=True)[:12]:
            pct = info.get("pct_change", 0)
            direction = info.get("direction", "neutral")
            price = info.get("price", 0)
            if direction == "long":
                badge_cls = "badge-green"
                dir_label = "LONG"
            elif direction == "short":
                badge_cls = "badge-red"
                dir_label = "SHORT"
            else:
                badge_cls = "badge-yellow"
                dir_label = "SCAN"
            scan_items.append(html.Div([
                html.Div([
                    html.Span(sym, style={"fontWeight": "600", "fontSize": "13px"}),
                    html.Span(f" ${price:.2f}" if price > 0 else "",
                              style={"color": TEXT_MUTED, "fontSize": "11px",
                                     "fontFamily": MONO, "marginLeft": "6px"}),
                    html.Span(f" {pct:+.1f}%" if pct != 0 else "",
                              style={"color": GREEN if pct > 0 else RED,
                                     "fontSize": "11px", "fontFamily": MONO,
                                     "marginLeft": "4px"}),
                ]),
                html.Span(dir_label, className=f"badge {badge_cls}"),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "center", "padding": "5px 0",
                      "borderBottom": f"1px solid {BORDER}"}))
        scanner_list = scan_items
    else:
        scanner_list = [html.Div("Scanner starting... scanning 12,000+ stocks",
                                  style={"color": TEXT_MUTED, "fontSize": "12px",
                                          "padding": "8px 0"})]

    return (time_str, status, metrics, chart, trade_count_str, trades_table,
            pos_list, strat_list, scanner_list, x_research, orders_list)


# ══════════════════════════════════════════════════════════════════════════════
#  Run
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("QuantBot Dashboard -> http://localhost:%d", DASH_PORT)
    app.run(host="0.0.0.0", port=DASH_PORT, debug=False)
