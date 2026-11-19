"""Fixed-asset schedule with explicit recognition and disposal movements."""

from dataclasses import dataclass
from math import fsum, isclose, isfinite


@dataclass(frozen=True)
class PPEBalance:
    """Property balances in millions, including land in gross in-service assets."""

    gross_in_service_millions: float
    construction_in_progress_millions: float
    accumulated_depreciation_millions: float

    @property
    def gross_millions(self) -> float:
        return fsum((self.gross_in_service_millions, self.construction_in_progress_millions))

    @property
    def net_millions(self) -> float:
        return fsum((self.gross_in_service_millions, self.construction_in_progress_millions,
                     -self.accumulated_depreciation_millions))


@dataclass(frozen=True)
class PPEMovements:
    """Positive amounts recognized during the period, in millions.

    Cash-funded additions exclude cash paid to settle assets already recognized
    in a previous period. Total cash capex belongs in the cash-flow schedule.
    """

    cash_additions_to_construction_millions: float
    noncash_additions_to_construction_millions: float
    cash_additions_to_in_service_millions: float
    noncash_additions_to_in_service_millions: float
    construction_commissioned_millions: float
    depreciation_millions: float
    gross_disposed_millions: float
    accumulated_depreciation_disposed_millions: float


@dataclass(frozen=True)
class PPERollForward:
    closing: PPEBalance
    cash_funded_additions_millions: float
    noncash_additions_millions: float
    total_additions_millions: float
    disposal_net_book_value_millions: float


_ROUNDING_TOLERANCE_MILLIONS = 1e-9


def _closing_amount(amount: float, message: str) -> float:
    if amount < -_ROUNDING_TOLERANCE_MILLIONS:
        raise ValueError(message)
    return max(amount, 0.0)


def roll_ppe(opening: PPEBalance, movements: PPEMovements) -> PPERollForward:
    """Close gross, construction and accumulated depreciation from explicit inputs."""
    for inputs in (opening, movements):
        for name, value in vars(inputs).items():
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, nonnegative amount.")
    if opening.accumulated_depreciation_millions > opening.gross_in_service_millions:
        raise ValueError("Opening accumulated depreciation exceeds gross in-service property.")

    closing_cip = _closing_amount(fsum((
        opening.construction_in_progress_millions,
        movements.cash_additions_to_construction_millions,
        movements.noncash_additions_to_construction_millions,
        -movements.construction_commissioned_millions,
    )), "Commissioning exceeds available construction in progress.")
    closing_in_service = _closing_amount(fsum((
        opening.gross_in_service_millions,
        movements.cash_additions_to_in_service_millions,
        movements.noncash_additions_to_in_service_millions,
        movements.construction_commissioned_millions,
        -movements.gross_disposed_millions,
    )), "Disposal exceeds available gross in-service property.")
    closing_accumulated = _closing_amount(fsum((
        opening.accumulated_depreciation_millions,
        movements.depreciation_millions,
        -movements.accumulated_depreciation_disposed_millions,
    )), "Disposal removes more accumulated depreciation than is available.")
    disposal_net = _closing_amount(
        movements.gross_disposed_millions - movements.accumulated_depreciation_disposed_millions,
        "Accumulated depreciation removed on disposal exceeds the disposed gross cost.",
    )
    if closing_accumulated - closing_in_service > _ROUNDING_TOLERANCE_MILLIONS:
        raise ValueError("Closing accumulated depreciation exceeds gross in-service property.")
    closing = PPEBalance(closing_in_service, closing_cip, min(closing_accumulated, closing_in_service))

    cash_additions = fsum((movements.cash_additions_to_construction_millions,
                           movements.cash_additions_to_in_service_millions))
    noncash_additions = fsum((movements.noncash_additions_to_construction_millions,
                              movements.noncash_additions_to_in_service_millions))
    total_additions = fsum((cash_additions, noncash_additions))
    expected_net = fsum((opening.net_millions, total_additions, -movements.depreciation_millions, -disposal_net))
    if not isclose(closing.net_millions, expected_net, rel_tol=1e-12, abs_tol=_ROUNDING_TOLERANCE_MILLIONS):
        raise ValueError("Net property disagrees with opening net property plus additions less depreciation and disposals.")
    return PPERollForward(closing, cash_additions, noncash_additions, total_additions, disposal_net)
