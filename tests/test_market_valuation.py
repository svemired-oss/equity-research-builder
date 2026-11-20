from datetime import date, timedelta
import json
from pathlib import Path
from shutil import which

import pytest

from equity_research.market_valuation import dated_erp, weekly_beta, select_beta, debt_cost
from equity_research.model_template import export_operating
from equity_research.operating_research import prepare_operating_model, revenue_candidates
from equity_research.valuation_comps import multiple_summary, same_business, collect_forward_eps

FIX = Path(__file__).parent / "fixtures"


def test_weekly_regression_and_industry_median():
    start = date(2024, 1, 5)
    stock, index = {}, {}
    a = b = 100
    for week in range(105):
        day = start + timedelta(weeks=week)
        change = (week % 9 - 4)/100
        a, b = a*(1+change), b*(1+1.6*change)
        stock[day], index[day] = b, a
    result = weekly_beta(stock,index,start,day)
    assert result["weeks"] == 104
    assert result["raw"] == pytest.approx(1.6)
    assert result["value"] == pytest.approx(1.4)
    assert select_beta(None, {"A": result,"B": {**result,"value": .8}})["value"] == pytest.approx(1.1)
    with pytest.raises(ValueError, match="90"):
        weekly_beta(dict(list(stock.items())[:50]),index,start,day)


def test_dated_erp_and_interest_debt_cost():
    html = "<p>Implied ERP on June 1, 2026 = 4.50% (Trailing 12 month, with adjusted payout)</p>"
    assert dated_erp(html,date(2026,6,30))["value"] == .045
    with pytest.raises(ValueError, match="45 days"):
        dated_erp(html,date(2026,8,1))
    packet = json.loads((FIX/"operating.json").read_text())["packet"]
    packet["actuals"]["periods"][-1]["metrics"]["interest_expense"] = 1_800_000
    assert debt_cost(packet,.04)["value"] == pytest.approx(.06)


def test_fred_table_fallback(monkeypatch):
    from equity_research import market_valuation
    def source(url):
        if "fredgraph" in url:
            raise TimeoutError
        return "<h1>SP500</h1><table><tr><td>2026-11-18</td><td>100</td></tr></table>"
    monkeypatch.setattr(market_valuation,"_download",source)
    assert market_valuation._series("SP500",date(2026,11,1),date(2026,11,19)) == {date(2026,11,18):100}


def test_peer_multiples_and_comparable_business():
    info = {"ebitda":10,"sales":25,"eps":2,"shares":4,"adjustment":-4}
    peers = [{"multiples":{"EV / EBITDA":8,"EV / Sales":2}},
             {"multiples":{"EV / EBITDA":12,"EV / Sales":4,"P/E":9}}]
    summary = multiple_summary(peers,info)
    assert summary["EV / EBITDA"]["quartiles"] == [9,10,11]
    assert summary["EV / Sales"]["values"] == pytest.approx([14.625,17.75,20.875])
    assert "P/E" not in summary
    assert same_business({"issuer":{"sic":"5411"}},{"issuer":{"sic":"5412"}},"segment_growth")
    assert not same_business({"issuer":{"sic":"5411"}},{"issuer":{"sic":"5812"}},"segment_growth")


def test_forward_gaap_disclosure_requires_next_twelve_months_and_usd():
    data = json.loads((FIX/"operating.json").read_text())
    packet = data["packet"]
    packet["actuals"]["periods"][-1]["sources"] = {"revenue":{"unit":"USD"}}
    packet["earnings"] = {"releases":[{"filed_on":"2026-06-12","url":"https://example.com/filing"}]}
    class Client:
        def get_text(self,url):
            return "<p>We expect next 12 months GAAP diluted EPS of $2.00 to $2.50.</p>"
    collect_forward_eps(packet, Client())
    assert packet["forward_eps"]["value"] == 2.25


