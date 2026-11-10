"""Build the outmigration report: one set of figures, written to a briefing page and to a sourced workbook.

Inputs (run these first):
  python3 -I pipeline/outmigration.py config.json      -> data/outmigration_<ccn>.json
  python3 -I pipeline/hcris.py hospital <ccn>          -> data/hcris_<ccn>.json
  node analysis/outmigration_value.js config.json      -> data/outmigration_value_<ccn>.json

Writes:
  output/report.html            the briefing page (from analysis/report.template.html)
  output/opportunities.xlsx     every figure with its source, plus a cost column for each lever
  data/outmigration_facts_<ccn>.json

With "anonymize": true in config.json, every hospital is shown by a label (config "provider_labels", else
"Hospital B", "Hospital C", ...), cities, ZIP codes and CMS numbers are left out of the page and workbook,
and the subject hospital is "hospital_name" (default "Hospital A").

Run: python3 analysis/build_report.py [config.json]
(openpyxl is the only dependency outside the standard library.)
"""
import html
import json
import re
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
API = "https://data.cms.gov/data-api/v1/dataset/{}/data"

# CAH-appropriate medical DRGs without major complications (tier A), medical DRGs with major
# complications a CAH can sometimes keep with specialist backup (tier B). Everything else (surgery,
# cardiac procedures, AMI, stroke, trauma) is tier C: tertiary care.
TIER_A = {"069", "101", "178", "179", "191", "192", "194", "195", "202", "203", "292", "293", "305", "310", "312", "313",
          "392", "552", "603", "638", "639", "641", "683", "684", "690", "699", "812", "872", "947", "948"}
TIER_B = {"100", "177", "189", "190", "193", "291", "308", "309", "377", "378", "391", "602", "640", "682", "689", "698", "811", "871"}
UTIL = [("ER_VISITS_PER_1000_BENES", "Emergency visits per 1,000"), ("IP_CVRD_STAYS_PER_1000_BENES", "Inpatient stays per 1,000"),
        ("ACUTE_HOSP_READMSN_PCT", "Hospital readmission rate"), ("SNF_CVRD_DAYS_PER_1000_BENES", "Skilled nursing days per 1,000"),
        ("EM_EVNTS_PER_1000_BENES", "Office and clinic visits per 1,000"), ("IMGNG_EVNTS_PER_1000_BENES", "Imaging events per 1,000"),
        ("TESTS_EVNTS_PER_1000_BENES", "Tests per 1,000"), ("ASC_EVNTS_PER_1000_BENES", "Surgery-center events per 1,000")]
DEFAULT_CAPACITY = ["MRI, surgery and cardiac procedures, along with heart attack and stroke care: the {tier_c} tertiary share above.",
                    "Dialysis, mental health, pediatrics and obstetrics, where the county lacks them.",
                    "Transportation to appointments. An AI booking line can schedule rides once a service exists."]


def money(x, unit=True):
    s = "-" if x < 0 else ""
    x = abs(x)
    if unit and x >= 1e6:
        return f"{s}${x / 1e6:.2f}M"
    if unit and x >= 10e3:
        return f"{s}${x / 1e3:,.0f}K"
    return f"{s}${x:,.0f}"


def pct(x, d=0):
    return f"{100 * x:.{d}f}%"


def esc(s):
    return html.escape(str(s), quote=True)


class Labels:
    """Display names for hospitals and ZIPs, honoring anonymize and the config's label maps."""

    def __init__(self, cfg, om, ranked):
        self.anon = bool(cfg.get("anonymize"))
        self.own = cfg["ccn"]
        self.map = cfg.get("provider_labels", {})
        self.zips = cfg.get("zip_labels", {})
        self.zip_order = om["zips"]
        self.own_short = cfg.get("hospital_short")
        self.P = om["providers"]
        own_real = ((self.P.get(self.own) or {}).get("name") or "The hospital").title()
        self.own_name = cfg.get("hospital_name") or ("Hospital A" if self.anon else own_real)
        self.letters = {}
        n = 0
        for c in ranked:
            if c != self.own and c not in self.map:
                self.letters[c] = f"Hospital {chr(ord('B') + n)}" if n < 24 else "Other hospital"
                n += 1

    def name(self, ccn):
        if ccn == self.own:
            return self.own_name
        m = self.map.get(ccn)
        if isinstance(m, dict):
            return m["name"]
        if m:
            return m
        if self.anon:
            return self.letters.get(ccn, "Other hospital")
        if re.fullmatch(r"\d\dT\d{3}", ccn):
            return name_case(self.P.get(ccn[:2] + "0" + ccn[3:], {}).get("name", ccn)) + " rehab unit"
        return name_case((self.P.get(ccn) or {}).get("name") or ccn)

    def short(self, ccn):
        m = self.map.get(ccn)
        if isinstance(m, dict) and m.get("short"):
            return m["short"]
        if ccn == self.own and self.own_short:
            return self.own_short
        n = self.name(ccn)
        return n if len(n) <= 14 else n.split()[0]

    def place(self, ccn):
        if self.anon:
            return ""
        p = self.P.get(ccn) or {}
        return f"{(p.get('city') or '').title()}, {p.get('state') or ''}".strip(", ")

    def zip(self, z):
        if self.zips.get(z):
            return self.zips[z]
        return f"Area {self.zip_order.index(z) + 1}" if self.anon else z


def name_case(n):
    return n.title().replace("Hca ", "HCA ").replace("Uf ", "UF ").replace(", Inc", "").replace("Inc.", "").strip(" ,")


