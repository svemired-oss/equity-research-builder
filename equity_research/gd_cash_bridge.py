"""Roll GD cash, carrying debt and shares across a dated valuation cutoff."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .gd_inputs import InputSource, _day, _fields, _number, _read, _source
from .gd_run_inputs import _header
from .valuation import FcfeStub


FCFE_FIELDS = {
    "cash_from_operations_millions", "capital_expenditures_millions",
    "debt_proceeds_millions", "debt_repayments_millions", "other_fcfe_cash_flows_millions",
}
BRIDGE_FIELDS = FCFE_FIELDS | {
    "dividends_paid_millions", "share_repurchases_millions", "share_issuance_proceeds_millions",
    "other_cash_movements_millions", "other_debt_carrying_change_millions", "net_share_change",
}
NONNEGATIVE_FIELDS = {
    "capital_expenditures_millions", "debt_proceeds_millions", "debt_repayments_millions",
    "dividends_paid_millions", "share_repurchases_millions", "share_issuance_proceeds_millions",
}


@dataclass(frozen=True)
class CitedAmount:
    amount: float
    source: InputSource


@dataclass(frozen=True)
class GDCashWindow:
    start_exclusive: date
    end_inclusive: date
    amounts: dict[str, CitedAmount]


@dataclass(frozen=True)
class GDCashInputs:
    as_of: date
    elapsed: GDCashWindow
    remaining: GDCashWindow


@dataclass(frozen=True)
class GDCashBalances:
    as_of: date
    cash_millions: float
    carrying_debt_millions: float
    shares_outstanding: int


@dataclass(frozen=True)
class GDCashBridge:
    opening: GDCashBalances
    elapsed: GDCashWindow
    remaining: GDCashWindow
    cutoff: GDCashBalances | None
    year_end: GDCashBalances | None
    remaining_fcfe: FcfeStub | None
    missing_inputs: tuple[str, ...]


def load_gd_cash_inputs(path: Path) -> GDCashInputs:
    """Load cited totals for two nonoverlapping windows. Omit unknown amounts."""
    item = _fields(_read(path), "GD cash bridge", {
        "schema_version", "ticker", "currency", "as_of", "elapsed", "remaining",
    })
    as_of = _header(item)

    def window(key: str) -> GDCashWindow:
        row = _fields(item[key], key, {"start_exclusive", "end_inclusive", "amounts"})
        start = _day(row["start_exclusive"], f"{key} start_exclusive")
        end = _day(row["end_inclusive"], f"{key} end_inclusive")
        amounts = {}
        for name, entry in _fields(row["amounts"], f"{key} amounts", set(), BRIDGE_FIELDS).items():
            label = f"{key}.{name}"
            fields = _fields(entry, label, {"amount", "source"})
            amount = _number(fields["amount"], label)
            if name in NONNEGATIVE_FIELDS and amount < 0:
                raise ValueError(f"{label} must be nonnegative.")
            if name == "net_share_change" and not amount.is_integer():
                raise ValueError(f"{label} must be a whole share count.")
            source = _source(fields["source"], label, as_of)
            if source.kind not in {"model_assumption", "derived", "sec_filing", "issuer_release"}:
                raise ValueError(f"{label} requires an independent filing, release, derivation or assumption.")
            amounts[name] = CitedAmount(amount, source)
        return GDCashWindow(start, end, amounts)

    return GDCashInputs(as_of, window("elapsed"), window("remaining"))


def _fcfe(amounts: dict[str, float]) -> float:
    return (
        amounts["cash_from_operations_millions"] - amounts["capital_expenditures_millions"]
        + amounts["debt_proceeds_millions"] - amounts["debt_repayments_millions"]
        + amounts["other_fcfe_cash_flows_millions"]
    )


def _roll(opening: GDCashBalances, end: date, amounts: dict[str, float]) -> GDCashBalances:
    cash = _number(
        opening.cash_millions + _fcfe(amounts) - amounts["dividends_paid_millions"]
        - amounts["share_repurchases_millions"] + amounts["share_issuance_proceeds_millions"]
        + amounts["other_cash_movements_millions"], "closing cash",
    )
    debt = _number(
        opening.carrying_debt_millions + amounts["debt_proceeds_millions"]
        - amounts["debt_repayments_millions"] + amounts["other_debt_carrying_change_millions"], "closing debt",
    )
    shares = opening.shares_outstanding + int(amounts["net_share_change"])
    if cash < 0 or debt < 0 or shares <= 0:
        raise ValueError(f"Balances at {end} require nonnegative cash and debt and positive outstanding shares.")
    return GDCashBalances(end, cash, debt, shares)


def build_gd_cash_bridge(
    opening: GDCashBalances, as_of: date, inputs: GDCashInputs | None = None,
) -> GDCashBridge:
    """Keep incomplete balances and FCFE unavailable until their inputs are present."""
    year_end = date(as_of.year, 12, 31)
    elapsed = inputs.elapsed if inputs else GDCashWindow(opening.as_of, as_of, {})
    remaining = inputs.remaining if inputs else GDCashWindow(as_of, year_end, {})
    if inputs and inputs.as_of != as_of:
        raise ValueError("The cash bridge and interim evidence must share the same cutoff.")
    if opening.as_of.year != as_of.year or opening.as_of > as_of or (
        elapsed.start_exclusive, elapsed.end_inclusive, remaining.start_exclusive, remaining.end_inclusive,
    ) != (opening.as_of, as_of, as_of, year_end):
        raise ValueError("Cash windows must run from the interim balance date through the cutoff, then through fiscal year-end.")

    missing = []
    for key, window in (("elapsed", elapsed), ("remaining", remaining)):
        if window.start_exclusive == window.end_inclusive:
            if window.amounts:
                raise ValueError(f"The empty {key} window cannot contain cash flows.")
        else:
            missing.extend(f"{key}.{name}" for name in sorted(BRIDGE_FIELDS - window.amounts.keys()))

    elapsed_amounts = {name: entry.amount for name, entry in elapsed.amounts.items()}
    remaining_amounts = {name: entry.amount for name, entry in remaining.amounts.items()}
    cutoff = None
    if as_of == opening.as_of:
        cutoff = opening
    elif BRIDGE_FIELDS <= elapsed_amounts.keys():
        cutoff = _roll(opening, as_of, elapsed_amounts)

    year_end_balances = None
    stub = None
    if as_of == year_end:
        year_end_balances = cutoff
    else:
        if FCFE_FIELDS <= remaining_amounts.keys():
            stub = FcfeStub(as_of, year_end, _number(_fcfe(remaining_amounts), "remaining-period FCFE"))
        if cutoff is not None and BRIDGE_FIELDS <= remaining_amounts.keys():
            year_end_balances = _roll(cutoff, year_end, remaining_amounts)

    return GDCashBridge(opening, elapsed, remaining, cutoff, year_end_balances, stub, tuple(missing))