def fixture_model():
    data = json.loads((FIX/"operating.json").read_text())
    packet = data["packet"]
    url = "https://example.com/evidence"
    packet["valuation_market"] = {
        "erp":{"value":.045,"date":"2026-06-01","url":url,"basis":"Fixture ERP"},
        "beta":{"value":1.2,"raw":1.3,"weeks":104,"start":"2024-06-28","date":"2026-06-30",
                "url":url,"index_url":url,"basis":"Fixture beta"},
        "debt":{"value":.06,"spread":.02,"url":url,"date":"2026-06-30","basis":"Fixture debt"},
        "range":{"low":20,"high":38,"date":"2026-06-30","url":url}}
    subject = {"end":"2025-12-31","close":30,"ev":320,"sales":120,"ebitda":30,"eps":3,
               "filings":[url],"quote_url":url,"shares":10,"adjustment":-20}
    peers = [{**subject,"ticker":"ONE","multiples":{"EV / EBITDA":320/30,"EV / Sales":320/120,"P/E":10}},
             {**subject,"ticker":"TWO","multiples":{"EV / EBITDA":320/30,"EV / Sales":320/120,"P/E":10}}]
    packet["valuation_comps"] = {"subject":subject,"rows":peers,"summary":multiple_summary(peers,subject),
                                 "value":30,"basis":"LTM","excluded":[]}
    return prepare_operating_model(packet,data["treasury"],data["quote"],data["selection"],revenue_candidates(packet))


def test_wacc_and_target_calculation():
    model = fixture_model()
    cost = model["cost"]
    equity = cost.risk_free_rate+.045*1.2
    expected = (cost.common_equity_market_value_millions*equity +
                cost.debt_market_value_millions*.06*(1-cost.marginal_tax_rate))/(
                cost.common_equity_market_value_millions+cost.debt_market_value_millions)
    assert model["wacc"] == pytest.approx(expected)
    result = model["valuation_summary"]
    assert result["target"] == pytest.approx(result["blended"]*(1+equity)-result["dividends"])
    assert result["implied_cagr"] is not None


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_phase_four_export(tmp_path):
    model = fixture_model()
    book, report = export_operating(model,tmp_path)
    from equity_research.acceptance import check_pair
    summary = {"rating":model["valuation_summary"]["rating"],"spot_value_usd":model["scenarios"]["Base"].value_per_share}
    assert check_pair({"source_mode":"operating_research","summary":summary},book.read_bytes(),report.read_bytes())["peer_value"] == 30



def _bars(start, days, base=100.0, step=0.001):
    from datetime import timedelta as _td
    rows, price = [], base
    for i in range(days):
        day = start + _td(days=i)
        if day.weekday() < 5:
            price *= 1 + step * (1 if i % 3 else -1.5)
            rows.append({"t": f"{day.isoformat()}T05:00:00Z", "c": price, "h": price, "l": price})
    return rows


class _SPYClient:
    def __init__(self, fail=False):
        self.fail = fail

    def adjusted_daily_bars(self, symbol, start, end):
        if self.fail:
            from equity_research.market_quotes import MarketQuoteError
            raise MarketQuoteError("unavailable")
        return _bars(start, (end - start).days + 1)


def test_index_prefers_spy_when_fred_times_out(monkeypatch):
    from datetime import date, timedelta
    from equity_research import market_valuation as mv

    def timeout(*args, **kwargs):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(mv, "_download", timeout)
    end = date(2026, 11, 18)
    series, url, basis = mv._index_series(_SPYClient(), end - timedelta(days=735), end)
    assert basis == mv.SPY_PROXY and len(series) >= 400


def test_index_reports_specific_error_when_all_sources_fail(monkeypatch):
    from datetime import date, timedelta
    import pytest
    from equity_research import market_valuation as mv

    def timeout(*args, **kwargs):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(mv, "_download", timeout)
    end = date(2026, 11, 18)
    with pytest.raises(ValueError, match="Neither SPY bars nor the FRED"):
        mv._index_series(_SPYClient(fail=True), end - timedelta(days=735), end)


def test_debt_cost_uses_closing_debt_when_only_latest_balance_reconciles(monkeypatch):
    from equity_research import market_valuation as mv
    from equity_research import operating_research as op

    def balance(row):
        if row["period_end"] == "2024-12-31":
            raise ValueError("Required financial fact is missing: current portion long term debt.")
        return (None, None, None, 45492.0)
    monkeypatch.setattr(op, "_balance", balance)
    packet = {"actuals": {"periods": [
        {"period_end": "2024-12-31", "metrics": {"interest_expense": 1656e6}, "filing": {"url": "a"}},
        {"period_end": "2025-12-31", "metrics": {"interest_expense": 1654e6}, "filing": {"url": "b"}}]}}
    cost = mv.debt_cost(packet, .05)
    assert abs(cost["value"] - 1654/45492) < 1e-12
    assert cost["basis"] == "Filed interest expense / closing carrying debt" and cost["prior_url"] == "b"
