"""Collect Oracle's common share and convertible preferred disclosures."""

from datetime import date
from decimal import Decimal
import re

from .oracle_offerings import _NUMBER, _OPENING_SHARES, _PRO_FORMA_SHARES, _day, _find_value, _int, _page
from .sec_capital_events import _DATE


_OPTIONS = re.compile(
    rf"\b({_NUMBER}) shares of common stock issuable upon the exercise of options outstanding at ({_DATE})"
    r" at a weighted average exercise price of \$(\d+(?:\.\d+)?) per share\b",
    re.I,
)
_RSUS = re.compile(
    rf"\b({_NUMBER}) shares of common stock issuable upon the vesting of restricted stock units outstanding at ({_DATE})\b",
    re.I,
)
_RESERVE = re.compile(
    rf"\b({_NUMBER}) shares of common stock reserved for future issuance under our stock-based compensation plans\b",
    re.I,
)
_TREASURY = re.compile(rf"\b({_NUMBER}) shares of common stock held in treasury\b", re.I)
_PREFERRED_SHARES = re.compile(
    rf"\bup to ({_NUMBER}) shares of common stock issuable upon conversion of the ({_NUMBER}) outstanding shares"
    r" of our (\d+(?:\.\d+)?)% Series D Mandatory Convertible Preferred Stock"
    r".{0,120}?maximum conversion rate of (\d+(?:\.\d+)?) shares of common stock per share\b",
    re.I,
)
_PREFERRED_ISSUED = re.compile(
    rf"\bwe issued ({_NUMBER}) shares of Series D Mandatory Convertible Preferred Stock, represented by ({_NUMBER})"
    rf" depositary shares, each representing a 1/({_NUMBER})th interest in a share\b",
    re.I,
)
_DEPOSITARY_LISTING = re.compile(
    r"\blisted on the New York Stock Exchange under the symbol [“\"]([A-Z]+-[A-Z]+)\.?[”\"]",
    re.I,
)
_PREFERRED_TERMS = re.compile(
    rf"\bThe Series D Mandatory Convertible Preferred Stock has a liquidation preference of \$({_NUMBER}) per share,"
    r" and dividends accumulate at a rate of (\d+(?:\.\d+)?)% per annum on the liquidation preference,"
    r".{0,200}?payable quarterly in cash or, subject to certain limitations, in shares of our common stock"
    r" or a combination of cash and shares of our common stock",
    re.I,
)
_CONVERSION = re.compile(
    rf"\bUnless earlier converted, each share of Series D Mandatory Convertible Preferred Stock will automatically convert"
    rf" on the mandatory conversion date, which is expected to be ({_DATE}), into between (\d+(?:\.\d+)?) and"
    r" (\d+(?:\.\d+)?) shares of our common stock, subject to anti-dilution adjustments",
    re.I,
)


def _match(pattern, text, label):
    matches = list(pattern.finditer(text))
    groups = _find_value((match.groups() for match in matches), f"No unique {label} disclosure in the final prospectus.")
    return next(match for match in matches if match.groups() == groups)


def _dated_award(pattern, text, balance_date, label):
    match = _match(pattern, text, label)
    shares, observed_on, *other = match.groups()
    if _day(observed_on) != balance_date or _int(shares) <= 0:
        raise ValueError(f"The {label} disclosure does not match the reported share date.")
    result = {"underlying_common_shares": _int(shares), "observed_on": balance_date.isoformat(), "evidence": match[0]}
    if other:
        strike = Decimal(other[0])
        if strike <= 0:
            raise ValueError("Option strike must be positive.")
        result["weighted_average_strike_usd"] = float(strike)
    return result


def _excluded_shares(pattern, text, label):
    match = _match(pattern, text, label)
    return {"common_shares": _int(match[1]), "include_in_diluted_shares": False, "evidence": match[0]}


