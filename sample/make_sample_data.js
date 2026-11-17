/* Inputs for the fictional sample hospital shown in the README and docs/sample-report.html.
 *
 * Nothing here comes from a real hospital. The hospital, its six ZIP codes, its referral hospitals, its cost reports
 * and its county's figures are invented, shaped like a typical rural county's public files so the real pipeline can
 * run on them. CCNs and ZIP codes start with 00, which CMS and the Postal Service don't use. Only the MS-DRG titles
 * (a public classification) and the CMS dataset IDs listed as sources are real.
 *
 * Writes data/outmigration_001301.json and data/hcris_001301.json. The cost-report settlement totals come from
 * engine/engine.js, so the made-up cost reports are internally consistent and pass the value model's checks.
 *
 * Rebuild the sample:
 *   node sample/make_sample_data.js
 *   node analysis/outmigration_value.js sample/sample-config.json
 *   python3 analysis/build_report.py sample/sample-config.json
 * docs/sample-report.html is output/report.html wrapped in a full HTML document.
 */
const fs = require('fs');
const path = require('path');
const E = require('../engine/engine.js');

const ROOT = path.join(__dirname, '..');
const CCN = '001301';
const ZIPS = ['00001', '00002', '00003', '00004', '00005', '00006'];
const ZIP_WEIGHT = [0.40, 0.19, 0.14, 0.11, 0.09, 0.07];

// A small deterministic random source, so the sample rebuilds identically.
let seed = 20261117;
const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
const jitter = (x, p) => x * (1 + p * (2 * rnd() - 1));
const r2 = (x) => Math.round(x * 100) / 100;

// ------------------------------------------------------------------ hospitals residents use

const DEST = [
  // ccn, true stays in 2016 and 2025, length of stay, miles
  ['001101', 238, 291, 5.4, 46.2],   // Referral Hospital B
  ['001102', 152, 139, 4.6, 29.4],   // Regional Hospital C
  [CCN, 196, 104, 3.4, 0.0],         // Hospital A, the subject hospital
  ['001103', 49, 58, 6.9, 112.5],    // Tertiary Hospital D
  ['001104', 40, 45, 4.9, 61.0],
  ['001105', 31, 27, 5.0, 74.3],
  ['001106', 22, 21, 5.2, 88.1],
  ['001107', 14, 17, 4.4, 53.6],
  ['001108', 12, 14, 6.1, 131.0],
  ['001109', 9, 12, 5.5, 95.2],
];
// Hospitals that only ever appear as hidden counts (1 to 10 stays).
for (let i = 0; i < 30; i++) DEST.push([`0012${String(i).padStart(2, '0')}`, 2 + (i % 4), 2 + ((i + 1) % 5), 5, 60 + 11 * i]);

const YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];
const hsaf = {};
const ownOrigin = {};
for (const y of YEARS) {
  const t = (y - 2016) / 9;
  const covid = y === 2020 ? 0.84 : y === 2021 ? 0.93 : 1;
  const rows = [];
  for (const [ccn, a, b, los] of DEST) {
    const total = (a + (b - a) * t) * covid;
    ZIPS.forEach((zip, i) => {
      const n = Math.round(jitter(total * ZIP_WEIGHT[i], 0.18));
      if (n <= 0) return;
      const hidden = n <= 10;
      rows.push({ zip, ccn, cases: hidden ? null : n, days: hidden ? null : Math.round(n * jitter(los, 0.08)), charges: null });
    });
  }
  hsaf[String(y)] = rows;
  if (y >= 2023) {
    // The subject hospital's own stays by home ZIP, including a few from outside its county.
    const own = rows.filter((r) => r.ccn === CCN).map((r) => ({ zip: r.zip, cases: r.cases, days: r.days }));
    ownOrigin[String(y)] = own.concat([{ zip: '00011', cases: null, days: null }, { zip: '00014', cases: 13, days: 41 }, { zip: '00019', cases: null, days: null }]);
  }
}

