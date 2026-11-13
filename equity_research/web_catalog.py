"""Read and stage the verified dated workbooks and research reports for the public demo."""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import json
from math import isclose, isfinite
from pathlib import Path
import re
from urllib.parse import urlsplit
from zipfile import ZipFile

from openpyxl import load_workbook
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject


@dataclass(frozen=True)
class _Snapshot:
    ticker: str
    company: str
    as_of: date
    price_date: date
    workbook_hash: str
    report_hash: str
    pages: int
    sector: str
    description: str


# These fingerprints identify the exact public pairs already checked by the research exporters.
_SNAPSHOTS = (
    _Snapshot("GD", "General Dynamics", date(2026, 11, 9), date(2026, 11, 6),
              "5da9a52407d253da29030b872791d2ec69d9a2b5dfa9e0233fe81edd93bdea02",
              "47a9df4d6d42d7f3d417c6983edcfec838ffd90387dfba821b66a2c3352906d0", 5,
              "Aerospace and defense", "Builds Gulfstream business jets and submarines, and operates Combat Systems and Technologies businesses."),
    _Snapshot("FDX", "FedEx Corporation", date(2026, 11, 12), date(2026, 11, 11),
              "8ed539d0a0e4a5277f4217e6c2c2f2eaf9608f620673b1fbf7e90586e2bc3252",
              "cb8f297940df618a9b6307ee6db60924bdad66904ba29a5df19137a7b1491f01", 6,
              "Transportation", "Provides parcel and air freight services through U.S. domestic and international networks."),
    _Snapshot("NEM", "Newmont Corporation", date(2026, 11, 12), date(2026, 11, 11),
              "afad3b716cc6fcd1d7a4270e60e1ff438575092987a70d2601a003c642d5bea0",
              "63f9be697c8e3920676914abb43f89ba2c4a792e35006ee0be8869079080f25d", 6,
              "Gold mining", "Operates gold mines and produces copper, silver, lead and zinc at selected sites."),
)
_CASES = ("bear", "base", "bull")
_RESULT_FIELDS = ("spot_value_usd", "target_price_usd", "total_return", "rating")
_NONPUBLIC_XLSX_PARTS = ("xl/externallinks/", "xl/webextensions/", "xl/querytables/", "xl/embeddings/", "customxml/")
_NONPUBLIC_XLSX_FILES = {"xl/connections.xml", "xl/vbaproject.bin"}


def _approved_file(root: Path, snapshot: _Snapshot, label: str, extension: str, expected_hash: str):
    day = snapshot.as_of.isoformat()
    filename = f"{snapshot.ticker}_{label}_{day}.{extension}"
    path = root / "outputs" / f"{snapshot.ticker.lower()}-{day}" / filename
    if not path.is_file() or path.resolve() != path or path.is_symlink():
        raise ValueError(f"Missing or redirected approved demo file {filename}.")
    contents = path.read_bytes()
    if sha256(contents).hexdigest() != expected_hash:
        raise ValueError(f"Approved demo file fingerprint mismatch for {filename}.")
    return filename, contents


def _public_pair(root, snapshot):
    workbook_name, workbook = _approved_file(root, snapshot, "model", "xlsx", snapshot.workbook_hash)
    report_name, report = _approved_file(root, snapshot, "research", "pdf", snapshot.report_hash)
    if snapshot.ticker == "GD":
        # The verified native Excel save recorded the local account in the document properties.
        # Retain the original formula and cache bytes and only remove that account field.
        scrubbed = BytesIO()
        with ZipFile(BytesIO(workbook)) as source, ZipFile(scrubbed, "w") as target:
            for member in source.infolist():
                data = source.read(member.filename)
                if member.filename == "docProps/core.xml":
                    data, removed = re.subn(rb"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>", b"", data)
                    if removed != 1:
                        raise ValueError("Cannot locate the approved GD workbook account metadata.")
                target.writestr(member, data)
        workbook = scrubbed.getvalue()
        public_hash = sha256(workbook).hexdigest()

        # The PDF names its exact workbook in metadata and on the evidence page.
        # Replace only those two occurrences and verify the page streams on reopening.
        previous = snapshot.workbook_hash.encode("ascii")
        current = public_hash.encode("ascii")
        writer = PdfWriter(clone_from=BytesIO(report))
        original_pages = [page.get_contents().get_data() for page in writer.pages]
        if sum(page.count(previous) for page in original_pages) != 1:
            raise ValueError("Cannot locate the approved GD report workbook fingerprint.")
        for page, data in zip(writer.pages, original_pages, strict=True):
            if previous in data:
                stream = DecodedStreamObject()
                stream.set_data(data.replace(previous, current))
                page.replace_contents(stream)
        writer.add_metadata({"/Keywords": f"workbook-sha256:{public_hash}"})
        rebound = BytesIO()
        writer.write(rebound)
        report = rebound.getvalue()
        public_pages = PdfReader(BytesIO(report)).pages
        if any(page.get_contents().get_data() != data.replace(previous, current)
               for page, data in zip(public_pages, original_pages, strict=True)):
            raise ValueError("The GD report page content changed during metadata sanitization.")
    return {"workbook": (workbook_name, workbook), "report": (report_name, report)}