def build_facts(cfg):
    ccn = cfg["ccn"]
    om = json.loads((ROOT / f"data/outmigration_{ccn}.json").read_text())
    val = json.loads((ROOT / f"data/outmigration_value_{ccn}.json").read_text())
    hcris = json.loads((ROOT / f"data/hcris_{ccn}.json").read_text())
    years = sorted(om["hsaf"], key=int)
    latest = years[-1]

    agg = {}
    for r in om["hsaf"][latest]:
        a = agg.setdefault(r["ccn"], {"ccn": r["ccn"], "cases": 0, "days": 0, "small": 0})
        if r["cases"] is None:
            a["small"] += 1
        else:
            a["cases"] += r["cases"]
            a["days"] += r["days"] or 0
    dests = sorted(agg.values(), key=lambda a: (-a["cases"], -a["small"]))
    ranked = [a["ccn"] for a in dests]
    L = Labels(cfg, om, ranked)
    others = [a for a in dests if a["ccn"] != ccn]
    d1, d2 = others[0]["ccn"], others[1]["ccn"]
    own = agg.get(ccn, {"cases": 0, "days": 0, "small": 0})
    known = sum(a["cases"] for a in dests)
    small = sum(a["small"] for a in dests)
    miles = lambda c: (om["providers"].get(c) or {}).get("miles")

    trend = []
    for y in years:
        rows = om["hsaf"][y]
        if not any(r["cases"] is None for r in rows):
            continue  # CY2015 has no suppressed cells and lists only a handful of hospitals: a different file layout
        g = lambda c: sum(r["cases"] or 0 for r in rows if r["ccn"] == c)
        trend.append({"year": int(y), "d1": g(d1), "d2": g(d2), "own": g(ccn), "known": sum(r["cases"] or 0 for r in rows),
                      "small": sum(1 for r in rows if r["cases"] is None)})

    drg = om["drg_mix_cy2024"].get(d1, [])
    tier = lambda code: "A" if code in TIER_A else "B" if code in TIER_B else "C"
    drg_tot = sum(r["discharges"] or 0 for r in drg) or 1
    tiers = {t: sum(r["discharges"] or 0 for r in drg if tier(r["drg"]) == t) for t in "ABC"}

    geo = {k: v[-1] for k, v in om["geovar"].items()}
    cg, sg = geo["county"], geo["state"]
    f = lambda r, k: float(r[k])
    util = [{"key": k, "label": lab, "rate": "PCT" in k, "county": f(cg, k), "state": f(sg, k), "us": f(geo["us"], k)} for k, lab in UTIL]

    last_fy = max(hcris)
    rec = hcris[last_fy]
    own_partb = rec["eb_outpatient"]["total"] - rec["eb_outpatient"]["sequestration"]
    own_swing = rec["e2_swing"]["total"] - rec["e2_swing"]["sequestration"]
    county_op = f(cg, "OP_MDCR_PYMT_PC") * f(cg, "BENES_OM_CNT")
    county_snf = f(cg, "SNF_MDCR_PYMT_PC") * f(cg, "BENES_OM_CNT")
    tm_acute = {y: hcris[y]["days"]["acute_discharges_tm"] for y in sorted(hcris)}
    icu = ((om["providers"].get(ccn) or {}).get("services") or {}).get("ICU")

    acs = om["acs"]
    tele = {(t["year"], t["ruca"]): t["pct"] for t in om["telehealth"]}
    tele_year = max(y for y, _ in tele)
    cases = val["cases"]
    I, Pl = cases["interim"], cases["placeholder"]
    lev = lambda c, k: [x["margin"] for x in c["levers"][k]]
    los = val["inputs"]["swingLOS"]

    return {
        "cfg": cfg, "labels": L, "om": om,
        "pulled": om["pulled"], "hsaf_year": latest, "fy": "FY" + last_fy, "beds": int(rec["days"]["beds"] or 0), "icu": icu,
        "known": known, "small": small, "n_hospitals": len(dests),
        "d1": d1, "d2": d2, "d1_cases": agg[d1]["cases"], "d2_cases": agg[d2]["cases"], "d1_miles": miles(d1),
        "own_cases": own["cases"], "own_share": own["cases"] / known if known else 0, "d1_share": agg[d1]["cases"] / known if known else 0,
        "trend": trend,
        "tm_acute_first": tm_acute[min(tm_acute)], "tm_acute_last": tm_acute[max(tm_acute)], "tm_acute_first_fy": "FY" + min(tm_acute),
        "ma_share": f(cg, "MA_PRTCPTN_RATE"), "ffs_benes": int(f(cg, "BENES_OM_CNT")), "ab_benes": int(f(cg, "BENES_WTH_PTAPTB_CNT")), "geo_year": cg["year"],
        "hsaf_ratio": sum(c["ratio"] for c in om["hsaf_ma_check"] if c["ratio"]) / max(1, sum(1 for c in om["hsaf_ma_check"] if c["ratio"])),
        "drg_rows": [{"drg": r["drg"], "desc": r["desc"], "discharges": r["discharges"], "tier": tier(r["drg"])} for r in drg],
        "drg_total": drg_tot, "tier_a": tiers["A"] / drg_tot, "tier_b": tiers["B"] / drg_tot, "tier_c": tiers["C"] / drg_tot,
        "tier_a_n": round(agg[d1]["cases"] * tiers["A"] / drg_tot), "tier_ab_n": round(agg[d1]["cases"] * (tiers["A"] + tiers["B"]) / drg_tot),
        "util": util,
        "county_op": county_op, "own_partb": own_partb, "op_capture": own_partb / county_op if county_op else 0, "op_elsewhere": max(0, county_op - own_partb),
        "county_snf": county_snf, "own_swing": own_swing, "swing_ratio": own_swing / county_snf if county_snf else 0,
        "acs": acs, "tele_rural": tele.get((tele_year, "Rural")), "tele_urban": tele.get((tele_year, "Urban")), "tele_year": tele_year,
        "rhcs": om["rhcs"], "rhc_radius": cfg.get("rhc_radius_miles", 45),
        "swing_los": los, "acute_los": val["inputs"]["acuteLOS"], "swing_tm_share": val["inputs"]["swingTMShare"], "acute_tm_share": val["inputs"]["acuteTMShare"],
        "per_day": {k: {"interim": I["perDay"][k]["margin"], "placeholder": Pl["perDay"][k]["margin"]} for k in I["perDay"]},
        "tm_stay": los * I["perDay"]["snfTM"]["margin"], "ma_stay_interim": los * I["perDay"]["snfMA"]["margin"], "ma_stay_placeholder": los * Pl["perDay"]["snfMA"]["margin"],
        "op_yield_interim": I["opYield"], "op_yield_placeholder": Pl["opYield"],
        "levers": {
            "swing": {"counts": val["levers"]["swing"]["counts"], "interim": lev(I, "swing"), "placeholder": lev(Pl, "swing")},
            "acute": {"counts": val["levers"]["acute"]["counts"], "interim": lev(I, "acute"), "placeholder": lev(Pl, "acute")},
            "outpatient": {"pcts": val["levers"]["outpatient"]["pcts"], "interim": lev(I, "outpatient"), "placeholder": lev(Pl, "outpatient"),
                           "charges": [x["charges"] for x in I["levers"]["outpatient"]],
                           "by_line": {l: [x["byLine"][l]["margin"] for x in I["levers"]["outpatient"] if l in x["byLine"]] for l in val["inputs"]["opLines"]},
                           "lines": val["inputs"]["opLines"]},
        },
        "dests": [{**a, "miles": miles(a["ccn"])} for a in dests],
        "check": val["check"],
    }


