"""MCP server: lets Claude Desktop read your finintel data.

Runs over stdio - stdout is the protocol channel, so NOTHING may print to stdout here.
Logging goes to stderr and ~/.finintel/logs (see fin.logsetup).
"""

from __future__ import annotations

from typing import Any

# mcp 2.x renamed FastMCP -> MCPServer (same decorators and run()).
from mcp.server.mcpserver import MCPServer

from fin.config import get_settings
from fin.logsetup import setup_logging
from fin_mcp import tools

INSTRUCTIONS = """\
finintel holds the user's REAL financial data from their linked banks and cards (Plaid),
statement imports, and brokerage (SnapTrade). Rules:
- Always use these tools for their finances; never invent or estimate figures the tools don't return.
  If a tool returns no data, say it is not connected or not available.
- State the 'as of' / last-synced date for balances. If data is more than ~2 days old, offer sync_now.
- Credit card 'current' = amount owed. Amounts: negative = money out, positive = money in.
- The user's own categorization rules (fin rules) decide labels such as rent or income.
  Tuition and trips are excluded from monthly averages and handled as planned expenses with dates.
- Distinguish verified account data from delayed market prices and from estimates/projections.
- Plans and outlooks are calculations from their data, not financial advice; never guarantee returns.
- Nothing here can move money or trade; the user makes every transaction themselves.
"""

mcp = MCPServer("finintel", instructions=INSTRUCTIONS)


@mcp.tool()
def get_data_status() -> dict[str, Any]:
    """Connected accounts, history coverage per account, last refresh times, re-login needs.
    Call this first when freshness or completeness matters."""
    return tools.data_status()


@mcp.tool()
def get_overview() -> dict[str, Any]:
    """Current balances for every account, net cash (cash minus card balances) and investments."""
    return tools.overview()


@mcp.tool()
def get_cashflow(months: int = 3, ef_months: int = 6) -> dict[str, Any]:
    """Income vs spending by month, spending by category, recurring charges, and the savings/
    investing plan. months = complete months to average; ef_months = emergency fund size (3-6)."""
    return tools.cashflow(months=months, ef_months=ef_months)


@mcp.tool()
def get_investing_plan(ef_months: int = 6) -> dict[str, Any]:
    """How much can be invested now and monthly, when investing can start, planned expenses,
    and the month-by-month outlook. ef_months = emergency fund size in months of costs (3-6)."""
    return tools.plan(ef_months=ef_months)


@mcp.tool()
def get_upcoming(days: int = 60) -> dict[str, Any]:
    """Planned expenses (tuition, repairs, trips) and expected recurring charges/paychecks in the
    next N days."""
    return tools.upcoming(days=days)


