"""Public-data outmigration scan for a rural hospital.

Answers, from public data only: where the service area's Medicare inpatients go, what kinds of stays the
destination hospitals take, how much care the county's beneficiaries use, what the hospital reports it offers,
and whether residents can reach a digital front door (age, internet, smartphone).

Reads the hospital's CMS Certification Number (CCN), service-area ZIP codes and county FIPS code from
config.json, and writes data/outmigration_<ccn>.json.

Run: python3 -I pipeline/outmigration.py config.json

Limits worth knowing:
  - The Hospital Service Area File counts Medicare inpatient stays, and its counts include Medicare Advantage:
    at large destination hospitals they run about 2.3x the fee-for-service discharges in Inpatient by Provider
    (fetch_ma_check). Cells of 1-10 are suppressed ("*"). Commercial, Medicaid and outpatient care are absent.
  - Inpatient by Provider and Service drops DRGs with fewer than 11 discharges at that hospital.
  - POS service codes are what the hospital reported at certification; they can be stale.
  - The Census API now needs a key, so ACS tables are streamed from the table-based summary files instead.
"""
import csv
import io
import json
import math
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

API = "https://data.cms.gov/data-api/v1/dataset/{}/data"

# Hospital Service Area File, keyed by calendar year (data.cms.gov dataset ids)
HSAF_IDS = {
    2015: "93a89060-c812-4955-8433-09cad35d5eb2", 2016: "c9912dd2-fac1-4929-9641-dc031422b458",
    2017: "6628dd7e-9706-4e8a-bd85-f8450308066a", 2018: "263734c7-f5fd-4df2-8436-2322c86e7191",
    2019: "2713ba99-c59e-4b25-9a3d-3661d35988da", 2020: "609f2b73-d7e0-4dd1-a8b1-f951860ad9f7",
    2021: "099b80df-be9c-4274-a974-31d4537bdc33", 2022: "84a0a165-a570-4ffe-a230-043176c1221a",
    2023: "7f749f00-bfa9-4377-9a98-90c15cacc2f3", 2024: "22e11819-48e8-44a8-a036-fd6351f4173a",
    2025: "8708ca8b-8636-44ed-8303-724cbfaf78ad",
}
POS_ID = "8ba0f9b4-9493-4aa0-9f82-44ea9468d1b5"          # Provider of Services, hospitals, 2026-07-01
IP_DRG_ID = "690ddc6c-2767-4618-b277-420ffb2bf27c"       # Medicare Inpatient Hospitals by Provider and Service, CY2024
GEOVAR_ID = "6219697b-8f6c-4164-bed4-cd9317c58ebc"       # Medicare Geographic Variation by National, State & County
TELEHEALTH_ID = "939226be-b107-476e-8777-f199a840138a"   # Medicare Telehealth Trends
RHC_ID = "3b7e7659-067e-41ea-8e36-f9ee2036e1f6"          # Rural Health Clinic Enrollments, 2026-10-01

GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip"
ACS = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-{}.dat"
ACS_TABLES = ["b28005", "b28002", "b28001"]   # age x computer/broadband; household internet; household devices