def page_payload(F):
    """Only what the page's charts need, already labeled: no CMS numbers, and no cities or ZIPs when anonymized."""
    L = F["labels"]
    zips = F["om"]["zips"]
    a = F["acs"]
    ready = [{"label": L.zip(z), **a["zcta"][z]} for z in zips if a["zcta"].get(z)]
    ready += [{"label": "County", **a["county"]}, {"label": "State", **a["state"]}]
    return {
        "dests": [{"label": L.name(d["ccn"]), "place": L.place(d["ccn"]), "miles": d["miles"] or 0, "cases": d["cases"], "days": d["days"],
                   "small": d["small"], "own": d["ccn"] == L.own} for d in F["dests"] if d["cases"] > 0],
        "trend": F["trend"],
        "names": {"d1": L.name(F["d1"]), "d2": L.name(F["d2"]), "own": L.own_name},
        "short": {"d1": L.short(F["d1"]), "d2": L.short(F["d2"]), "own": L.short(L.own)},
        "util": [{"label": u["label"], "rate": u["rate"], "county": u["county"], "state": u["state"]} for u in F["util"]],
        "ready": [{k: v for k, v in r.items() if k in ("label", "age65_share", "age65_broadband_share", "age65_no_computer_share",
                                                        "hh_no_internet_share", "hh_smartphone_share", "hh_cellular_only_share")} for r in ready],
        "levers": F["levers"], "geo_year": F["geo_year"],
    }


