"""Project GD's three statements from the segment and supporting schedules."""

from collections.abc import Callable, Iterable

from .gd_projection_types import Period


INCOME = "Income statement"
BALANCE = "Balance sheet"
CASH = "Cash flow"
REVENUE = "Revenue model"
SCHEDULES = "Schedules"
EQUITY = "Equity"
Formula = Callable[[Period], float]


def _sum_rows(period: Period, sheet: str, rows: Iterable[int]) -> float:
    return sum(period.get(sheet, row) for row in rows)


def _scaled_income(period: Period, revenue_row: int, expense_row: int) -> float:
    last_actual = -period.index - 1
    ratio = period.get(INCOME, expense_row, last_actual) / period.get(INCOME, revenue_row, last_actual)
    return period.get(INCOME, revenue_row) * ratio


def operating_expense_gap(period: Period) -> float:
    """Return the signed adjustment needed to reconcile the saved expense presentation."""
    return period.get(INCOME, 15) - _sum_rows(period, INCOME, (12, 13, 14))


def income_formulas() -> dict[int, Formula]:
    formulas: dict[int, Formula] = {
        7: lambda p: _sum_rows(p, REVENUE, (8, 23, 24, 36, 37)),
        8: lambda p: _sum_rows(p, REVENUE, (9, 25, 38, 49, 50)),
        9: lambda p: _sum_rows(p, INCOME, (7, 8)),
        12: lambda p: _scaled_income(p, revenue_row=7, expense_row=12),
        13: lambda p: _scaled_income(p, revenue_row=8, expense_row=13),
        14: lambda p: _scaled_income(p, revenue_row=9, expense_row=14),
        # Segment margins drive consolidated operating earnings in GD V4.
        15: lambda p: p.get(INCOME, 17) - p.get(INCOME, 9),
        17: lambda p: p.get(REVENUE, 74),
        20: lambda p: -p.get(SCHEDULES, 95),
        22: lambda p: _sum_rows(p, INCOME, (17, 19, 20)),
        24: lambda p: p.get(INCOME, 22) * p.get(INCOME, 30),
        26: lambda p: _sum_rows(p, INCOME, (22, 24)),
        29: lambda p: p.get(INCOME, 17) / p.get(INCOME, 9),
        30: lambda p: p.get(INCOME, 30, -1),
        31: lambda p: p.get(INCOME, 26) / p.get(INCOME, 9),
    }
    common_size_rows = {
        38: (7, 9), 39: (8, 9), 40: (9, 9), 43: (12, 7), 44: (13, 8),
        45: (14, 9), 46: (15, 9), 48: (17, 9), 50: (19, 9), 51: (20, 9),
        53: (22, 9), 55: (24, 9), 57: (26, 9),
    }
    for row, (numerator, denominator) in common_size_rows.items():
        formulas[row] = lambda p, n=numerator, d=denominator: p.get(INCOME, n) / p.get(INCOME, d)
    return formulas


def _carry_assumption(period: Period, row: int) -> float:
    if period.index == 0:
        return period.input(BALANCE, row)
    return period.get(BALANCE, row, -1)


def _historical_ratio(period: Period, row: int) -> float:
    if period.index == 0:
        return sum(period.get(BALANCE, row, offset) for offset in range(-5, 0)) / 5
    return period.get(BALANCE, row, -1)


def balance_formulas() -> dict[int, Formula]:
    return {
        8: lambda p: p.get(CASH, 56),
        9: lambda p: p.get(SCHEDULES, 116),
        10: lambda p: p.get(BALANCE, 59) * p.get(INCOME, 9),
        11: lambda p: p.get(SCHEDULES, 117),
        12: lambda p: p.get(BALANCE, 61) * p.get(INCOME, 9),
        13: lambda p: _sum_rows(p, BALANCE, range(8, 13)),
        16: lambda p: p.get(SCHEDULES, 50),
        17: lambda p: p.get(BALANCE, 17, -1) + p.get(BALANCE, 66),
        19: lambda p: p.get(BALANCE, 68) * p.get(INCOME, 9),
        20: lambda p: _sum_rows(p, BALANCE, range(16, 20)),
        22: lambda p: _sum_rows(p, BALANCE, (13, 20)),
        26: lambda p: p.get(SCHEDULES, 88),
        27: lambda p: p.get(SCHEDULES, 118),
        28: lambda p: p.get(BALANCE, 77) * p.get(INCOME, 9),
        29: lambda p: p.get(BALANCE, 78) * p.get(INCOME, 9),
        30: lambda p: _sum_rows(p, BALANCE, range(26, 30)),
        33: lambda p: p.get(SCHEDULES, 89),
        34: lambda p: p.get(BALANCE, 83) * p.get(INCOME, 9),
        35: lambda p: _sum_rows(p, BALANCE, (33, 34)),
        37: lambda p: _sum_rows(p, BALANCE, (30, 35)),
        40: lambda p: p.get(EQUITY, 45),
        41: lambda p: p.get(EQUITY, 32),
        42: lambda p: p.get(EQUITY, 13),
        43: lambda p: p.get(EQUITY, 22),
        44: lambda p: p.get(EQUITY, 41),
        45: lambda p: _sum_rows(p, BALANCE, range(40, 45)),
        47: lambda p: _sum_rows(p, BALANCE, (37, 45)),
        49: lambda p: p.get(BALANCE, 22) - p.get(BALANCE, 47),
        58: lambda p: p.get(SCHEDULES, 110),
        59: lambda p: _carry_assumption(p, 59),
        60: lambda p: p.get(SCHEDULES, 111),
        68: lambda p: _historical_ratio(p, 68),
        75: lambda p: p.get(SCHEDULES, 88),
        76: lambda p: p.get(SCHEDULES, 112),
        77: lambda p: _historical_ratio(p, 77),
        83: lambda p: _carry_assumption(p, 83),
    }


def cash_flow_formulas() -> dict[int, Formula]:
    asset_changes = {8: 9, 9: 10, 10: 11, 11: 12, 15: 16, 16: 17, 17: 18, 18: 19}
    liability_and_equity_changes = {25: 26, 26: 27, 27: 28, 28: 29, 32: 33, 33: 34, 39: 40, 40: 41, 43: 44}
    # These rows subtract empty presentation cells on the balance sheet.
    blank_movements = (13, 14, 30, 31, 35, 37, 38)
    movements: dict[int, Formula] = {row: lambda _p: 0.0 for row in blank_movements}
    for row, balance_row in asset_changes.items():
        movements[row] = lambda p, r=balance_row: p.get(BALANCE, r, -1) - p.get(BALANCE, r)
    for row, balance_row in liability_and_equity_changes.items():
        movements[row] = lambda p, r=balance_row: p.get(BALANCE, r) - p.get(BALANCE, r, -1)

    return {
        3: lambda p: p.get(INCOME, 26),
        **movements,
        48: lambda p: p.get(CASH, 3) + _sum_rows(p, CASH, sorted(movements)),
        50: lambda p: p.get(EQUITY, 10),
        51: lambda p: p.get(EQUITY, 18),
        53: lambda p: _sum_rows(p, CASH, (48, 50, 51)),
        55: lambda p: p.get(BALANCE, 8, -1),
        56: lambda p: _sum_rows(p, CASH, (53, 55)),
    }