const providers = {};
for (const [ccn, , , , miles] of DEST) {
  const own = ccn === CCN;
  providers[ccn] = {
    ccn, name: own ? 'SAMPLE HOSPITAL A' : `SAMPLE HOSPITAL ${ccn}`, city: '', state: 'XX', zip: '',
    subtype: own ? '11' : '01', beds: own ? 22 : 60 + Math.round(miles * 3), certified_beds: own ? 22 : 60 + Math.round(miles * 3), terminated: false,
    services: own ? { CT: '1', MRI: '0', 'Diagnostic radiology': '3', Laboratory: '', 'Physical therapy': '3', 'Occupational therapy': '3', 'Emergency department': '3', 'Operating room': '3', ICU: '0' }
      : { ICU: '3', 'Emergency department': '3', 'Operating room': '3' },
    rhc_affiliates: own ? 2 : 0, swing_beds: own ? 'Y' : 'N', miles,
  };
}

// ------------------------------------------------------------------ the referral hospital's Medicare DRG mix

const DRG = {
  '064': 'INTRACRANIAL HEMORRHAGE OR CEREBRAL INFARCTION WITH MCC', '065': 'INTRACRANIAL HEMORRHAGE OR CEREBRAL INFARCTION WITH CC OR TPA IN 24 HOURS',
  '069': 'TRANSIENT ISCHEMIA WITHOUT THROMBOLYTIC', '100': 'SEIZURES WITH MCC', '101': 'SEIZURES WITHOUT MCC',
  '177': 'RESPIRATORY INFECTIONS AND INFLAMMATIONS WITH MCC', '178': 'RESPIRATORY INFECTIONS AND INFLAMMATIONS WITH CC',
  '189': 'PULMONARY EDEMA AND RESPIRATORY FAILURE', '190': 'CHRONIC OBSTRUCTIVE PULMONARY DISEASE WITH MCC', '191': 'CHRONIC OBSTRUCTIVE PULMONARY DISEASE WITH CC',
  '193': 'SIMPLE PNEUMONIA AND PLEURISY WITH MCC', '194': 'SIMPLE PNEUMONIA AND PLEURISY WITH CC',
  '207': 'RESPIRATORY SYSTEM DIAGNOSIS WITH VENTILATOR SUPPORT >96 HOURS', '208': 'RESPIRATORY SYSTEM DIAGNOSIS WITH VENTILATOR SUPPORT <=96 HOURS',
  '216': 'CARDIAC VALVE AND OTHER MAJOR CARDIOTHORACIC PROCEDURES WITH CARDIAC CATHETERIZATION WITH MCC',
  '233': 'CORONARY BYPASS WITH CARDIAC CATHETERIZATION OR OPEN ABLATION WITH MCC',
  '280': 'ACUTE MYOCARDIAL INFARCTION, DISCHARGED ALIVE WITH MCC', '281': 'ACUTE MYOCARDIAL INFARCTION, DISCHARGED ALIVE WITH CC',
  '286': 'CIRCULATORY DISORDERS EXCEPT AMI, WITH CARDIAC CATHETERIZATION WITH MCC', '287': 'CIRCULATORY DISORDERS EXCEPT AMI, WITH CARDIAC CATHETERIZATION WITHOUT MCC',
  '291': 'HEART FAILURE AND SHOCK WITH MCC', '292': 'HEART FAILURE AND SHOCK WITH CC', '305': 'HYPERTENSION WITHOUT MCC',
  '308': 'CARDIAC ARRHYTHMIA AND CONDUCTION DISORDERS WITH MCC', '309': 'CARDIAC ARRHYTHMIA AND CONDUCTION DISORDERS WITH CC',
  '310': 'CARDIAC ARRHYTHMIA AND CONDUCTION DISORDERS WITHOUT CC/MCC', '312': 'SYNCOPE AND COLLAPSE', '313': 'CHEST PAIN',
  '329': 'MAJOR SMALL AND LARGE BOWEL PROCEDURES WITH MCC', '330': 'MAJOR SMALL AND LARGE BOWEL PROCEDURES WITH CC',
  '377': 'GASTROINTESTINAL HEMORRHAGE WITH MCC', '378': 'GASTROINTESTINAL HEMORRHAGE WITH CC', '389': 'GASTROINTESTINAL OBSTRUCTION WITH CC',
  '391': 'ESOPHAGITIS, GASTROENTERITIS AND MISCELLANEOUS DIGESTIVE DISORDERS WITH MCC', '392': 'ESOPHAGITIS, GASTROENTERITIS AND MISCELLANEOUS DIGESTIVE DISORDERS WITHOUT MCC',
  '460': 'SPINAL FUSION EXCEPT CERVICAL WITHOUT MCC', '470': 'MAJOR HIP AND KNEE JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY WITHOUT MCC',
  '480': 'HIP AND FEMUR PROCEDURES EXCEPT MAJOR JOINT WITH MCC', '481': 'HIP AND FEMUR PROCEDURES EXCEPT MAJOR JOINT WITH CC',
  '552': 'MEDICAL BACK PROBLEMS WITHOUT MCC', '602': 'CELLULITIS WITH MCC', '603': 'CELLULITIS WITHOUT MCC', '638': 'DIABETES WITH CC',
  '640': 'MISCELLANEOUS DISORDERS OF NUTRITION, METABOLISM, FLUIDS AND ELECTROLYTES WITH MCC', '641': 'MISCELLANEOUS DISORDERS OF NUTRITION, METABOLISM, FLUIDS AND ELECTROLYTES WITHOUT MCC',
  '682': 'RENAL FAILURE WITH MCC', '683': 'RENAL FAILURE WITH CC', '684': 'RENAL FAILURE WITHOUT CC/MCC',
  '689': 'KIDNEY AND URINARY TRACT INFECTIONS WITH MCC', '690': 'KIDNEY AND URINARY TRACT INFECTIONS WITHOUT MCC', '698': 'OTHER KIDNEY AND URINARY TRACT DIAGNOSES WITH MCC',
  '811': 'RED BLOOD CELL DISORDERS WITH MCC', '812': 'RED BLOOD CELL DISORDERS WITHOUT MCC', '853': 'INFECTIOUS AND PARASITIC DISEASES WITH O.R. PROCEDURES WITH MCC',
  '871': 'SEPTICEMIA OR SEVERE SEPSIS WITHOUT MV >96 HOURS WITH MCC', '872': 'SEPTICEMIA OR SEVERE SEPSIS WITHOUT MV >96 HOURS WITHOUT MCC', '948': 'SIGNS AND SYMPTOMS WITHOUT MCC',
};
// Invented discharge counts (CMS publishes a DRG only when it has 11 or more).
const MIX = {
  '871': 212, '291': 128, '470': 182, '193': 64, '190': 47, '189': 58, '177': 52, '280': 66, '064': 62, '065': 50, '308': 30, '309': 28, '378': 31,
  '377': 22, '682': 37, '683': 41, '689': 34, '640': 33, '603': 27, '602': 15, '811': 14, '100': 12, '698': 13, '391': 23, '392': 39,
  '872': 76, '292': 52, '194': 44, '191': 26, '690': 45, '641': 26, '684': 13, '069': 21, '101': 14, '178': 23, '305': 12, '310': 22, '312': 26,
  '313': 11, '552': 15, '638': 13, '812': 18, '948': 17,
  '207': 39, '208': 46, '216': 27, '233': 21, '281': 44, '286': 34, '287': 39, '329': 50, '330': 36, '389': 32, '460': 28, '480': 58, '481': 48, '853': 33,
};
const drgRows = (scale) => Object.entries(MIX).map(([drg, n]) => ({ drg, desc: DRG[drg], discharges: Math.max(11, Math.round(n * scale)), avg_medicare_payment: null }))
  .sort((x, y) => x.drg.localeCompare(y.drg));