def tokens(F):
    cfg, L, a = F["cfg"], F["labels"], F["acs"]
    Lv = F["levers"]
    st = cfg.get("state_label") or ("the state" if L.anon else F["om"].get("state", "the state"))
    util = {u["key"]: u["county"] / u["state"] for u in F["util"]}
    er, ip, snf = util["ER_VISITS_PER_1000_BENES"], util["IP_CVRD_STAYS_PER_1000_BENES"], util["SNF_CVRD_DAYS_PER_1000_BENES"]
    paras = []
    if er >= 1.15 and ip >= 1.15:
        paras.append(f"<p><b>Emergency and inpatient use run well above {esc(st)}</b> ({er:.1f} and {ip:.1f} times per beneficiary), a sign of thin primary-care access. After-hours phone and text booking into the hospital's clinic is the front-door answer, and it keeps the primary-care relationship local.</p>")
    elif er >= 1.15:
        paras.append(f"<p><b>Emergency use runs {er:.1f} times {esc(st)}'s rate,</b> a sign that residents use the ER for care a clinic visit could handle. After-hours phone and text booking into the hospital's clinic is the front-door answer.</p>")
    if snf >= 1.5:
        paras.append(f"<p><b>Skilled nursing days run {snf:.1f} times {esc(st)}'s</b> per beneficiary.</p>")
    if not paras:
        paras.append(f"<p>Use per beneficiary is close to {esc(st)}'s on most measures.</p>")
    if F["swing_ratio"] > 1:
        swing_p = (f"<b>The swing program already draws regionally.</b> The hospital's {F['fy']} Medicare swing settlement ({money(F['own_swing'])}) is "
                   f"{F['swing_ratio']:.1f} times what the county's fee-for-service residents spent on skilled nursing anywhere ({money(F['county_snf'])}), "
                   "so it is the natural place to bring residents home.")
    else:
        swing_p = (f"<b>Skilled nursing leaks too.</b> County fee-for-service residents spent {money(F['county_snf'])} on skilled nursing, against the "
                   f"hospital's {F['fy']} Medicare swing settlement of {money(F['own_swing'])}, so there is room to bring residents home to swing beds.")
    zips = [z for z in F["om"]["zips"] if a["zcta"].get(z)]
    weak = min(zips, key=lambda z: a["zcta"][z]["hh_broadband_share"])
    voice_first = a["county"]["hh_no_internet_share"] > 1.5 * a["state"]["hh_no_internet_share"] or a["county"]["age65_broadband_share"] < a["state"]["age65_broadband_share"] - 0.05
    items = lambda rows, empty: "".join(f"<li><span>{esc(k)}</span><span>{esc(v)}</span></li>" for k, v in rows) or empty
    spec = cfg.get("specialties")
    neg = lambda x: (f"loses {money(-x)}" if x < 0 else f"adds {money(x)}")
    neg_day = lambda x: (f"loses {money(-x, False)}" if x < 0 else f"adds {money(x, False)}")
    T = {
        "page_title": esc(cfg.get("page_title", "Outmigration Scan")), "prepared_by": esc(cfg.get("prepared_by", "ScaleStreet")),
        "headline": esc(cfg.get("headline", "Keeping care local")), "pulled": F["pulled"],
        "anon_note": " The hospital and nearby hospitals are de-identified." if L.anon else "",
        "d1_name": esc(L.name(F["d1"])), "d2_name": esc(L.name(F["d2"])), "own_name": esc(L.own_name),
        "d1_miles": f"{F['d1_miles']:.0f}" if F["d1_miles"] else "some", "hsaf_year": F["hsaf_year"], "fy": F["fy"],
        "d1_cases": str(F["d1_cases"]), "d2_cases": str(F["d2_cases"]), "own_cases": str(F["own_cases"]), "known": f"{F['known']:,}",
        "d1_share": pct(F["d1_share"]), "own_share": pct(F["own_share"]),
        "tier_a": pct(F["tier_a"]), "tier_b": pct(F["tier_b"]), "tier_c": pct(F["tier_c"]), "tier_a_n": str(F["tier_a_n"]), "tier_ab_n": str(F["tier_ab_n"]),
        "drg_total": f"{F['drg_total']:,}", "beds": str(F["beds"]), "icu_phrase": " without an ICU" if F["icu"] == "0" else "",
        "small": str(F["small"]), "range_hi_extra": f"{10 * F['small']:,}", "n_hospitals": str(F["n_hospitals"]),
        "first_year": str(F["trend"][0]["year"]), "own_first": str(F["trend"][0]["own"]), "d1_first": str(F["trend"][0]["d1"]),
        "tm_acute_first": str(int(F["tm_acute_first"])), "tm_acute_last": str(int(F["tm_acute_last"])), "tm_acute_first_fy": F["tm_acute_first_fy"],
        "hsaf_ratio": f"{F['hsaf_ratio']:.1f}", "state_label": esc(st), "geo_year": str(F["geo_year"]),
        "ma_share": pct(F["ma_share"]), "ffs_benes": f"{F['ffs_benes']:,}", "ab_benes": f"{F['ab_benes']:,}",
        "util_paragraphs": "\n        ".join(paras), "swing_paragraph": swing_p, "er_ratio": f"{er:.1f}",
        "own_partb": money(F["own_partb"]), "county_op": money(F["county_op"]), "op_capture": pct(F["op_capture"]), "op_elsewhere": money(F["op_elsewhere"]),
        "hh_no_net": pct(a["county"]["hh_no_internet_share"]), "hh_no_net_state": pct(a["state"]["hh_no_internet_share"]),
        "bb65": pct(a["county"]["age65_broadband_share"]), "bb65_state": pct(a["state"]["age65_broadband_share"]),
        "nocomp65": pct(a["county"]["age65_no_computer_share"]), "phone": pct(a["county"]["hh_smartphone_share"]), "cell_only": pct(a["county"]["hh_cellular_only_share"]),
        "weak_zip": esc(L.zip(weak)), "weak_zip_bb": pct(a["zcta"][weak]["hh_broadband_share"]),
        "channel_advice": ("<b>Build voice and text first,</b> and treat the portal and online booking as a second channel." if voice_first
                           else "<b>Online booking and the portal can lead,</b> with voice and text as backup for older residents."),
        "tele_rural": pct(F["tele_rural"] or 0, 1), "tele_urban": pct(F["tele_urban"] or 0, 1), "tele_year": str(F["tele_year"]),
        "front_door_items": items(cfg.get("front_door", []), "<li><span>Not checked</span><span>No website audit in this run.</span></li>"),
        "community_items": items(cfg.get("community", []), ""),
        "capacity_items": "".join(f"<li>{esc(c.format(tier_c=pct(F['tier_c'])))}</li>" for c in cfg.get("capacity_gaps", DEFAULT_CAPACITY)),
        "rhc_n": str(len(F["rhcs"])), "rhc_radius": str(F["rhc_radius"]), "rhc_nearest": f"{F['rhcs'][0]['miles']:.0f}" if F["rhcs"] else "no",
        "local_services": esc(cfg.get("local_services", "imaging, lab, therapy and GI work")),
        "access_note": esc(cfg.get("access_note", "")), "plan_note": esc(cfg.get("plan_note", "")),
        "telehealth_text": (f"Start with {esc(spec)}, the specialties residents most often say they lack. Tests the specialist orders get booked locally." if spec
                            else "Start with the specialties residents name most often in the community health needs assessment. Tests the specialist orders get booked locally."),
        "stay_evidence": (f"Residents name {esc(spec)} as the specialties they lack." if spec else "See the community health needs assessment."),
        "swing_los": str(F["swing_los"]), "acute_los": f"{F['acute_los']}", "swing_tm_share": pct(F["swing_tm_share"]), "acute_tm_share": pct(F["acute_tm_share"]),
        "tm_stay": money(F["tm_stay"]), "ma_stay_interim": money(F["ma_stay_interim"]), "ma_stay_placeholder_phrase": neg(F["ma_stay_placeholder"]),
        "tm_day": money(F["per_day"]["snfTM"]["interim"], False), "ma_day_interim": money(F["per_day"]["snfMA"]["interim"], False),
        "ma_day_placeholder_phrase": neg_day(F["per_day"]["snfMA"]["placeholder"]),
        "acute_tm_day": money(F["per_day"]["acuteTM"]["interim"], False), "acute_ma_day": money(F["per_day"]["acuteMA"]["interim"], False),
        "op_yield_interim": pct(F["op_yield_interim"]), "op_yield_placeholder": pct(F["op_yield_placeholder"]),
    }
    for k in ("swing", "acute", "outpatient"):
        T[f"{k}_interim_lo"], _, T[f"{k}_interim_hi"] = (money(x) for x in Lv[k]["interim"])
    T["swing_n0"], T["swing_n2"] = str(Lv["swing"]["counts"][0]), str(Lv["swing"]["counts"][-1])
    T["acute_n0"], T["acute_n2"] = str(Lv["acute"]["counts"][0]), str(Lv["acute"]["counts"][-1])
    T["op_p0"], T["op_p2"] = pct(Lv["outpatient"]["pcts"][0]), pct(Lv["outpatient"]["pcts"][-1])
    return T


