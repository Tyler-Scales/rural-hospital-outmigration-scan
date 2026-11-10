"""CMS hospital cost reports (HCRIS, Form 2552-10) for any Critical Access Hospital.

  extract   read a downloaded HOSP10FY<year>.zip and keep every CAH's reports
            (CCN digits 3-6 in 1300-1399): cache/extract/FY<year>/rpt.csv + cells/<rpt>.csv
  survey    decode every extracted CAH report into the record engine/engine.js uses, plus a
            hospital profile and flags for parts the model doesn't cover (ICU, nursery,
            distinct-part units, rural health clinic, ...): cache/survey/FY<year>.json
  hospital  collect one hospital's decoded reports, keyed by fiscal-year-end year, into
            data/hcris_<ccn>.json for analysis/outmigration_value.js

Downloads are untrusted data: run with python3 -I and keep scripts outside the cache.
Line/column codes are 5 characters: 3-digit number + 2-digit subscript ("01901" = 19.01).

  python3 -I pipeline/hcris.py extract cache/hcris/HOSP10FY2024.zip 2024
  python3 -I pipeline/hcris.py survey 2024
  python3 -I pipeline/hcris.py hospital <ccn>

The HOSP10FY<year>.zip files come from CMS (Cost Reports > Hospital 2552-2010 Form).
"""
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
STATUS = {"1": "As submitted", "2": "Settled without audit", "3": "Settled with audit", "4": "Reopened", "5": "Amended"}
csv.field_size_limit(10_000_000)


def is_cah(ccn):
    c = str(ccn).strip().zfill(6)
    return c[2:6].isdigit() and 1300 <= int(c[2:6]) <= 1399


def us_date(s):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return ""


# ---------- extract ----------
def extract(zip_path, year, only_ccns=None):
    out = CACHE / "extract" / f"FY{year}"
    (out / "cells").mkdir(parents=True, exist_ok=True)
    keep = (lambda c: c in only_ccns) if only_ccns else is_cah
    with zipfile.ZipFile(zip_path) as z:
        names = {k: next((n for n in z.namelist() if k in n.upper() and n.upper().endswith(".CSV")), None) for k in ("RPT", "NMRC", "ALPHA")}
        if not all(names.values()):
            raise SystemExit(f"{zip_path}: expected RPT, NMRC and ALPHA files, found {z.namelist()}")
        wanted = {}
        with z.open(names["RPT"]) as f, open(out / "rpt.csv", "w", newline="") as w:
            wr = csv.writer(w)
            for row in csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline="")):
                if len(row) > 7:
                    ccn = row[2].strip().zfill(6)
                    if keep(ccn):
                        row[2] = ccn
                        wr.writerow(row)
                        wanted[row[0]] = ccn
        handles = {}
        try:
            for key in ("NMRC", "ALPHA"):
                with z.open(names[key]) as f:
                    for row in csv.reader(io.TextIOWrapper(f, encoding="latin-1", newline="")):
                        if row and row[0] in wanted:
                            h = handles.get(row[0])
                            if h is None:
                                h = handles[row[0]] = open(out / "cells" / f"{row[0]}.csv", "w", newline="")
                            h.write(",".join(row[:4]) + "," + ",".join(row[4:]).replace("\n", " ") + "\n")
        finally:
            for h in handles.values():
                h.close()
    print(f"FY{year}: {len(wanted)} reports from {len(set(wanted.values()))} hospitals -> {out}")


# ---------- decode ----------
def load_cells(path):
    cells = defaultdict(dict)
    with open(path, newline="", encoding="latin-1") as f:
        for r in csv.reader(f):
            if len(r) >= 5:
                cells[r[1]][(r[2], r[3])] = ",".join(r[4:])
    return cells


class Report:
    def __init__(self, cells):
        self.c = cells

    def v(self, ws, line, col="00100", default=0.0):
        raw = self.c.get(ws, {}).get((line, col))
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return raw

    def s(self, ws, line, col="00100"):
        raw = self.c.get(ws, {}).get((line, col))
        return raw.strip() if isinstance(raw, str) else ""

    def lines(self, ws, col):
        return {l: self.v(ws, l, col) for (l, c) in self.c.get(ws, {}) if c == col}


def label_map(rep):
    out = {}
    for (line, col), val in rep.c.get("A000000", {}).items():
        if col == "00000":
            out[line] = val[5:].strip() if val[:5].isdigit() else val.strip()
    return out


# Ancillary (lines 50-76) and outpatient (90-93) cost centers are modeled as departments.
# Everything else on C Part I (ICU, nursery, subproviders, distinct-part SNF/NF, rural health
# clinics, home health, ambulance, ...) is left out and flagged when it carries cost.
def modeled_department(line):
    return "05000" <= line < "07700" or "09000" <= line < "09400"


