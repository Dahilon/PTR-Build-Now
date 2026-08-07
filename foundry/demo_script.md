# Foundry demo script

## 90-second recording path

1. Open **SF Overdose Response Operations** and point out that the evidence
   monitor is explicitly labeled as public aggregate data.
2. Explain that the source supports citywide readiness, not neighborhood
   targeting; the system preserves that boundary as `decision_scope` and
   `data_limitations`.
3. Open **Operational Signal** in Ontology Manager and show the 126 indexed
   objects plus severity, confidence, observed/expected counts, provenance,
   recommended action, and limitations.
4. Open **Response Scenario** and show the three constrained options, including
   cost, estimated reach, strategy utility, evidence class, and approval state.
5. Show the links from each scenario to its originating signal and response
   zone.
6. Show **Review Response Scenario** and explain that approval is an explicit
   human writeback; analysis does not autonomously deploy resources.
7. Return to Workshop and show the deployment form as the final auditable
   operational step.

## Spoken close

“The main finding is both analytical and operational: public exports are good
enough for citywide trend monitoring, but not honest neighborhood targeting.
Foundry lets us preserve that uncertainty in the ontology, connect evidence to
budget-constrained response choices, and keep consequential decisions behind
a human approval action. With governed internal incident and inventory data,
the same workflow can operate at neighborhood level without changing the
decision architecture.”

## Claims to avoid

- Do not say the public data identifies neighborhood hotspots.
- Do not describe notional resource capacity as real city inventory.
- Do not claim AIP automatically approves or activates deployments.
- Do not present simulated Hawkes, spatial, or DiD estimates as real SF causal
  findings.
