from equity_research.fdx_fresh_excel import SHEETS as FDX_SHEETS
from equity_research.model_template import SHEETS
from equity_research.model_template.reference import _rename, reference_plan


def test_only_formula_references_are_renamed():
    assert _rename('=IF(A1="Revenue",Revenue!B5,\'Cash flow\'!B7)',
                   {"Revenue":"Revenue Build","Cash flow":"Cash Flow Statement"}) == (
        '=IF(A1="Revenue",\'Revenue Build\'!B5,\'Cash Flow Statement\'!B7)')
    assert _rename("=SUM('Cash flow'!D20:'Cash flow'!D22)",{"Cash flow":"Cash Flow Statement"}) == (
        "=SUM('Cash Flow Statement'!D20:D22)")


def test_reference_plan_uses_shared_spine_and_retains_independent_checks():
    cells = {tab:{"A1":(tab,"title",None)} for tab in FDX_SHEETS}
    cells["Revenue"]["B5"] = ("='Cash flow'!B7","text",None)
    original = {"case":"base","cells":cells,"expected":{},
                "scenarioExpected":{case:{"Revenue":{"B5":100}} for case in ("base","bear","bull")}}
    plan = reference_plan("FDX",original)
    assert tuple(plan["cells"])[:len(SHEETS)] == SHEETS
    assert plan["expected"]["Revenue Build"]["B5"] == 100
    assert set(plan["scenarioExpected"]) == {"bear","base","bull"}
    assert plan["cells"]["Revenue Build"]["B5"][0] == "='Cash Flow Statement'!B7"
    assert "Debt schedule" in plan["cells"]
    assert original["cells"]["Revenue"]["B5"][0] == "='Cash flow'!B7"