def departments(rep, labels):
    out = {}
    for line, cost in rep.lines("C000001", "00100").items():
        if not modeled_department(line) or not isinstance(cost, float):
            continue
        out[line] = {
            "name": labels.get(line, line), "cost": cost,
            "ip_charges": rep.v("C000001", line, "00600"), "op_charges": rep.v("C000001", line, "00700"),
            "ccr": rep.v("C000001", line, "00900"),
        }
    return out


def dept_table(rep, ws, charge_cols, cost_cols):
    out = {}
    for line in sorted({l for (l, c) in rep.c.get(ws, {})}):
        if line >= "20000":
            continue
        chg = sum(rep.v(ws, line, c) for c in charge_cols)
        cost = sum(rep.v(ws, line, c) for c in cost_cols)
        if chg or cost:
            out[line] = {"charges": chg, "cost": cost, "ccr": rep.v(ws, line, "00100")}
    return out


def profile(rep, rpt_row):
    s2 = "S200001"
    return {
        "name": rep.s(s2, "00300", "00100"),
        "ccn": rpt_row[2].strip().zfill(6),
        "street": rep.s(s2, "00100", "00100"),
        "city": rep.s(s2, "00200", "00100"),
        "state": rep.s(s2, "00200", "00200"),
        "zip": rep.s(s2, "00200", "00300"),
        "county": rep.s(s2, "00200", "00400"),
        "cah": rep.s(s2, "10500", "00100").upper() == "Y",
        "methodII": rep.s(s2, "10600", "00100").upper() == "Y",
    }


def flags_for(rep, days, labels):
    """Parts of the hospital the model doesn't cover; each one can make the fit worse."""
    f = []
    if days["icu"] > 0:
        f.append("icu_days")
    if days["nursery"] > 0:
        f.append("nursery_days")
    if days["swing_snf_total"] + days["swing_nf_total"] == 0:
        f.append("no_swing_beds")
    for line, cost in rep.lines("C000001", "00100").items():
        if not isinstance(cost, float) or cost <= 0 or modeled_department(line) or line >= "11800" or line == "03000":
            continue
        n = int(line[:3])
        tag = ("icu" if 31 <= n <= 35 else "subprovider" if 40 <= n <= 42 else "nursery" if n == 43 else "distinct_part_snf_nf" if 44 <= n <= 46
               else "rural_health_clinic" if n == 88 else "fqhc" if n == 89 else "ambulance" if n == 95 else "home_health" if n == 101 else "other_cost_center")
        if tag + "_cost" not in f:
            f.append(tag + "_cost")
    return f


