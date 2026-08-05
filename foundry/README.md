# Foundry implementation contract

## Live resources

- Workshop: **SF Overdose Response Operations**
  (`ri.workshop.main.module.6dd1494d-d90b-41eb-b947-b365f880ab23`)
- Dataset: `real_events_normalized`
  (`ri.foundry.main.dataset.199d1086-f3a6-43ad-8f59-d074e648a5c9`)
- Dataset: `real_time_series_weekly`
  (`ri.foundry.main.dataset.24022566-607d-4301-a69b-9b9ec8328fea`)
- Dataset: `real_time_series_monthly`
  (`ri.foundry.main.dataset.a495726d-f0c3-4230-aed7-d3ec0dfdc896`)

## Existing ontology

- **Overdose Response Zone** — `Zone ID` primary key, `Zone Name` title.
- **Overdose Signal** — `Signal ID` primary key, `Signal Label` title,
  `Zone ID`, `Signal Date`, `Event Count`, `Signal Type`, `Risk Score`.
- **Overdose Response Deployment** — `Deployment ID` primary key,
  `Deployment Summary` title, `Zone ID`, `Resource Type`, `Notes`, `Status`.
- Links: Signal → Zone (`Signal Zone`); Deployment → Zone (`Target Zone`).
- Standard create, edit, and delete actions exist for each object type.

The normalized public aggregate is already mapped to Overdose Signal. Because
the source has no sub-city geography, its `Zone ID` is honestly `Citywide`.

## Submission-ready additions

Upload and map these generated artifacts:

1. `outputs/operational_signals.csv` → **Overdose Signal**
   - Add properties: Observed Count, Expected Count, Excess Count, Severity,
     Confidence Score, Confidence Label, Evidence Class, Decision Scope,
     Recommended Action, Source File, Data Limitations.
2. `outputs/response_scenarios.csv` → new **Response Scenario** object type.
   - Primary key: Scenario ID; title: Scenario Title.
   - Link to Overdose Signal with Target Signal ID and to Zone with Target Zone
     ID.
3. `data/notional_response_resources.csv` → new **Response Resource** object
   type.
   - Display `evidence_class=notional_demo` prominently until replaced by an
     internal inventory source.

Recommended action lifecycle:

`Propose Scenario → Route for Approval → Approve / Reject → Mark Active →
Complete Deployment → Record Outcome`

Each transition should require an operator comment, actor, and timestamp.
Approval and activation must be separate actions. AIP must not call either
action automatically.

## Workshop pages

1. **Command Center** — latest actionable signals, severity, confidence, source
   freshness, and unresolved data-quality warnings.
2. **Investigate Signal** — observed vs expected history, evidence provenance,
   limitations, related zone, and the grounded AIP brief.
3. **Compare & Approve** — three constrained scenarios, budget/inventory use,
   explicit notional labels, operator rationale, and approval actions.
4. **Outcomes** — deployment status, recorded outcomes, and planned-vs-actual
   comparison. This page may be empty until real operations data exists; that
   is preferable to fabricated outcomes.

## Production boundary

Public data supports citywide readiness only. Neighborhood prioritization,
incident-level contagion, site-effect measurement, actual resource capacity,
and outcome evaluation must come from governed internal data. Preserve
`evidence_class` and `decision_scope` on every object so the UI cannot blur
these sources.
