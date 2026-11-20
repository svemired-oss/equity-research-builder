"""Read Oracle's filed next-quarter revenue guidance and apply the prior actuals."""

from datetime import date, timedelta
from decimal import Decimal
from math import isfinite
import re

from .filing_xbrl import load_filing_instance
from .sec_event_guidance import _EventText, _verify_url
from .sec_operating_facts import _observations


_CIK = "1341439"
_HEADING = re.compile(r"^Guidance for Q([1-4])\s*FY\s*(20\d{2}|\d{2})$", re.I)
_INTRO = re.compile(
    r"^Oracle is providing the following forward-looking guidance for Q[1-4]\s*FY\s*(?:20\d{2}|\d{2}):?$",
    re.I,
)
_RANGE = (
    r"(?:between|from)\s+(?P<{label}_low>-?\d+(?:\.\d+)?)\s*%?\s+(?:and|to)\s+"
    r"(?P<{label}_high>-?\d+(?:\.\d+)?)\s*%"
)
_USD_RANGE = _RANGE.format(label="usd")
_CC_RANGE = _RANGE.format(label="cc")
_OUTLOOK = re.compile(
    rf"^Total (?P<cloud>Cloud )?revenues? (?:is|are) expected to grow "
    rf"(?:{_CC_RANGE} in constant currency and (?:(?:is|are) expected to grow )?)?{_USD_RANGE}"
    r" in (?:(?:both )?constant currency and )?USD\.?$",
    re.I,
)
_OTHER_LINE = re.compile(r"^Non-GAAP (?:earnings per share|EPS)\b", re.I)
_BULLET = re.compile(r"^[•●▪\-]\s*")
_MAX_EXHIBIT_CHARS = 2_000_000
_CLOUD_AXIS = "srt:ProductOrServiceAxis"
_QUARTERS = ((6, 1, 8, 31), (9, 1, 11, 30), (12, 1, 2, 28), (3, 1, 5, 31))


def _quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    start_month, start_day, end_month, end_day = _QUARTERS[quarter - 1]
    start = date(year - (quarter <= 3), start_month, start_day)
    if quarter == 3:
        return start, date(year, 3, 1) - timedelta(days=1)
    return start, date(year - (quarter <= 2), end_month, end_day)


def parse_oracle_quarter_guidance(html: str, *, url: str, accession: str, filed_on: date, as_of: date) -> dict:
    """Extract USD ranges from the quarter guidance section in a verified Oracle exhibit."""
    _verify_url(url, _CIK, accession)
    if type(filed_on) is not date or type(as_of) is not date:
        raise ValueError("Oracle guidance requires dates for filing and valuation cutoff.")
    result = {"status": "missing", "observations": [], "missing": []}
    if filed_on > as_of or len(html) > _MAX_EXHIBIT_CHARS:
        result["missing"].append("Oracle exhibit is after the cutoff or exceeds the size limit.")
        return result

    scope = None
    rows = {}
    for block in _EventText(html).blocks:
        block = _BULLET.sub("", block)
        if not block:
            continue
        if heading := _HEADING.fullmatch(block):
            quarter = int(heading[1])
            year = int(heading[2])
            year += 2000 if year < 100 else 0
            start, end = _quarter_dates(year, quarter)
            scope = (year, quarter, block) if start <= filed_on <= end else None
            continue
        if not scope or _INTRO.fullmatch(block) or _OTHER_LINE.match(block):
            continue
        match = _OUTLOOK.fullmatch(block)
        if not match:
            scope = None
            continue
        low, high = Decimal(match["usd_low"]) / 100, Decimal(match["usd_high"]) / 100
        if not -1 <= low < high or not all(isfinite(float(rate)) for rate in (low, high)):
            continue
        year, quarter, heading_text = scope
        metric = "cloud_revenue" if match["cloud"] else "consolidated_revenue"
        rows.setdefault((year, quarter, metric), {})[(low, high)] = {
            "metric": metric,
            "fiscal_year": year,
            "quarter": quarter,
            "basis": "USD",
            "lower_rate": float(low),
            "upper_rate": float(high),
            "heading": heading_text,
            "evidence": block,
            "url": url,
            "accession": accession,
            "filed_on": filed_on.isoformat(),
        }

    for (_, _, metric), candidates in rows.items():
        if len(candidates) == 1:
            result["observations"].extend(candidates.values())
        else:
            result["missing"].append(f"Oracle exhibit has conflicting USD guidance for {metric}.")
    found = {row["metric"] for row in result["observations"]}
    expected = {"cloud_revenue", "consolidated_revenue"}
    result["missing"].extend(f"No unambiguous next-quarter USD range for {metric}." for metric in sorted(expected - found))
    if found:
        result["status"] = "available" if found == expected else "partial"
    return result


def _revenue_metric(fact):
    dimensions = fact.get("dimensions", ())
    if fact["metric"] == "consolidated_revenue" and not dimensions:
        return "consolidated_revenue"
    if fact["metric"] == "product_or_service_revenue" and len(dimensions) == 1:
        dimension = dimensions[0]
        if dimension["axis"] == _CLOUD_AXIS and dimension["member"].rpartition("}")[2] == "CloudRevenuesMember":
            return "cloud_revenue"
    return None


