"""Builds a Strands Agent per request: model, tools, system prompt.

See idx_agent_infrastructure_diagrams_md.md section 2 and 4.
"""

from strands import Agent

from gateway.registry import ModelEntry, build_model
from gateway.tools.company_report import get_company_report
from gateway.tools.market_movers import get_market_movers
from gateway.tools.price_history import get_price_history
from gateway.tools.screener import screen_companies

SYSTEM_PROMPT = """\
You are the IDX Analyst, an assistant for Indonesia Stock Exchange (IDX) data.

Rules:
- Use the available tools to look up data; never invent prices, financials, or rankings.
- Every answer about a specific company or market data must state the `as_of` date \
and data source returned by the tool.
- You are not a financial adviser. Do not give buy/sell/hold recommendations. \
End answers touching valuation or investment decisions with a brief \
not-financial-advice note.
- If a tool reports an unknown symbol, tell the user rather than guessing a ticker.
"""

MVP_TOOLS = [get_company_report, screen_companies, get_price_history, get_market_movers]


def build_agent(model_entry: ModelEntry) -> Agent:
    if not model_entry.supports_tools:
        raise ValueError(f"model {model_entry.name} does not support tool calling")

    return Agent(
        model=build_model(model_entry),
        tools=MVP_TOOLS,
        system_prompt=SYSTEM_PROMPT,
    )
