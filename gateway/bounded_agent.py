"""BoundedAgent: an Agent that enforces a default per-invocation token/turn cap
unless the caller explicitly overrides it.

Why this exists: Strands' native `Limits` (`strands.types.agent.Limits` — `turns`,
`output_tokens`, `total_tokens`) is exactly the guardrail this project needs against
a runaway conversation burning credit on unbounded retries or tool-call loops. But
it is a per-CALL argument to `agent(...)`/`stream_async(...)`, not something you can
set once on the Agent and have it apply automatically — and `.as_tool()`
(`strands/agent/_agent_as_tool.py`) calls the wrapped specialist's `stream_async`
directly without forwarding a `limits` kwarg at all. So a specialist attached to the
Chief via `.as_tool()` (every specialist in this project — see gateway/roles/
orchestrator.py) would otherwise have NO cap of its own: the Chief's own `limits`
only bounds the Chief's own loop, and "call a specialist" counts as one Chief turn
regardless of how long that specialist spends internally.

`BoundedAgent` closes that gap with the smallest possible change: override
`stream_async` to fall back to a stored default `Limits` whenever the caller (in
practice, `.as_tool()`) didn't pass one explicitly. A caller that DOES pass `limits`
(e.g. gateway/main.py calling the Chief directly) still gets exactly what it asked
for — this never tightens or loosens an explicit caller-supplied cap, only fills the
gap when nothing was supplied at all.
"""

from typing import Any

from strands import Agent
from strands.types.agent import Limits


class BoundedAgent(Agent):
    def __init__(self, *args: Any, default_limits: Limits, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._default_limits = default_limits

    async def stream_async(self, *args: Any, limits: Limits | None = None, **kwargs: Any):
        async for event in super().stream_async(*args, limits=limits or self._default_limits, **kwargs):
            yield event
