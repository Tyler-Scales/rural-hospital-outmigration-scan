# Outmigration assessment: data request

Public data shows where Medicare patients go for a hospital stay, and roughly how much outpatient care leaves. Deciding what to do about it takes four more kinds of information: where all patients go, why they leave, what bringing them back is worth, and what's feasible. Almost all of it is a standard report or a contract the hospital already has.

## Send counts, not records

**Do not send protected health information.** This assessment needs aggregate counts only.

- No patient names, addresses, patient ZIP codes, dates of birth or service, phone numbers, email addresses, Social Security numbers, medical record or account numbers, health plan member numbers, or anything else that identifies a patient.
- No files with one row per patient, visit, admission, claim, referral or order.
- Group counts by month or year, service, destination, payer group or reason.
- Show any count from 1 to 10 as `<11`.
- Put files in the `inputs/` folder. It stays on your machine and is never committed or uploaded.

The CSV files in [`templates/`](../templates) show the expected columns.

## Start with these five

| # | Item | Why it matters | Template |
|---|---|---|---|
| 1 | Medicare Advantage contract rates, especially the swing-bed per diem | Decides whether bringing residents home to a swing bed makes or loses money | `payer_rates.csv` |
| 2 | Outgoing referrals and orders, by type and destination | Replaces the outpatient estimate with real counts of the work sent elsewhere | `outgoing_referrals.csv` |
| 3 | Transfers out and swing-bed referrals, with reason and decision | Shows which admissions and post-acute stays are lost, and why | `transfers_out.csv`, `swing_referrals.csv` |
| 4 | Phone and scheduling access: calls, abandoned calls, wait for an appointment | Shows whether patients leave because they can't get in | `access_metrics.csv` |
| 5 | Any claims data the hospital already receives from an ACO or a health plan | Shows every service attributed patients got anywhere | Summarize as counts by service and destination |

## Everything that helps

### Where all patients go

| Item | What to send | Answers |
|---|---|---|
| Outgoing referrals and orders | Monthly counts by order type (imaging, lab, specialist referral, GI, therapy), service, destination and payer group | Which tests, scopes and specialist visits leave, and to whom |
| Transfers out | Monthly counts by reason (no bed, no specialist, no ICU, higher level of care, patient choice), diagnosis group, destination and payer group | Which admissions leave and why |
| Swing-bed referrals | Monthly counts by referring hospital, decision (accepted, declined, chose elsewhere), decline reason and payer group | How many post-acute patients were offered and lost |
| Emergency department visits | Monthly counts by acuity level, arrival time band and disposition (discharged, admitted, transferred, left without being seen) | After-hours demand and transfer patterns |
| ACO or health plan claims summaries | Counts by service type and destination, from reports the hospital already receives | Leakage across every setting, all from one payer |
| All-payer inpatient data, if purchased (state inpatient databases, claims vendors) | Counts by diagnosis group, home ZIP and destination | Stays for every payer, not just Medicare |

### Why they leave

| Item | What to send | Answers |
|---|---|---|
| Access metrics | Monthly calls received, calls abandoned, average seconds to answer, and third-next-available appointment, by clinic or department | Whether access, not capability, drives leakage |
| Primary care roster | Each practice serving the county and its affiliation (no patient data) | Whether referrals default to the system that employs the doctor |
| Service inventory | Each service with its hours, equipment and visiting specialist days | What's actually offered, and when |
| Network status | Which major plans the hospital is in network with, and for which services | Whether insurance steers residents elsewhere |
| Community input | Community health needs assessment, patient survey summaries, focus group notes | Perception, trust and preference |

### What bringing patients back is worth

| Item | What to send | Answers |
|---|---|---|
| Payer contract rates | Rates for Medicare Advantage, commercial and Medicaid plans by service (swing per diem, acute per diem, outpatient basis) | Whether added volume pays for each payer |
| Volumes by code | Annual counts and gross charges by CPT or HCPCS code and by MS-DRG, by setting and payer group | Real counts in place of the estimates |
| Spare capacity | Staffed beds, open bed days, CT and ultrasound hours used against hours available, staffed swing beds | Whether new volume needs new staff |
| Cost to add a service | Quotes or estimates for a visiting specialist, staff hours, equipment time | The net value of each move, not just revenue |
| Orders that follow a specialist visit | Counts of imaging and lab orders by ordering specialty | The full value of keeping one visit local |

### What's feasible

| Item | What to send | Answers |
|---|---|---|
| Referral hospital relationships | Transfer agreements, telehealth agreements, and how back-transfers to swing beds work today | Whether residents can be brought home after a tertiary stay |
| Staffing and physician coverage | Coverage model for nights, weekends and inpatient care | Whether more patients can be admitted locally |
| Technology | EHR reporting, health information exchange connection, phone and scheduling systems | Whether the digital front door can be built on what's there |
| Strategic plan and board priorities | The current plan | Which moves have support |

## Rules worth knowing before you plan

- **Critical access hospital limits:** a 25-bed cap, and a 96-hour annual average length of stay for acute patients.
- **Swing-bed coverage:** Traditional Medicare covers a swing-bed stay after a qualifying inpatient stay of three days. Some Medicare Advantage plans waive that.

## Measure from day one

Track a baseline before any change, then track it monthly:
- local share of stays
- referral leakage rate
- share of tests ordered here but done elsewhere
- swing-bed homecomings
- call answer rate
