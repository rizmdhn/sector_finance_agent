"""Entry point the gateway builds an agent from.

This used to build a single flat MVP agent with every tool attached. It now builds
the Chief Portfolio Intelligence Orchestrator (gateway/roles/orchestrator.py), which
delegates company research to the Investment Research Lead sub-agent
(gateway/roles/investment_research.py) rather than calling tools directly — the
start of the 5-role architecture in
portfolio-intelligence-business-requirements-v1.1.md section 4. `build_agent`'s name
and signature are unchanged so gateway/main.py needs no changes.
"""

from gateway.roles.orchestrator import build_agent

__all__ = ["build_agent"]