def _number(value):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError("A demo valuation must contain finite numbers.")
    return value


def _day(value):
    return value.date() if isinstance(value, datetime) else value


def _outcome(case, values):
    spot, target, total, rating = values
    if case not in _CASES or rating not in {"Buy", "Hold", "Sell"}:
        raise ValueError("A demo valuation contains an unrecognized case or rating.")
    return {"case": case, "spot_value_usd": _number(spot), "target_price_usd": _number(target),
            "total_return": _number(total), "rating": rating}


def _report_pages(contents, snapshot, workbook_hash):
    report = PdfReader(BytesIO(contents))
    metadata = report.metadata or {}
    if (metadata.get("/Keywords") != f"workbook-sha256:{workbook_hash}" or
            len(report.pages) != snapshot.pages or snapshot.company not in metadata.get("/Title", "") or
            not metadata.get("/Subject", "").startswith("Base ")):
        raise ValueError(f"{snapshot.ticker} research does not identify the approved base workbook.")
    if any(key in report.root_object for key in ("/AA", "/OpenAction", "/Names", "/AcroForm", "/AF")):
        raise ValueError(f"{snapshot.ticker} research contains an active action or embedded file.")
    text = []
    cutoff_label = f"{snapshot.as_of:%B} {snapshot.as_of.day}, {snapshot.as_of.year}"
    for page in report.pages:
        paragraph = " ".join(page.extract_text().split())
        if cutoff_label not in paragraph:
            raise ValueError(f"{snapshot.ticker} research is missing its dated cutoff.")
        for annotation in page.get("/Annots") or ():
            annotation = annotation.get_object()
            action = annotation.get("/A") or {}
            uri = urlsplit(action.get("/URI", ""))
            if (annotation.get("/Subtype") != "/Link" or action.get("/S") != "/URI" or
                    uri.scheme != "https" or not uri.hostname or uri.username or uri.password):
                raise ValueError(f"{snapshot.ticker} research contains an unapproved annotation.")
        text.append(paragraph)
    return text


def _gd_cases(workbook, valuation_page):
    thresholds = workbook["Valuation inputs"]
    buy, sell = (_number(thresholds[coord].value) for coord in ("B14", "B15"))
    if not -1 < sell < 0 < buy:
        raise ValueError("General Dynamics has invalid rating thresholds.")
    cases = []
    for case in _CASES:
        row = re.search(rf"\b{case.title()}\s+\d+\s+\d+\.\d+%\s+\$([\d,.]+)\s+\$([\d,.]+)\s+([+-]?\d+\.\d+)%", valuation_page)
        if row is None:
            raise ValueError(f"General Dynamics is missing the published {case} case.")
        spot, target, total = (float(value.replace(",", "")) for value in row.groups())
        total /= 100
        rating = "Buy" if total >= buy else "Sell" if total <= sell else "Hold"
        cases.append(_outcome(case, (spot, target, total, rating)))
    return cases


