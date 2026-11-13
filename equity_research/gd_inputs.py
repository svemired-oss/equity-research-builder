"""Versioned, private inputs for a standalone GD projection."""

from collections.abc import Collection
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
import json
from math import isfinite
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
Cell = tuple[str, int, int]  # Sheet, row, period index relative to the first forecast year.
SHEET_ROWS = {
    "Revenue model": 134, "Income statement": 58, "Balance sheet": 96,
    "Cash flow": 62, "Schedules": 122, "Equity": 53,
}
MODEL_INPUT_SHEETS = tuple(SHEET_ROWS)
# Only these workbook-layout cells are inputs in the v1 model. The dated rows
# are carried or calculated in their other periods.
ANNUAL_ASSUMPTION_ROWS = {
    "Revenue model": {14, 20, 32, 73, 93, 97, 101, 107, 111, 115, 119, 125, 129, 133},
    "Income statement": {19},
    "Balance sheet": {18, 61, 66, 78},
    "Schedules": {22, 24, 25, *range(59, 72), 74, 79, 110, 111, 112},
    "Equity": {11, 18, 19, 27, 28, 37, 45},
}
DATED_ASSUMPTION_PERIODS = {
    ("Revenue model", 16): {0, 2, 3},
    ("Revenue model", 18): {0},
    ("Balance sheet", 59): {0},
    ("Balance sheet", 83): {0},
    ("Schedules", 88): {5},
    ("Schedules", 93): {1, 2, 3, 4, 5},
}
SELECTORS = {f"Cover!I{row}": row for row in range(14, 20)}
GUIDED_SCALARS = {"cost_of_equity", "terminal_growth", "dividend_payout_ratio"}
SCALARS = GUIDED_SCALARS | {"opening_shares_millions"}
DATE_FIELDS = {"as_of", "twelve_month_date", "forecast_ends"}
SOURCE_KINDS = {"archived_workbook", "sec_filing", "issuer_release", "issuer_call", "market_data", "model_assumption", "derived"}


@dataclass(frozen=True)
class InputSource:
    kind: str
    citation: str
    available_on: date
    url: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class GuidedOverride:
    target: str
    before: float
    value: float
    reason: str


@dataclass(frozen=True)
class GDInputs:
    history: dict[Cell, float]
    assumptions: dict[Cell, float]
    scenarios: dict[int, int]
    cost_of_equity: float
    dividend_payout_ratio: float
    as_of: date
    forecast_ends: tuple[date, ...]
    twelve_month_date: date
    terminal_growth: float
    opening_shares_millions: float
    source: InputSource
    provenance: dict[str, InputSource] = field(default_factory=dict)
    override_log: tuple[GuidedOverride, ...] = ()


def cell_address(cell: Cell) -> str:
    sheet, row, index = cell
    if (
        sheet not in SHEET_ROWS
        or type(row) is not int or not 1 <= row <= SHEET_ROWS[sheet]
        or type(index) is not int or not -5 <= index <= 5
    ):
        raise ValueError(f"Invalid GD input cell {cell!r}.")
    return f"{sheet}!{'BCDEFGHIJKL'[index + 5]}{row}"