def render_page(F, T, out):
    tpl = (ROOT / "analysis/report.template.html").read_text()

    def sub(m):
        if m.group(1) not in T:
            raise KeyError(f"template token {{{{{m.group(1)}}}}} has no value")
        return T[m.group(1)]
    page = re.sub(r"\{\{(\w+)\}\}", sub, tpl)
    page = page.replace("/*__PAGE__*/null", json.dumps(page_payload(F), separators=(",", ":")))
    markup = page.split("<script>")[0]
    for bad in ("{{", "}}", "/*__", "NaN", "undefined", "[object", "Infinity"):
        if bad in markup:
            raise ValueError(f"unexpected {bad!r} in the page markup")
    m = re.search(r"\$(?![\d{(])", markup)
    if m:
        raise ValueError(f"stray $ in page markup: {markup[max(0, m.start() - 30): m.end() + 30]!r}")
    out.write_text(page)
    return out


# ---------------------------------------------------------------------------------------------- workbook

ARIAL = "Arial"
H = Font(name=ARIAL, bold=True, size=11)
TITLE = Font(name=ARIAL, bold=True, size=14)
BODY = Font(name=ARIAL, size=10)
INPUT = Font(name=ARIAL, size=10, color="0000FF")
LINK = Font(name=ARIAL, size=10, color="008000")
NOTE = Font(name=ARIAL, size=9, italic=True, color="555555")
HEAD_FILL = PatternFill("solid", fgColor="DCE8F4")
KEY_FILL = PatternFill("solid", fgColor="FFFF00")
USD = '$#,##0;($#,##0);"-"'
PCT = '0.0%;(0.0%);"-"'
NUM = '#,##0;(#,##0);"-"'


def sheet(wb, title, widths):
    ws = wb.create_sheet(title)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    return ws


def header(ws, row, labels):
    for i, v in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font, c.fill = H, HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")


def put(ws, row, col, v, font=BODY, fmt=None, comment=None):
    c = ws.cell(row=row, column=col, value=v)
    c.font = font
    if fmt:
        c.number_format = fmt
    if comment:
        c.comment = Comment(comment, "analysis")
    c.alignment = Alignment(wrap_text=isinstance(v, str) and len(v) > 40 and not v.startswith("="), vertical="top")
    return c


