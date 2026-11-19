"""Regenerate the three published base cases and compare both per-share values."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from equity_research.web_api import _generate
from equity_research.web_catalog import _catalog, _SNAPSHOTS


def compare(root, destination):
    catalog,_ = _catalog(root)
    for spec,item in zip(_SNAPSHOTS,catalog["snapshots"],strict=True):
        names={"workbook":f"{spec.ticker}-shared.xlsx","report":f"{spec.ticker}-shared.pdf"}
        summary,cases,files=_generate(spec.ticker,spec.as_of,root/"inputs",None,None,"base",[],names)
        for key in ("spot_value_usd","target_price_usd"):
            published, regenerated=item[key],summary[key]
            change=regenerated/published-1
            print(f"{spec.ticker} {key}: {published:.6f} -> {regenerated:.6f} ({change:+.6%})",flush=True)
            if abs(change)>.01:
                raise ValueError(f"{spec.ticker} {key} exceeds the permitted 1% difference.")
        if summary["rating"]!=item["rating"]:
            raise ValueError(f"{spec.ticker} rating changed under identical dated assumptions.")
        for kind,name in names.items():
            with (destination/name).open("xb") as output:
                output.write(files[kind])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",required=True,type=Path,help="Private directory containing dated evidence and published outputs")
    parser.add_argument("--output",required=True,type=Path,help="New directory for verified parity artifacts")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    compare(args.root.resolve(),args.output)


if __name__=="__main__":
    main()