def decode(rpt_row, rep):
    labels = label_map(rep)
    s3, d1, e2, e3, eb = "S300001", "D10A181", "E20F180", "E30A185", "E00A18B"
    g3_other = {}
    for (line, col), val in rep.c.get("G300000", {}).items():
        if col == "00100" and "00600" <= line <= "02499":
            try:
                g3_other[line] = {"label": rep.c["G300000"].get((line, "00000"), ""), "amount": float(val)}
            except ValueError:
                pass
    nonreimb = {line: {"name": labels.get(line, line), "cost": val} for line, val in rep.lines("B000001", "02600").items() if "19000" <= line < "20000"}
    days = {
        "beds": rep.v(s3, "00100", "00200"), "bed_days_available": rep.v(s3, "00100", "00300"), "cah_hours": rep.v(s3, "00100", "00400"),
        "acute_total": rep.v(s3, "00100", "00800"), "acute_tm": rep.v(s3, "00100", "00600"), "acute_mcd": rep.v(s3, "00100", "00700"),
        "acute_ma": rep.v(s3, "00200", "00600"), "acute_discharges": rep.v(s3, "00100", "01500"), "acute_discharges_tm": rep.v(s3, "00100", "01300"),
        "swing_snf_total": rep.v(s3, "00500", "00800"), "swing_snf_tm": rep.v(s3, "00500", "00600"), "swing_nf_total": rep.v(s3, "00600", "00800"),
        "adults_peds_total": rep.v(s3, "00700", "00800"),
        "icu": sum(rep.v(s3, l, "00800") for l in ("00800", "00900", "01000", "01100", "01200")),
        "nursery": rep.v(s3, "01300", "00800"),
        "total": rep.v(s3, "01400", "00800"), "total_tm": rep.v(s3, "01400", "00600"), "total_mcd": rep.v(s3, "01400", "00700"),
        "observation": rep.v(s3, "02800", "00800"), "fte": rep.v(s3, "01400", "01000"),
    }
    rec = {
        "rpt_rec_num": rpt_row[0], "fy_begin": rpt_row[5], "fy_end": rpt_row[6], "processed": rpt_row[7],
        "status": STATUS.get(rpt_row[4], rpt_row[4]),
        "mac": rep.v("E10A181", "00800", "00000", ""),
        "profile": profile(rep, rpt_row),
        "days": days,
        "d1": {
            "routine_cost": rep.v(d1, "02100"), "nf_rate": rep.v(d1, "01900"), "nf_carveout": rep.v(d1, "02400") + rep.v(d1, "02500"),
            "swing_cost_total": rep.v(d1, "02600"), "per_diem": rep.v(d1, "03800"), "program_routine_cost": rep.v(d1, "04100"),
            "program_ancillary_cost": rep.v(d1, "04800"), "program_ip_cost": rep.v(d1, "04900"),
            "swing_routine_cost_tm": rep.v(d1, "06600"), "observation_cost": rep.v(d1, "08900"),
        },
        "departments": departments(rep, labels),
        "routine_ip_charges": rep.v("C000001", "03000", "00600"),
        "totals": {
            "reimbursable_cost": rep.v("C000001", "20200", "00100"), "ip_charges": rep.v("C000001", "20200", "00600"),
            "op_charges": rep.v("C000001", "20200", "00700"), "operating_expense": rep.v("A000000", "20000", "00300"),
            "a8_adjustments": rep.v("A000000", "20000", "00600"), "nonreimbursable": nonreimb,
        },
        "a8": {line: val for line, val in rep.lines("A800000", "00200").items() if isinstance(val, float) and line != "05000"},
        "a8_labels": {line: rep.c["A800000"].get((line, "00000"), "") for line in rep.lines("A800000", "00200") if line != "05000"},
        "medicare_ip_acute_ancillary": dept_table(rep, "D30A180", ["00200"], ["00300"]),
        "medicare_swing_ancillary": dept_table(rep, "D30F180", ["00200"], ["00300"]),
        "medicare_op": dept_table(rep, "D00A185", ["00300", "00400"], ["00600", "00700"]),
        "e2_swing": {
            "routine": rep.v(e2, "00100"), "ancillary": rep.v(e2, "00300"), "days": rep.v(e2, "00500"), "subtotal": rep.v(e2, "00800"),
            "coinsurance": rep.v(e2, "01300"), "net": rep.v(e2, "01500"), "bad_debt": rep.v(e2, "01700"), "bad_debt_reimb": rep.v(e2, "01701"),
            "total": rep.v(e2, "01900"), "sequestration": rep.v(e2, "01901"), "interim": rep.v(e2, "02000"), "tentative": rep.v(e2, "02100"), "balance": rep.v(e2, "02200"),
        },
        "e3v_acute": {
            "cost": rep.v(e3, "00100"), "reimb": rep.v(e3, "00600"), "deductibles": rep.v(e3, "02000"), "net": rep.v(e3, "02200"),
            "bad_debt": rep.v(e3, "02500"), "bad_debt_reimb": rep.v(e3, "02600"), "total": rep.v(e3, "03000"), "sequestration": rep.v(e3, "03001"),
            "interim": rep.v(e3, "03100"), "tentative": rep.v(e3, "03200"), "balance": rep.v(e3, "03300"),
        },
        "eb_outpatient": {
            "cost": rep.v(eb, "00100"), "reimb": rep.v(eb, "02100"), "deductibles": rep.v(eb, "02500"), "coinsurance": rep.v(eb, "02600"),
            "net": rep.v(eb, "02700"), "primary_payer": rep.v(eb, "03100"), "bad_debt": rep.v(eb, "03400"), "bad_debt_reimb": rep.v(eb, "03500"),
            "total": rep.v(eb, "04000"), "sequestration": rep.v(eb, "04001"), "interim": rep.v(eb, "04100"), "tentative": rep.v(eb, "04200"), "balance": rep.v(eb, "04300"),
        },
        "settlement": {"part_a": rep.v("S000003", "20000", "00200"), "part_b": rep.v("S000003", "20000", "00300"), "medicaid": rep.v("S000003", "20000", "00500")},
        "income": {
            "gross_patient_revenue": rep.v("G300000", "00100"), "contractual_allowances": rep.v("G300000", "00200"),
            "net_patient_revenue": rep.v("G300000", "00300"), "operating_expense": rep.v("G300000", "00400"),
            "operating_income": rep.v("G300000", "00500"), "other_income_total": rep.v("G300000", "02500"),
            "other_income_lines": g3_other, "net_income": rep.v("G300000", "02900"),
            "ip_revenue": rep.v("G200000", "02800", "00100"), "op_revenue": rep.v("G200000", "02800", "00200"),
        },
        "s10": {
            "medicaid_net_revenue": rep.v("S100001", "00200"), "medicaid_supplemental": rep.v("S100001", "00500"),
            "medicaid_charges": rep.v("S100001", "00600"), "chip_net_revenue": rep.v("S100001", "00900"), "chip_charges": rep.v("S100001", "01000"),
            "charity_cost": rep.v("S100001", "02300"), "bad_debt_total": rep.v("S100001", "02600"), "medicare_bad_debt_reimb": rep.v("S100001", "02700"),
        },
    }
    rec["flags"] = flags_for(rep, days, labels)
    return rec