# POS service codes: 0 not provided, 1 by staff, 2 under arrangement, 3 both
POS_SERVICES = {
    "CT_SCAN_SRVC_CD": "CT", "MGNTC_RSNC_IMG_SRVC_CD": "MRI", "NUCLR_MDCN_SRVC_CD": "Nuclear medicine",
    "DGNSTC_RDLGY_SRVC_CD": "Diagnostic radiology", "LAB_SRVC_CD": "Laboratory", "PT_SRVC_CD": "Physical therapy",
    "OT_SRVC_CD": "Occupational therapy", "SPCH_PTHLGY_SRVC_CD": "Speech pathology", "RSPRTRY_CARE_SRVC_CD": "Respiratory care",
    "DCTD_ER_SRVC_CD": "Emergency department", "OPRTG_ROOM_SRVC_CD": "Operating room", "OP_SRGRY_UNIT_SRVC_CD": "Outpatient surgery",
    "OB_SRVC_CD": "Obstetrics", "ICU_SRVC_CD": "ICU", "CHMTHRPY_SRVC_CD": "Chemotherapy", "OP_PSYCH_SRVC_CD": "Outpatient psych",
    "PSYCH_SRVC_CD": "Psychiatric", "URGNT_CARE_SRVC_CD": "Urgent care", "PHRMCY_SRVC_CD": "Pharmacy",
    "OP_REHAB_SRVC_CD": "Outpatient rehab", "DTRY_SRVC_CD": "Dietary", "SCL_SRVC_CD": "Social services",
}
GEOVAR_FIELDS = [
    "BENES_TOTAL_CNT", "BENES_WTH_PTAPTB_CNT", "BENES_OM_CNT", "BENES_MA_CNT", "MA_PRTCPTN_RATE", "BENE_AVG_AGE", "BENE_DUAL_PCT",
    "TOT_MDCR_PYMT_PC", "IP_MDCR_PYMT_PC", "OP_MDCR_PYMT_PC", "SNF_MDCR_PYMT_PC", "HH_MDCR_PYMT_PC", "ASC_MDCR_PYMT_PC",
    "IP_CVRD_STAYS_PER_1000_BENES", "IP_CVRD_DAYS_PER_1000_BENES", "ACUTE_HOSP_READMSN_PCT", "ER_VISITS_PER_1000_BENES",
    "OP_VISITS_PER_1000_BENES", "ASC_EVNTS_PER_1000_BENES", "SNF_CVRD_STAYS_PER_1000_BENES", "SNF_CVRD_DAYS_PER_1000_BENES",
    "HH_EPISODES_PER_1000_BENES", "EM_EVNTS_PER_1000_BENES", "PRCDRS_EVNTS_PER_1000_BENES", "IMGNG_EVNTS_PER_1000_BENES",
    "TESTS_EVNTS_PER_1000_BENES", "OP_DLYS_EVNTS_PER_1000_BENES", "BENES_EM_PCT", "BENES_IMGNG_PCT", "BENES_TESTS_PCT",
    "PQI03_DBTS_AGE_LT_75", "PQI05_COPD_ASTHMA_AGE_40_74", "PQI07_HYPRTNSN_AGE_LT_75", "PQI08_CHF_AGE_LT_75",
    "PQI11_BCTRL_PNA_AGE_LT_75", "PQI12_UTI_AGE_LT_75", "PQI15_ASTHMA_AGE_LT_40", "PQI16_LWRXTRMTY_AMPUTN_AGE_LT_75",
]


def get(dataset_id, params):
    url = API.format(dataset_id) + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.load(r)


def get_all(dataset_id, params, page=5000):
    out, offset = [], 0
    while True:
        rows = get(dataset_id, {**params, "size": page, "offset": offset})
        out += rows
        if len(rows) < page:
            return out
        offset += page


