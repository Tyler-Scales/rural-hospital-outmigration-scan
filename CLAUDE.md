# Outmigration assessment: instructions for Claude Code

This repository runs an outmigration assessment for one rural hospital, normally a critical access hospital (CAH). It covers where the hospital's residents go for care, which leaks a digital front door and AI patient engagement can fix, and what each fix is worth under CAH payment rules. Claude Code gathers the public data, builds the briefing, and then tells the hospital what internal information would sharpen it.

The user may type `/outmigration-assessment <CCN>`, or simply ask for an assessment. Either way, follow this file.

## Privacy rules: no PHI, ever

The assessment needs public data plus **aggregate counts** from the hospital. It never needs protected health information, and Claude Code must not use any.

- **Never ask for, accept, open, copy, quote, summarize or commit patient-level data.** That means any file with one row per patient, visit, admission, claim, referral or order, and any file with a HIPAA identifier: names, street addresses, a patient's ZIP code, dates tied to a person (birth, admission, discharge, service), phone numbers, email addresses, Social Security numbers, medical record or account numbers, health plan member numbers, device identifiers, photos, or anything else that could identify a patient.
- **Ask only for counts.** Group them by month or year, service, destination, payer group or reason. Show any count from 1 to 10 as `<11`, the same rule CMS uses. The templates in `templates/` show the expected shapes.
- **Check before you use.** When the user provides a file, read only its header and first few lines to check the structure. If it looks patient-level or holds an identifier, stop: don't read further, don't copy or quote it, and don't use it. Tell the user it looks like PHI, ask them to delete it from this folder, and ask for an aggregate version instead.
- **Keep hospital files local.** Hospital-supplied files go in `inputs/`, which git ignores. Never commit them. Never put their contents in a web request, a GitHub issue or pull request, or any other outside service. Public downloads only come in; hospital data never goes out.
- **Hold the line.** If the user asks you to work with PHI anyway, decline, explain why, and offer the aggregate route.
- **Report at group level only.** Reports may name the hospital and the hospitals its residents use, which is public CMS data. They never describe an individual patient.

Say this to the user once, plainly, before asking for any hospital data: "Please don't share patient-level data or anything that identifies a patient. Send counts only, with any count from 1 to 10 shown as <11."

## Workflow

### 0. Check the tools
Python 3.9 or later, Node 18 or later, and the `openpyxl` package (`python3 -m pip install openpyxl`). Everything else uses the standard library. Run pipeline scripts with `python3 -I`, since they read downloaded files.

### 1. Identify the hospital
Ask for the hospital's CMS Certification Number (CCN). It is six characters long and appears on the Medicare cost report, in Care Compare and on the hospital's price transparency file. If the user gives a name and state instead, find the CCN in the CMS Provider of Services file. A CAH's CCN has digits 3 to 6 between 1300 and 1399. If the hospital isn't a CAH, say that the scan still works but the dollar values won't, because the model assumes cost-based Medicare payment.

### 2. Draft the service area
```bash
python3 -I pipeline/service_area.py <ccn>
```
This writes `config.json`: the hospital's county, and every ZIP code with more than half its land in that county. Show the user the ZIP list, including the "partly in the county" ZIPs it prints, and ask them to confirm or adjust it. Also confirm the hospital name and the outpatient services it offers (`local_services`). The outpatient lever's department settings come after the cost reports are in (step 3).

### 3. Gather the public data
```bash
python3 -I pipeline/outmigration.py config.json      # CMS and Census; streams about 190 MB of Census files
python3 -I pipeline/outmigration_geo.py config.json  # map shapes
python3 -I pipeline/hcris.py run <ccn>               # downloads three CMS cost-report files, 80 to 140 MB each
```
Then check `data/hcris_<ccn>.json`. CT and MRI are added to the outpatient lever automatically when the cost report keeps them as separate departments. If the operating room carries outpatient surgery as well as scopes, set `op_lines` and `op_move_title` in `config.json` so the labels say so. If the latest report's `flags` include an ICU, a nursery, a distinct-part nursing unit or no swing beds, tell the user those parts aren't modeled. If `problems` is not empty, say which cost-report identity failed.

### 4. Run the model and build the briefing
```bash
node analysis/outmigration_value.js config.json
python3 analysis/build_report.py config.json
```
The value model refuses to run if it can't reproduce the hospital's filed operating income, or if its radiology hand check fails. The builder refuses to build if a workbook formula disagrees with the page. Report any failure as it is.

Open `output/report.html` and summarize it for the user: the top destinations, the local share and its trend, the ranked moves with their value ranges, and the Medicare Advantage caveat.

### 5. Write the data request
Write `output/data-request.md` for this hospital, starting from `docs/data-request.md`.
- Put the no-PHI rule at the top.
- Name the hospital and its top destinations from the report.
- Say which move or figure each item would sharpen.
- List the five most valuable items first.
- Point to the matching file in `templates/` for each item.

Tell the user that every item is a standard report or a contract they already have, and that none of it needs patient-level data.

### 6. Use what comes back
When aggregate files arrive in `inputs/`, apply the privacy check first. Then use them to replace the matching estimates (for example, actual imaging counts in place of the charge-based estimate, or real MA swing rates in place of the placeholders), rerun the model if rates changed, and tell the user which figures moved and by how much.

## How to read the public data (don't skip)

- **CAH payment rules come first.** Traditional Medicare pays a CAH 101% of allowable cost, so added Medicare volume is worth its share of fixed cost, not its charges. Medicare Advantage pays contract rates. At a nursing-home-level MA rate, a swing-bed stay can lose money. Get the MA contract rates before recommending a swing-bed push.
- **The service-area file counts acute hospital stays only,** and the counts include Medicare Advantage (about 2.3 times fee-for-service discharges at the same hospitals). Swing-bed stays are billed as skilled nursing and are not in it.
- **Hidden counts are not zeros.** CMS hides counts from 1 to 10. Carry them as a range, never as 0.
- **Diagnoses aren't published by home ZIP.** Each destination hospital's own DRG mix stands in for residents' stays there.
- **No public file follows outpatient care by home ZIP.** County imaging and procedure counts come from physician claims. County lab test counts leave out hospital lab work, so they can't be compared with a hospital's lab volume.
- **Cost-report years.** CMS files a cost report under the federal fiscal year in which its period begins. A report for a year ending June 30, 2025 sits in the FY2024 file.
- **Names and geography.** With `"anonymize": true` the report carries labels only and leaves the map out. With `"hide_towns": true` hospitals keep their names but no town appears.

## Layout

- `pipeline/`: data pulls (`service_area.py`, `outmigration.py`, `outmigration_geo.py`, `hcris.py`)
- `engine/engine.js`: the CAH cost-based reimbursement engine
- `analysis/`: the value model, the report builder and the page template
- `docs/data-request.md`: the generic data request
- `templates/`: CSV shapes for the aggregate counts
- `config.json`, `data/`, `output/`, `inputs/` and `pipeline/cache/` are git-ignored, so a hospital's run stays on its own machine
