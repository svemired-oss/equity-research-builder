"""Select Oracle's nonoverlapping product lines from the ticker evidence packet."""

from dataclasses import asdict
from datetime import date
from math import fsum, isfinite

from .reconciliation import TOLERANCE_MILLIONS
from .revenue_forecast import RevenueBase, RevenueGrowth, forecast_revenue


_PRODUCT = "srt:ProductOrServiceAxis"
_SEGMENT = "us-gaap:StatementBusinessSegmentsAxis"
_LINES = {
    "cloud_infrastructure": (_PRODUCT, "CloudInfrastructureMember"),
    "cloud_applications": (_PRODUCT, "CloudApplicationsMember"),
    "software_license": (_PRODUCT, "SoftwareLicenseMember"),
    "software_support": (_PRODUCT, "SoftwareSupportMember"),
    "hardware": (_SEGMENT, "HardwareBusinessMember"),
    "services": (_SEGMENT, "ServicesBusinessMember"),
}
_OCI = "cloud_infrastructure"
_NON_OCI_LINES = tuple(line for line in _LINES if line != _OCI)
_MILLION = 1_000_000


def _matching_lines(facts: list[dict], period: dict) -> dict[str, list[dict]]:
    candidates = {key: [] for key in _LINES}
    for fact in facts:
        if (fact.get("accession") != period["filing"]["accession"]
                or fact.get("period_start") != period["period_start"]
                or fact.get("period_end") != period["period_end"]
                or fact.get("unit") != "USD"):
            continue
        for key, (axis, member) in _LINES.items():
            expected_metric = "segment_revenue" if axis == _SEGMENT else "product_or_service_revenue"
            if fact["metric"] == expected_metric and any(
                dimension["axis"] == axis and dimension["member"].rpartition("}")[2] == member
                for dimension in fact.get("dimensions", ())
            ):
                candidates[key].append(fact)
    return candidates


def _period_basis(operating: dict, period: dict) -> tuple[dict | None, list[str]]:
    end = period["period_end"]
    source = period.get("sources", {}).get("revenue", {})
    total = period.get("metrics", {}).get("revenue")
    if (not period.get("period_start") or not period.get("filing")
            or source.get("accession") != period["filing"]["accession"]
            or source.get("unit") != "USD"
            or type(total) not in (int, float) or not isfinite(total)):
        return None, [f"{end}: consolidated revenue and dimensional lines require the same reporting period and accession."]

    lines = _matching_lines(operating["observations"], period)
    conflicting = _matching_lines(operating["conflicts"], period)
    selected, issues = {}, []
    for key, candidates in lines.items():
        amounts = {fact["value"] for fact in candidates}
        if len(amounts) != 1 or conflicting[key]:
            reason = "conflicting" if conflicting[key] or len(amounts) > 1 else "unavailable"
            issues.append(f"{end}: {key} revenue {reason}.")
            continue
        amount = next(iter(amounts))
        if not isfinite(amount) or amount < 0:
            issues.append(f"{end}: {key} revenue is invalid for Oracle's operating revenue basis.")
            continue
        selected[key] = {"usd": amount, "sources": candidates}
    if issues:
        return None, issues
    difference = fsum(line["usd"] for line in selected.values()) - total
    if abs(difference / 1_000_000) > TOLERANCE_MILLIONS:
        return None, [f"{end}: Oracle's six operating revenue lines differ from consolidated revenue by {difference:,.0f} USD."]
    return {
        "period_start": period["period_start"], "period_end": end,
        "consolidated_revenue_usd": total, "consolidated_revenue_source": source, "lines": selected,
    }, []


def _comparison_basis(operating: dict, current: dict) -> tuple[dict | None, list[str]]:
    accession = current["filing"]["accession"]
    reported = [
        fact for fact in operating["observations"]
        if fact["metric"] == "consolidated_revenue" and fact["accession"] == accession and fact.get("comparison")
    ]
    values = {(fact["period_start"], fact["period_end"], fact["value"]) for fact in reported}
    conflicting = any(
        fact["metric"] == "consolidated_revenue" and fact["accession"] == accession and fact.get("comparison")
        for fact in operating["conflicts"]
    )
    if len(values) != 1 or conflicting:
        return None, [f"{current['period_end']}: no unambiguous comparative consolidated revenue in {accession}."]
    source = reported[0]
    prior = {
        "period_start": source["period_start"], "period_end": source["period_end"], "filing": current["filing"],
        "metrics": {"revenue": source["value"]}, "sources": {"revenue": source},
    }
    return _period_basis(operating, prior)


