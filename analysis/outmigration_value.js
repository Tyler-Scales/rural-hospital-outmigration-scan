/* What keeping patients local is worth to a critical access hospital, under CAH payment rules.
 *
 * Uses the CAH engine (engine/engine.js), calibrated to the hospital's latest cost report:
 * Traditional Medicare pays 101% of cost, MA and other payers pay their rates, and non-Medicare outpatient
 * collects the fitted yield. Every figure is the change in operating margin, not gross charges or revenue.
 *
 * Reads config.json (for the CCN), data/hcris_<ccn>.json and data/outmigration_<ccn>.json.
 * Writes data/outmigration_value_<ccn>.json. Run: node analysis/outmigration_value.js [config.json]
 */
const fs = require('fs');
const path = require('path');
const E = require('../engine/engine.js');

const ROOT = path.join(__dirname, '..');
const cfg = JSON.parse(fs.readFileSync(path.resolve(process.argv[2] || path.join(ROOT, 'config.json')), 'utf8'));
const ccn = cfg.ccn;
const hcris = require(path.join(ROOT, `data/hcris_${ccn}.json`));
const om = require(path.join(ROOT, `data/outmigration_${ccn}.json`));
const fye = Object.keys(hcris).sort().pop();
const rec = hcris[fye];

// Two MA cases: the NIC nursing-home placeholder, and MA paying off the MAC interim
// rate (about 95% of Traditional Medicare; AHA). Acute MA and other-payer rates stay engine placeholders.
const CASES = {
  placeholder: { label: 'MA swing at $487/day (NIC nursing-home placeholder)', rates: { snfMA: 487 } },
  interim: { label: 'MA swing at $3,180/day (about 95% of the Medicare interim rate)', rates: { snfMA: 3180 } },
};
const STEPS = { low: 0, base: 1, high: 2 };

// Lever inputs. Counts are deliberately small next to the volume that leaves (see `evidence`).
const geo = om.geovar.county[om.geovar.county.length - 1];
const swingLOS = Math.round(+geo.SNF_CVRD_DAYS_PER_1000_BENES / +geo.SNF_CVRD_STAYS_PER_1000_BENES);
const ownDays = om.hsaf_own_origin[Object.keys(om.hsaf_own_origin).sort().pop()];
const acuteLOS = (() => {
  const k = ownDays.filter((r) => r.cases && r.days);
  return +(k.reduce((t, r) => t + r.days, 0) / k.reduce((t, r) => t + r.cases, 0)).toFixed(1);
})();
const d = rec.days;
const swingTMShare = d.swing_snf_tm / d.swing_snf_total;
const acuteTMShare = d.acute_tm / (d.acute_tm + d.acute_ma);

const LEVERS = {
  swing: { label: 'Bring residents home to a swing bed after a tertiary stay', unit: 'swing admissions a year', counts: [3, 6, 12] },
  acute: { label: 'Admit CAH-appropriate medical patients locally', unit: 'acute admissions a year', counts: [6, 12, 24] },
  outpatient: { label: 'Keep ordered imaging, lab, therapy and GI work local', unit: 'more outpatient volume in those departments', pcts: [0.05, 0.10, 0.20] },
};
const OP_LINES = { '05400': 'Radiology', '06000': 'Laboratory', '06600': 'Physical therapy', '06700': 'Occupational therapy', '05000': 'GI endoscopy (OR cost center)' };

function marginDelta(base, mutate) {
  const r0 = E.computeScenario(base, {});
  const b1 = JSON.parse(JSON.stringify(base));
  const sc = mutate(b1) || {};
  const r1 = E.computeScenario(b1, sc);
  return {
    margin: r1.margin - r0.margin,
    medicare: r1.medicare.revenue - r0.medicare.revenue,
    revenue: r1.revenue.total - r0.revenue.total,
    cost: r1.cost.total - r0.cost.total,
  };
}

