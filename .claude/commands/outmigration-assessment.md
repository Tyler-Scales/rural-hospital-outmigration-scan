---
description: Run a public-data outmigration assessment for a critical access hospital, then draft the hospital's data request. No PHI.
argument-hint: "[CCN, or hospital name and state]"
---

Run an outmigration assessment for this hospital: $ARGUMENTS

Follow CLAUDE.md in this repository from step 0 to step 5:

1. Read the privacy rules in CLAUDE.md first. Before asking for any hospital information, tell the user plainly that this assessment never uses protected health information, and that any hospital data must be aggregate counts with counts from 1 to 10 shown as <11.
2. Identify the hospital and confirm its CCN and whether it is a critical access hospital.
3. Draft the service area with `pipeline/service_area.py` and confirm the ZIP list, the hospital name and its outpatient services with the user.
4. Gather the public data, run the value model and build the briefing. Report any failed check as it is.
5. Summarize the briefing, then write `output/data-request.md` from `docs/data-request.md`, tailored to this hospital, with the five most valuable items first.

If no hospital was given, ask for its CCN, or its name and state.