def _cell(address: str, *, forecast: bool) -> Cell:
    sheet, _, address_part = address.rpartition("!")
    columns = "GHIJKL" if forecast else "BCDEF"
    match = re.fullmatch(fr"([{columns}])([1-9][0-9]{{0,2}})", address_part)
    if sheet not in SHEET_ROWS or not match or int(match[2]) > SHEET_ROWS[sheet]:
        raise ValueError(f"Invalid {'forecast' if forecast else 'historical'} cell {address!r}.")
    row, period = int(match[2]), ord(match[1]) - ord("G")
    if forecast and (
        row not in ANNUAL_ASSUMPTION_ROWS.get(sheet, ())
        and period not in DATED_ASSUMPTION_PERIODS.get((sheet, row), ())
    ):
        raise ValueError(f"{address} is outside the GD v1 forecast assumptions.")
    return sheet, row, period


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _fields(value: object, label: str, required: set[str], optional: Collection[str] = frozenset()) -> dict:
    fields = _object(value, label)
    missing = required - fields.keys()
    unknown = fields.keys() - required - optional
    if missing:
        raise ValueError(f"Missing {label} field(s) {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"Unknown {label} field(s) {', '.join(sorted(unknown))}.")
    return fields


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array.")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    return value.strip()


def _number(value: object, label: str) -> float:
    if type(value) not in (float, int):
        raise ValueError(f"{label} must be a finite number.")
    try:
        if isfinite(value):
            return float(value)
    except OverflowError:
        pass
    raise ValueError(f"{label} must be a finite number.")


def _day(value: object, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD.")
    return parsed


def _choice(value: object, label: str) -> int:
    if type(value) is not int or value not in (1, 2, 3):
        raise ValueError(f"{label} must select Bull (1), Base (2), or Bear (3).")
    return value


def _source(value: object, label: str, as_of: date) -> InputSource:
    item = _fields(value, label, {"kind", "citation", "available_on"}, {"url", "sha256"})
    kind = _text(item["kind"], f"{label} kind")
    if kind not in SOURCE_KINDS:
        raise ValueError(f"Invalid {label} kind {kind!r}.")
    available = _day(item["available_on"], f"{label} available_on")
    if available > as_of:
        raise ValueError(f"{label} became available after as_of.")
    url = item.get("url")
    if url is not None:
        url = _text(url, f"{label} URL")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"{label} URL must be an HTTP(S) address.")
    if kind in {"sec_filing", "issuer_release", "issuer_call", "market_data"} and url is None:
        raise ValueError(f"{label} requires a source URL.")
    checksum = item.get("sha256")
    if checksum is not None and (not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum)):
        raise ValueError(f"{label} sha256 must contain 64 hexadecimal characters.")
    return InputSource(kind, _text(item["citation"], f"{label} citation"), available, url, checksum)


def _metadata(item: dict) -> None:
    version = item.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version {version!r}. Expected {SCHEMA_VERSION}.")


def _validate_parameters(parameters: dict[str, float]) -> None:
    if not 0 <= parameters["dividend_payout_ratio"] <= 1:
        raise ValueError("dividend_payout_ratio must be between 0 and 1.")
    if parameters["cost_of_equity"] <= 0:
        raise ValueError("cost_of_equity must be positive.")
    if not -1 < parameters["terminal_growth"] < parameters["cost_of_equity"]:
        raise ValueError("terminal_growth must be greater than -1 and below cost_of_equity.")
    if parameters["opening_shares_millions"] <= 0:
        raise ValueError("opening_shares_millions must be positive.")


