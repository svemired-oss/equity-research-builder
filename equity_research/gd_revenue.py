"""Build GD's aerospace and defense segments from their operating drivers."""

from collections.abc import Callable

from .gd_projection_types import Period, excel_round


SHEET = "Revenue model"

# Active driver row: (Cover selector row, base assumption row, bull/bear spread).
SCENARIO_DRIVERS = {
    29: (14, 93, 0.005),   # Marine submarine revenue growth
    30: (14, 97, 0.005),   # Marine surface ship revenue growth
    33: (15, 101, 0.001),  # Marine operating margin
    42: (16, 107, 0.005),  # Combat military vehicle revenue growth
    43: (16, 111, 0.005),  # Combat weapons revenue growth
    44: (16, 115, 0.005),  # Combat engineering revenue growth
    46: (17, 119, 0.001),  # Combat operating margin
    54: (18, 125, 0.005),  # Technologies IT services revenue growth
    55: (18, 129, 0.005),  # Technologies C5ISR revenue growth
    57: (19, 133, 0.001),  # Technologies operating margin
}


def _value(period: Period, row: int, offset: int = 0) -> float:
    return period.get(SHEET, row, offset)


def _grow(row: int, growth_row: int) -> Callable[[Period], float]:
    return lambda period: _value(period, row, -1) * (1 + _value(period, growth_row))


def _sum_rows(*rows: int) -> Callable[[Period], float]:
    return lambda period: sum(_value(period, row) for row in rows)


def _deliveries(period: Period) -> float:
    units = _value(period, 13, -1) * (1 + _value(period, 14))
    return excel_round(units)


def _aircraft_price_growth(period: Period) -> float:
    if period.index in (0, 2, 3):
        return period.input(SHEET, 16)
    return _value(period, 16, -1)


def _aircraft_services_growth(period: Period) -> float:
    if period.index == 0:
        return period.input(SHEET, 18)
    return _value(period, 18, -1)


def _defense_revenue_growth(period: Period) -> float:
    segments = (26, 39, 51)
    current = sum(_value(period, row) for row in segments)
    prior = sum(_value(period, row, -1) for row in segments)
    return current / prior - 1


def _case(selector_row: int, base_row: int) -> Callable[[Period], float]:
    return lambda period: _value(period, base_row + period.selector(selector_row) - 2)


def _scenario(base_row: int, spread: float) -> Callable[[Period], float]:
    return lambda period: _value(period, base_row) + spread


def revenue_formulas() -> dict[int, Callable[[Period], float]]:
    """Map calculated forecast rows, leaving blank forward backlog unavailable."""
    formulas = {
        # Aerospace manufacturing, services, and operating earnings.
        8: lambda p: _value(p, 13) * _value(p, 15),
        9: _grow(9, 18),
        10: _sum_rows(8, 9),
        13: _deliveries,
        15: _grow(15, 16),
        16: _aircraft_price_growth,
        17: lambda p: _value(p, 8) / _value(p, 8, -1) - 1,
        18: _aircraft_services_growth,
        19: lambda p: _value(p, 10) * _value(p, 20),
        # Marine.
        23: _grow(23, 29),
        24: _grow(24, 30),
        25: _grow(25, 32),
        26: _sum_rows(23, 24, 25),
        31: lambda p: _value(p, 26) * _value(p, 33),
        # Combat.
        36: _grow(36, 42),
        37: _grow(37, 43),
        38: _grow(38, 44),
        39: _sum_rows(36, 37, 38),
        45: lambda p: _value(p, 39) * _value(p, 46),
        # Technologies.
        49: _grow(49, 54),
        50: _grow(50, 55),
        51: _sum_rows(49, 50),
        56: lambda p: _value(p, 51) * _value(p, 57),
        # Consolidated revenue and operating earnings.
        60: lambda p: _value(p, 10),
        61: lambda p: _value(p, 26),
        62: lambda p: _value(p, 39),
        63: lambda p: _value(p, 51),
        64: _sum_rows(60, 61, 62, 63),
        66: lambda p: _value(p, 64) / _value(p, 64, -1) - 1,
        69: lambda p: _value(p, 19),
        70: lambda p: _value(p, 31),
        71: lambda p: _value(p, 45),
        72: lambda p: _value(p, 56),
        74: _sum_rows(69, 70, 71, 72, 73),
        75: lambda p: _value(p, 74) / _value(p, 64),
        78: _defense_revenue_growth,
    }

    for row, (selector_row, base_row, spread) in SCENARIO_DRIVERS.items():
        formulas[row] = _case(selector_row, base_row)
        formulas[base_row - 1] = _scenario(base_row, spread)
        formulas[base_row + 1] = _scenario(base_row, -spread)

    return formulas