def apply_quarter_revenue_guidance(guidance: dict, operating_facts: dict, *,
                                   prior_start: date, prior_end: date, prior_accession: str) -> dict:
    """Translate growth ranges into dollars using uniquely identified prior-quarter facts."""
    if type(prior_start) is not date or type(prior_end) is not date or not prior_start < prior_end:
        raise ValueError("Specify the prior-year Oracle fiscal quarter's start and end dates.")
    source_rows, missing = [], list(guidance["missing"])
    for observation in guidance["observations"]:
        metric = observation["metric"]
        if (prior_start, prior_end) != _quarter_dates(observation["fiscal_year"] - 1, observation["quarter"]):
            missing.append(f"{metric}: the prior actual must match the same Oracle fiscal quarter.")
            continue
        scoped = {}
        for kind in ("observations", "conflicts"):
            scoped[kind] = [
                row for row in operating_facts[kind]
                if row["accession"] == prior_accession and row["unit"] == "USD"
                and row["period_start"] == prior_start.isoformat() and row["period_end"] == prior_end.isoformat()
                and _revenue_metric(row) == metric
            ]
        values = {row["value"] for row in scoped["observations"]}
        if len(values) != 1 or scoped["conflicts"]:
            missing.append(f"{metric}: prior-year quarterly revenue is missing or conflicting in the selected filing.")
            continue
        prior = next(iter(values))
        if not isfinite(prior) or prior <= 0:
            missing.append(f"{metric}: prior-year quarterly revenue must be positive and finite.")
            continue
        amounts = {
            f"{bound}_revenue_usd": float(Decimal(str(prior)) * (1 + Decimal(str(observation[f"{bound}_rate"]))))
            for bound in ("lower", "upper")
        }
        source_rows.append({
            **observation, **amounts, "prior_revenue_usd": prior, "prior_sources": scoped["observations"],
        })
    status = "missing" if not source_rows else "available" if not missing else "partial"
    return {"status": status, "observations": source_rows, "missing": missing}


def collect_oracle_quarter_guidance(client, releases, filings, *, as_of: date) -> dict:
    """Find guidance for the current Oracle fiscal quarter and match its prior-year filing."""
    if type(as_of) is not date:
        raise ValueError("Oracle guidance requires a valuation cutoff date.")
    year = as_of.year + (as_of.month >= 6)
    quarter = next(index for index in range(1, 5) if as_of <= _quarter_dates(year, index)[1])
    start, _ = _quarter_dates(year, quarter)
    guidance = {"status": "missing", "observations": [], "missing": []}
    candidates = sorted(releases, key=lambda release: (release["filed_on"], release["accession"]), reverse=True)
    for release in candidates:
        filed_on = date.fromisoformat(release["filed_on"])
        if not start <= filed_on <= as_of:
            continue
        try:
            _verify_url(release["url"], _CIK, release["accession"])
            parsed = parse_oracle_quarter_guidance(
                client.get_text(release["url"]), url=release["url"], accession=release["accession"],
                filed_on=filed_on, as_of=as_of,
            )
        except (ValueError, OSError):
            guidance["missing"].append(f"{release['accession']}: Oracle quarterly guidance exhibit unavailable.")
            continue
        if parsed["observations"]:
            guidance["observations"] = parsed["observations"]
            guidance["missing"].extend(parsed["missing"])
            guidance["status"] = "partial" if guidance["missing"] else "available"
            break
        guidance["missing"].extend(parsed["missing"])
    if not guidance["observations"]:
        guidance["missing"].append(f"No Oracle guidance available for current fiscal quarter Q{quarter} FY{year}.")
        return guidance

    prior_start, prior_end = _quarter_dates(year - 1, quarter)
    prior_forms = {"10-K", "10-K/A"} if quarter == 4 else {"10-Q", "10-Q/A"}
    prior_filings = [
        filing for filing in filings if filing["form"] in prior_forms
        and filing["period_end"] == prior_end.isoformat()
        and prior_end.isoformat() <= filing["filed_on"] <= as_of.isoformat()
    ]
    for prior in sorted(prior_filings, key=lambda filing: (filing["filed_on"], filing["accession"]), reverse=True):
        try:
            instance = load_filing_instance(client, _CIK, prior["accession"])
            observations, conflicts = _observations(instance, {
                "period_start": prior_start.isoformat(), "period_end": prior_end.isoformat(), "filing": prior,
            })
        except (ValueError, OSError):
            continue
        result = apply_quarter_revenue_guidance(
            guidance, {"observations": observations, "conflicts": conflicts},
            prior_start=prior_start, prior_end=prior_end, prior_accession=prior["accession"],
        )
        if result["observations"]:
            converted = {row["metric"]: row for row in result["observations"]}
            result["observations"] = [converted.get(row["metric"], row) for row in guidance["observations"]]
            return result
        if any(_revenue_metric(conflict) for conflict in conflicts):
            return {**guidance, "status": "partial", "missing": result["missing"]}
    return {
        **guidance,
        "status": "partial",
        "missing": [*guidance["missing"], f"Prior-year Oracle Q{quarter} FY{year - 1} exact quarterly actuals unavailable."],
    }