def count(v):
    """HSAF counts: '*' (1-10, suppressed) becomes None; everything else an int."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def miles(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def fetch_hsaf(ccn, zips):
    by_year = {}
    for year, ds in HSAF_IDS.items():
        rows = []
        for z in zips:
            for r in get_all(ds, {"filter[ZIP_CD_OF_RESIDENCE]": z}):
                rows.append({"zip": z, "ccn": r["MEDICARE_PROV_NUM"], "cases": count(r["TOTAL_CASES"]),
                             "days": count(r["TOTAL_DAYS_OF_CARE"]), "charges": count(r["TOTAL_CHARGES"])})
        by_year[year] = rows
    # where the hospital's own Medicare inpatients come from, latest three years
    own = {}
    for year in sorted(HSAF_IDS)[-3:]:
        own[year] = [{"zip": r["ZIP_CD_OF_RESIDENCE"], "cases": count(r["TOTAL_CASES"]), "days": count(r["TOTAL_DAYS_OF_CARE"])}
                     for r in get_all(HSAF_IDS[year], {"filter[MEDICARE_PROV_NUM]": ccn})]
    return by_year, own


IP_PROVIDER_ID = "df6b25d8-a00d-4d64-96c5-65fb441e51f6"  # Medicare Inpatient Hospitals by Provider, CY2024 (FFS)


def fetch_ma_check(ccns, year=2024):
    """HSAF cases vs fee-for-service discharges at the same hospitals: a ratio well above 1 means HSAF counts MA stays too."""
    out = []
    for c in ccns:
        rows = get_all(HSAF_IDS[year], {"filter[MEDICARE_PROV_NUM]": c})
        ffs = get(IP_PROVIDER_ID, {"filter[Rndrng_Prvdr_CCN]": c})
        hsaf = sum(count(r["TOTAL_CASES"]) or 0 for r in rows)
        n = count(ffs[0]["Tot_Dschrgs"]) if ffs else None
        out.append({"ccn": c, "year": year, "hsaf_cases_known": hsaf, "ffs_discharges": n, "ratio": hsaf / n if n else None})
    return out


def fetch_pos(ccns):
    out = {}
    for c in sorted(ccns):
        rows = get(POS_ID, {"filter[PRVDR_NUM]": c})
        if not rows:
            out[c] = {"ccn": c, "name": None}
            continue
        r = rows[0]
        out[c] = {
            "ccn": c, "name": r["FAC_NAME"], "city": r["CITY_NAME"], "state": r["STATE_CD"], "zip": r["ZIP_CD"][:5],
            "subtype": r["PRVDR_CTGRY_SBTYP_CD"], "beds": count(r["BED_CNT"]), "certified_beds": count(r["CRTFD_BED_CNT"]),
            "terminated": r.get("PGM_TRMNTN_CD") not in ("00", "", None),
            "services": {label: r.get(code) for code, label in POS_SERVICES.items()},
            "rhc_affiliates": count(r.get("TOT_AFLTD_RHC_CNT")), "swing_beds": r.get("CAH_SB_SW"),
        }
    return out


def fetch_drg_mix(ccns):
    return {c: [{"drg": r["DRG_Cd"], "desc": r["DRG_Desc"], "discharges": count(r["Tot_Dschrgs"]),
                 "avg_medicare_payment": float(r["Avg_Mdcr_Pymt_Amt"] or 0)}
                for r in get_all(IP_DRG_ID, {"filter[Rndrng_Prvdr_CCN]": c})]
            for c in ccns}


def fetch_geovar(state, fips):
    out = {}
    for key, flt in {"county": {"BENE_GEO_LVL": "County", "BENE_GEO_CD": fips},
                     "state": {"BENE_GEO_LVL": "State", "BENE_GEO_DESC": state},
                     "us": {"BENE_GEO_LVL": "National"}}.items():
        rows = get_all(GEOVAR_ID, {**{f"filter[{k}]": v for k, v in flt.items()}, "filter[BENE_AGE_LVL]": "All"})
        out[key] = sorted(({"year": int(r["YEAR"]), **{f: r.get(f) for f in GEOVAR_FIELDS}} for r in rows), key=lambda r: r["year"])
    return out


def fetch_telehealth(state_name):
    rows = get_all(TELEHEALTH_ID, {"filter[Bene_Geo_Desc]": state_name, "filter[Quarter]": "Overall",
                                   "filter[Bene_Age_Desc]": "All", "filter[Bene_Race_Desc]": "All", "filter[Bene_Sex_Desc]": "All",
                                   "filter[Bene_Mdcd_Mdcr_Enrl_Stus]": "All", "filter[Bene_Mdcr_Entlmt_Stus]": "All"})
    return [{"year": int(r["Year"]), "ruca": r["Bene_RUCA_Desc"], "pct": float(r["Pct_Telehealth"])}
            for r in rows if r["Pct_Telehealth"] not in ("", None)]


def load_gazetteer():
    with urllib.request.urlopen(GAZETTEER, timeout=180) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    text = z.read(z.namelist()[0]).decode("utf-8")
    rows = csv.reader(io.StringIO(text), delimiter="\t")
    head = [h.strip() for h in next(rows)]
    i, la, lo = head.index("GEOID"), head.index("INTPTLAT"), head.index("INTPTLONG")
    return {row[i]: (float(row[la]), float(row[lo].strip())) for row in rows}


def fetch_rhcs(state, origin, gaz, radius):
    out = []
    for r in get_all(RHC_ID, {"filter[STATE]": state}):
        z = (r["ZIP CODE"] or "")[:5]
        if z in gaz and miles(origin, gaz[z]) <= radius:
            out.append({"ccn": r["CCN"], "name": r["DOING BUSINESS AS NAME"] or r["ORGANIZATION NAME"], "org": r["ORGANIZATION NAME"],
                        "city": r["CITY"], "zip": z, "miles": round(miles(origin, gaz[z]), 1)})
    return sorted(out, key=lambda r: r["miles"])


def fetch_acs(geo_ids):
    """Stream the ACS 5-year table files and keep only the rows we need (each file is 50-80 MB)."""
    out = {g: {} for g in geo_ids}
    for t in ACS_TABLES:
        with urllib.request.urlopen(ACS.format(t), timeout=600) as r:
            head = None
            for raw in io.TextIOWrapper(r, encoding="utf-8"):
                f = raw.rstrip("\n").split("|")
                if head is None:
                    head = f
                    continue
                if f[0] in out:
                    out[f[0]].update({h: (int(v) if v.lstrip("-").isdigit() else v) for h, v in zip(head[1:], f[1:]) if "_E" in h})
    def summary(v):
        if not v:
            return None
        pop, p65 = v["B28005_E001"], v["B28005_E014"]
        hh = v["B28002_E001"]
        return {
            "population_in_households": pop, "age65_plus": p65, "age65_share": p65 / pop if pop else None,
            "age65_broadband_share": v["B28005_E017"] / p65 if p65 else None,
            "age65_no_computer_share": v["B28005_E019"] / p65 if p65 else None,
            "households": hh, "hh_broadband_share": v["B28002_E004"] / hh if hh else None,
            "hh_cellular_only_share": v["B28002_E006"] / hh if hh else None,
            "hh_no_internet_share": v["B28002_E013"] / hh if hh else None,
            "hh_smartphone_share": v["B28001_E005"] / v["B28001_E001"] if v["B28001_E001"] else None,
        }
    return {g: summary(v) for g, v in out.items()}


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def main(config_path):
    cfg = json.loads(Path(config_path).read_text())
    ccn, zips, fips = cfg["ccn"], tuple(cfg["zips"]), cfg["county_fips"]
    data = Path(config_path).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    pos_self = fetch_pos([ccn])[ccn]
    state = pos_self["state"]

    print("HSAF ...")
    hsaf, own = fetch_hsaf(ccn, zips)
    latest = max(hsaf)
    ccns = {r["ccn"] for rows in hsaf.values() for r in rows} | {ccn}
    print(f"POS for {len(ccns)} providers ...")
    providers = fetch_pos(ccns)

    # destinations ranked by latest-year unsuppressed cases from the service area
    tot = {}
    for r in hsaf[latest]:
        tot[r["ccn"]] = tot.get(r["ccn"], 0) + (r["cases"] or 0)
    top = [c for c, n in sorted(tot.items(), key=lambda kv: -kv[1]) if n > 0][:6]
    print("DRG mix for", top)
    drg = fetch_drg_mix(top)
    ma_check = fetch_ma_check([c for c in top if c != ccn][:3])

    print("gazetteer, RHCs, geographic variation, telehealth ...")
    gaz = load_gazetteer()
    origin = gaz[zips[0]]
    for p in providers.values():
        if p.get("zip") in gaz:
            p["miles"] = round(miles(origin, gaz[p["zip"]]), 1)
    rhcs = fetch_rhcs(state, origin, gaz, cfg.get("rhc_radius_miles", 45))
    geovar = fetch_geovar(state, fips)
    tele = fetch_telehealth(STATE_NAMES.get(state, state))

    print("ACS (streams ~190 MB) ...")
    geo_ids = [f"860Z200US{z}" for z in zips] + [f"0500000US{fips}", f"0400000US{fips[:2]}", "0100000US"]
    acs = fetch_acs(geo_ids)

    out = {
        "pulled": date.today().isoformat(), "ccn": ccn, "zips": list(zips), "county_fips": fips, "state": state,
        "sources": {
            "hsaf": {str(y): i for y, i in HSAF_IDS.items()}, "pos": POS_ID, "ip_drg_cy2024": IP_DRG_ID, "geovar": GEOVAR_ID,
            "telehealth": TELEHEALTH_ID, "rhc": RHC_ID, "ip_provider_cy2024": IP_PROVIDER_ID, "gazetteer": GAZETTEER,
            "acs_5yr_2024": [ACS.format(t) for t in ACS_TABLES],
        },
        "hsaf": {str(y): rows for y, rows in hsaf.items()},
        "hsaf_own_origin": {str(y): rows for y, rows in own.items()}, "hsaf_ma_check": ma_check,
        "providers": providers, "drg_mix_cy2024": drg, "geovar": geovar, "telehealth": tele, "rhcs": rhcs,
        "acs": {"zcta": {z: acs[f"860Z200US{z}"] for z in zips}, "county": acs[f"0500000US{fips}"],
                "state": acs[f"0400000US{fips[:2]}"], "us": acs["0100000US"]},
    }
    path = data / f"outmigration_{ccn}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"wrote {path}  ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "config.json")
