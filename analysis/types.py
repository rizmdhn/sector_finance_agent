"""Shared sentinels for Appendix A calculations
(portfolio-intelligence-business-requirements-v1.1.md).

Appendix A's preamble: "Use `Unavailable` for missing inputs and `NM` for an
economically meaningless ratio; neither should become zero." The two are kept as
distinct singleton types so a caller can never mistake one for a real number, and
`is_missing` only ever treats `Unavailable`/`None` as "no input" — `NM` is a valid
*result*, not a missing input, and is never silently propagated through further
arithmetic by these functions.
"""

from typing import Union


class Unavailable:
    """A required input was missing. Never treat this as zero."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Unavailable"

    def __bool__(self) -> bool:
        return False


class NotMeaningful:
    """The ratio has no economic meaning for this input (e.g. negative equity ROE).
    Never treat this as zero."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NM"

    def __bool__(self) -> bool:
        return False


UNAVAILABLE = Unavailable()
NM = NotMeaningful()

Number = Union[float, int, Unavailable]


def is_missing(value) -> bool:
    return value is None or isinstance(value, Unavailable)


class InvalidValuationAssumption(ValueError):
    """A valuation input violates a hard modeling constraint that Appendix A says
    should reject the calculation outright rather than return a number (e.g. the
    discount rate not exceeding the terminal growth rate)."""
