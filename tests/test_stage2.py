"""Stage 2: FCFE at the cost of equity, CAPM betas, SIC-tier peers, carried other income and the 0.5-2x stop."""
from copy import deepcopy
from shutil import which

import pytest

from equity_research import fcfe_valuation as fv
from equity_research import peer_selection as ps
from equity_research.operating_research import ResearchGap, prepare_operating_model, revenue_candidates, verify_valuation_range
from test_market_valuation import fixture_data


def _peer(ticker, cik, sic, shares=10e6):
    return {"issuer": {"ticker": ticker, "cik": cik, "sic": sic},
            "actuals": {"periods": [{"metrics": {"ending_shares": shares}}]}, "interim_actuals": {"periods": []}}


def _quote(close, day="2026-11-19"):
    return {"close": close, "trading_date": day, "currency": "USD", "url": "https://example.org/q"}


def test_peers_use_three_digit_sic_first_and_label_the_two_digit_fallback():
    subject = _peer("PG", "1", "2840")
    quotes = {"PG": _quote(100)}
    packets = {}
    for i, (ticker, sic) in enumerate((("CL", "2844"), ("CHD", "2841"), ("KMB", "2621"), ("JNJ", "2834"),
                                       ("ABT", "2834"), ("PFE", "2834")), 2):
        packets[ticker] = _peer(ticker, str(i), sic)
        quotes[ticker] = _quote(50)
    proposals = [{"ticker": t, "reason": "fixture"} for t in ("CL", "CHD", "KMB", "JNJ", "ABT", "PFE")]
    selection = ps.screen_peers(subject, proposals, packets, quotes)
    # PG (2840) shares 284 with CL and CHD only. JNJ, ABT and PFE share 28 and fill in as a labeled fallback.
    assert [(p["ticker"], p["tier"]) for p in selection["peers"]] == [
        ("CL", ps.THREE_DIGIT), ("CHD", ps.THREE_DIGIT), ("JNJ", ps.TWO_DIGIT), ("ABT", ps.TWO_DIGIT), ("PFE", ps.TWO_DIGIT)]
    assert selection["tier"] == ps.TWO_DIGIT
    assert dict(selection["rejected"])["KMB"].startswith("SIC 2621 shares neither")
    # With three 3-digit peers, the 2-digit matches are not used.
    packets["CLX"], quotes["CLX"] = _peer("CLX", "99", "2842"), _quote(50)
    selection = ps.screen_peers(subject, [{"ticker": "CLX", "reason": "fixture"}, *proposals], packets, quotes)
    assert {p["ticker"] for p in selection["peers"]} == {"CLX", "CL", "CHD"}
    assert selection["tier"] == ps.THREE_DIGIT
    assert "fallback not needed" in dict(selection["rejected"])["JNJ"]
    assert not ps.needs_more(selection)


def test_peer_screen_applies_size_band_session_and_issuer_rules():
    subject = _peer("AAA", "1", "3674")
    quotes = {"AAA": _quote(100), "BIG": _quote(1001), "SMALL": _quote(.99), "OLD": _quote(50, "2026-11-18"),
              "DUP": _quote(50), "OK": _quote(1000), "BANK": _quote(50)}
    packets = {"BIG": _peer("BIG", "2", "3674"), "SMALL": _peer("SMALL", "3", "3674"), "OLD": _peer("OLD", "4", "3674"),
               "DUP": _peer("DUP", "1", "3674"), "OK": _peer("OK", "5", "3672"), "BANK": _peer("BANK", "6", "6022"),
               "FOREIGN": {**_peer("FOREIGN", "7", "3674"), "actuals": {"periods": []}}}
    proposals = [{"ticker": t, "reason": ""} for t in ("BIG", "SMALL", "OLD", "DUP", "OK", "BANK", "FOREIGN", "AAA")]
    selection = ps.screen_peers(subject, proposals, packets, quotes)
    rejected = dict(selection["rejected"])
    assert [p["ticker"] for p in selection["peers"]] == ["OK"] and selection["peers"][0]["market_cap_ratio"] == 10
    assert "outside 0.1x-10x" in rejected["BIG"] and "outside 0.1x-10x" in rejected["SMALL"]
    assert "quote date" in rejected["OLD"] and "Duplicate issuer" in rejected["DUP"]
    assert "neither" in rejected["BANK"] and "10-K" in rejected["FOREIGN"] and "AAA" not in rejected
    assert ps.needs_more(selection)


