"""Replay FY2026 revenue from the private FedEx portfolio workbook."""

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path
import re

from openpyxl import load_workbook

from .fdx_revenue import (
    RevenueAssumptions,
    RevenueForecast,
    ServiceAssumptions,
    forecast_fdx_revenue,
)


BASELINE_SHA256 = "1d09c3b5d44f9db71a81c962320ab5722288ab526c2d531b4c655b17ae477e0d"
REPLAY_TOLERANCE = 1e-6  # In the workbook cell's native units.

# Volume, yield and daily revenue rows, followed by the two growth assumption rows.
SERVICE_ROWS = {
    "us_priority": (46, 47, 48, 97, 98),
    "us_deferred": (50, 51, 52, 101, 102),
    "us_ground": (54, 55, 56, 105, 110),
    "international_export": (58, 59, 60, 117, 121),
    "international_domestic": (62, 63, 64, 128, 129),
    "express_us_freight": (66, 67, 68, 132, 133),
    "express_international_freight": (70, 71, 72, 136, 137),
    "ltl_priority": (77, 78, 79, 143, 144),
    "ltl_economy": (81, 82, 83, 147, 148),
}


@dataclass(frozen=True)
class FDXReplay:
    workbook_sha256: str
    base: RevenueForecast
    scenario: RevenueForecast
    priority_yield_growth_base: float
    priority_yield_growth_scenario: float
    saved_segment_total_millions: float
    segment_gap_millions: float
    cells_matched: int
    known_issues: tuple[str, ...]


def _read_cells(contents: bytes, *, data_only: bool) -> dict[str, object]:
    workbook = load_workbook(
        BytesIO(contents), data_only=data_only, read_only=True, keep_links=False,
    )
    try:
        cells = {}
        for sheet, address in (("Revenue", "D1:J148"), ("Cover", "I11:I14"), ("DCF", "C14:C14")):
            for row in workbook[sheet][address]:
                for cell in row:
                    if cell.value is not None:
                        cells[f"{sheet}!{cell.coordinate}"] = cell.value
        return cells
    finally:
        workbook.close()


def _number(cells: dict[str, object], address: str) -> float:
    value = cells.get(address)
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"Expected a finite number in {address}, got {value!r}.")
    return float(value)


def _literal_terms(cells: dict[str, object], address: str, pattern: str) -> tuple[int, ...]:
    value = cells.get(address)
    match = re.fullmatch(pattern, value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"Unexpected workbook formula in {address}: {value!r}.")
    return tuple(int(term) for term in match.groups())


def _compare(label: str, expected: float, saved: float) -> None:
    if not isclose(expected, saved, rel_tol=1e-12, abs_tol=REPLAY_TOLERANCE):
        raise ValueError(f"{label} disagrees with the replay: calculated {expected}, expected {saved}.")