def build_workbook(F, path):
    cfg, L, om = F["cfg"], F["labels"], F["om"]
    S = om["sources"]
    st = cfg.get("state_label") or ("the state" if L.anon else om.get("state", "the state"))
    wb = Workbook()
    wb.remove(wb.active)
    checks = []

    ws = sheet(wb, "Read me", [110])
    lines = [
        ("Outmigration scan: sources and figures", TITLE),
        (f"Prepared by {cfg.get('prepared_by', 'ScaleStreet')}, {F['pulled']}. Public data only; no internal hospital data was used."
         + (" The hospital and nearby hospitals are de-identified." if L.anon else ""), BODY),
        ("", BODY), ("Conventions", H),
        ("Blue text: figures taken from a source or from the engine run (inputs). Black: formulas. Yellow fill: cells for you to fill in (lever cost).", BODY),
        ("Money is operating-margin change per year, not gross charges or revenue.", BODY),
        ("Suppressed CMS cells (1-10 cases) are never counted as zero; they are shown as a count of small cells.", BODY),
        ("", BODY), ("Caveats", H),
        (f"The Hospital Service Area File counts include Medicare Advantage stays: at the largest destinations they run about {F['hsaf_ratio']:.1f}x the fee-for-service discharges in Medicare Inpatient Hospitals by Provider. It has no commercial, Medicaid or outpatient care.", BODY),
        (f"The destination DRG mix is all of {L.name(F['d1'])}'s fee-for-service Medicare patients, used as a proxy for county residents' stays there. DRGs with fewer than 11 discharges are not published.", BODY),
        (f"Lever values come from the CAH engine calibrated to the hospital's {F['fy']} cost report. MA rates are placeholders until the hospital's MA contracts are loaded; the swing lever is shown at both MA rates.", BODY),
    ]
    for i, (t, fnt) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=t)
        c.font, c.alignment = fnt, Alignment(wrap_text=True)

    # Inpatient destinations
    cols = ([] if L.anon else ["CCN"]) + ["Hospital", "Miles", "Stays (known)", "Days (known)", "Small cells (1-10 each)", "Share of known stays"]
    ws = sheet(wb, "Inpatient destinations", ([10] if not L.anon else []) + [44, 8, 12, 12, 14, 12])
    put(ws, 1, 1, f"Medicare inpatient stays by hospital, residents of the county's ZIP codes, CY{F['hsaf_year']}", TITLE)
    put(ws, 2, 1, f"Source: CMS Hospital Service Area File CY{F['hsaf_year']}, {API.format(S['hsaf'][F['hsaf_year']])}. Distance is straight-line from the first ZIP's centroid (Census 2024 Gazetteer).", NOTE)
    header(ws, 4, cols)
    o = 0 if L.anon else 1
    r0 = 5
    for i, d in enumerate(F["dests"]):
        r = r0 + i
        if not L.anon:
            put(ws, r, 1, d["ccn"])
        put(ws, r, 1 + o, L.name(d["ccn"])); put(ws, r, 2 + o, d["miles"], INPUT, "0")
        put(ws, r, 3 + o, d["cases"], INPUT, NUM); put(ws, r, 4 + o, d["days"], INPUT, NUM); put(ws, r, 5 + o, d["small"], INPUT, NUM)
    r1 = r0 + len(F["dests"]) - 1
    cS, cD, cM, cH = (get_column_letter(3 + o), get_column_letter(4 + o), get_column_letter(5 + o), get_column_letter(6 + o))
    for i in range(len(F["dests"])):
        r = r0 + i
        put(ws, r, 6 + o, f"=IF(${cS}${r1 + 1}=0,0,{cS}{r}/${cS}${r1 + 1})", BODY, PCT)
    put(ws, r1 + 1, 1 + o, "Total", H)
    for c in (cS, cD, cM):
        put(ws, r1 + 1, ord(c) - 64, f"=SUM({c}{r0}:{c}{r1})", H, NUM)
    put(ws, r1 + 2, 1 + o, "Stays including small cells, low end (each small cell = 1)"); put(ws, r1 + 2, 3 + o, f"={cS}{r1 + 1}+{cM}{r1 + 1}", BODY, NUM)
    put(ws, r1 + 3, 1 + o, "Stays including small cells, high end (each small cell = 10)"); put(ws, r1 + 3, 3 + o, f"={cS}{r1 + 1}+10*{cM}{r1 + 1}", BODY, NUM)
    checks += [("Inpatient destinations", f"{cS}{r1 + 1}", F["known"]), ("Inpatient destinations", f"{cM}{r1 + 1}", F["small"]),
               ("Inpatient destinations", f"{cS}{r1 + 3}", F["known"] + 10 * F["small"])]
    ws.freeze_panes = "A5"

    # Trend
    ws = sheet(wb, "Trend", [8, 18, 18, 18, 14, 14, 16])
    put(ws, 1, 1, "Medicare inpatient stays from the county's ZIP codes, by calendar year", TITLE)
    put(ws, 2, 1, "Source: CMS Hospital Service Area File, one dataset per year. Known stays only; small cells listed separately.", NOTE)
    header(ws, 4, ["Year", L.name(F["d1"]), L.name(F["d2"]), L.own_name, "Known total", "Small cells", f"{L.own_name} share of known"])
    for i, t in enumerate(F["trend"]):
        r = 5 + i
        put(ws, r, 1, str(t["year"]))
        for col, k in ((2, "d1"), (3, "d2"), (4, "own"), (5, "known"), (6, "small")):
            put(ws, r, col, t[k], INPUT, NUM)
        put(ws, r, 7, f"=IF(E{r}=0,0,D{r}/E{r})", BODY, PCT)

    # Destination DRG mix
    ws = sheet(wb, "Destination DRG mix", [8, 70, 12, 8])
    put(ws, 1, 1, f"What {L.name(F['d1'])}'s Medicare fee-for-service patients were admitted for, CY2024", TITLE)
    put(ws, 2, 1, f"Source: CMS Medicare Inpatient Hospitals by Provider and Service, CY2024, {API.format(S['ip_drg_cy2024'])}. Tier A: CAH-appropriate medical DRGs without major complications. Tier B: medical DRGs with major complications a CAH sometimes keeps with specialist backup. Tier C: tertiary.", NOTE)
    put(ws, 4, 1, "Tier", H); put(ws, 4, 2, "Discharges", H); put(ws, 4, 3, "Share", H)
    rows = sorted(F["drg_rows"], key=lambda r: -(r["discharges"] or 0))
    first, last = 12, 12 + len(rows) - 1
    for i, t in enumerate("ABC"):
        put(ws, 5 + i, 1, t)
        put(ws, 5 + i, 2, f"=SUMIF($D${first}:$D${last},A{5 + i},$C${first}:$C${last})", BODY, NUM)
        put(ws, 5 + i, 3, f"=IF($B$8=0,0,B{5 + i}/$B$8)", BODY, PCT)
    put(ws, 8, 1, "Total", H); put(ws, 8, 2, "=SUM(B5:B7)", H, NUM)
    put(ws, 9, 1, f"Applied to the {F['d1_cases']} known CY{F['hsaf_year']} stays there from the county:", BODY)
    put(ws, 9, 3, F["d1_cases"], LINK, NUM, "From the Inpatient destinations tab")
    put(ws, 10, 1, "Tier A stays (estimate)"); put(ws, 10, 3, "=ROUND(C9*C5,0)", BODY, NUM)
    put(ws, 10, 4, "=ROUND(C9*(C5+C6),0)", BODY, NUM, "Tier A + B stays (estimate)")
    header(ws, 11, ["DRG", "Description", "Discharges", "Tier"])
    for i, r in enumerate(rows):
        put(ws, first + i, 1, r["drg"]); put(ws, first + i, 2, r["desc"]); put(ws, first + i, 3, r["discharges"], INPUT, NUM); put(ws, first + i, 4, r["tier"])
    if rows:
        checks += [("Destination DRG mix", "B8", sum(r["discharges"] or 0 for r in rows)), ("Destination DRG mix", "C10", F["tier_a_n"])]

    # County use vs state
    ws = sheet(wb, "County use vs state", [40, 14, 14, 14, 16])
    put(ws, 1, 1, f"Medicare fee-for-service use, the county vs {st} and the US, {F['geo_year']}", TITLE)
    put(ws, 2, 1, f"Source: CMS Medicare Geographic Variation by National, State & County, {API.format(S['geovar'])}. Fee-for-service beneficiaries only ({F['ffs_benes']:,}; {pct(F['ma_share'], 1)} of Part A+B beneficiaries are in Medicare Advantage).", NOTE)
    header(ws, 4, ["Measure", "County", "State", "US", "County vs state"])
    for i, u in enumerate(F["util"]):
        r = 5 + i
        fmt = PCT if u["rate"] else "#,##0"
        put(ws, r, 1, u["label"]); put(ws, r, 2, u["county"], INPUT, fmt); put(ws, r, 3, u["state"], INPUT, fmt); put(ws, r, 4, u["us"], INPUT, fmt)
        put(ws, r, 5, f"=IF(C{r}=0,0,B{r}/C{r})", BODY, "0.00x")
        checks.append(("County use vs state", f"E{r}", u["county"] / u["state"]))
    r = 5 + len(F["util"]) + 1
    put(ws, r, 1, "Medicare fee-for-service dollars", H)
    put(ws, r + 1, 1, f"County residents' outpatient facility payments, {F['geo_year']}"); put(ws, r + 1, 2, F["county_op"], INPUT, USD)
    put(ws, r + 2, 1, f"Hospital's Medicare Part B outpatient settlement, {F['fy']} (E Pt B line 40 less sequestration)"); put(ws, r + 2, 2, F["own_partb"], INPUT, USD)
    put(ws, r + 3, 1, "Hospital's share (upper bound)"); put(ws, r + 3, 2, f"=IF(B{r + 1}=0,0,B{r + 2}/B{r + 1})", BODY, PCT)
    put(ws, r + 4, 1, "Fee-for-service outpatient dollars that went elsewhere (at least)"); put(ws, r + 4, 2, f"=MAX(0,B{r + 1}-B{r + 2})", BODY, USD)
    put(ws, r + 5, 1, f"County residents' skilled nursing payments, {F['geo_year']}"); put(ws, r + 5, 2, F["county_snf"], INPUT, USD)
    put(ws, r + 6, 1, f"Hospital's Medicare swing-bed settlement, {F['fy']} (E-2 line 19 less sequestration)"); put(ws, r + 6, 2, F["own_swing"], INPUT, USD)
    put(ws, r + 7, 1, "Swing settlement as a multiple of county SNF spending"); put(ws, r + 7, 2, f"=IF(B{r + 5}=0,0,B{r + 6}/B{r + 5})", BODY, "0.0x")
    checks += [("County use vs state", f"B{r + 3}", F["op_capture"]), ("County use vs state", f"B{r + 7}", F["swing_ratio"])]

    # Digital readiness
    a = F["acs"]
    geos = [(L.zip(z), a["zcta"][z]) for z in om["zips"] if a["zcta"].get(z)] + [("County", a["county"]), ("State", a["state"]), ("US", a["us"])]
    ws = sheet(wb, "Digital readiness", [40] + [13] * len(geos))
    put(ws, 1, 1, "Can residents use a digital front door?", TITLE)
    put(ws, 2, 1, "Source: Census ACS 5-year 2020-2024, tables B28005, B28002 and B28001 (table-based summary files). Small ZIPs carry wide margins of error.", NOTE)
    header(ws, 4, ["Measure"] + [g for g, _ in geos])
    for i, (k, label, fmt) in enumerate([("population_in_households", "People in households", NUM), ("age65_share", "Share 65 and over", PCT),
                                          ("age65_broadband_share", "65+ with a computer and broadband", PCT), ("age65_no_computer_share", "65+ with no computer", PCT),
                                          ("hh_broadband_share", "Households with broadband", PCT), ("hh_cellular_only_share", "Households on a cellular plan only", PCT),
                                          ("hh_no_internet_share", "Households with no internet", PCT), ("hh_smartphone_share", "Households with a smartphone", PCT)]):
        put(ws, 5 + i, 1, label)
        for j, (_, v) in enumerate(geos):
            put(ws, 5 + i, 2 + j, v[k], INPUT, fmt)

    # Front door audit
    ws = sheet(wb, "Front door audit", [22, 90])
    put(ws, 1, 1, "What a patient finds on the hospital's website, and what the community says", TITLE)
    header(ws, 3, ["Stage", "Finding"])
    for i, (k, v) in enumerate(cfg.get("front_door", []) + cfg.get("community", [])):
        put(ws, 4 + i, 1, k); put(ws, 4 + i, 2, v)

    # Engine inputs
    ws = sheet(wb, "Engine inputs", [48, 18, 18, 50])
    put(ws, 1, 1, f"CAH engine values, calibrated to the hospital's {F['fy']} cost report", TITLE)
    put(ws, 2, 1, "Source: analysis/outmigration_value.js using engine/engine.js. Margin change per added patient day, with its ancillaries.", NOTE)
    header(ws, 4, ["Stream", "MA swing at $3,180/day", "MA swing at $487/day", "Note"])
    labels = {"acuteTM": "Acute day, Traditional Medicare", "acuteMA": "Acute day, Medicare Advantage", "acuteOther": "Acute day, other payers",
              "snfTM": "Swing (skilled) day, Traditional Medicare", "snfMA": "Swing (skilled) day, Medicare Advantage", "nf": "Swing day, other payers (NF level)"}
    notes = {"acuteMA": "MA acute rate is a $3,500/day placeholder", "acuteOther": "Other-payer acute rate is a $3,000/day placeholder",
             "snfMA": "Depends on the MA contract rate", "nf": "Paid at the Medicaid NF rate"}
    for i, (k, lab) in enumerate(labels.items()):
        put(ws, 5 + i, 1, lab); put(ws, 5 + i, 2, F["per_day"][k]["interim"], INPUT, USD); put(ws, 5 + i, 3, F["per_day"][k]["placeholder"], INPUT, USD); put(ws, 5 + i, 4, notes.get(k, ""))
    r = 12
    put(ws, r, 1, "Swing stay length, days (county Medicare SNF days per stay)"); put(ws, r, 2, F["swing_los"], INPUT, "0")
    put(ws, r + 1, 1, "Acute stay length, days (the hospital's own service-area stays)"); put(ws, r + 1, 2, F["acute_los"], INPUT, "0.0")
    put(ws, r + 2, 1, "Traditional Medicare share of swing (skilled) days"); put(ws, r + 2, 2, F["swing_tm_share"], INPUT, PCT)
    put(ws, r + 3, 1, "Traditional Medicare share of acute Medicare days"); put(ws, r + 3, 2, F["acute_tm_share"], INPUT, PCT)
    put(ws, r + 4, 1, "Fitted non-Medicare outpatient yield (share of charges collected)"); put(ws, r + 4, 2, F["op_yield_interim"], INPUT, PCT); put(ws, r + 4, 3, F["op_yield_placeholder"], INPUT, PCT)
    put(ws, r + 6, 1, "Value of one swing stay", H)
    put(ws, r + 7, 1, "Traditional Medicare"); put(ws, r + 7, 2, f"=B{r}*B8", BODY, USD); put(ws, r + 7, 3, f"=B{r}*C8", BODY, USD)
    put(ws, r + 8, 1, "Medicare Advantage"); put(ws, r + 8, 2, f"=B{r}*B9", BODY, USD); put(ws, r + 8, 3, f"=B{r}*C9", BODY, USD)
    checks += [("Engine inputs", f"B{r + 7}", F["tm_stay"]), ("Engine inputs", f"C{r + 8}", F["ma_stay_placeholder"])]

    # Levers
    ws = sheet(wb, "Levers", [12, 40, 44, 14, 14, 14, 14, 14, 14])
    put(ws, 1, 1, "Where AI and the digital front door can keep patients in the county", TITLE)
    put(ws, 2, 1, "Value = change in the hospital's annual operating margin from the engine. Fill the yellow cost column to see the net.", NOTE)
    header(ws, 4, ["Stage", "Lever", "Volume steps (low / base / high)", "Value low", "Value base", "Value high", "If MA swing pays $487: base", "Annual cost (fill in)", "Net at base"])
    Lv = F["levers"]
    rows = [
        ("Return", "Admission alerts for residents at the main destinations, plus an AI-drafted swing-bed liaison worklist",
         " / ".join(str(n) for n in Lv["swing"]["counts"]) + f" more swing stays ({F['swing_los']} days each)", Lv["swing"]["interim"], Lv["swing"]["placeholder"][1]),
        ("Refer / Access", "Local-first AI scheduling and reminder agent (voice and text) for imaging, lab, therapy and GI",
         " / ".join(pct(p) for p in Lv["outpatient"]["pcts"]) + " more outpatient volume", Lv["outpatient"]["interim"], Lv["outpatient"]["placeholder"][1]),
        ("Access", "After-hours AI phone and text answering", "Enabler; not valued on its own", None, None),
        ("Stay", "Specialty telehealth with AI intake and local follow-up booking", "Downstream tests counted in the local-first row", None, None),
        ("Admit (flagged)", "Tele-specialist backup to keep CAH-appropriate medical admissions local (needs physician coverage)",
         " / ".join(str(n) for n in Lv["acute"]["counts"]) + f" more acute admissions ({F['acute_los']} days each)", Lv["acute"]["interim"], Lv["acute"]["placeholder"][1]),
    ]
    for i, (stage, lever, steps, vals, pl) in enumerate(rows):
        r = 5 + i
        put(ws, r, 1, stage); put(ws, r, 2, lever); put(ws, r, 3, steps)
        if vals:
            for j, v in enumerate(vals):
                put(ws, r, 4 + j, v, INPUT, USD)
            put(ws, r, 7, pl, INPUT, USD)
            put(ws, r, 8, None, INPUT, USD).fill = KEY_FILL
            put(ws, r, 9, f"=E{r}-H{r}", BODY, USD)
            checks.append(("Levers", f"I{r}", vals[1]))
        ws.row_dimensions[r].height = 45
    ws.freeze_panes = "B5"

    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return checks


