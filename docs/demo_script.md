# Four-minute submission demo

## 0:00–0:25 — User and operational problem

“A San Francisco public-health operator has limited outreach, naloxone, care
navigation, and analyst capacity. The hard problem is not drawing another
chart—it is deciding what evidence is strong enough to act on, what can be
done within constraints, and who is accountable for approval.”

## 0:25–0:55 — Start with honest real evidence

Open **Real SF Data**. Show that EMS calls and deaths declined significantly,
then point out the weak negative temperature association and say it is not a
causal result. Emphasize that the public export contains citywide weekly or
monthly counts, not incident locations or timestamps.

## 0:55–1:35 — Turn analysis into a decision contract

Open **Response Operations**. Select the scenario-target signal.

“Severity and confidence are different. This historical count was unusual,
but confidence remains low because the public evidence is citywide and
aggregate. The tool allows a citywide readiness response and requests internal
incident validation; it does not invent a neighborhood hotspot.”

Show the deterministic risk score, low-confidence badge, source scope, and
latest routine signals.

## 1:35–2:15 — Compare feasible options

Compare Rapid stabilization, Balanced response, and Validate before
deployment. Explain that exhaustive search enforces a $30,000 budget and
resource inventory. Point out that costs, inventory, and reach are visibly
labeled notional demo inputs and would be replaced by governed internal data.

Select one option and click **Route for approval**.

“This does not deploy resources. It creates a review step. The operator—not
the model—owns approval.”

## 2:15–3:20 — Show Foundry under the hood

In Foundry, show:

1. Overdose Signal, Response Zone, Response Scenario/Deployment, and Response
   Resource object types and their links.
2. The public evidence mapping with `Citywide`, `evidence_class`, severity,
   confidence, source, and limitations.
3. The action lifecycle: propose → route → approve/reject → activate → complete
   → record outcome.
4. The AIP Logic prompt. Explain that AIP summarizes supplied evidence,
   compares only feasible options, repeats mandatory caveats, and asks for
   missing information. It cannot change numeric scores or approve a plan.

## 3:20–3:45 — Engineering proof

Briefly show `scripts/run_pipeline.py`, `src/decision_support.py`, the artifact
manifest, and the passing test suite. Mention that the simulator is recursive,
tract IDs preserve leading zeros, spatial permutations and optimizer restarts
are seeded, and unsupported real spatial/Hawkes paths fail honestly.

## 3:45–4:00 — Impact and next data milestone

“The prototype already turns weak public evidence into a safe readiness
workflow. With internal incident, site, inventory, and outcome data in
Foundry, the same ontology becomes neighborhood-level operational software
without changing the governance model. The public data’s missing resolution
is not hidden—it becomes a visible data-quality task.”