@mcp.tool()
def search_transactions(query: str = "", month: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Find transactions whose merchant/description contains `query` (case-insensitive),
    optionally only in `month` (YYYY-MM). Returns at most `limit` (max 100), newest first."""
    return tools.search_transactions(query=query, month=month, limit=limit)


@mcp.tool()
def get_holdings() -> dict[str, Any]:
    """Fidelity positions (ticker, shares, price, value, cost basis) per brokerage account."""
    return tools.holdings()


@mcp.tool()
def get_investment_activity(limit: int = 50) -> dict[str, Any]:
    """Recent Fidelity activity: buys, sells, dividends, deposits and withdrawals."""
    return tools.investment_activity(limit=limit)


@mcp.tool()
def get_portfolio(new_money: float = 0) -> dict[str, Any]:
    """Portfolio analytics: allocation vs the user's targets (cash excluded), concentration warnings
    (any single stock > 10%), unrealized gains, value history, and - if new_money > 0 - a
    buy-only plan for splitting new money toward the targets. Read-only."""
    return tools.portfolio(new_money=new_money)


@mcp.tool()
def research_stock(ticker: str, planned: float = 0.0, refresh: bool = False) -> dict[str, Any]:
    """12-point investment memo for one stock (e.g. NVDA, TSM) from public data: business,
    growth, profitability, balance sheet, cash flow, valuation, price action, analysts, news,
    macro, risks, and fit with the user's plan (planned = dollars he plans to invest in total,
    to size the 10% position limit). Each point has FACTS (dated data) and a VIEW (rule-of-thumb
    opinion) and a score. Use it before giving any single-stock opinion; cite it; add your own
    judgment and current news searches; keep facts, estimates and opinions labeled."""
    return tools.research(ticker=ticker, planned=planned, refresh=refresh)


@mcp.tool()
def get_macro(refresh: bool = False) -> dict[str, Any]:
    """Latest US macro indicators from FRED: 10-year Treasury yield, Fed funds rate,
    unemployment, CPI inflation (year over year)."""
    return tools.macro(refresh=refresh)


@mcp.tool()
def get_risk(new_money: float = 0, keep_cash: float = 0, pick: str | None = None) -> dict[str, Any]:
    """Risk check: cash runway (months of costs covered, also if sponsor support paused),
    bills due in 6 months vs cash, stress tests (correction, 2022 bear, 2008 crash, 2000 tech bust)
    with dollar losses and historical recovery times, estimated volatility and a 1-in-20 bad year.
    Optional what-if: new_money split by the portfolio targets, keep_cash = part kept in SPAXX,
    pick = ticker for the individual-stock slice (uses its beta). Estimates, not predictions."""
    return tools.risk(new_money=new_money, keep_cash=keep_cash, pick=pick)


@mcp.tool()
def owed_to_me(action: str = "list", who: str = "", amount: float | None = None,
               card: str | None = None, note: str = "", item_id: int | None = None) -> dict[str, Any]:
    """Money people owe the user for charges on his cards (bookkeeping only; moves no money).
    action="list"; action="add" with who, amount, card (e.g. "chase"), note;
    action="settle" with item_id (and amount for a partial repayment).
    Open items on a card are excluded from what the user owes and from net cash."""
    return tools.owed_to_me(action=action, who=who, amount=amount, card=card, note=note,
                            item_id=item_id)


@mcp.tool()
def sync_now() -> dict[str, Any]:
    """Pull fresh balances and transactions from the banks/cards and Fidelity (read-only).
    Takes up to a minute. Use when data is stale or the user asks for an update."""
    return tools.sync_now()


@mcp.prompt()
def financial_review() -> str:
    """A complete review of the user's current financial situation."""
    return (
        "Do a complete review of my finances using the finintel tools: get_data_status, "
        "get_overview, get_cashflow, get_investing_plan and get_upcoming. Cover: where I stand "
        "today (with as-of dates), monthly income vs costs, the biggest spending categories, "
        "recurring charges worth questioning, upcoming obligations and any cash-flow risks, my "
        "emergency-fund progress, and when and how much I can start investing. Challenge my "
        "assumptions, point out risks and missed opportunities, flag data gaps, and finish with "
        "the 3 most valuable actions for the next 30 days. Not financial advice - show the numbers."
    )


@mcp.prompt()
def investment_memo(ticker: str) -> str:
    """Institutional-style 12-point evaluation of a stock or ETF."""
    return (
        f"Write an institutional-quality investment memo on {ticker}. Use current, sourced data "
        "(web search and any research tools available) and cite sources with dates; never invent "
        "figures. Structure: 1) Investment thesis 2) Business fundamentals 3) Competitive "
        "advantages 4) Valuation (P/E, forward P/E, P/S, EV/EBITDA, FCF yield vs history and "
        "peers) 5) Growth prospects 6) Potential catalysts 7) Bull case 8) Base case 9) Bear case "
        "10) Downside risks 11) Investment horizon 12) Final assessment. Include technicals "
        "(50/200-day moving averages, RSI, trend) and the macro backdrop (rates, inflation). "
        "Then check fit with MY situation using get_investing_plan and get_holdings: whether I "
        "should be investing yet, position size relative to my plan, and concentration risk. "
        "Never recommend based on popularity or recent price moves; say clearly when it doesn't "
        "make sense for me. Not financial advice; no guaranteed returns."
    )


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)  # stderr + file only, never stdout
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
