"""Inputs available to an individual GD forecast formula."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Protocol


class Period(Protocol):
    @property
    def index(self) -> int: ...

    @property
    def cost_of_equity(self) -> float: ...

    @property
    def dividend_payout_ratio(self) -> float: ...

    def get(self, sheet: str, row: int, offset: int = 0) -> float: ...

    def input(self, sheet: str, row: int) -> float: ...

    def selector(self, row: int) -> int: ...


ForecastFormula = Callable[[Period], float]


def excel_round(value: float, digits: int = 0) -> float:
    """Use Excel's rounding of ties away from zero."""
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))