def _read_workbook(contents, snapshot, report_pages):
    with ZipFile(BytesIO(contents)) as archive:
        for name in archive.namelist():
            path = name.casefold()
            if path.startswith(_NONPUBLIC_XLSX_PARTS) or path in _NONPUBLIC_XLSX_FILES:
                raise ValueError(f"{snapshot.ticker} workbook contains external content or an embedded object.")
    formula_cells = ("E18", "E19", "E23", "E15") if snapshot.ticker == "GD" else ("B9", "B10", "B11", "B12")
    formulas = load_workbook(BytesIO(contents), read_only=True, data_only=False, keep_links=False)
    try:
        if any(formulas["Cover"][cell].data_type != "f" for cell in formula_cells):
            raise ValueError(f"{snapshot.ticker} workbook is missing editable valuation formulas.")
    finally:
        formulas.close()
    with BytesIO(contents) as workbook_source:
        workbook = load_workbook(workbook_source, read_only=True, data_only=True, keep_links=False)
        try:
            if any(sheet.sheet_state != "visible" for sheet in workbook):
                raise ValueError(f"{snapshot.ticker} demo workbook contains hidden sheets.")
            cover = workbook["Cover"]
            if snapshot.ticker == "GD":
                as_of, price_date, target_date = (_day(cover[cell].value) for cell in ("E11", "E12", "E14"))
                close = _number(cover["E17"].value)
                selected, mode = cover["E13"].value, "automated"
                base = _outcome("base", (cover[cell].value for cell in ("E18", "E19", "E23", "E15")))
                cases = _gd_cases(workbook, report_pages[3])
            else:
                as_of, target_date = (_day(cover[cell].value) for cell in ("B3", "B14"))
                close = _number(cover["B4"].value)
                selected, mode = cover["B5"].value, str(cover["B6"].value).casefold()
                base = _outcome("base", (cover[cell].value for cell in ("B9", "B10", "B11", "B12")))
                if snapshot.ticker == "FDX":
                    price_date = _day(workbook["DCF"]["B4"].value)
                    cases = [_outcome(str(cover[f"A{row}"].value).casefold(),
                                      (cover[f"{column}{row}"].value for column in "BCDE")) for row in range(18, 21)]
                else:
                    price_date = _day(cover["B7"].value)
                    cases = [_outcome(str(cover[f"{column}18"].value).casefold(),
                                      (cover[f"{column}{row}"].value for row in range(19, 23))) for column in "BCD"]
        finally:
            workbook.close()
    if ((as_of, price_date, target_date) != (snapshot.as_of, snapshot.price_date, snapshot.as_of.replace(year=snapshot.as_of.year + 1)) or
            selected != "Base" or mode != "automated" or close <= 0):
        raise ValueError(f"{snapshot.ticker} workbook dates, case, or mode differ from the published snapshot.")
    if tuple(case["case"] for case in cases) != _CASES or cases[1]["rating"] != base["rating"]:
        raise ValueError(f"{snapshot.ticker} workbook case order or rating differs from the published scenario comparison.")
    for key in _RESULT_FIELDS[:-1]:
        tolerance = (0.00005 if key == "total_return" else 0.005) if snapshot.ticker == "GD" else 1e-6
        if not isclose(base[key], cases[1][key], rel_tol=0, abs_tol=tolerance):
            raise ValueError(f"{snapshot.ticker} published base case differs from the workbook's {key}.")
    first_page = report_pages[0]
    dollar_values = (close, base["spot_value_usd"], base["target_price_usd"])
    if (f"Model rating {base['rating']}" not in first_page or f"{base['total_return']:+.2%}" not in first_page or
            any(f"${amount:,.2f}" not in first_page for amount in dollar_values)):
        raise ValueError(f"{snapshot.ticker} PDF headline values differ from the workbook.")
    cases[1] = base
    return {"as_of": as_of.isoformat(), "price_date": price_date.isoformat(), "target_date": target_date.isoformat(),
            "mode": mode, "reference_close_usd": close, **base, "cases": cases}


def _catalog(root: Path):
    root = root.resolve()
    snapshots, files = [], {}
    for spec in _SNAPSHOTS:
        pair = _public_pair(root, spec)
        downloads = {}
        for kind, (filename, contents) in pair.items():
            downloads[kind] = {"filename": filename, "path": f"downloads/{filename}",
                               "sha256": sha256(contents).hexdigest(), "size_bytes": len(contents)}
            files[filename] = contents
        workbook_bytes, report_bytes = pair["workbook"][1], pair["report"][1]
        values = _read_workbook(workbook_bytes, spec, _report_pages(report_bytes, spec, downloads["workbook"]["sha256"]))
        snapshots.append({"ticker": spec.ticker, "company": spec.company, "sector": spec.sector, "description": spec.description,
                          **values, "downloads": downloads})
    return {"schema_version": 1, "snapshots": snapshots}, files


def build_catalog(root: Path) -> dict:
    """Return the serializable summary of the three approved automated base snapshots."""
    catalog, _ = _catalog(root)
    return catalog


def read_demo_download(root: Path, ticker: str, kind: str) -> bytes:
    """Read a public demo download from its verified source pair."""
    snapshot = next((snapshot for snapshot in _SNAPSHOTS if snapshot.ticker == ticker), None)
    if snapshot is None or kind not in ("workbook", "report"):
        raise ValueError("Unknown demo download.")
    return _public_pair(root.resolve(), snapshot)[kind][1]


def package_static_demo(root: Path, destination: Path) -> dict:
    """Write the catalog and six approved downloads into a static site directory."""
    catalog, files = _catalog(root)
    downloads = destination / "downloads"
    catalog_file = destination / "catalog.json"
    if destination.is_symlink() or downloads.is_symlink() or catalog_file.is_symlink() or catalog_file.exists():
        raise FileExistsError("Use a static demo directory without a catalog or redirected downloads.")
    if downloads.exists() and (not downloads.is_dir() or any(downloads.iterdir())):
        raise FileExistsError("The static demo downloads directory must be empty.")

    destination.mkdir(parents=True, exist_ok=True)
    created_downloads = not downloads.exists()
    downloads.mkdir(exist_ok=True)
    created = []
    try:
        for path, contents in (
            *((downloads / filename, content) for filename, content in files.items()),
            (catalog_file, (json.dumps(catalog, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")),
        ):
            with path.open("xb") as output:
                created.append(path)
                output.write(contents)
    except OSError:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        if created_downloads and not any(downloads.iterdir()):
            downloads.rmdir()
        raise
    return catalog