def replay_fdx(path: Path, *, priority_yield_growth: float = 0.025) -> FDXReplay:
    contents = path.read_bytes()
    digest = sha256(contents).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError(
            "The workbook does not match the private FedEx portfolio copy "
            f"(SHA-256 {digest}). Use the original portfolio workbook for this replay."
        )

    source = _read_cells(contents, data_only=False)
    saved = _read_cells(contents, data_only=True)
    period_end = source.get("DCF!C14")
    if not isinstance(period_end, datetime) or source.get("Revenue!J1") != "2026E":
        raise ValueError("The saved period and forecast column disagree with the FedEx FY2026 replay.")

    compared = {
        "G54": sum(_literal_terms(source, "Revenue!G54", r"=(\d+)\+(\d+)")),
        "G70": sum(_literal_terms(source, "Revenue!G70", r"=(\d+)\+(\d+)")),
    }
    for growth_row, selector_row in ((105, 11), (110, 12), (117, 13), (121, 14)):
        selection = _number(source, f"Cover!I{selector_row}")
        if selection not in (1, 2, 3):
            raise ValueError(f"Invalid scenario selector in Cover!I{selector_row}.")
        compared[f"J{growth_row}"] = _number(source, f"Revenue!J{growth_row + int(selection)}")

    for growth_row, columns in ((128, "EFGH"), (129, "EFGH"), (133, "DEFGH")):
        historical_growth = [_number(saved, f"Revenue!{column}{growth_row}") for column in columns]
        compared[f"J{growth_row}"] = sum(historical_growth) / len(historical_growth)

    operating_days = _number(source, "Revenue!J43")
    if not operating_days.is_integer():
        raise ValueError("Expected whole operating days in Revenue!J43.")
    ground_days, = _literal_terms(source, "Revenue!J14", r"=J56\*(\d+)/1000")
    quarterly_other, quarters = _literal_terms(source, "Revenue!J88", r"=(\d+)\*(\d+)")
    express_other = _number(source, "Revenue!J74")

    def assumption(address: str) -> float:
        if address in compared:
            return compared[address]
        return _number(source, f"Revenue!{address}")

    services = {
        key: ServiceAssumptions(
            prior_daily_volume_thousands=assumption(f"G{volume}"),
            prior_yield_usd=assumption(f"G{yield_}"),
            volume_growth=assumption(f"J{volume_growth}"),
            yield_growth=assumption(f"J{yield_growth}"),
        )
        for key, (volume, yield_, _, volume_growth, yield_growth) in SERVICE_ROWS.items()
    }
    assumptions = RevenueAssumptions(
        period_end=period_end.date(),
        operating_days=int(operating_days),
        services=services,
        express_other_millions=express_other,
        other_and_eliminations_millions=quarterly_other * quarters,
    )
    base = forecast_fdx_revenue(assumptions)

    for key, (volume, yield_, daily, _, _) in SERVICE_ROWS.items():
        service = base.services[key]
        compared[f"J{volume}"] = service.daily_volume_thousands
        compared[f"J{yield_}"] = service.yield_usd
        compared[f"J{daily}"] = service.daily_revenue_thousands

    annual = {key: service.revenue_millions for key, service in base.services.items()}
    ground_in_segment_view = base.services["us_ground"].daily_revenue_thousands * ground_days / 1000
    domestic_package = annual["us_priority"] + annual["us_deferred"] + ground_in_segment_view
    package_total = domestic_package + annual["international_export"] + annual["international_domestic"]
    express_freight = annual["express_us_freight"] + annual["express_international_freight"]
    express_segment_view = package_total + express_freight + express_other
    freight_segment = base.freight_segment_revenue_millions
    other = assumptions.other_and_eliminations_millions
    segment_total = express_segment_view + freight_segment + other
    compared.update({
        "J4": express_segment_view,
        "J5": freight_segment,
        "J6": other,
        "J7": segment_total,
        "J12": annual["us_priority"],
        "J13": annual["us_deferred"],
        "J14": ground_in_segment_view,
        "J15": domestic_package,
        "J18": annual["international_export"],
        "J19": annual["international_domestic"],
        "J20": package_total,
        "J23": annual["express_us_freight"],
        "J25": annual["express_international_freight"],
        "J26": express_freight,
        "J28": express_other,
        "J30": express_segment_view,
        "J31": express_segment_view / _number(saved, "Revenue!G30") - 1,
        "J32": freight_segment,
        "J33": freight_segment / _number(saved, "Revenue!G32") - 1,
        "J34": other,
        "J35": other / _number(saved, "Revenue!G34") - 1,
        "J36": base.total_revenue_millions,
        "J86": base.daily_revenue_thousands,
        "J87": base.annualized_service_revenue_millions,
        "J88": other,
        "J89": base.total_revenue_millions,
    })
    for cell, modeled in compared.items():
        _compare(f"Revenue!{cell}", modeled, _number(saved, f"Revenue!{cell}"))

    saved_segment_total = _number(saved, "Revenue!J7")
    segment_gap = saved_segment_total - base.total_revenue_millions
    _compare("Segment revenue gap", segment_gap, ground_in_segment_view - annual["us_ground"])
    _compare("Federal Express revenue", base.express_revenue_millions, express_segment_view - segment_gap)

    priority = services["us_priority"]
    scenario = forecast_fdx_revenue(replace(assumptions, services={
        **services, "us_priority": replace(priority, yield_growth=priority_yield_growth),
    }))
    expected_change = (
        base.services["us_priority"].daily_volume_thousands
        * priority.prior_yield_usd
        * (priority_yield_growth - priority.yield_growth)
        * assumptions.operating_days / 1000
    )
    _compare(
        "U.S. Priority yield scenario",
        scenario.total_revenue_millions - base.total_revenue_millions,
        expected_change,
    )

    return FDXReplay(
        workbook_sha256=digest,
        base=base,
        scenario=scenario,
        priority_yield_growth_base=priority.yield_growth,
        priority_yield_growth_scenario=priority_yield_growth,
        saved_segment_total_millions=saved_segment_total,
        segment_gap_millions=segment_gap,
        cells_matched=len(compared),
        known_issues=(
            f"Revenue!J14 uses {ground_days} days for U.S. Ground. Revenue!J43 uses "
            f"{assumptions.operating_days} days for the consolidated build, leaving "
            f"Revenue!J7 ${segment_gap:,.2f} million above Revenue!J89.",
            f"Revenue!J88 annualizes ${quarterly_other:,} million across {quarters} quarters. "
            "The underlying period and source need independent verification.",
            "The private FY2026 replay includes FedEx Freight and precedes the fiscal calendar change.",
        ),
    )
