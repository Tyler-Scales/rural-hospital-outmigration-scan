"""Draft config.json for one hospital: its county and the ZIP codes that make up its service area.

The default service area is every ZIP code tabulation area (ZCTA) with more than half its land in the hospital's
county, from the Census 2020 ZCTA-to-county relationship file. The hospital's county is the one holding most of
its own ZIP's land. ZIPs that are partly in the county are listed so they can be added by hand.

Uses public data only: CMS Provider of Services (name, ZIP, state) and the Census relationship file.

Run: python3 -I pipeline/service_area.py <ccn> [--county FIPS] [--min-share 0.5] [--out config.json] [--force]
  --county     use this 5-digit county FIPS instead of the one found from the hospital's ZIP
  --min-share  share of a ZIP's land that must lie in the county (default 0.5)
  --out        where to write the draft (default config.json); an existing file is kept unless --force
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outmigration import STATE_NAMES, fetch_pos  # noqa: E402

REL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"
CACHE = Path(__file__).resolve().parent / "cache" / "geo"


def relationship():
    """{zcta: [(county_fips, county_name, share_of_zcta_land)]} from the Census file (cached)."""
    path = CACHE / "tab20_zcta520_county20_natl.txt"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(REL, timeout=300) as r:
            path.write_bytes(r.read())
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            z, total = row["GEOID_ZCTA5_20"], int(row["AREALAND_ZCTA5_20"] or 0)
            if z and total:
                out.setdefault(z, []).append((row["GEOID_COUNTY_20"], row["NAMELSAD_COUNTY_20"], int(row["AREALAND_PART"] or 0) / total))
    return out


def args(argv):
    opts = {"county": None, "min_share": 0.5, "out": "config.json", "force": False}
    pos = []
    it = iter(argv)
    for a in it:
        if a == "--county":
            opts["county"] = next(it)
        elif a == "--min-share":
            opts["min_share"] = float(next(it))
        elif a == "--out":
            opts["out"] = next(it)
        elif a == "--force":
            opts["force"] = True
        else:
            pos.append(a)
    if not pos:
        sys.exit(__doc__)
    return pos[0].strip().zfill(6), opts


def main(argv):
    ccn, o = args(argv)
    p = fetch_pos([ccn])[ccn]
    if not p.get("name"):
        sys.exit(f"CCN {ccn} is not in the CMS Provider of Services file")
    cah = ccn[2:6].isdigit() and 1300 <= int(ccn[2:6]) <= 1399
    print(f"{p['name'].title()} ({ccn}), ZIP {p['zip']}, {p['state']}; critical access hospital: {'yes' if cah else 'NO'}")
    if not cah:
        print("  The value model is built for critical access hospitals (cost-based Medicare). Other hospitals get the scan,")
        print("  but the dollar values will not be right.")
    rel = relationship()
    if o["county"]:
        fips = o["county"]
    elif p["zip"] in rel:
        fips = max(rel[p["zip"]], key=lambda t: t[2])[0]
    else:
        sys.exit(f"ZIP {p['zip']} is not a ZIP code tabulation area (often a PO box ZIP); rerun with --county <FIPS>")
    county = next(n for parts in rel.values() for c, n, s in parts if c == fips)
    inside, partial = [], []
    for z, parts in rel.items():
        share = sum(s for c, n, s in parts if c == fips)
        if share >= o["min_share"]:
            inside.append((z, share))
        elif share >= 0.1:
            partial.append((z, share))
    inside.sort(key=lambda t: (t[0] != p["zip"], -t[1], t[0]))
    print(f"County: {county}, FIPS {fips}")
    print(f"Service area ({len(inside)} ZIPs with at least {o['min_share']:.0%} of their land in the county):")
    for z, s in inside:
        print(f"  {z}  {s:.0%}{'  (hospital ZIP)' if z == p['zip'] else ''}")
    if partial:
        print("Partly in the county (add by hand if they belong):")
        for z, s in sorted(partial, key=lambda t: -t[1]):
            print(f"  {z}  {s:.0%}")
    if not inside:
        sys.exit("no ZIPs found; check the county FIPS")
    cfg = {
        "ccn": ccn, "zips": [z for z, _ in inside], "county_fips": fips,
        "anonymize": False, "hide_towns": False, "map_radius_miles": 100,
        "page_title": f"{county} Outmigration Scan", "headline": f"Keeping care in {county}",
        "prepared_by": "", "hospital_name": p["name"].title(), "hospital_short": p["name"].title().split()[0],
        "state_label": STATE_NAMES.get(p["state"], p["state"]),
        "local_services": "imaging, lab, therapy and GI work",
    }
    out = Path(o["out"])
    if out.exists() and not o["force"]:
        print(f"{out} exists; not overwritten (use --force). Draft:\n" + json.dumps(cfg, indent=2))
        return
    out.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"wrote {out}: review the ZIP list, hospital name and services before running the pipeline")


if __name__ == "__main__":
    main(sys.argv[1:])
