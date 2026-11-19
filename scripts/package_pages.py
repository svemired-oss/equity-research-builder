"""Stage public Pages files from approved dated pairs and independently checked benchmark runs."""
import argparse
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZipFile

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark import failure_type, load_universe
from equity_research.acceptance import check_pair
from equity_research.operating_report import fact_sheet, verify_report
from equity_research.web_catalog import _NONPUBLIC_XLSX_FILES, _NONPUBLIC_XLSX_PARTS, _catalog

BENCHMARK_SOURCE = "https://github.com/svemired-oss/equity-research-builder/blob/phase-1-coverage/benchmarks/results.md"


def _safe_pair(workbook, pdf):
    with ZipFile(BytesIO(workbook)) as archive:
        for name in archive.namelist():
            part = name.casefold()
            if part.startswith(_NONPUBLIC_XLSX_PARTS) or part in _NONPUBLIC_XLSX_FILES:
                raise ValueError("The workbook contains embedded or external content.")
            if part.endswith((".xml",".rels")):
                data=archive.read(name)
                if re.search(rb"(?:[a-z]:\\(?:users|onedrive)\\|file:/+|/workspaces/|sk-(?:proj-)?[a-z0-9_-]{20,})",data,re.I):
                    raise ValueError("A workbook contains a local path or credential-like content.")
    report = PdfReader(BytesIO(pdf))
    if any(k in report.root_object for k in ("/AA","/OpenAction","/AcroForm","/AF","/Names")):
        raise ValueError("A report contains active or embedded material.")
    for page in report.pages:
        for ref in page.get("/Annots") or []:
            item=ref.get_object()
            action=item.get("/A") or {}
            url=urlsplit(action.get("/URI",""))
            if item.get("/Subtype")!="/Link" or action.get("/S")!="/URI" or url.scheme!="https" or not url.hostname or url.username or url.password:
                raise ValueError("A report contains an unapproved annotation.")


def benchmark_summary(rows, phase, source):
    expected = {(item["ticker"],item["sector"]) for item in load_universe(ROOT/"benchmarks/tickers.csv")}
    observed = {(item["ticker"],item["sector"]) for item in rows}
    if len(rows)!=len(expected) or observed!=expected or any(
            row["status"] not in ("PASS","FAIL") or row["stage"]=="cache" for row in rows):
        raise ValueError("Supply a complete, fixed-universe benchmark with no missing caches.")
    if not source.startswith("https://github.com/svemired-oss/equity-research-builder/"):
        raise ValueError("The benchmark source must be the project's published report.")
    failures=Counter(failure_type(row["reason"]) for row in rows if row["status"]=="FAIL")
    return {"phase":phase,"passed":len(rows)-sum(failures.values()),"total":len(rows),
            "failures":dict(sorted(failures.items())),"source":source}


def _new_snapshot(row, folder):
    ticker = row["ticker"]
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}",ticker):
        raise ValueError("Invalid public ticker.")
    cutoff = date.fromisoformat(row["cutoff"])
    stamp = f"{ticker}-{cutoff}"
    contents = {ext:(folder/f"{stamp}.{ext}").read_bytes() for ext in ("xlsx","pdf")}
    for ext,key in (("xlsx","workbook_sha256"),("pdf","report_sha256")):
        if sha256(contents[ext]).hexdigest()!=row.get(key):
            raise ValueError(f"{ticker} {ext} no longer matches the benchmark.")
    _safe_pair(contents["xlsx"],contents["pdf"])
    verify_report(contents["xlsx"],contents["pdf"])
    sheet=fact_sheet(contents["xlsx"])
    facts={key:entry["value"] for key,entry in sheet["facts"].items()}
    if facts["ticker"]!=ticker or facts["date"]!=str(cutoff) or sheet["changes"]:
        raise ValueError("The registered public files must be the named automated benchmark pair.")
    run={"source_mode":"operating_research","summary":{"rating":row["rating"],"spot_value_usd":row["value"]}}
    check_pair(run,contents["xlsx"],contents["pdf"])
    if facts["upside"] is None:
        raise ValueError("The pair lacks a verified valuation.")
    price_date=str(date.fromisoformat(row["price_date"]))
    snapshot={"ticker":ticker,"company":facts["company"],"sector":row["sector"],"as_of":str(cutoff),
              "reference_close_usd":facts["close"],"price_date":price_date,"rating":facts["rating"],
              "spot_value_usd":facts["dcf"],"spot_upside":facts["upside"],"case":"base",
              "description":facts["method"],"driver_type":row["driver_type"],
              "cases":[{"case":case,"spot_value_usd":facts[key]} for case,key in
                       (("bear","bear_value"),("base","dcf"),("bull","bull_value"))]}
    if type(facts["target"]) in (int,float):
        snapshot.update(target_price_usd=facts["target"],total_return=facts["total_return"],
                        target_date=str(cutoff+timedelta(days=365)))
    files={f"{stamp}.{ext}":blob for ext,blob in contents.items()}
    snapshot["downloads"]={kind:{"path":f"downloads/{stamp}.{ext}","filename":f"{stamp}.{ext}",
                                  "sha256":sha256(contents[ext]).hexdigest(),"size_bytes":len(contents[ext])}
                           for kind,ext in (("workbook","xlsx"),("report","pdf"))}
    return snapshot,files


