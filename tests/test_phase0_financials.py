"""Frozen synthetic examples characterize the existing engine without network access."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import pytest

from equity_research.fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff
from equity_research.operating_research import (
    ResearchGap, _balance, _forecast_dates, prepare_operating_model, revenue_candidates,
)
from equity_research import nintendo_valuation as nintendo

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text())


def test_discount_fcff_frozen_values_and_guards():
    data = read("fcff.json")
    day = date.fromisoformat(data["as_of"])
    flows = [DatedFCFF(date.fromisoformat(row["period_end"]), row["amount_millions"]) for row in data["flows"]]
    terminal = TerminalAssumptions(**data["terminal"])
    bridge = EquityBridge(day, **data["bridge"])
    result = discount_fcff(flows, day, data["wacc"], terminal, bridge)
    for key, value in read("expected.json")["fcff"].items():
        assert getattr(result, key) == pytest.approx(value, rel=1e-12)
    assert result.equity_value_millions == pytest.approx(result.enterprise_value_millions - 30)
    assert discount_fcff(flows, day, .11, terminal, bridge).value_per_share < result.value_per_share
    for bad in (replace(terminal, growth_rate=.10), replace(terminal, marginal_roic=0)):
        with pytest.raises(ValueError):
            discount_fcff(flows, day, .10, bad, bridge)
    with pytest.raises(ValueError, match="strictly increasing"):
        discount_fcff(list(reversed(flows)), day, .10, terminal, bridge)
    with pytest.raises(ValueError, match="valuation date"):
        discount_fcff(flows, day, .10, terminal, replace(bridge, as_of=date(2025, 1, 1)))


def test_balance_frozen_outputs():
    period = read("operating.json")["packet"]["actuals"]["periods"][-1]
    _, *values = _balance(period)
    assert values == pytest.approx(read("expected.json")["balance"])
    alternative = deepcopy(period)
    alternative["metrics"].pop("total_liabilities")
    alternative["metrics"]["liabilities_and_equity"] = 150e6
    assert _balance(alternative)[1:] == pytest.approx(values)


@pytest.mark.parametrize("field,reason", [
    ("cash_and_equivalents", "cash and equivalents"),
    ("short_term_investments", "short-term investment"),
    ("finance_lease_current", "finance lease current"),
])
def test_balance_missing_facts_stop(field, reason):
    period = read("operating.json")["packet"]["actuals"]["periods"][-1]
    period["metrics"].pop(field)
    if field != "cash_and_equivalents":
        period["metrics"]["total_assets"] += 2_000_000
    with pytest.raises(ResearchGap, match=reason):
        _balance(period)


def test_balance_conflict_stops():
    period = read("operating.json")["packet"]["actuals"]["periods"][-1]
    period["metrics"]["total_assets"] += 5e6
    with pytest.raises(ResearchGap, match="did not reconcile"):
        _balance(period)


def test_revenue_candidates_match_saved_totals_and_reject_overlap():
    packet = read("operating.json")["packet"]
    assert revenue_candidates(packet) == read("expected.json")["candidates"]
    packet["operating_facts"]["observations"].append({
        **packet["operating_facts"]["observations"][-1], "dimensions": [{"member": "Overlapping"}],
    })
    # The exact rule rejects the overlap. The fallback ignores a member filed in
    # one year only and keeps the lines that reconcile in both years, with a basis note.
    candidates = revenue_candidates(packet)
    assert set(candidates["segment_revenue"]["latest"]) == {"Products", "Services"}
    assert "reconciles to consolidated revenue in both years" in candidates["segment_revenue"]["basis"]
    packet["actuals"]["periods"][-1]["metrics"]["revenue"] = None
    with pytest.raises(ResearchGap, match="did not reconcile"):
        revenue_candidates(packet)


@pytest.mark.parametrize("case", read("dates.json"), ids=lambda row: row["name"])
def test_forecast_dates_saved_calendar_cases(case):
    assert [value.isoformat() for value in _forecast_dates(
        [{"period_end": value} for value in case["history"]])] == case["expected"]


def test_prepare_model_frozen_outputs_and_guided_change():
    data = read("operating.json")
    candidates = revenue_candidates(data["packet"])
    filed = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"], candidates)
    evidence = filed["roic_evidence"]
    assert filed["roic"] == evidence["value"] and .08 <= filed["roic"] <= .40
    assert filed["roic_comparison"]["roic"] == .15
    # Frozen values were recorded with the former fixed 15% terminal ROIC; pin it to keep them as a regression lock.
    prior = [{"target": "roic", "value": .15, "reason": "Former fixed terminal ROIC"}]
    model = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"], candidates, prior)
    assert filed["roic_comparison"]["value_per_share"] == pytest.approx(model["fcfe"]["Base"].value_per_share)
    expected = read("expected.json")["operating"]
    assert model["wacc"] == pytest.approx(expected["wacc"])
    assert model["revenue"] == pytest.approx(expected["revenue"])
    # Frozen FCFF values use the margin-only cases; Stage 2 bull and bear also shift revenue growth.
    assert [model["legacy_scenarios"][name].value_per_share for name in ("Bear", "Base", "Bull")] == pytest.approx(expected["values"])
    revised = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"], candidates,
        [*prior, {"target": "margin", "value": model["margin"] + .01, "reason": "Synthetic margin sensitivity"}])
    assert revised["scenarios"]["Base"].value_per_share == pytest.approx(expected["guided_value"])
    assert revised["scenarios"]["Base"].value_per_share > model["scenarios"]["Base"].value_per_share
    assert model["history"] == revised["history"]
    data["quote"]["currency"] = "EUR"
    with pytest.raises(ResearchGap, match="USD market inputs"):
        prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"], candidates)


def test_nintendo_parser_frozen_text(monkeypatch):
    data = read("nintendo.json")
    monkeypatch.setattr(nintendo, "public_document", lambda *args, **kwargs: data["archive"].encode())
    monkeypatch.setattr(nintendo, "_pages", lambda url, *args:
        data["annual_pages"] if "260508" in url else data["interim_pages"])
    parsed = nintendo._nintendo_filings(date.fromisoformat(data["cutoff"]))
    expected = read("expected.json")["nintendo"]
    for key, value in expected.items():
        actual = parsed[key]
        if isinstance(actual, date):
            actual = actual.isoformat()
        if isinstance(actual, tuple):
            actual = list(actual)
        assert actual == value
    data["interim_pages"][2] = data["interim_pages"][2].replace("2,200", "2,205")
    with pytest.raises(ResearchGap, match="did not reconcile"):
        nintendo._nintendo_filings(date.fromisoformat(data["cutoff"]))


def test_nintendo_archive_and_row_guards():
    data = read("nintendo.json")
    with pytest.raises(ResearchGap, match="too old"):
        nintendo._latest_releases(date(2027, 4, 1), data["archive"].encode())
    with pytest.raises(ResearchGap, match="could not be read"):
        nintendo._latest_releases(date(2026, 11, 18), b"<!DOCTYPE rss><rss/>")
    with pytest.raises(ResearchGap, match="unique paired row"):
        nintendo._row("Cash  1  2\nCash  3  4", "Cash")


def test_nintendo_pdf_extraction_checks_identity(monkeypatch):
    from io import BytesIO
    from reportlab.pdfgen import canvas
    stream = BytesIO()
    page = canvas.Canvas(stream)
    page.drawString(40, 740, "Nintendo Co., Ltd. September 30, 2026 million yen")
    page.drawString(40, 720, "Cash  100  200")
    page.save()
    monkeypatch.setattr(nintendo, "public_document", lambda *args, **kwargs: stream.getvalue())
    pages = nintendo._pages("https://example.org/synthetic.pdf", "Nintendo Co., Ltd.", "September 30, 2026")
    assert "Cash" in pages[0]
    with pytest.raises(ResearchGap, match="did not match"):
        nintendo._pages("https://example.org/synthetic.pdf", "Wrong issuer", "September 30, 2026")