// ------------------------------------------------------------------ county, state and national figures

function geovarRow(year, kind) {
  const t = (year - 2014) / 10;
  const base = {
    county: { benes: 6380, ma0: 0.21, ma1: 0.51, op: 3060, snf: 1040, er: 912, ip: 316, readm: 0.181, snfDays: 1490, snfStays: 61.5, em: 9720, img: 4080, tests: 3590, asc: 92 },
    state: { benes: 1650000, ma0: 0.30, ma1: 0.53, op: 3310, snf: 980, er: 688, ip: 258, readm: 0.172, snfDays: 1370, snfStays: 57.2, em: 11640, img: 4870, tests: 4420, asc: 158 },
    us: { benes: 67400000, ma0: 0.31, ma1: 0.50, op: 3580, snf: 1010, er: 676, ip: 251, readm: 0.170, snfDays: 1320, snfStays: 56.0, em: 11980, img: 5010, tests: 4610, asc: 171 },
  }[kind];
  const ma = base.ma0 + (base.ma1 - base.ma0) * t;
  const benes = Math.round(base.benes * (1 + 0.012 * (year - 2024)));
  const om = Math.round(benes * (1 - ma) * 0.94);
  const s = (x) => String(r2(x * (0.9 + 0.1 * t)));
  return {
    year, BENES_TOTAL_CNT: String(benes), BENES_WTH_PTAPTB_CNT: String(Math.round(benes * 0.94)), BENES_OM_CNT: String(om), BENES_MA_CNT: String(Math.round(benes * ma * 0.94)),
    MA_PRTCPTN_RATE: String(Math.round(ma * 10000) / 10000), OP_MDCR_PYMT_PC: s(base.op), SNF_MDCR_PYMT_PC: s(base.snf),
    ER_VISITS_PER_1000_BENES: s(base.er), IP_CVRD_STAYS_PER_1000_BENES: s(base.ip), ACUTE_HOSP_READMSN_PCT: String(base.readm),
    SNF_CVRD_DAYS_PER_1000_BENES: s(base.snfDays), SNF_CVRD_STAYS_PER_1000_BENES: s(base.snfStays), EM_EVNTS_PER_1000_BENES: s(base.em),
    IMGNG_EVNTS_PER_1000_BENES: s(base.img), TESTS_EVNTS_PER_1000_BENES: s(base.tests), ASC_EVNTS_PER_1000_BENES: s(base.asc),
  };
}
const geoYears = [2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024];
const geovar = { county: geoYears.map((y) => geovarRow(y, 'county')), state: geoYears.map((y) => geovarRow(y, 'state')), us: geoYears.map((y) => geovarRow(y, 'us')) };

