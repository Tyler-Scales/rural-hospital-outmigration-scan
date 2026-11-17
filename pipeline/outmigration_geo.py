"""Shapes and coordinates for the outmigration map.

Reads config.json and data/outmigration_<ccn>.json (from pipeline/outmigration.py), and writes
data/outmigration_geo_<ccn>.json:
  - outlines of the service-area ZIPs (2020 ZCTAs), the counties and the states around the destination hospitals
  - a coordinate for every ZIP that holds a destination hospital, a nearby rural health clinic or the service area,
    taken from the ZCTA's internal point in the 2024 Census Gazetteer

Sources (no key needed):
  - Census TIGERweb generalized boundaries, which are clipped to the shoreline: counties and states from
    Generalized_ACS2024/State_County, ZCTAs from Generalized_TAB2020/PUMA_TAD_TAZ_UGA_ZCTA
  - Census 2024 ZCTA Gazetteer

Hospitals are placed at the internal point of their ZIP, not their street address. At a regional scale that is
a few miles at most.

Run: python3 -I pipeline/outmigration_geo.py [config.json]
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outmigration import load_gazetteer, miles, GAZETTEER  # noqa: E402

TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services"
COUNTIES = TIGER + "/Generalized_ACS2024/State_County/MapServer/11"   # Counties 500K
STATES = TIGER + "/Generalized_ACS2024/State_County/MapServer/7"      # States 500K
ZCTAS = TIGER + "/Generalized_TAB2020/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/3"  # 2020 ZCTAs 500K (layer 1 returns no geometry)
RADIUS = 100        # miles (config "map_radius_miles"): destinations inside go on the map, the rest into a list
SIMPLIFY = 0.0025   # degrees (~250 m), plenty for a map about 1,000 px across


def parent(ccn):
    """Psychiatric (S) and rehab (T) units carry their parent hospital's number with a letter in third place."""
    return ccn if ccn[2].isdigit() else ccn[:2] + "0" + ccn[3:]


def query(layer, params):
    base = {"outSR": 4326, "f": "geojson", "returnGeometry": "true", "maxAllowableOffset": SIMPLIFY}
    url = layer + "/query?" + urllib.parse.urlencode({**base, **params})
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.load(r)["features"]


def envelope_query(layer, bbox, fields):
    lo1, la1, lo2, la2 = bbox
    return query(layer, {"where": "1=1", "outFields": fields, "geometry": f"{lo1},{la1},{lo2},{la2}",
                         "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})


def rounded(geom):
    def r(c):
        return [round(c[0], 4), round(c[1], 4)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def main(config_path):
    cfg = json.loads(Path(config_path).read_text())
    ccn, radius = cfg["ccn"], cfg.get("map_radius_miles", RADIUS)
    data = Path(config_path).resolve().parent / "data"
    om = json.loads((data / f"outmigration_{ccn}.json").read_text())
    P, zips = om["providers"], om["zips"]
    gaz = load_gazetteer()
    origin = gaz[zips[0]]

    dest = {parent(r["ccn"]) for y, rows in om["hsaf"].items() if y != "2015" for r in rows}
    hosp_zips = {P[c]["zip"] for c in dest if c in P and P[c].get("zip") in gaz}
    near = [z for z in hosp_zips if miles(origin, gaz[z]) <= radius]
    pts = [gaz[z] for z in near + zips]
    pad_la = 0.08 * (max(p[0] for p in pts) - min(p[0] for p in pts))
    pad_lo = 0.08 * (max(p[1] for p in pts) - min(p[1] for p in pts))
    bbox = (round(min(p[1] for p in pts) - pad_lo, 3), round(min(p[0] for p in pts) - pad_la, 3),
            round(max(p[1] for p in pts) + pad_lo, 3), round(max(p[0] for p in pts) + pad_la, 3))
    print(f"{len(near)} destination ZIPs within {radius} miles; map extent {bbox}")

    counties = envelope_query(COUNTIES, bbox, "NAME,STATE,COUNTY")
    states = envelope_query(STATES, bbox, "NAME,STUSAB,STATE")
    zl = ",".join(f"'{z}'" for z in zips)
    zctas = query(ZCTAS, {"where": f"ZCTA5 IN ({zl})", "outFields": "ZCTA5"})
    print(f"{len(counties)} counties, {len(states)} states, {len(zctas)} ZCTAs")

    point_zips = hosp_zips | {r["zip"] for r in om["rhcs"]} | set(zips)
    out = {
        "ccn": ccn, "radius_miles": radius, "bbox": bbox,
        "sources": {"counties": COUNTIES, "states": STATES, "zctas": ZCTAS, "gazetteer": GAZETTEER},
        "counties": [{"name": f["properties"]["NAME"], "fips": f["properties"]["STATE"] + f["properties"]["COUNTY"],
                      "geometry": rounded(f["geometry"])} for f in counties],
        "states": [{"name": f["properties"]["NAME"], "abbr": f["properties"]["STUSAB"], "geometry": rounded(f["geometry"])} for f in states],
        "zctas": [{"zip": f["properties"]["ZCTA5"], "geometry": rounded(f["geometry"])} for f in zctas],
        "zip_points": {z: [round(gaz[z][0], 4), round(gaz[z][1], 4)] for z in sorted(point_zips) if z in gaz},
    }
    path = data / f"outmigration_geo_{ccn}.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {path}  ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "config.json")
