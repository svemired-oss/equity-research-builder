"""Exercise the actual job worker, spreadsheet engine and audited downloads."""
from io import BytesIO
import json
from shutil import which
from time import monotonic, sleep

from openpyxl import load_workbook
from pypdf import PdfReader
import pytest

from equity_research.acceptance import check_pair
from equity_research.model_template import workbook_plan
from equity_research.model_template.guided import fields
from equity_research.operating_research import revenue_candidates
from equity_research.operating_report import fact_sheet
from equity_research.web_api import _ResearchService
from test_market_valuation import FIX, fixture_model
from test_web_api import _http, _post


def test_all_blue_assumption_cells_are_catalogued():
    model=fixture_model()
    plan=workbook_plan(model)
    catalog=fields(plan,model)
    covered={(field["sheet"],field["cell"]) for field in catalog}
    for address,(value,kind,_) in plan["cells"]["Assumptions"].items():
        if kind=="input" and type(value) in (int,float):
            assert ("Assumptions",address) in covered
    assert {field["group"] for field in catalog} == {
        "Drivers","Margins","Capex and working capital","WACC","Terminal"}
    assert all(field["explanation"] for field in catalog)


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_three_guided_changes_pass_job_and_download_validation(tmp_path,monkeypatch):
    data=json.loads((FIX/"operating.json").read_text())
    model=fixture_model()
    packet=data["packet"]
    packet.update(valuation_market=model["valuation_market"],valuation_comps=model["valuation_comps"])
    evidence=(packet,data["treasury"],data["quote"],data["selection"],revenue_candidates(packet),None)
    for key in ("OPENAI_API_KEY","APCA_API_KEY_ID","APCA_API_SECRET_KEY"):
        monkeypatch.setenv(key,"fixture")
    monkeypatch.setenv("SEC_USER_AGENT","Test Builder fixture@example.com")
    service=_ResearchService(tmp_path,None,None,{"snapshots":[],"live_only":True},oracle=True)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(service,"_collect_operating",lambda *_:evidence)
    def finish(payload):
        status,_,body=_post(service,payload)
        assert status==202,body
        run=json.loads(body)
        limit=monotonic()+70
        while run["state"] in ("pending","queued","running") and monotonic()<limit:
            sleep(.05)
            run=service.get_run(run["id"])
        assert run["state"]=="completed",run
        files={}
        for kind,meta in run["downloads"].items():
            status,_,content=_http(service,f"GET {meta['path']} HTTP/1.0\r\n\r\n".encode())
            assert status==200
            files[kind]=content
        return run,files
    try:
        base,old=finish({"ticker":"TEST","mode":"automated"})
        edits=[
            {"target":"margin","value":.22,"reason":"Improved gross margin mix."},
            {"target":"wc_assets","value":1.12,"reason":"Higher receivables intensity."},
            {"target":"erp","value":.05,"reason":"Higher compensation for equity risk."},
        ]
        payload={"ticker":"TEST","mode":"guided","base_run_id":base["id"],"overrides":edits}
        invalid={**payload,"overrides":[{**edits[0],"value":1.2}]}
        status,_,content=_post(service,invalid)
        assert status==400 and b"operating margin below" in content
        run,new=finish(payload)
        final=check_pair(run,new["workbook"],new["report"])
        assert final["dcf"]!=base["summary"]["spot_value_usd"]
        assert fact_sheet(old["workbook"])["changes"]==[]
        changes=fact_sheet(new["workbook"])["changes"]
        assert len(changes)==3
        source=load_workbook(BytesIO(new["workbook"]),read_only=True,data_only=True)
        assert source["Assumptions"]["B3"].value==.22
        assert source["Assumptions"]["B30"].value==1.12
        assert source["WACC"]["B4"].value==.05
        assert all(abs(source["Checks"][f"{c}4"].value)<1e-6 for c in "BCDEF")
        source.close()
        report=" ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(new["report"])).pages)
        assert "Changes from automated case" in report
        for edit,after in zip(edits,("22.00%","112.00%","5.00%"),strict=True):
            assert after in report and edit["reason"] in report
    finally:
        service.close()