function acsRow(pop, age65, bb65, noComp65, hhSize, bb, cell, noNet, phone) {
  const households = Math.round(pop / hhSize);
  return {
    population_in_households: pop, age65_plus: Math.round(pop * age65), age65_share: age65, age65_broadband_share: bb65, age65_no_computer_share: noComp65,
    households, hh_broadband_share: bb, hh_cellular_only_share: cell, hh_no_internet_share: noNet, hh_smartphone_share: phone,
  };
}
const zcta = {
  '00001': acsRow(7420, 0.214, 0.66, 0.17, 2.45, 0.80, 0.24, 0.135, 0.84),
  '00002': acsRow(3310, 0.236, 0.58, 0.21, 2.40, 0.74, 0.27, 0.172, 0.80),
  '00003': acsRow(2460, 0.229, 0.61, 0.19, 2.38, 0.77, 0.25, 0.158, 0.82),
  '00004': acsRow(1890, 0.251, 0.52, 0.25, 2.33, 0.69, 0.29, 0.211, 0.77),
  '00005': acsRow(1520, 0.243, 0.55, 0.23, 2.36, 0.71, 0.28, 0.194, 0.79),
  '00006': acsRow(1130, 0.262, 0.49, 0.27, 2.31, 0.66, 0.30, 0.226, 0.75),
};
const weighted = (k) => {
  const z = Object.values(zcta), w = z.reduce((t, r) => t + r.population_in_households, 0);
  return Math.round(z.reduce((t, r) => t + r[k] * r.population_in_households, 0) / w * 10000) / 10000;
};
const county = acsRow(Object.values(zcta).reduce((t, r) => t + r.population_in_households, 0), weighted('age65_share'), weighted('age65_broadband_share'),
  weighted('age65_no_computer_share'), 2.41, weighted('hh_broadband_share'), weighted('hh_cellular_only_share'), weighted('hh_no_internet_share'), weighted('hh_smartphone_share'));