def near(a, b, tol=2.0):
    return isinstance(a, float) and isinstance(b, float) and abs(a - b) <= tol


def problems(r):
    """Arithmetic identities of the 2552-10 that a correct decode must satisfy."""
    d, e2, e3, eb, d1 = r["days"], r["e2_swing"], r["e3v_acute"], r["eb_outpatient"], r["d1"]
    out = []
    if not near(d["adults_peds_total"], d["acute_total"] + d["swing_snf_total"] + d["swing_nf_total"], 0.5):
        out.append("S-3 L7 != L1 + L5 + L6")
    if not near(d["total"], d["adults_peds_total"] + d["icu"] + d["nursery"], 0.5):
        out.append("S-3 L14 != L7 + L8-L13")
    if d["swing_snf_tm"] and not near(e2["routine"], 1.01 * d1["swing_routine_cost_tm"]):
        out.append("E-2 L1 != 1.01 x D-1 L66")
    if not near(e3["cost"], d1["program_ip_cost"]):
        out.append("E-3 V L1 != D-1 L49")
    for name, w in (("E-2", e2), ("E-3 V", e3), ("E Pt B", eb)):
        if not near(w["balance"], w["total"] - w["sequestration"] - w["interim"] - w["tentative"]):
            out.append(f"{name} balance")
    denom = d["acute_total"] + d["swing_snf_total"] + d["observation"]
    if denom <= 0 or not near((d1["routine_cost"] - d1["nf_carveout"]) / denom, d1["per_diem"], 0.05):
        out.append("D-1 per diem")
    if not r["income"]["net_patient_revenue"] or not r["totals"]["operating_expense"]:
        out.append("missing G-3 / A totals")
    return out


def latest_per_hospital(rows):
    """One report per hospital and fiscal-year end: the most recently processed (dates compared as dates)."""
    best = {}
    for row in rows:
        key = (row[2], us_date(row[6]))
        if key not in best or us_date(row[7]) > us_date(best[key][7]):
            best[key] = row
    return list(best.values())


def survey(year):
    src = CACHE / "extract" / f"FY{year}"
    with open(src / "rpt.csv", newline="") as f:
        rows = latest_per_hospital([r for r in csv.reader(f)])
    out, bad = [], 0
    for row in rows:
        cells = src / "cells" / f"{row[0]}.csv"
        if not cells.exists():
            continue
        try:
            rec = decode(row, Report(load_cells(cells)))
        except Exception as e:  # noqa: BLE001  one bad report shouldn't stop the survey
            bad += 1
            out.append({"rpt_rec_num": row[0], "ccn": row[2], "error": str(e)[:200]})
            continue
        rec["problems"] = problems(rec)
        out.append(rec)
    dst = CACHE / "survey"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / f"FY{year}.json").write_text(json.dumps(out))
    ok = sum(1 for r in out if "problems" in r and not r["problems"])
    print(f"FY{year}: {len(out)} reports decoded, {ok} pass every identity, {bad} failed to decode -> {dst / f'FY{year}.json'}")


def hospital(ccn):
    """One hospital's decoded reports from every surveyed year, keyed by fiscal-year-end year."""
    ccn = str(ccn).strip().zfill(6)
    out = {}
    for path in sorted((CACHE / "survey").glob("FY*.json")):
        for rec in json.loads(path.read_text()):
            if (rec.get("profile") or {}).get("ccn") == ccn and not rec.get("error"):
                out[rec["fy_end"][-4:]] = rec
    if not out:
        sys.exit(f"no decoded reports for CCN {ccn}; run extract and survey first")
    dst = HERE.parent / "data" / f"hcris_{ccn}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(dict(sorted(out.items())), indent=1))
    print(f"{ccn}: fiscal years {', '.join(sorted(out))} -> {dst}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "extract":
        extract(Path(sys.argv[2]), sys.argv[3], set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None)
    elif cmd == "survey":
        survey(sys.argv[2])
    elif cmd == "hospital":
        hospital(sys.argv[2])
    else:
        print(__doc__)
