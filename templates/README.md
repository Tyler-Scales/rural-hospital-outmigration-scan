# Templates for the hospital's counts

Each file holds column headers only. Fill in a copy, save it in `inputs/` (git ignores that folder), and keep to these rules:

- **Counts only.** One row per group (for example, one month, one order type and one destination), never one row per patient, visit or order.
- **No identifiers.** No names, addresses, patient ZIP codes, dates of birth or service, record or account numbers, or member numbers.
- **Small counts hidden.** Write any count from 1 to 10 as `<11`.
- **Months as `YYYY-MM`.** Years as `YYYY`.

| File | One row per | Notes |
|---|---|---|
| `outgoing_referrals.csv` | Month, order type, service, destination and payer group | `order_type`: imaging, lab, specialist referral, GI, therapy or other. `destination_type`: this hospital, other hospital, independent site or unknown. |
| `transfers_out.csv` | Month, reason, diagnosis group, destination and payer group | `reason`: no bed, no specialist, no ICU, higher level of care, patient choice or other. Use broad diagnosis groups or MS-DRG families. |
| `swing_referrals.csv` | Month, referring hospital, decision, reason and payer group | `decision`: accepted, declined or chose elsewhere. |
| `ed_visits.csv` | Month, acuity level, arrival time band and disposition | `arrival_time_band`: for example 8am to 5pm, 5pm to 11pm, 11pm to 8am. `disposition`: discharged, admitted, transferred or left without being seen. |
| `access_metrics.csv` | Month and clinic or department | Phone and scheduling system reports. |
| `payer_rates.csv` | Payer, product and service | `rate_basis`: per diem, case rate, percent of charges or fee schedule. |
| `volumes_by_code.csv` | Year, code, setting and payer group | `code_type`: CPT, HCPCS or MS-DRG. `setting`: inpatient, outpatient or swing. |

`payer_group` throughout: Medicare, Medicare Advantage, Medicaid, commercial, self-pay or other.