const acs = {
  zcta, county,
  state: acsRow(9850000, 0.172, 0.81, 0.10, 2.52, 0.89, 0.13, 0.082, 0.90),
  us: acsRow(327000000, 0.171, 0.84, 0.09, 2.53, 0.91, 0.115, 0.066, 0.91),
};

const telehealth = [];
const teleOverall = { 2020: 0.318, 2021: 0.231, 2022: 0.171, 2023: 0.142, 2024: 0.126, 2025: 0.114 };
for (const [y, rural] of Object.entries(teleOverall)) {
  telehealth.push({ year: +y, quarter: 'Overall', ruca: 'Rural', pct: rural }, { year: +y, quarter: 'Overall', ruca: 'Urban', pct: r2(rural * 1.38 * 100) / 100 });
  ['1', '2', '3', '4'].forEach((q, i) => {
    const p = Math.round(rural * (0.58 - 0.02 * i) * 1000) / 1000;
    telehealth.push({ year: +y, quarter: q, ruca: 'Rural', pct: p }, { year: +y, quarter: q, ruca: 'Urban', pct: Math.round(p * 1.38 * 1000) / 1000 });
  });
}
telehealth.push({ year: 2026, quarter: '1', ruca: 'Rural', pct: 0.063 }, { year: 2026, quarter: '1', ruca: 'Urban', pct: 0.087 });

const rhcs = [0.0, 9.8, 14.6, 19.1, 24.0, 31.7, 38.2, 43.5].map((miles, i) => ({ ccn: `0034${String(i).padStart(2, '0')}`, name: `RURAL HEALTH CLINIC ${i + 1}`, org: '', city: '', zip: '', miles }));

const sources = {
  hsaf: { 2016: 'c9912dd2-fac1-4929-9641-dc031422b458', 2017: '6628dd7e-9706-4e8a-bd85-f8450308066a', 2018: '263734c7-f5fd-4df2-8436-2322c86e7191',
    2019: '2713ba99-c59e-4b25-9a3d-3661d35988da', 2020: '609f2b73-d7e0-4dd1-a8b1-f951860ad9f7', 2021: '099b80df-be9c-4274-a974-31d4537bdc33',
    2022: '84a0a165-a570-4ffe-a230-043176c1221a', 2023: '7f749f00-bfa9-4377-9a98-90c15cacc2f3', 2024: '22e11819-48e8-44a8-a036-fd6351f4173a',
    2025: '8708ca8b-8636-44ed-8303-724cbfaf78ad' },
  pos: '8ba0f9b4-9493-4aa0-9f82-44ea9468d1b5', ip_drg_cy2024: '690ddc6c-2767-4618-b277-420ffb2bf27c', geovar: '6219697b-8f6c-4164-bed4-cd9317c58ebc',
  telehealth: '939226be-b107-476e-8777-f199a840138a', rhc: '3b7e7659-067e-41ea-8e36-f9ee2036e1f6', monthly_enrollment: 'd7fabe1e-d19b-4333-9eff-e80e0643f2fd',
  ip_provider_cy2024: 'df6b25d8-a00d-4d64-96c5-65fb441e51f6',
};

const known = (y, ccn) => hsaf[y].filter((r) => r.ccn === ccn).reduce((t, r) => t + (r.cases || 0), 0);
const om = {
  pulled: '2026-11-17', fictional: true, ccn: CCN, zips: ZIPS, county_fips: '00000', state: 'XX', sources, hsaf, hsaf_own_origin: ownOrigin,
  hsaf_ma_check: [['001101', 4310], ['001102', 2030], ['001103', 9120]].map(([ccn, ffs]) => {
    const k = Math.round(ffs * jitter(2.25, 0.06));
    return { ccn, year: 2024, hsaf_cases_known: k, ffs_discharges: ffs, ratio: k / ffs };
  }),
  providers, drg_mix_cy2024: { '001101': drgRows(1), '001102': drgRows(0.45), [CCN]: [] }, geovar, telehealth, rhcs,
  ma_monthly: { year: 2026, month: 'July', ab_total: 6512, ab_ma: 3653, ma_share: 3653 / 6512 },
  acs,
};