def _target_value(inputs: GDInputs, target: str) -> float:
    if target in GUIDED_SCALARS:
        return getattr(inputs, target)
    if target in SELECTORS:
        return inputs.scenarios[SELECTORS[target]]
    try:
        return inputs.assumptions[_cell(target, forecast=True)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Guided target {target!r} is not an editable forecast assumption.") from exc


def _parse_inputs(value: object) -> GDInputs:
    required = {"schema_version", "ticker", "currency", "source", "history", "assumptions", "scenarios"}
    item = _fields(value, "input", required | SCALARS | DATE_FIELDS, {"provenance", "override_log"})
    _metadata(item)
    if item["ticker"] != "GD" or item["currency"] != "USD":
        raise ValueError("This input schema requires ticker GD and currency USD.")
    as_of = _day(item["as_of"], "as_of")
    forward = _day(item["twelve_month_date"], "twelve_month_date")
    ends = tuple(_day(entry, "forecast end") for entry in _array(item["forecast_ends"], "forecast_ends"))
    if len(ends) != 6 or ends[0].year <= 5 or any(
        end.month != 12 or end.day != 31 or end.year != ends[0].year + index
        for index, end in enumerate(ends)
    ):
        raise ValueError("forecast_ends must contain six consecutive December 31 dates.")
    if not date(ends[0].year - 1, 12, 31) <= as_of <= ends[0]:
        raise ValueError("as_of must fall between the last historical year-end and first forecast year-end.")
    if forward - as_of != timedelta(days=365):
        raise ValueError("twelve_month_date must be 365 days after as_of.")
    history = {
        _cell(key, forecast=False): _number(number, key)
        for key, number in _object(item["history"], "history").items()
    }
    assumptions = {
        _cell(key, forecast=True): _number(number, key)
        for key, number in _object(item["assumptions"], "assumptions").items()
    }
    selectors = _fields(item["scenarios"], "scenario", {str(row) for row in SELECTORS.values()})
    scenarios = {int(row): _choice(choice, f"Cover!I{row}") for row, choice in selectors.items()}
    parameters = {key: _number(item[key], key) for key in SCALARS}
    _validate_parameters(parameters)
    provenance_fields = SCALARS | DATE_FIELDS | SELECTORS.keys() | item["history"].keys() | item["assumptions"].keys()
    sources = _fields(item.get("provenance", {}), "provenance", set(), provenance_fields)
    inputs = GDInputs(
        history=history, assumptions=assumptions, scenarios=scenarios, as_of=as_of,
        forecast_ends=ends, twelve_month_date=forward, **parameters,
        source=_source(item["source"], "source", as_of),
        provenance={key: _source(entry, f"source for {key}", as_of) for key, entry in sources.items()},
    )
    log = []
    previous = {}
    for entry in _array(item.get("override_log", []), "override_log"):
        record = _fields(entry, "override log", {"target", "before", "value", "reason"})
        target = _text(record["target"], "override target")
        _target_value(inputs, target)
        parser = _choice if target in SELECTORS else _number
        before, value = parser(record["before"], target), parser(record["value"], target)
        if target in previous and previous[target] != before:
            raise ValueError(f"Override log for {target} has inconsistent previous values.")
        previous[target] = value
        log.append(GuidedOverride(target, before, value, _text(record["reason"], "override reason")))
    for target, value in previous.items():
        if _target_value(inputs, target) != value:
            raise ValueError(f"Override log for {target} disagrees with the current input value.")
    return replace(inputs, override_log=tuple(log))


def _unique_object(entries: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)


def load_inputs(path: Path) -> GDInputs:
    """Load a standalone GD input file and validate its point-in-time metadata."""
    return _parse_inputs(_read(path))


def _source_json(source: InputSource) -> dict:
    data = {key: value for key, value in asdict(source).items() if value is not None}
    data["available_on"] = source.available_on.isoformat()
    return data


def save_inputs(path: Path, inputs: GDInputs) -> None:
    """Save private inputs atomically after validating the serialized representation."""
    if path.suffix.lower() != ".json":
        raise ValueError("The input destination must be a .json file.")
    data = {
        "schema_version": SCHEMA_VERSION,
        "ticker": "GD",
        "currency": "USD",
        "as_of": inputs.as_of.isoformat(),
        "forecast_ends": [end.isoformat() for end in inputs.forecast_ends],
        "twelve_month_date": inputs.twelve_month_date.isoformat(),
        "source": _source_json(inputs.source),
        "history": {cell_address(cell): value for cell, value in sorted(inputs.history.items())},
        "assumptions": {cell_address(cell): value for cell, value in sorted(inputs.assumptions.items())},
        "scenarios": {str(row): choice for row, choice in sorted(inputs.scenarios.items())},
        **{key: getattr(inputs, key) for key in sorted(SCALARS)},
        "provenance": {key: _source_json(source) for key, source in sorted(inputs.provenance.items())},
        "override_log": [asdict(entry) for entry in inputs.override_log],
    }
    _parse_inputs(data)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_guided_overrides(path: Path, inputs: GDInputs) -> GDInputs:
    """Apply a user-authored override file and preserve each original source and value."""
    data = _fields(_read(path), "guided", {"schema_version", "overrides"})
    _metadata(data)
    changes = {}
    assumptions, scenarios = inputs.assumptions.copy(), inputs.scenarios.copy()
    overrides = []
    seen = set()
    for raw in _array(data["overrides"], "overrides"):
        entry = _fields(raw, "override", {"target", "value", "reason"})
        target = _text(entry["target"], "override target")
        if target in seen:
            raise ValueError(f"Duplicate guided target {target!r}.")
        seen.add(target)
        before = _target_value(inputs, target)
        reason = _text(entry["reason"], "override reason")
        value = _choice(entry["value"], target) if target in SELECTORS else _number(entry["value"], target)
        if target in SELECTORS:
            scenarios[SELECTORS[target]] = value
        elif target in GUIDED_SCALARS:
            changes[target] = value
        else:
            assumptions[_cell(target, forecast=True)] = value
        overrides.append(GuidedOverride(target, before, value, reason))
    _validate_parameters({key: changes.get(key, getattr(inputs, key)) for key in SCALARS})
    return replace(
        inputs, assumptions=assumptions, scenarios=scenarios, **changes,
        override_log=(*inputs.override_log, *overrides),
    )