def build_oracle_revenue_basis(packet: dict) -> dict:
    """Validate the latest annual and fiscal-YTD revenue drivers for Oracle."""
    if packet["issuer"]["cik"].lstrip("0") != "1341439":
        raise ValueError("Oracle revenue definitions apply to Oracle's SEC filer only.")
    result = {
        "status": "incomplete", "annual": None, "interim": None, "missing": [],
        "comparatives": {"annual": None, "interim": None, "missing": []},
    }
    for key, field in (("annual", "actuals"), ("interim", "interim_actuals")):
        data = packet[field]
        if not data["periods"]:
            if key == "annual" or data["status"] != "superseded_by_annual":
                result["missing"].append(f"No reported {key} period is available for Oracle's revenue build.")
            continue
        period = data["periods"][-1]
        basis, missing = _period_basis(packet["operating_facts"], period)
        result[key] = basis
        result["missing"].extend(missing)
        if basis:
            prior, missing = _comparison_basis(packet["operating_facts"], period)
            result["comparatives"][key] = prior
            result["comparatives"]["missing"].extend(missing)
    if not result["missing"]:
        result["status"] = "available"
    return result


def _guidance_anchor(packet: dict, fiscal_year: int) -> tuple[dict | None, str | None]:
    dated = [
        row for field in ("revenue_guidance", "event_revenue_guidance")
        for row in packet[field]["observations"]
        if row["fiscal_year"] == fiscal_year and row["currency"] == "USD" and row["filed_on"] <= packet["as_of"]
    ]
    if not dated:
        return None, f"FY{fiscal_year}: no usable consolidated USD revenue guidance."
    latest = max(row["filed_on"] for row in dated)
    conflicts = [
        row for field in ("revenue_guidance", "event_revenue_guidance")
        for row in packet[field]["conflicts"]
        if row["fiscal_year"] == fiscal_year and row["currency"] == "USD" and latest <= row["filed_on"] <= packet["as_of"]
    ]
    if conflicts:
        return None, f"FY{fiscal_year}: the latest revenue guidance conflicts."
    latest_rows = [row for row in dated if row["filed_on"] == latest]
    limits = [(row["point"], row["point"]) if row["kind"] == "point" else (row["lower"], row["upper"])
              for row in latest_rows]
    lower = max((lo for lo, _ in limits if lo is not None), default=None)
    upper = min((hi for _, hi in limits if hi is not None), default=None)
    if lower is None or lower <= 0 or (upper is not None and upper < lower):
        return None, f"FY{fiscal_year}: the latest guidance lacks a usable lower bound or its limits conflict."
    value = lower if upper is None else (lower + upper) / 2
    method = "published floor used as the reference case" if upper is None else "midpoint of the latest compatible guidance"
    return {"usd_millions": value / _MILLION, "method": method, "sources": latest_rows}, None


def _revenue_anchors(packet: dict, base_end: date) -> tuple[dict, list[str]]:
    anchors, missing = {}, []
    for year in (base_end.year + 1, base_end.year + 4):
        anchor, issue = _guidance_anchor(packet, year)
        if issue:
            missing.append(issue)
        else:
            anchors[year] = anchor
    return anchors, missing


def _quarter_guidance_checks(packet: dict, interim: dict, first_year: int, first: dict) -> list[dict]:
    """Check that the annual case leaves room for the published next-quarter ranges."""
    if interim["period_end"] != f"{first_year - 1}-08-31":
        return []
    actuals = {line: row["usd"] / _MILLION for line, row in interim["lines"].items()}
    metrics = {
        "consolidated_revenue": (fsum(first.values()), interim["consolidated_revenue_usd"] / _MILLION),
        "cloud_revenue": (first[_OCI] + first["cloud_applications"], actuals[_OCI] + actuals["cloud_applications"]),
    }
    checks = []
    for guide in packet.get("quarter_revenue_guidance", {}).get("observations", []):
        if guide["fiscal_year"] != first_year or guide["quarter"] != 2 or guide["metric"] not in metrics:
            continue
        if "lower_revenue_usd" not in guide or "upper_revenue_usd" not in guide:
            continue
        full_year, first_quarter = metrics[guide["metric"]]
        lower = guide["lower_revenue_usd"] / _MILLION
        upper = guide["upper_revenue_usd"] / _MILLION
        checks.append({
            "metric": guide["metric"],
            "source": guide,
            "implied_second_half_at_q2_low_millions": full_year - first_quarter - lower,
            "implied_second_half_at_q2_high_millions": full_year - first_quarter - upper,
            "passes_nonnegative_second_half": full_year >= first_quarter + upper,
        })
    return checks