// ------------------------------------------------------------------ the subject hospital's cost reports

// Ancillary departments: [name, charges per acute day, per swing day, outpatient charges, cost-to-charge ratio, Medicare share of outpatient charges]
const DEPTS = {
  '05000': ['OPERATING ROOM', 20, 0, 1380000, 0.45, 0.42],
  '05400': ['RADIOLOGY-DIAGNOSTIC', 180, 25, 4620000, 0.27, 0.37],
  '05700': ['CT SCAN', 420, 30, 4380000, 0.12, 0.39],
  '06000': ['LABORATORY', 900, 110, 9810000, 0.24, 0.35],
  '06500': ['RESPIRATORY THERAPY', 650, 160, 760000, 0.42, 0.46],
  '06600': ['PHYSICAL THERAPY', 160, 420, 2110000, 0.44, 0.41],
  '06700': ['OCCUPATIONAL THERAPY', 90, 260, 420000, 0.46, 0.40],
  '06800': ['SPEECH PATHOLOGY', 30, 60, 90000, 0.60, 0.52],
  '07100': ['MEDICAL SUPPLIES CHARGED TO PATIENTS', 240, 70, 810000, 0.35, 0.31],
  '07300': ['DRUGS CHARGED TO PATIENTS', 1150, 230, 3920000, 0.33, 0.36],
  '09100': ['EMERGENCY', 40, 2, 7640000, 0.42, 0.29],
  '09200': ['OBSERVATION BEDS', 0, 0, 960000, 0, 0.46],
};