def package(root, destination, rows, phase, source, *, artifacts=None, allow_incomplete=False):
    if destination.exists():
        raise FileExistsError("Choose a new Pages staging directory.")
    catalog,files=_catalog(root)
    for row in catalog["snapshots"]:
        _safe_pair(files[row["downloads"]["workbook"]["filename"]],files[row["downloads"]["report"]["filename"]])
    new=[]
    if artifacts:
        for row in rows:
            if row["status"]!="PASS" or row["ticker"] in {"GD","FDX","NEM"}:
                continue
            if not (artifacts/f'{row["ticker"]}-{row.get("cutoff")}.xlsx').is_file():
                continue
            snapshot,pair=_new_snapshot(row,artifacts)
            new.append(snapshot)
            files.update(pair)
    if len(new)>10:
        raise ValueError("Supply exactly ten nominated benchmark pairs in the artifact directory.")
    if not allow_incomplete and (len(new)!=10 or len({row["driver_type"] for row in new})<2):
        raise ValueError("Public acceptance needs ten additional verified runs across multiple driver types.")
    catalog["snapshots"].extend(new)
    catalog.update(public_downloads=True,benchmark=benchmark_summary(rows,phase,source))
    destination.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        stage=Path(temp)/"release"
        stage.mkdir()
        markup=(ROOT/"web/dist/index.html").read_text(encoding="utf-8")
        markup=markup.replace('<html lang="en">','<html lang="en" data-preview="public">')
        markup=markup.replace('<fieldset class="mode-field">','<fieldset class="mode-field" hidden>')
        (stage/"index.html").write_text(markup,encoding="utf-8")
        for name in ("app.js","styles.css"):
            (stage/name).write_bytes((ROOT/"web/dist"/name).read_bytes())
        (stage/".nojekyll").touch()
        (stage/"catalog.json").write_text(json.dumps(catalog,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
        (stage/"downloads").mkdir()
        for filename,blob in files.items():
            (stage/"downloads"/filename).write_bytes(blob)
        stage.rename(destination)
    return len(new)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True,help="Private directory with the approved dated outputs.")
    parser.add_argument("--destination",type=Path,required=True)
    parser.add_argument("--benchmark",type=Path,default=ROOT/"benchmarks/results.json")
    parser.add_argument("--phase",default="Phase 1 benchmark")
    parser.add_argument("--source",default=BENCHMARK_SOURCE)
    parser.add_argument("--artifacts",type=Path)
    parser.add_argument("--allow-incomplete",action="store_true")
    args=parser.parse_args()
    rows=json.loads(args.benchmark.read_text(encoding="utf-8"))
    count=package(args.root.resolve(),args.destination,rows,args.phase,args.source,
                  artifacts=args.artifacts,allow_incomplete=args.allow_incomplete)
    print(f"Staged {count} benchmark pairs plus three approved dated pairs.")


if __name__=="__main__":
    main()

