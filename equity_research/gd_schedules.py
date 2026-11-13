"""GD V4 forecast schedules in USD millions, except ratios and per-share data."""

from collections.abc import Callable

from .gd_projection_types import Period, excel_round


SCHEDULES = "Schedules"
EQUITY = "Equity"
INCOME_STATEMENT = "Income statement"
BALANCE_SHEET = "Balance sheet"


def _legacy_debt_principal(period: Period) -> float:
    return sum(period.input(SCHEDULES, row) for row in range(59, 72))


def _new_debt_to_date(period: Period) -> float:
    return sum(period.get(SCHEDULES, 79, offset=-lag) for lag in range(period.index + 1))


def _current_debt(period: Period) -> float:
    # Contractual maturities are inferred from the change in legacy debt principal.
    if period.index == 5:
        return period.input(SCHEDULES, 88)
    return period.get(SCHEDULES, 73) - period.get(SCHEDULES, 73, offset=1)


def _borrowing_rate(period: Period) -> float:
    if period.index == 0:
        return period.get(SCHEDULES, 93, offset=-1)
    return period.input(SCHEDULES, 93)


def schedules_formulas() -> dict[int, Callable[[Period], float]]:
    """Map the forecast PP&E, debt, interest, and working-capital schedules."""
    return {
        # PP&E and depreciation roll-forwards.
        13: lambda p: p.get(SCHEDULES, 33),
        14: lambda p: -p.get(SCHEDULES, 42),
        16: lambda p: p.get(SCHEDULES, 13) + p.get(SCHEDULES, 14),
        20: lambda p: excel_round(p.input(SCHEDULES, 24) * p.get(INCOME_STATEMENT, 9)),
        21: lambda p: excel_round(
            p.input(SCHEDULES, 25) * (p.get(SCHEDULES, 29) + p.get(SCHEDULES, 33)) / 2
        ),
        26: lambda p: p.get(SCHEDULES, 13) / p.get(SCHEDULES, 21),
        29: lambda p: p.get(SCHEDULES, 33, offset=-1),
        30: lambda p: p.get(SCHEDULES, 20),
        31: lambda p: p.input(SCHEDULES, 22),
        33: lambda p: p.get(SCHEDULES, 29) + p.get(SCHEDULES, 30) - p.get(SCHEDULES, 31),
        35: lambda p: p.get(SCHEDULES, 33) / p.get(SCHEDULES, 33, offset=-1) - 1,
        38: lambda p: p.get(SCHEDULES, 42, offset=-1),
        39: lambda p: p.get(SCHEDULES, 21),
        40: lambda p: p.input(SCHEDULES, 22),
        42: lambda p: p.get(SCHEDULES, 38) + p.get(SCHEDULES, 39) - p.get(SCHEDULES, 40),
        44: lambda p: p.get(SCHEDULES, 42) / p.get(SCHEDULES, 13),
        47: lambda p: p.get(SCHEDULES, 13),
        48: lambda p: -p.get(SCHEDULES, 42),
        50: lambda p: p.get(SCHEDULES, 47) + p.get(SCHEDULES, 48),
        # Legacy notes, cumulative forecast issuance, and debt classification.
        73: _legacy_debt_principal,
        75: lambda p: p.get(SCHEDULES, 81) + p.input(SCHEDULES, 74),
        78: lambda p: p.get(SCHEDULES, 73 if p.index == 0 else 81, offset=-1),
        80: lambda p: p.get(SCHEDULES, 81) - p.get(SCHEDULES, 78) - p.input(SCHEDULES, 79),
        81: lambda p: p.get(SCHEDULES, 73) + _new_debt_to_date(p),
        82: lambda p: p.get(SCHEDULES, 81) - p.get(SCHEDULES, 73) - _new_debt_to_date(p),
        85: lambda p: p.get(SCHEDULES, 90) / p.get(BALANCE_SHEET, 45),
        88: _current_debt,
        89: lambda p: p.get(SCHEDULES, 75) - p.get(SCHEDULES, 88),
        90: lambda p: p.get(SCHEDULES, 88) + p.get(SCHEDULES, 89),
        93: _borrowing_rate,
        94: lambda p: (p.get(SCHEDULES, 75, offset=-1) + p.get(SCHEDULES, 75)) / 2,
        95: lambda p: p.get(SCHEDULES, 93) * p.get(SCHEDULES, 94),
        # Working capital from turnover days and the linked P&L.
        104: lambda p: p.get(INCOME_STATEMENT, 9),
        105: lambda p: abs(p.get(INCOME_STATEMENT, 12)),
        106: lambda p: abs(p.get(INCOME_STATEMENT, 13)),
        107: lambda p: p.get(SCHEDULES, 105) + p.get(SCHEDULES, 106),
        113: lambda p: p.input(SCHEDULES, 110) + p.input(SCHEDULES, 111) - p.input(SCHEDULES, 112),
        116: lambda p: p.input(SCHEDULES, 110) / 365 * p.get(SCHEDULES, 104),
        117: lambda p: p.input(SCHEDULES, 111) / 365 * p.get(SCHEDULES, 107),
        118: lambda p: p.input(SCHEDULES, 112) / 365 * p.get(SCHEDULES, 107),
        120: lambda p: p.get(SCHEDULES, 116) + p.get(SCHEDULES, 117) - p.get(SCHEDULES, 118),
        121: lambda p: p.get(SCHEDULES, 120) - p.get(SCHEDULES, 120, offset=-1),
        122: lambda p: p.get(SCHEDULES, 120) / p.get(SCHEDULES, 104),
    }


def equity_formulas() -> dict[int, Callable[[Period], float]]:
    """Map retained earnings, treasury stock, shares, APIC, and AOCI."""
    return {
        8: lambda p: p.get(EQUITY, 13, offset=-1),
        9: lambda p: p.get(INCOME_STATEMENT, 26),
        10: lambda p: -p.get(EQUITY, 9) * p.dividend_payout_ratio,
        13: lambda p: p.get(EQUITY, 8) + p.get(EQUITY, 9) + p.get(EQUITY, 10) + p.input(EQUITY, 11),
        17: lambda p: p.get(EQUITY, 22, offset=-1),
        20: lambda p: p.get(EQUITY, 20, offset=-1) * (1 + p.cost_of_equity),
        21: lambda p: p.get(EQUITY, 21, offset=-1) + p.input(EQUITY, 18) / p.get(EQUITY, 20) + p.get(EQUITY, 29),
        22: lambda p: p.get(EQUITY, 17) + p.input(EQUITY, 18) + p.input(EQUITY, 19),
        26: lambda p: p.get(EQUITY, 32, offset=-1),
        29: lambda p: p.input(EQUITY, 27) / p.get(EQUITY, 20),
        32: lambda p: p.get(EQUITY, 26) + p.input(EQUITY, 27) + p.input(EQUITY, 28),
        36: lambda p: p.get(EQUITY, 41, offset=-1),
        41: lambda p: p.get(EQUITY, 36) + p.input(EQUITY, 37),
        46: lambda p: p.get(EQUITY, 32),
        47: lambda p: p.get(EQUITY, 13),
        48: lambda p: p.get(EQUITY, 22),
        49: lambda p: p.get(EQUITY, 41),
        52: lambda p: sum(p.get(EQUITY, row) for row in range(45, 50)),
    }