function costReport(fy, v) {
  const days = {
    beds: 22, bed_days_available: 8030, acute_total: v.acute, acute_tm: v.acuteTM, acute_mcd: Math.round(v.acute * 0.06), acute_ma: v.acuteMA,
    acute_discharges: Math.round(v.acute / 3.35), acute_discharges_tm: v.dischTM, swing_snf_total: v.snf, swing_snf_tm: v.snfTM, swing_nf_total: v.nf,
    icu: 0, nursery: 0, observation: v.obs, fte: 146.2,
  };
  days.adults_peds_total = days.total = v.acute + v.snf + v.nf;
  days.total_tm = v.acuteTM + v.snfTM;
  const nfRate = 262;
  const routine = v.routine;
  const d1 = { routine_cost: routine, nf_rate: nfRate, nf_carveout: nfRate * v.nf, per_diem: r2((routine - nfRate * v.nf) / (v.acute + v.snf + v.obs)) };

  const departments = {}, ipAcuteTM = {}, ipSwingTM = {}, medOP = {};
  for (const [line, [name, a, s, op, ccr, medShare]] of Object.entries(DEPTS)) {
    const ip = a * v.acute + s * (v.snf + 0.4 * v.nf);
    const opChg = Math.round(op * v.opScale);
    const cost = Math.round(ccr * (ip + opChg));
    departments[line] = { name, cost, ip_charges: Math.round(ip), op_charges: opChg, ccr };
    if (a) ipAcuteTM[line] = { charges: Math.round(a * v.acuteTM) };
    if (s) ipSwingTM[line] = { charges: Math.round(s * v.snfTM) };
    medOP[line] = { charges: Math.round(opChg * medShare) };
  }
  const medOPTotal = Object.values(medOP).reduce((t, r) => t + r.charges, 0);
  const reimbursable = routine + Object.entries(departments).filter(([l]) => l !== '09200').reduce((t, [, d]) => t + d.cost, 0);

  const rec = {
    rpt_rec_num: '000000', fy_begin: `10/01/${fy - 1}`, fy_end: `09/30/${fy}`, processed: '', status: 'Fictional', mac: '',
    profile: { name: 'SAMPLE HOSPITAL A', ccn: CCN, street: '', city: '', state: 'XX', zip: '00001', county: '', cah: true, methodII: true },
    days, d1, departments, routine_ip_charges: Math.round(v.acute * 2450 + (v.snf + v.nf) * 1150),
    medicare_ip_acute_ancillary: ipAcuteTM, medicare_swing_ancillary: ipSwingTM, medicare_op: medOP,
    e2_swing: { coinsurance: Math.round(v.snfTM * 0.3 * 209.5), bad_debt: 18400 },
    e3v_acute: { deductibles: Math.round(v.dischTM * 1676 * 0.78), bad_debt: 12700 },
    eb_outpatient: { deductibles: 46200, coinsurance: Math.round(medOPTotal * 0.181), primary_payer: 2300, bad_debt: Math.round(medOPTotal * 0.021) },
    s10: { medicaid_supplemental: 0, charity_cost: 96000, bad_debt_total: 2150000 },
    totals: { reimbursable_cost: reimbursable, operating_expense: reimbursable + v.otherCost },
    income: { other_income_total: 1840000 },
    flags: [], problems: [],
  };
  rec.totals.ip_charges = rec.routine_ip_charges + Object.values(departments).reduce((t, d) => t + d.ip_charges, 0);
  rec.totals.op_charges = Object.values(departments).reduce((t, d) => t + d.op_charges, 0);

  // Settlement totals the way the engine computes them, so the record is internally consistent.
  rec.income.net_patient_revenue = 1;
  const base = E.buildBaseline(rec, { rates: { snfMA: 487 } });
  const r0 = E.computeScenario(Object.assign({}, base, { opYield: 0 }), {});
  const m = r0.medicare;
  const settle = (total, extra) => Object.assign(extra, { total: Math.round(total), sequestration: Math.round(total * 0.02), interim: Math.round(total * 1.03),
    balance: Math.round(total * 0.98 - total * 1.03) });
  settle(m.totalSwing, rec.e2_swing);
  settle(m.totalA, rec.e3v_acute);
  settle(m.totalB, rec.eb_outpatient);
  // Net patient revenue: non-Medicare outpatient collects about 44% of charges at the $487 MA swing rate.
  const npr = Math.round(r0.revenue.total + v.opYield * r0.charges.opNonMedicare);
  rec.income.net_patient_revenue = npr;
  rec.income.operating_expense = rec.totals.operating_expense;
  rec.income.operating_income = npr - rec.totals.operating_expense;
  rec.income.gross_patient_revenue = rec.totals.ip_charges + rec.totals.op_charges;
  rec.income.contractual_allowances = rec.income.gross_patient_revenue - npr;
  rec.income.net_income = rec.income.operating_income + rec.income.other_income_total;
  return rec;
}

const hcris = {
  2023: costReport(2023, { acute: 905, acuteTM: 512, acuteMA: 141, dischTM: 152, snf: 1810, snfTM: 1290, nf: 255, obs: 505, routine: 5520000, opScale: 0.91, otherCost: 2380000, opYield: 0.45 }),
  2024: costReport(2024, { acute: 842, acuteTM: 461, acuteMA: 152, dischTM: 138, snf: 1760, snfTM: 1225, nf: 248, obs: 522, routine: 5710000, opScale: 0.96, otherCost: 2510000, opYield: 0.44 }),
  2025: costReport(2025, { acute: 768, acuteTM: 412, acuteMA: 158, dischTM: 123, snf: 1705, snfTM: 1162, nf: 241, obs: 541, routine: 5890000, opScale: 1.0, otherCost: 2640000, opYield: 0.44 }),
};

fs.mkdirSync(path.join(ROOT, 'data'), { recursive: true });
fs.writeFileSync(path.join(ROOT, `data/outmigration_${CCN}.json`), JSON.stringify(om));
fs.writeFileSync(path.join(ROOT, `data/hcris_${CCN}.json`), JSON.stringify(hcris, null, 1));
const r = hcris[2025];
console.log(`fictional hospital ${CCN}: 2025 known stays B ${known('2025', '001101')}, C ${known('2025', '001102')}, A ${known('2025', CCN)};`
  + ` FY2025 net patient revenue $${r.income.net_patient_revenue.toLocaleString()}, operating income $${r.income.operating_income.toLocaleString()}`);