def _preferred(text, exclusions, balance_date):
    outstanding = _match(_PREFERRED_SHARES, exclusions, "Series D preferred shares and maximum conversion")
    issued = _match(_PREFERRED_ISSUED, text, "Series D depositary structure")
    terms = _match(_PREFERRED_TERMS, text, "Series D liquidation and dividend terms")
    conversion = _match(_CONVERSION, text, "Series D conversion date and range")
    listing = _match(_DEPOSITARY_LISTING, text[issued.end():terms.start()], "Series D depositary ticker")
    shares = _int(outstanding[2])
    max_common = _int(outstanding[1])
    low, high = map(Decimal, conversion.groups()[1:])
    rate = Decimal(terms[2])
    ratio = _int(issued[3])
    depositary = _int(issued[2])
    preference = _int(terms[1])
    converts_on = _day(conversion[1])
    if (shares <= 0 or shares > _int(issued[1]) or ratio <= 0 or depositary != _int(issued[1]) * ratio
            or low <= 0 or high <= low or high != Decimal(outstanding[4]) or high * shares != max_common
            or rate <= 0 or rate != Decimal(outstanding[3]) or preference <= 0 or converts_on <= balance_date):
        raise ValueError("The Series D conversion and issuance disclosures did not reconcile.")
    return {
        "series": "Series D Mandatory Convertible Preferred Stock",
        "observed_on": balance_date.isoformat(),
        "preferred_shares_outstanding": shares,
        "depositary_ticker": listing[1].upper(),
        "depositary_exchange": "NYSE",
        "depositary_shares_per_preferred_share": ratio,
        "depositary_shares_at_issuance": depositary,
        "liquidation_preference_per_preferred_share_usd": preference,
        "liquidation_preference_per_depositary_share_usd": preference / ratio,
        "aggregate_liquidation_preference_usd": preference * shares,
        "annual_dividend_rate": float(rate / 100),
        "dividend_frequency": "quarterly if declared",
        "permitted_dividend_settlement": ["cash", "common stock subject to limitations", "combination"],
        "expected_mandatory_conversion_date": converts_on.isoformat(),
        "minimum_common_per_preferred": float(low),
        "maximum_common_per_preferred": float(high),
        "aggregate_minimum_common_shares_before_adjustments": float(low * shares),
        "aggregate_maximum_common_shares_before_adjustments": max_common,
        "evidence": [match[0] for match in (outstanding, issued, listing, terms, conversion)],
    }


def collect_oracle_dilution(client, offering: dict, *, as_of: date) -> dict:
    """Read the verified final equity supplement without assuming cutoff dilution."""
    result = {
        "status": "missing",
        "balance_date": offering.get("balance_date"),
        "closing_date": offering.get("closed_on"),
        "basic_shares": None,
        "options": None,
        "restricted_stock_units": None,
        "preferred": None,
        "future_share_reserve": None,
        "treasury_shares": None,
        "source": None,
        "missing": [],
        "limitations": [
            "Award balances and potential shares are reported as of the opening balance date. "
            "Offering-adjusted basic shares cover only the matched equity issuance.",
            "The option strike is a weighted average. Individual option strikes, subsequent awards and vesting "
            "require separate evidence for an economic diluted share estimate.",
            "Treasury shares and future stock-plan capacity are excluded from reported basic and modeled dilution.",
            "Preferred liquidation preference and conversion bounds are separate from current market value. "
            "Conversion is subject to anti-dilution adjustments and may be earlier.",
        ],
    }
    equity = offering.get("equity")
    if offering.get("status") not in {"available", "partial"} or not equity:
        result["missing"].append("No verified final Oracle common stock prospectus was available.")
        return result
    try:
        if type(as_of) is not date:
            raise ValueError("The valuation cutoff must be a date.")
        balance = date.fromisoformat(result["balance_date"])
        closed = date.fromisoformat(result["closing_date"])
        source = equity["source"]
        filed = date.fromisoformat(source["filed_on"])
        if not balance < closed <= as_of or not balance < filed <= as_of:
            raise ValueError("The prospectus or verified closing is outside the valuation window.")
        text = _page(client, source).text
        opening = _match(_OPENING_SHARES, text, "opening common shares")
        adjusted = _match(_PRO_FORMA_SHARES, text, "pro forma common shares")
        basic, pro_forma = _int(opening[1]), _int(adjusted[1])
        if (_day(opening[2]) != balance or basic != equity["balance_date_basic_shares"]
                or pro_forma != equity["pro_forma_basic_shares"] or basic + equity["shares_issued"] != pro_forma):
            raise ValueError("The filed common share amounts do not match the verified offering.")
    except (KeyError, OSError, TypeError, ValueError) as exc:
        result["missing"].append(f"Oracle dilution disclosures unavailable. {exc}")
        return result
    result["source"] = source
    result["basic_shares"] = {
        "reported_basic_shares": basic,
        "offering_adjusted_basic_shares": pro_forma,
        "shares_issued": equity["shares_issued"],
        "observed_on": balance.isoformat(),
        "evidence": [opening[0], adjusted[0]],
    }
    exclusions = re.split(r"\bRISK FACTORS\b", text[opening.end():opening.end() + 3000], maxsplit=1, flags=re.I)[0]
    for field, read in (
        ("options", lambda: _dated_award(_OPTIONS, exclusions, balance, "option")),
        ("restricted_stock_units", lambda: _dated_award(_RSUS, exclusions, balance, "restricted stock unit")),
        ("future_share_reserve", lambda: _excluded_shares(_RESERVE, exclusions, "future stock-plan reserve")),
        ("treasury_shares", lambda: _excluded_shares(_TREASURY, exclusions, "treasury share")),
        ("preferred", lambda: _preferred(text, exclusions, balance)),
    ):
        try:
            result[field] = read()
        except ValueError as exc:
            result["missing"].append(str(exc))
    result["status"] = "partial" if result["missing"] else "available"
    return result