def test_comparable_beta_unlevers_each_peer_and_relevers_at_the_subject():
    peers = {"A": {"value": 1.2, "debt_to_equity": .5, "tax_rate": .2},
             "B": {"value": .9, "debt_to_equity": 0., "tax_rate": .25},
             "C": {"value": 1.5, "debt_to_equity": 1., "tax_rate": 0.},
             "D": {"excluded": "Beta unavailable."}}
    result = fv.comparable_beta(peers, 50., 200., .21)
    unlevered = sorted((1.2/1.4, .9, 1.5/2))
    assert result["unlevered_median"] == pytest.approx(unlevered[1])
    assert result["value"] == pytest.approx(unlevered[1]*(1+.79*.25))
    assert result["excluded"] == [["D", "Beta unavailable."]]
    assert fv.comparable_beta({"A": peers["A"]}, 50., 200., .21)["value"] is None


def test_regression_beta_needs_ninety_weekly_returns():
    comparable = {"value": .8, "reason": None}
    assert fv.select_beta({"value": 1.1, "weeks": 104}, comparable) == (1.1, "regression")
    assert fv.select_beta({"value": 1.1, "weeks": 89}, comparable) == (.8, "comparable")
    with pytest.raises(ResearchGap, match="89 weekly returns"):
        fv.select_beta({"value": 1.1, "weeks": 89}, {"value": None, "reason": "Comparable beta unavailable: 1 peer betas, 2 required."})
    # Older caches that fell back to a peer median are read as having no subject regression.
    assert fv.regression_evidence({"beta": {"value": 1, "peers": {}, "url": "u", "date": "d"}})["value"] is None


def test_other_and_equity_method_income_carry_at_the_historical_median():
    # KO: other income 570, 1,992 and 1,073 plus equity income 1,691, 1,770 and 2,031 (USD millions).
    history = [{"metrics": {"other_nonoperating_income": o*1e6, "equity_method_income": e*1e6}}
               for o, e in ((570, 1691), (1992, 1770), (1073, 2031))]
    assert fv.other_income_carry(history) == pytest.approx(3104)
    history[0]["metrics"]["equity_method_income"] = None
    assert fv.other_income_carry(history) == pytest.approx(3104)


def test_case_revenue_adds_the_step_to_every_lines_growth_each_year():
    latest = {"A": 100., "B": 50.}
    base = [{"A": 110., "B": 51.}, {"A": 118., "B": 53.}]
    path = fv.case_revenues(base, latest, .005)
    first = {"A": 110 + 100*.005, "B": 51 + 50*.005}
    assert path[0] == pytest.approx(sum(first.values()))
    second = {k: first[k]*(base[1][k]/base[0][k] + .005) for k in first}
    assert path[1] == pytest.approx(sum(second.values()))
    assert first["A"]/latest["A"] - 1 == pytest.approx(base[0]["A"]/latest["A"] - 1 + .005)


def test_fcfe_engine_matches_a_hand_calculation():
    inputs = fv.FCFEInputs(100., 10., .1, .2, .05, (.06,)*5, (2.,)*5, 1., 40., .05, (0.,)*5, .5,
                           (.5, 1.5, 2.5, 3.5, 4.5), .02, .1, 30., 10.)
    revenues = [110., 120., 130., 140., 150.]
    result = fv.value_fcfe(inputs, revenues, .2, .09)
    flows, previous = [], 10.
    for i, r in enumerate(revenues):
        income = (r*.2 - 2 + 1)*.8
        operating = income + r*.05 - (r*.1 - previous) - r*.06
        flows.append(operating*(.5 if i == 0 else 1))
        previous = r*.1
    terminal = 150*.2*.8*1.02*(1-.2) - 40*.05*.8 + 1*1.02*.8 + .02*40
    equity = sum(f/1.09**t for f, t in zip(flows, inputs.discount_years)) + terminal/.07/1.09**4.5 + 30
    assert result.post_cutoff_fcfe == pytest.approx(flows)
    assert result.value_per_share == pytest.approx(equity/10)


def test_valuation_outside_half_to_double_the_close_stops_for_review():
    class Value:
        def __init__(self, value):
            self.value_per_share = value
    for value in (50., 200.):
        assert verify_valuation_range({"fcfe": {"Base": Value(value)}, "close": 100.}) is None
    for value in (49.9, 200.1):
        with pytest.raises(ResearchGap, match="^Valuation outside 0.5–2×, analyst review needed"):
            verify_valuation_range({"fcfe": {"Base": Value(value)}, "close": 100., "margin": .2, "implied_margin": .3})


