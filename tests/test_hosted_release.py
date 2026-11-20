"""Hosted authentication, cached jobs and durable spend enforcement."""
from base64 import b64encode
import json
from urllib.error import URLError
from time import monotonic, sleep

import pytest

from equity_research import operating_report, research_agent
from equity_research.operating_research import ResearchGap
from equity_research.spend_budget import MODEL, enforce, worst_case
from equity_research.web_api import _ResearchService
from equity_research.web_jobs import JobStore
from test_web_api import _http


def test_provider_retry_and_report_writer_share_durable_daily_budget(tmp_path,monkeypatch):
    from threading import Lock
    db = tmp_path/"cap.sqlite"
    store = JobStore(db)
    lock = Lock()
    cap = 103_600
    calls=[]
    def unavailable(request, **kwargs):
        calls.append(request.full_url)
        raise URLError("Simulated interrupted provider call")
    monkeypatch.setattr(operating_report,"urlopen",unavailable)
    monkeypatch.setattr(operating_report,"thesis_options",lambda _:["a","b","c","d"])
    assert worst_case({"model":MODEL,"max_output_tokens":1800}) == cap
    with enforce((store,lock,cap)):
        with pytest.raises(URLError):
            operating_report.write_thesis({},key="fixture",model=MODEL)
    store.close()
    reopened = JobStore(db)
    with enforce((reopened,lock,cap)):
        with pytest.raises(ResearchGap,match="spend cap"):
            operating_report.write_thesis({},key="fixture",model=MODEL)
    reopened.close()
    assert len(calls)==1


def test_research_retry_spends_only_with_remaining_room(tmp_path,monkeypatch):
    from threading import Lock
    packet={"actuals":{"periods":[{"filing":{"url":"https://example.com/filing"}}]},
            "issuer":{},"revenue_guidance":{},"event_revenue_guidance":{},"earnings":{"releases":[]}}
    calls=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*exc): return None
        def read(self,limit):
            return b'{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}'
    def endpoint(*args,**kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(research_agent,"urlopen",endpoint)
    store=JobStore(tmp_path/"usage.sqlite")
    try:
        with enforce((store,Lock(),112_000)):
            with pytest.raises(ResearchGap,match="spend cap"):
                research_agent.select_forecast(packet,{"fixture":{}},key="fixture",model=MODEL)
        assert len(calls)==1
    finally:
        store.close()


def test_hosted_routes_require_password_and_reuse_completed_pair(tmp_path,monkeypatch):
    password="synthetic-test-password"
    env={"RENDER_EXTERNAL_HOSTNAME":"fixture.onrender.com","RECRUITER_PASSWORD":password,
         "OPENAI_DAILY_CAP_USD":".50","RESEARCH_MODEL":MODEL,"RESEARCH_STATE_DIR":str(tmp_path/"state"),
         "SEC_USER_AGENT":"Fixture test contact@example.com","OPENAI_API_KEY":"test",
         "APCA_API_KEY_ID":"test","APCA_API_SECRET_KEY":"test"}
    for k,v in env.items():
        monkeypatch.setenv(k,v)
    service=_ResearchService(tmp_path,None,None,{"snapshots":[],"live_only":True},oracle=True)
    pair={"workbook":b"verified-test-workbook","report":b"verified-test-report"}
    calls=[]
    def finish(run_id):
        calls.append(run_id)
        service._save_result(run_id,{"workbook":"sample.xlsx","report":"sample.pdf"},{},[],pair)
    monkeypatch.setattr(service,"_finish_operating",finish)
    token=b64encode(("recruiter:"+password).encode())
    def request(method,path,body=None,auth=True,tls=True,fetch_site=None):
        request=f"{method} {path} HTTP/1.0\r\nHost: fixture.onrender.com\r\n".encode()
        if tls: request+=b"X-Forwarded-Proto: https\r\n"
        if auth: request+=b"Authorization: Basic "+token+b"\r\n"
        if fetch_site: request+=f"Sec-Fetch-Site: {fetch_site}\r\n".encode()
        if body is not None:
            body=json.dumps(body).encode()
            request+=f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n".encode()
        return _http(service,request+b"\r\n"+(body or b""))
    try:
        assert request("GET","/healthz",auth=False,tls=False)[0]==200
        status,headers,_=request("GET","/",auth=False)
        assert status==401 and b"WWW-Authenticate: Basic" in headers
        assert request("GET","/",tls=False)[0]==403
        assert request("GET","/api/companies")[0]==200
        assert request("GET","/private/web-runs/research-jobs.sqlite3")[0]==404
        payload={"ticker":"NVDA","mode":"automated"}
        assert request("POST","/api/runs",payload,fetch_site="cross-site")[0]==403
        status,_,body=request("POST","/api/runs",payload)
        assert status==202
        job_id=json.loads(body)["id"]
        limit=monotonic()+3
        run=service.get_run(job_id)
        while run["state"]!="completed" and monotonic()<limit:
            sleep(.01)
            run=service.get_run(job_id)
        assert run["state"]=="completed"
        for kind,item in run["downloads"].items():
            assert request("GET",item["path"],auth=False)[0]==401
            assert request("GET",item["path"])[2]==pair[kind]
        status,_,body=request("POST","/api/runs",payload)
        assert status==200 and json.loads(body)["id"]==job_id and len(calls)==1
    finally:
        service.close()