const out = { ccn, fye: rec.fy_end, inputs: { swingLOS, acuteLOS, swingTMShare, acuteTMShare, opLines: OP_LINES }, cases: {}, levers: LEVERS };
for (const [key, c] of Object.entries(CASES)) {
  const base = E.buildBaseline(rec, { rates: c.rates });
  const mv = E.marginalValues(base, {});
  const res = { label: c.label, rates: base.rates, opYield: base.opYield, perDay: mv, levers: {} };

  res.levers.swing = LEVERS.swing.counts.map((n) => {
    const days = n * swingLOS;
    return { n, days, ...marginDelta(base, (b) => ({ days: Object.assign({}, b.days, { snfTM: b.days.snfTM + days * swingTMShare, snfMA: b.days.snfMA + days * (1 - swingTMShare) }) })) };
  });
  res.levers.acute = LEVERS.acute.counts.map((n) => {
    const days = n * acuteLOS;
    return { n, days, ...marginDelta(base, (b) => ({ days: Object.assign({}, b.days, { acuteTM: b.days.acuteTM + days * acuteTMShare, acuteMA: b.days.acuteMA + days * (1 - acuteTMShare) }) })) };
  });
  res.levers.outpatient = LEVERS.outpatient.pcts.map((p) => {
    const byLine = {};
    for (const line of Object.keys(OP_LINES)) {
      if (!base.depts[line]) continue;
      byLine[line] = marginDelta(base, (b) => { b.depts[line].opChg *= 1 + p; b.medOP[line] = (b.medOP[line] || 0) * (1 + p); });
    }
    const all = marginDelta(base, (b) => {
      for (const line of Object.keys(byLine)) { b.depts[line].opChg *= 1 + p; b.medOP[line] = (b.medOP[line] || 0) * (1 + p); }
    });
    const charges = Object.keys(byLine).reduce((t, l) => t + base.depts[l].opChg * p, 0);
    return { pct: p, charges, ...all, byLine };
  });
  out.cases[key] = res;
}

// Self-checks: no change reproduces the filed operating income; one department matches a hand calculation.
{
  const base = E.buildBaseline(rec, { rates: CASES.placeholder.rates });
  const zero = marginDelta(base, () => {});
  if (Math.abs(zero.margin) > 1e-6) throw new Error('zero scenario moved margin');
  if (Math.abs(E.computeScenario(base, {}).margin - rec.income.operating_income) > 1) throw new Error('baseline does not match operating income');
  // Radiology +10%: Medicare pays 101% of its (now lower-CCR) cost share less cost sharing; others pay opYield.
  const L = '05400', p = 0.10, dep = base.depts[L];
  const chg0 = dep.ipChg + dep.opChg, chg1 = chg0 + dep.opChg * p;
  const cost0 = dep.fixed + dep.varPerChg * chg0, cost1 = dep.fixed + dep.varPerChg * chg1;
  const med0 = base.medOP[L] * cost0 / chg0, med1 = base.medOP[L] * (1 + p) * cost1 / chg1;
  const cs = base.share.opCostSharePerChg + base.share.opPrimaryPayerPerChg;
  const dMedicare = (1.01 * (med1 - med0) - cs * base.medOP[L] * p) * (1 - base.sequestration)
    + (base.share.opCostSharePerChg - base.share.opBadDebtPerChg + base.share.opPrimaryPayerPerChg) * base.medOP[L] * p
    + 0.65 * base.share.opBadDebtPerChg * base.medOP[L] * p * (1 - base.sequestration);
  // A lower cost-to-charge ratio also trims what Medicare pays for this department's inpatient charges.
  const ipMedChg = base.perDay.acuteTM[L] * base.days.acuteTM + base.perDay.snfTM[L] * base.days.snfTM;
  const dPartA = 1.01 * (cost1 / chg1 - cost0 / chg0) * ipMedChg * (1 - base.sequestration);
  const dOther = base.opYield * (dep.opChg - base.medOP[L]) * p;
  const hand = dMedicare + dPartA + dOther - (cost1 - cost0);
  const eng = marginDelta(base, (b) => { b.depts[L].opChg *= 1 + p; b.medOP[L] *= 1 + p; }).margin;
  out.check = { radiology10pct: { engine: eng, hand } };
  if (Math.abs(eng - hand) > 1) throw new Error(`radiology hand check ${hand} vs engine ${eng}`);
}

const file = path.join(ROOT, `data/outmigration_value_${ccn}.json`);
fs.writeFileSync(file, JSON.stringify(out, null, 1));
const f = (x) => (x < 0 ? '-' : '') + '$' + Math.round(Math.abs(x)).toLocaleString('en-US');
console.log(`FY${fye.slice(-4)} base; swing LOS ${swingLOS} d, acute LOS ${acuteLOS} d, swing TM share ${(swingTMShare * 100).toFixed(0)}%, acute TM share ${(acuteTMShare * 100).toFixed(0)}%`);
for (const [k, c] of Object.entries(out.cases)) {
  console.log(`\n${c.label}  (opYield ${(c.opYield * 100).toFixed(0)}%)`);
  console.log('  per day:', Object.entries(c.perDay).map(([s, v]) => `${s} ${f(v.margin)}`).join(', '));
  for (const s of c.levers.swing) console.log(`  swing +${s.n} stays (${s.days} d): margin ${f(s.margin)}`);
  for (const s of c.levers.acute) console.log(`  acute +${s.n} admits (${s.days.toFixed(0)} d): margin ${f(s.margin)}`);
  for (const s of c.levers.outpatient) console.log(`  outpatient +${s.pct * 100}% (${f(s.charges)} charges): margin ${f(s.margin)}  [${Object.entries(s.byLine).map(([l, v]) => `${OP_LINES[l]} ${f(v.margin)}`).join('; ')}]`);
}
console.log('\ncheck', out.check);
