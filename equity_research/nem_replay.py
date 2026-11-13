"""Replay FY2026 revenue and production costs from the private Newmont model."""

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path
import re

from openpyxl import load_workbook

from .nem_revenue import CommodityAssumptions, RevenueAssumptions, RevenueForecast, forecast_nem_revenue


BASELINE_SHA256 = "0dd2bd138c4acb803cc5325df1389d2b2779b890aa9fa3a7ffc045191164969d"
REPLAY_TOLERANCE = 1e-6  # In each source cell's native units.

# Revenue row, sold-volume row, price row, cost row, unit-cost row, production row.
COMMODITY_ROWS = {
    "gold": (4, 15, 24, 3, 12, 44),
    "silver": (5, 16, 25, 4, 17, 48),
    "copper": (6, 17, 26, 5, 18, 53),
    "zinc": (7, 18, 27, 6, 20, 58),
    "lead": (8, 19, 28, 7, 19, 62),
}


@dataclass(frozen=True)
class NEMReplay:
    workbook_sha256: str
    base: RevenueForecast
    scenario: RevenueForecast
    gold_price_base_usd_per_oz: float
    gold_price_scenario_usd_per_oz: float
    cells_matched: int
    known_issues: tuple[str, ...]


def _read_cells(contents: bytes, *, data_only: bool) -> dict[str, object]:
    workbook = load_workbook(BytesIO(contents), data_only=data_only, read_only=True, keep_links=False)
    try:
        cells = {}
        for sheet, address in (
            ("Cover", "I14:I15"),
            ("Revenue", "B3:N65"),
            ("Cost of sales", "A1:N62"),
            ("Income statement", "H2:H4"),
            ("DCF", "D14:D14"),
        ):
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


def _formula_number(cells: dict[str, object], address: str, pattern: str) -> float:
    value = cells.get(address)
    match = re.fullmatch(pattern, value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"Unexpected workbook formula in {address}: {value!r}.")
    return float(match.group(1))


def _compare(address: str, calculated: float, saved: float) -> None:
    if not isclose(calculated, saved, rel_tol=1e-12, abs_tol=REPLAY_TOLERANCE):
        raise ValueError(f"{address} disagrees with the replay: calculated {calculated}, saved {saved}.")