def build_oracle_revenue_forecast(packet: dict, basis: dict) -> dict:
    """Build a guidance-anchored reference case and expose its forecast assumptions."""
    annual, interim = basis["annual"], basis["interim"]
    prior_annual = basis["comparatives"]["annual"]
    prior_interim = basis["comparatives"]["interim"]
    result = {"status": "incomplete", "years": [], "assumptions": [], "guidance_anchors": {}, "missing": []}
    if not all((annual, interim, prior_annual, prior_interim)):
        result["missing"].append("Comparable annual and fiscal-YTD Oracle operating revenue lines are required.")
        return result
    base_end = date.fromisoformat(annual["period_end"])
    interim_end = date.fromisoformat(interim["period_end"])
    if (base_end.month, base_end.day) != (5, 31) or not base_end < interim_end < date(base_end.year + 1, 5, 31):
        result["missing"].append("The Oracle revenue forecast requires the latest May fiscal year and a subsequent fiscal-YTD filing.")
        return result
    first_year = base_end.year + 1
    long_year = first_year + 3
    result["guidance_anchors"], result["missing"] = _revenue_anchors(packet, base_end)
    if result["missing"]:
        return result

    annual_amounts, prior_annual_amounts, ytd, prior_ytd = (
        {key: line["usd"] / _MILLION for key, line in period["lines"].items()}
        for period in (annual, prior_annual, interim, prior_interim)
    )
    if any(amounts[key] <= 0 for amounts in (annual_amounts, prior_annual_amounts, prior_ytd) for key in _LINES):
        result["missing"].append("A revenue line lacks a positive prior-period base for computing its growth.")
        return result
    if any(prior_ytd[key] > annual_amounts[key] for key in _LINES):
        result["missing"].append("A prior-year interim line exceeds its matching full-year revenue.")
        return result

    historical_growth = {line: annual_amounts[line] / prior_annual_amounts[line] - 1 for line in _NON_OCI_LINES}
    observed_growth = {line: ytd[line] / prior_ytd[line] - 1 for line in _NON_OCI_LINES}
    first_amounts = {
        line: ytd[line] + (annual_amounts[line] - prior_ytd[line]) * (1 + observed_growth[line])
        for line in _NON_OCI_LINES
    }
    annual_projections = [first_amounts]
    for _ in range(4):
        annual_projections.append({
            line: annual_projections[-1][line] * (1 + historical_growth[line])
            for line in _NON_OCI_LINES
        })

    implied_oci = {
        index: result["guidance_anchors"][year]["usd_millions"] - fsum(annual_projections[index].values())
        for index, year in ((0, first_year), (3, long_year))
    }
    if implied_oci[0] < ytd[_OCI] or min(implied_oci.values()) <= 0:
        result["missing"].append("The revenue anchors imply less OCI revenue than reported fiscal-YTD actuals or the projected non-OCI lines.")
        return result
    implied_oci_growth = (implied_oci[3] / implied_oci[0]) ** (1 / 3) - 1
    other_revenue_growth = fsum(annual_projections[4].values()) / fsum(annual_projections[3].values()) - 1
    final_oci_growth = (implied_oci_growth + other_revenue_growth) / 2
    for index, projection in enumerate(annual_projections):
        if index in implied_oci:
            projection[_OCI] = implied_oci[index]
        elif index < 3:
            projection[_OCI] = annual_projections[index - 1][_OCI] * (1 + implied_oci_growth)
        else:
            projection[_OCI] = annual_projections[index - 1][_OCI] * (1 + final_oci_growth)

    assumptions = []
    previous = annual_amounts
    for offset, amounts in enumerate(annual_projections):
        end = date(first_year + offset, 5, 31)
        growth = {key: amounts[key] / previous[key] - 1 for key in _LINES}
        assumptions.append(RevenueGrowth(end, growth))
        previous = amounts
    base = RevenueBase(base_end, annual["consolidated_revenue_usd"] / _MILLION, annual_amounts)
    forecast = forecast_revenue(base, assumptions)
    result["quarter_guidance_checks"] = _quarter_guidance_checks(packet, interim, first_year, annual_projections[0])
    if any(not check["passes_nonnegative_second_half"] for check in result["quarter_guidance_checks"]):
        result["missing"].append("The first-year revenue assumption does not accommodate the complete published Q2 range.")
        return result
    result["status"] = "reference_case"
    result["years"] = [{**asdict(year), "period_end": year.period_end.isoformat()} for year in forecast.years]
    result["assumptions"] = [{**asdict(row), "period_end": row.period_end.isoformat()} for row in assumptions]
    result["method"] = {
        "non_oci_first_year": "Reported fiscal-YTD revenue plus prior-year remaining revenue grown at each line's observed YTD rate.",
        "non_oci_later_years": "Each line carries its most recent reported full-year growth rate.",
        "oci_anchored_years": "Subtract the other five modeled lines from the selected consolidated guidance references.",
        "oci_interim_years": "Use a constant compound growth rate between the two implied OCI revenue amounts.",
        "oci_final_year": "Move halfway from the implied OCI compound rate toward the modeled growth of the other five lines.",
        "limitations": "This is an analyst reference case. Management does not provide its segment allocation, interpolated path or final-year rate. Commissioning and RPO conversion still need a separate forecast check.",
    }
    return result