def test_equity_method_income_flows_into_fcfe_and_its_investment_leaves_the_bridge():
    data = fixture_data()
    packet = data["packet"]
    base = prepare_operating_model(deepcopy(packet), data["treasury"], data["quote"], data["selection"], revenue_candidates(packet))
    for period in packet["actuals"]["periods"]:
        period["metrics"]["equity_method_income"] = 2e6
    carried = prepare_operating_model(packet, data["treasury"], data["quote"], data["selection"], revenue_candidates(packet))
    assert carried["other_income"] == pytest.approx(base["other_income"] + 2)
    assert carried["fcfe"]["Base"].value_per_share > base["fcfe"]["Base"].value_per_share
    # FCFF ignores nonoperating income, so the cross-check is unchanged.
    assert carried["scenarios"]["Base"].value_per_share == pytest.approx(base["scenarios"]["Base"].value_per_share)
    bridge = carried["scenarios"]["Base"].bridge
    assert carried["fcfe_inputs"].bridge_adjustment == pytest.approx(
        bridge.adjustment_millions + carried["bridge_debt"] - carried["bridge_affiliate_book_value"])
    # Bull and bear shift both growth and margin around the base FCFE value.
    values = [carried["fcfe"][c].value_per_share for c in ("Bear", "Base", "Bull")]
    assert values == sorted(values)


def test_comps_with_fewer_than_three_peers_are_insufficient_and_never_set_the_target():
    data = fixture_data()
    comps = data["packet"]["valuation_comps"]
    comps["rows"] = comps["rows"][:2]
    model = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                    revenue_candidates(data["packet"]))
    summary = model["valuation_summary"]
    assert model["valuation_comps"]["value"] is None and model["valuation_comps"]["reason"].startswith("Insufficient peers: 2")
    assert summary["peer"] is None
    assert summary["target"] == pytest.approx(model["fcfe"]["Base"].value_per_share*(1+model["cost_of_equity"])
                                              - summary["dividends"])


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_insufficient_peers_pair_is_verified_and_states_it(tmp_path):
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from equity_research.acceptance import check_pair
    from equity_research.model_template import export_operating
    data = fixture_data()
    data["packet"]["valuation_comps"]["rows"] = data["packet"]["valuation_comps"]["rows"][:2]
    data["packet"]["peer_selection"] = {"tier": ps.TWO_DIGIT, "rules": ps.PEER_RULES, "rejected": [["XYZ", "fixture"]],
                                        "peers": [{"ticker": t, "sic": "2840", "tier": ps.TWO_DIGIT,
                                                   "market_cap_ratio": 1., "reason": ""} for t in ("ONE", "TWO")]}
    model = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                    revenue_candidates(data["packet"]))
    book, report = export_operating(model, tmp_path)
    summary = {"rating": model["valuation_summary"]["rating"], "spot_value_usd": model["fcfe"]["Base"].value_per_share}
    outcome = check_pair({"source_mode": "operating_research", "summary": summary}, book.read_bytes(), report.read_bytes())
    assert outcome["peer_value"] is None
    saved = load_workbook(book, data_only=True)
    assert saved["Comps"]["B14"].value == "Insufficient peers"
    assert saved["Comps"]["C53"].value == ps.TWO_DIGIT and saved["Report Evidence"]["B12"].value == ps.TWO_DIGIT
    text = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(report).pages)
    assert "Peer multiples cross-check unavailable: insufficient peers, 3 required." in text
    assert "Forward beta Unavailable: no data source." in text


def test_peer_leverage_uses_a_labeled_filed_total_or_zero_only_without_any_debt_fact():
    from equity_research.market_valuation import _peer_leverage
    def packet(metrics):
        period = {"period_end": "2026-09-30", "filing": {"url": "https://www.sec.gov/q"},
                  "metrics": {"ending_shares": 10e6, "income_taxes": 2e6, "income_before_taxes": 10e6, **metrics}}
        return {"actuals": {"periods": [period]}, "interim_actuals": {"periods": []}}
    quote = {"close": 30., "url": "https://example.org/q"}
    filed = _peer_leverage(packet({"current_debt": 5e6, "long_term_debt": 25e6}), quote)
    assert filed["debt"] == 30 and filed["debt_to_equity"] == pytest.approx(.1) and filed["tax_rate"] == pytest.approx(.2)
    assert "bridge did not reconcile" in filed["debt_basis"]
    none = _peer_leverage(packet({}), quote)
    assert none["debt"] == 0 and "No funded debt fact" in none["debt_basis"]
    with pytest.raises(ValueError, match="filed total"):
        _peer_leverage(packet({"commercial_paper": 4e6}), quote)