def check_formulas(path, checks):
    """Evaluate the few formula shapes this workbook uses (no spreadsheet engine needed) and compare with the page."""
    wb = load_workbook(path)

    def val(ws, ref):
        v = ws[ref].value
        if isinstance(v, str) and v.startswith("="):
            return ev(ws, v[1:])
        return v or 0

    def rng(ws, a, b):
        return [ws.cell(row=r, column=c) for r in range(ws[a].row, ws[b].row + 1) for c in range(ws[a].column, ws[b].column + 1)]

    def ev(ws, e):
        e = e.replace("$", "")
        m = re.fullmatch(r"SUM\((\w+):(\w+)\)", e)
        if m:
            return sum(val(ws, c.coordinate) for c in rng(ws, *m.groups()))
        m = re.fullmatch(r"SUMIF\((\w+):(\w+),(\w+),(\w+):(\w+)\)", e)
        if m:
            crit = val(ws, m.group(3))
            return sum(val(ws, v.coordinate) for k, v in zip(rng(ws, m.group(1), m.group(2)), rng(ws, m.group(4), m.group(5))) if k.value == crit)
        m = re.fullmatch(r"IF\((\w+)=0,0,(.+)\)", e)
        if m:
            return 0 if val(ws, m.group(1)) == 0 else ev(ws, m.group(2))
        m = re.fullmatch(r"ROUND\((.+),0\)", e)
        if m:
            return round(ev(ws, m.group(1)))
        m = re.fullmatch(r"MAX\(0,(.+)\)", e)
        if m:
            return max(0, ev(ws, m.group(1)))
        expr = re.sub(r"\b([A-Z]{1,2}\d+)\b", lambda mm: repr(val(ws, mm.group(1))), e)
        if re.fullmatch(r"[\d.()+\-*/ eE]+", expr):
            return eval(expr)  # arithmetic on numbers only
        raise ValueError(f"unhandled formula {e}")

    bad = [(s, ref, val(wb[s], ref), want) for s, ref, want in checks if abs((val(wb[s], ref) or 0) - want) > 1e-6 * max(1, abs(want))]
    if bad:
        raise AssertionError(f"workbook formulas disagree with the page: {bad}")
    return len(checks)


def main(config_path):
    cfg = json.loads(Path(config_path).read_text())
    F = build_facts(cfg)
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    facts = {k: v for k, v in F.items() if k not in ("cfg", "labels", "om")}
    (ROOT / f"data/outmigration_facts_{cfg['ccn']}.json").write_text(json.dumps(facts, indent=1, default=str))
    page = render_page(F, tokens(F), out / "report.html")
    book = out / "opportunities.xlsx"
    n = check_formulas(book, build_workbook(F, book))
    print(f"wrote {page.relative_to(ROOT)} ({page.stat().st_size // 1024} KB) and {book.relative_to(ROOT)}; {n} workbook formulas match the page")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "config.json")