def replay_nem(path: Path, *, gold_price_change_usd: float = 200.0) -> NEMReplay:
    """Rebuild source formula results, then shock gold price with costs held fixed.

    Gold quantities are thousand troy ounces. Silver quantities are million
    troy ounces, and copper, zinc and lead quantities are million pounds.
    Forecast caches provide comparison values only. The extraction retains
    the archived production-as-sales assumption for gold and the production
    basis of the workbook's cost build.
    """
    contents = path.read_bytes()
    digest = sha256(contents).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError(
            "The workbook does not match the private Newmont portfolio copy "
            f"(SHA-256 {digest}). Use the original portfolio workbook for this replay."
        )
    if type(gold_price_change_usd) not in (int, float) or not isfinite(gold_price_change_usd):
        raise ValueError("Gold price change must be a finite number.")

    source = _read_cells(contents, data_only=False)
    saved = _read_cells(contents, data_only=True)
    period_end = source.get("DCF!D14")
    if not isinstance(period_end, datetime) or period_end.date().isoformat() != "2026-12-31":
        raise ValueError("The Newmont private replay requires the FY2026 forecast period.")
    if source.get("Revenue!H3") != "2026E" or source.get("Income statement!H2") != "2026E":
        raise ValueError("The saved forecast columns disagree with the Newmont FY2026 replay.")
    for selector in ("Cover!I14", "Cover!I15"):
        if _number(source, selector) != 2:
            raise ValueError(f"The archived Newmont base case requires {selector}=2.")

    compared = {}
    mine_addresses = [f"Cost of sales!H{row}" for row in range(25, 44)]
    blank_mines = [address for address in mine_addresses if address not in source]
    # SUM ignores source blanks. Preserve those blanks as a known coverage issue.
    gold_production = sum(_number(source, address) for address in mine_addresses if address in source)
    compared["Cost of sales!H44"] = gold_production
    compared["Revenue!H15"] = gold_production

    sold_volumes = {"gold": gold_production}
    for commodity, row in (("silver", 47), ("copper", 56), ("zinc", 61), ("lead", 65)):
        sold_volumes[commodity] = _number(source, f"Revenue!H{row}")

    gold_base = _number(source, "Revenue!H31")
    bull_change = _formula_number(source, "Revenue!H30", r"=H31\+(\d+(?:\.\d+)?)")
    bear_change = _formula_number(source, "Revenue!H32", r"=H31-(\d+(?:\.\d+)?)")
    compared["Revenue!H30"] = gold_base + bull_change
    compared["Revenue!H32"] = gold_base - bear_change
    silver_spot = _formula_number(source, "Revenue!H25", r"=\+\((\d+(?:\.\d+)?)\+H37\)/2")
    copper_spot = _formula_number(source, "Revenue!H26", r"=\+\((\d+(?:\.\d+)?)\+H38\)/2")
    zinc_factor = _formula_number(source, "Revenue!H27", r"=\+H40\*(\d+(?:\.\d+)?)%") / 100
    lead_factor = _formula_number(source, "Revenue!H28", r"=\+H39\*(\d+(?:\.\d+)?)%") / 100
    prices = {
        "gold": gold_base,
        "silver": (silver_spot + _number(source, "Revenue!H37")) / 2,
        "copper": (copper_spot + _number(source, "Revenue!H38")) / 2,
        "zinc": _number(source, "Revenue!H40") * zinc_factor,
        "lead": _number(source, "Revenue!H39") * lead_factor,
    }

    gold_cost_growth = _number(source, "Cost of sales!H15")
    compared["Cost of sales!H13"] = gold_cost_growth
    bull_cost_change = _formula_number(source, "Cost of sales!H14", r"=H15-(\d+(?:\.\d+)?)")
    bear_cost_change = _formula_number(source, "Cost of sales!H16", r"=H15\+(\d+(?:\.\d+)?)")
    compared["Cost of sales!H14"] = gold_cost_growth - bull_cost_change
    compared["Cost of sales!H16"] = gold_cost_growth + bear_cost_change
    unit_costs = {"gold": _number(source, "Cost of sales!G12") * (1 + gold_cost_growth)}
    production_volumes = {"gold": gold_production}
    for commodity in ("silver", "copper", "zinc", "lead"):
        _, _, _, cost_row, unit_cost_row, production_row = COMMODITY_ROWS[commodity]
        history = []
        for column in "BCDEF":
            cost = _number(source, f"Cost of sales!{column}{cost_row}")
            quantity = _number(source, f"Cost of sales!{column}{production_row}")
            if quantity <= 0:
                raise ValueError(f"Historical {commodity} production must be positive for a unit cost.")
            unit_cost = cost / quantity
            compared[f"Cost of sales!{column}{unit_cost_row}"] = unit_cost
            history.append(unit_cost)
        unit_costs[commodity] = sum(history) / len(history)
        compared[f"Cost of sales!G{unit_cost_row}"] = unit_costs[commodity]
        production_address = "Cost of sales!G48" if commodity == "silver" else f"Cost of sales!H{production_row}"
        production_volumes[commodity] = _number(source, production_address)

    commodities = {
        commodity: CommodityAssumptions(
            production_volume=production_volumes[commodity],
            sold_volume=sold_volumes[commodity],
            price_usd_per_unit=prices[commodity],
            production_cost_usd_per_unit=unit_costs[commodity],
        )
        for commodity in COMMODITY_ROWS
    }
    assumptions = RevenueAssumptions(period_end=period_end.date(), commodities=commodities)
    base = forecast_nem_revenue(assumptions)
    for commodity, (revenue_row, sold_row, price_row, cost_row, unit_cost_row, production_row) in COMMODITY_ROWS.items():
        line = base.commodities[commodity]
        compared[f"Revenue!H{revenue_row}"] = line.revenue_millions
        compared[f"Revenue!H{sold_row}"] = line.sold_volume
        compared[f"Revenue!H{price_row}"] = line.price_usd_per_unit
        compared[f"Cost of sales!H{cost_row}"] = line.production_cost_millions
        compared[f"Cost of sales!H{unit_cost_row}"] = line.production_cost_usd_per_unit
        compared[f"Cost of sales!H{production_row}"] = line.production_volume
    compared.update({
        "Revenue!H9": base.total_revenue_millions,
        "Cost of sales!H8": base.total_production_cost_millions,
        "Income statement!H3": base.total_revenue_millions,
        "Income statement!H4": base.total_production_cost_millions,
    })
    for address, calculated in compared.items():
        _compare(address, calculated, _number(saved, address))

    scenario_price = gold_base + gold_price_change_usd
    scenario = forecast_nem_revenue(replace(assumptions, commodities={
        **commodities, "gold": replace(commodities["gold"], price_usd_per_unit=scenario_price),
    }))
    expected_change = gold_production * gold_price_change_usd / 1000
    _compare("Gold price scenario revenue change", scenario.total_revenue_millions - base.total_revenue_millions, expected_change)
    _compare("Gold price scenario production costs", scenario.total_production_cost_millions, base.total_production_cost_millions)

    known_issues = [
        "Revenue!H15 uses Cost of sales!H44 gold production directly as sales. "
        "The archived replay retains this shortcut and needs a production-to-sales bridge for fresh research.",
        "Byproduct sales and production volumes differ. Production costs require an inventory and cost-to-sales "
        "bridge before being used for forecast earnings.",
        "Cost of sales!A24 labels gold production as million ounces, but H44 and the /1000 cost conversion "
        "use thousand troy ounces. Revenue!A13 says thousands above silver, which uses million troy ounces. "
        "The base-metal tables say Ounces Sold, while their prices and formulas use million pounds.",
        "Revenue!N31 cites a futures page for the gold curve. Other metal prices blend fixed spot proxies "
        "and report assumptions or apply discounts. Their source dates and realized-price basis need verification.",
        "Mine ownership, consolidated versus attributable production, and asset divestiture assumptions are unverified workbook inputs.",
        "Byproduct unit costs use an unweighted average of 2020-2024 historical production cost ratios. "
        "They remain fixed in the archived FY2026 forecast.",
    ]
    if blank_mines:
        known_issues.append(
            "The mine production SUM ignores source blanks at " + ", ".join(blank_mines)
            + ". These remain missing source entries rather than verified zero production."
        )
    return NEMReplay(
        workbook_sha256=digest,
        base=base,
        scenario=scenario,
        gold_price_base_usd_per_oz=gold_base,
        gold_price_scenario_usd_per_oz=scenario_price,
        cells_matched=len(compared),
        known_issues=tuple(known_issues),
    )
