/* Critical Access Hospital cost-based reimbursement engine.
 *
 * A simplified, deterministic re-statement of the Medicare cost report (Form CMS-2552-10)
 * for a CAH with swing beds:
 *   - routine per diem (D-1): swing days of payers other than Traditional Medicare and MA
 *     (NF-type on S-3 line 6) are carved out at the Medicaid NF rate; acute, Medicare and MA
 *     swing (SNF-type, S-3 line 5) and observation days share one per diem
 *   - ancillary cost by department (C Pt I): charges x cost-to-charge ratio
 *   - Medicare pays 101% of cost, less beneficiary cost sharing, plus 65% of allowable
 *     bad debt, less sequestration (E-3 Pt V, E-2, E Pt B)
 * Costs are split into fixed and variable parts so volume and payer mix can move.
 *
 * Pure functions, no DOM. Browser: window.CAHEngine. Node: require('./engine.js').
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CAHEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Inpatient day streams. snfMA = Medicare Advantage swing days (SNF-type, S-3 line 5 column 8);
  // nf = all other payers' swing days (NF-type, S-3 line 6).
  const STREAMS = ['acuteTM', 'acuteMA', 'acuteOther', 'snfTM', 'snfMA', 'nf'];
  const STREAM_LABELS = {
    acuteTM: 'Acute, Traditional Medicare', acuteMA: 'Acute, Medicare Advantage', acuteOther: 'Acute, other payers',
    snfTM: 'Skilled swing, Traditional Medicare', snfMA: 'Skilled swing, Medicare Advantage', nf: 'Swing, other payers (NF-type)',
  };
  const OBS_LINE = '09200';
  const MEDICARE_MARKUP = 1.01;      // CAHs are paid 101% of reasonable cost
  const BAD_DEBT_SHARE = 0.65;       // share of allowable Medicare bad debt reimbursed

  const DEFAULT_ASSUMPTIONS = {
    fixedShareRoutine: 0.75,         // share of routine (nursing unit) cost that does not move with days
    fixedShareAncillary: 0.60,       // same, for ancillary departments
    maSnfAncillaryVsTM: 1.0,         // MA skilled-swing ancillary use per day, relative to Medicare's
    sequestration: 0.02,
    // $ per patient day. snfMA: what MA plans paid nursing homes in August 2023 (NIC MAP Vision),
    // a placeholder until the hospital's MA contract rates are loaded. Acute rates are placeholders.
    // nf: replaced by the hospital's own D-1 Medicaid NF rate when its cost report has one.
    rates: { acuteMA: 3500, acuteOther: 3000, snfMA: 487, nf: 250 },
  };

  const sum = (xs) => xs.reduce((t, x) => t + x, 0);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function withDefaults(a) {
    const out = clone(DEFAULT_ASSUMPTIONS);
    if (!a) return out;
    for (const k of Object.keys(a)) {
      if (k === 'rates') Object.assign(out.rates, a.rates);
      else out[k] = a[k];
    }
    return out;
  }

  /** Build the calibrated baseline from one decoded cost report (data/hcris_<ccn>.json[fye], from pipeline/hcris.py). */
  // Worksheets a hospital may not file (no swing beds, no outpatient Medicare) default to zeros.
  const ZERO_SETTLEMENT = { total: 0, sequestration: 0, coinsurance: 0, deductibles: 0, bad_debt: 0, primary_payer: 0, interim: 0 };

  function buildBaseline(rec, assumptions) {
    const a = withDefaults(assumptions);
    if (!(assumptions && assumptions.rates && assumptions.rates.nf != null) && rec.d1 && rec.d1.nf_rate > 0) a.rates.nf = Math.round(rec.d1.nf_rate);
    const d = rec.days;
    const days = {
      acuteTM: d.acute_tm,
      acuteMA: d.acute_ma,
      acuteOther: Math.max(0, d.acute_total - d.acute_tm - d.acute_ma),
      snfTM: d.swing_snf_tm,
      snfMA: Math.max(0, d.swing_snf_total - d.swing_snf_tm),
      nf: d.swing_nf_total,
    };
    const obs = d.observation;
    const allRoutineDays = d.acute_total + d.swing_snf_total + d.swing_nf_total + obs;
    const routine = {
      fixed: a.fixedShareRoutine * rec.d1.routine_cost,
      varPerDay: allRoutineDays ? (1 - a.fixedShareRoutine) * rec.d1.routine_cost / allRoutineDays : 0,
      // effective Medicaid NF rate actually used in the D-1 carve-out (lines 24-25; the rate can change mid-year)
      nfRate: d.swing_nf_total ? rec.d1.nf_carveout / d.swing_nf_total : rec.d1.nf_rate,
    };

    const depts = {};
    for (const [line, dep] of Object.entries(rec.departments)) {
      const chg = dep.ip_charges + dep.op_charges;
      if (chg <= 0) continue;
      const isObs = line === OBS_LINE;
      depts[line] = {
        name: dep.name, isObs, baseCost: dep.cost, ipChg: dep.ip_charges, opChg: dep.op_charges,
        fixed: isObs ? 0 : a.fixedShareAncillary * dep.cost,
        varPerChg: isObs ? 0 : (1 - a.fixedShareAncillary) * dep.cost / chg,
      };
    }
    const lines = Object.keys(depts);

    // Ancillary charges per patient day, by stream and department.
    const perDay = {};
    for (const s of STREAMS) perDay[s] = {};
    const tmAcute = rec.medicare_ip_acute_ancillary || {};
    const tmSwing = rec.medicare_swing_ancillary || {};
    for (const line of lines) {
      const at = (tmAcute[line] || {}).charges || 0;
      const st = (tmSwing[line] || {}).charges || 0;
      perDay.acuteTM[line] = days.acuteTM ? at / days.acuteTM : 0;
      perDay.acuteMA[line] = perDay.acuteTM[line];
      perDay.acuteOther[line] = perDay.acuteTM[line];
      perDay.snfTM[line] = days.snfTM ? st / days.snfTM : 0;
    }
    // What is left of inpatient ancillary charges belongs to non-Medicare swing stays.
    // Split it between MA skilled and NF-level stays so MA skilled patients use ancillaries
    // at `maSnfAncillaryVsTM` times Medicare's daily rate (capped by what is there).
    const residual = {};
    for (const line of lines) {
      const used = ['acuteTM', 'acuteMA', 'acuteOther', 'snfTM'].reduce((t, s) => t + perDay[s][line] * days[s], 0);
      residual[line] = Math.max(0, depts[line].ipChg - used);
    }
    const residualTotal = sum(Object.values(residual));
    const tmSwingPerDay = sum(lines.map((l) => perDay.snfTM[l]));
    const maTarget = Math.min(residualTotal, days.snfMA * tmSwingPerDay * a.maSnfAncillaryVsTM);
    const maShare = residualTotal > 0 ? maTarget / residualTotal : 0;
    for (const line of lines) {
      perDay.snfMA[line] = days.snfMA
        ? residual[line] * maShare / days.snfMA
        : perDay.snfTM[line] * a.maSnfAncillaryVsTM;
      perDay.nf[line] = days.nf ? residual[line] * (1 - maShare) / days.nf : 0;
    }

    const medOP = {};
    for (const line of lines) medOP[line] = ((rec.medicare_op || {})[line] || {}).charges || 0;
    const medOPTotal = sum(Object.values(medOP));
    const e2 = Object.assign({}, ZERO_SETTLEMENT, rec.e2_swing), e3 = Object.assign({}, ZERO_SETTLEMENT, rec.e3v_acute), eb = Object.assign({}, ZERO_SETTLEMENT, rec.eb_outpatient);
    const share = {
      swingCoinsPerDay: days.snfTM ? e2.coinsurance / days.snfTM : 0,
      swingBadDebtPerDay: days.snfTM ? e2.bad_debt / days.snfTM : 0,
      acuteDedPerDay: days.acuteTM ? e3.deductibles / days.acuteTM : 0,
      acuteBadDebtPerDay: days.acuteTM ? e3.bad_debt / days.acuteTM : 0,
      opCostSharePerChg: medOPTotal ? (eb.deductibles + eb.coinsurance) / medOPTotal : 0,
      opPrimaryPayerPerChg: medOPTotal ? eb.primary_payer / medOPTotal : 0,
      opBadDebtPerChg: medOPTotal ? eb.bad_debt / medOPTotal : 0,
    };

    const base = {
      fye: rec.fy_end, days, obs, routine, depts, perDay, medOP, share,
      rates: clone(a.rates), sequestration: a.sequestration, assumptions: a,
      medicaidSupplemental: (rec.s10 && rec.s10.medicaid_supplemental) || 0,
      fixedCostBlock: rec.totals.operating_expense - rec.totals.reimbursable_cost,
      otherIncome: rec.income.other_income_total,
      opYield: 0,
      actual: {
        netPatientRevenue: rec.income.net_patient_revenue,
        operatingIncome: rec.income.operating_income,
        operatingExpense: rec.totals.operating_expense,
        e2Total: e2.total, e3Total: e3.total, ebTotal: eb.total,
        medicareProgram: (e2.total - e2.sequestration) + (e3.total - e3.sequestration) + (eb.total - eb.sequestration),
        perDiem: rec.d1.per_diem,
      },
    };
    // Costs the model doesn't break out (non-reimbursable areas, and for some hospitals ICU, nursery,
    // rural health clinic or distinct-part units) sit in one block, so modeled total cost equals the
    // hospital's operating expense exactly.
    const modeled = computeScenario(base, {}).cost.reimbursable;
    base.fixedCostBlock = rec.totals.operating_expense - modeled;
    // One fitted scalar: what non-Medicare outpatient charges collect, so modeled net
    // patient revenue equals the cost report's. Everything else is cost-report data or a labeled assumption.
    const r0 = computeScenario(base, {});
    base.opYield = r0.charges.opNonMedicare > 0 ? (rec.income.net_patient_revenue - r0.revenue.total) / r0.charges.opNonMedicare : 0;
    return base;
  }

  /** Run one scenario. `sc.days` overrides annual days by stream; everything else is optional. */
  function computeScenario(base, sc) {
    sc = sc || {};
    const days = Object.assign({}, base.days, sc.days || {});
    const obs = sc.obs != null ? sc.obs : base.obs;
    const rates = Object.assign({}, base.rates, sc.rates || {});
    const seq = sc.sequestration != null ? sc.sequestration : base.sequestration;
    const opVolume = sc.opVolume != null ? sc.opVolume : 1;
    const medOPShare = sc.medicareOPShareFactor != null ? sc.medicareOPShareFactor : 1;
    const trend = sc.costTrend != null ? sc.costTrend : 1;
    const price = sc.priceTrend != null ? sc.priceTrend : 1;   // escalates non-Medicare payment rates

    const A = days.acuteTM + days.acuteMA + days.acuteOther;
    const S = days.snfTM + days.snfMA;
    const N = days.nf;
    const obsScale = base.obs ? obs / base.obs : 0;

    const routineCost = trend * (base.routine.fixed + base.routine.varPerDay * (A + S + N + obs));
    const perDiemDays = A + S + obs;
    const perDiem = perDiemDays > 0 ? (routineCost - N * trend * base.routine.nfRate) / perDiemDays : 0;
    const obsCost = obs * perDiem;

    const dept = {};
    let ancillaryCost = 0, opAll = 0;
    for (const [line, dp] of Object.entries(base.depts)) {
      let ip = 0;
      for (const s of STREAMS) ip += (base.perDay[s][line] || 0) * days[s];
      const op = dp.opChg * (dp.isObs ? obsScale : opVolume);
      const chg = ip + op;
      const cost = dp.isObs ? obsCost : trend * (dp.fixed + dp.varPerChg * chg);
      dept[line] = { ip, op, chg, cost, ccr: chg > 0 ? cost / chg : 0 };
      if (!dp.isObs) ancillaryCost += cost;
      opAll += op;
    }

    let medOPChg = 0, costOP = 0;
    for (const [line, c] of Object.entries(base.medOP)) {
      const dp = base.depts[line];
      const ch = c * (dp && dp.isObs ? obsScale : opVolume) * medOPShare;
      medOPChg += ch;
      costOP += (dept[line] ? dept[line].ccr : 0) * ch;
    }
    const ancFor = (s, n) => {
      let t = 0;
      for (const line in dept) t += dept[line].ccr * (base.perDay[s][line] || 0) * n;
      return t;
    };
    const costAcute = days.acuteTM * perDiem + ancFor('acuteTM', days.acuteTM);
    const costSwing = days.snfTM * perDiem + ancFor('snfTM', days.snfTM);

    const sh = base.share;
    const acuteDed = sh.acuteDedPerDay * days.acuteTM, acuteBD = sh.acuteBadDebtPerDay * days.acuteTM;
    const swingCoins = sh.swingCoinsPerDay * days.snfTM, swingBD = sh.swingBadDebtPerDay * days.snfTM;
    const opCS = sh.opCostSharePerChg * medOPChg, opPP = sh.opPrimaryPayerPerChg * medOPChg, opBD = sh.opBadDebtPerChg * medOPChg;

    const totalA = MEDICARE_MARKUP * costAcute - acuteDed + BAD_DEBT_SHARE * acuteBD;      // E-3 Pt V line 30
    const totalSwing = MEDICARE_MARKUP * costSwing - swingCoins + BAD_DEBT_SHARE * swingBD; // E-2 line 19
    const totalB = MEDICARE_MARKUP * costOP - opCS - opPP + BAD_DEBT_SHARE * opBD;          // E Pt B line 40
    const programA = totalA * (1 - seq), programSwing = totalSwing * (1 - seq), programB = totalB * (1 - seq);
    const collected = (acuteDed - acuteBD) + (swingCoins - swingBD) + (opCS - opBD) + opPP;
    const medicareRevenue = programA + programSwing + programB + collected;

    const rev = {
      medicare: medicareRevenue,
      maAcute: days.acuteMA * rates.acuteMA * price,
      otherAcute: days.acuteOther * rates.acuteOther * price,
      maSnf: days.snfMA * rates.snfMA * price,
      nf: days.nf * rates.nf * price,
      outpatientNonMedicare: base.opYield * (opAll - medOPChg) * price,
      medicaidSupplemental: base.medicaidSupplemental,
    };
    rev.total = sum(Object.values(rev));
    const reimbursableCost = routineCost + ancillaryCost;
    const totalCost = reimbursableCost + trend * base.fixedCostBlock;
    const inpatientDays = A + S + N;
    const margin = rev.total - totalCost;
    return {
      days, obs, perDiem, routineCost, obsCost, dept,
      medicare: {
        costAcute, costSwing, costOP, totalA, totalSwing, totalB,
        programA, programSwing, programB, program: programA + programSwing + programB,
        sequestration: (totalA + totalSwing + totalB) * seq, revenue: medicareRevenue,
      },
      revenue: rev,
      charges: { opAll, opMedicare: medOPChg, opNonMedicare: opAll - medOPChg },
      cost: { routine: routineCost, ancillary: ancillaryCost, reimbursable: reimbursableCost, fixedBlock: trend * base.fixedCostBlock, total: totalCost },
      margin,
      netMargin: margin + base.otherIncome,
      inpatientDays,
      adc: inpatientDays / 365,
      tmShare: inpatientDays ? (days.acuteTM + days.snfTM) / inpatientDays : 0,
      tmShareOfPerDiemDays: perDiemDays ? (days.acuteTM + days.snfTM) / perDiemDays : 0,
    };
  }

  /** Margin change from one more patient day in each stream (with its ancillaries). */
  function marginalValues(base, sc) {
    sc = sc || {};
    const r0 = computeScenario(base, sc);
    const out = {};
    for (const s of STREAMS) {
      const days = Object.assign({}, base.days, sc.days || {});
      days[s] += 1;
      const r1 = computeScenario(base, Object.assign({}, sc, { days }));
      out[s] = { margin: r1.margin - r0.margin, medicare: r1.medicare.revenue - r0.medicare.revenue, cost: r1.cost.total - r0.cost.total };
    }
    return out;
  }

  /** The MA skilled-swing $/day at which one more such day leaves margin unchanged. */
  function breakEvenRate(base, sc, stream) {
    stream = stream || 'snfMA';
    sc = sc || {};
    const price = sc.priceTrend != null ? sc.priceTrend : 1;
    const rate = Object.assign({}, base.rates, sc.rates || {})[stream] * price;
    return rate - marginalValues(base, sc)[stream].margin;
  }

  /** Non-Medicare day composition of a scenario (shares of the non-Traditional-Medicare days). */
  function nonTMMix(days) {
    const keys = ['acuteMA', 'acuteOther', 'snfMA', 'nf'];
    const tot = sum(keys.map((k) => days[k]));
    const out = {};
    for (const k of keys) out[k] = tot ? days[k] / tot : 0.25;
    return out;
  }

  /** Annual days by stream for a given ADC (acute + swing) and Traditional Medicare share. */
  function daysForMix(base, adc, tmShare, mix, opts) {
    opts = opts || {};
    mix = mix || nonTMMix(base.days);
    const total = adc * 365;
    const tm = total * tmShare, other = total - tm;
    const acuteShareOfTM = opts.acuteShareOfTM != null ? opts.acuteShareOfTM
      : base.days.acuteTM / Math.max(1, base.days.acuteTM + base.days.snfTM);
    return {
      acuteTM: tm * acuteShareOfTM,
      snfTM: tm * (1 - acuteShareOfTM),
      acuteMA: other * mix.acuteMA,
      acuteOther: other * mix.acuteOther,
      snfMA: other * mix.snfMA,
      nf: other * mix.nf,
    };
  }

  /** ADC at which (operating or net) margin reaches zero for a fixed payer mix; null if none in range. */
  function breakEvenADC(base, tmShare, mix, sc, opts) {
    opts = opts || {};
    const key = opts.net ? 'netMargin' : 'margin';
    const f = (adc) => computeScenario(base, Object.assign({}, sc, { days: daysForMix(base, adc, tmShare, mix) }))[key];
    let lo = opts.min || 0.5, hi = opts.max || 40;
    let flo = f(lo), fhi = f(hi);
    if (flo === 0) return lo;
    if (Math.sign(flo) === Math.sign(fhi)) return null;
    for (let i = 0; i < 60; i++) {
      const mid = (lo + hi) / 2, fm = f(mid);
      if (Math.sign(fm) === Math.sign(flo)) { lo = mid; flo = fm; } else { hi = mid; }
    }
    return (lo + hi) / 2;
  }

  /** Margin surface over ADC x Traditional Medicare share. */
  function surface(base, xs, ys, mix, sc, opts) {
    opts = opts || {};
    const key = opts.net ? 'netMargin' : 'margin';
    const z = [];
    for (const y of ys) {
      const row = [];
      for (const x of xs) row.push(computeScenario(base, Object.assign({}, sc, { days: daysForMix(base, x, y, mix) }))[key]);
      z.push(row);
    }
    return z;
  }

  /** The census and Traditional Medicare share with the highest operating margin on a grid. */
  function bestOnGrid(base, xs, ys, mix, sc) {
    let best = null;
    for (const x of xs) for (const y of ys) {
      const r = computeScenario(base, Object.assign({}, sc, { days: daysForMix(base, x, y, mix) }));
      if (!best || r.margin > best.margin) best = { margin: r.margin, netMargin: r.netMargin, adc: x, tmShare: y };
    }
    return best;
  }

  return {
    STREAMS, STREAM_LABELS, DEFAULT_ASSUMPTIONS, buildBaseline, computeScenario, marginalValues,
    breakEvenRate, breakEvenADC, daysForMix, nonTMMix, surface, bestOnGrid,
  };
});
