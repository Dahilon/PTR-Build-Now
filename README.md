# Overdose Cluster Intelligence System

Palantir AIP Build Challenge submission.

## The question
Does harm reduction resource deployment (needle exchanges, naloxone access
points, safe-use sites) correlate with overdose cluster **concentration** or
**dispersal** over time — for a city public health operations director
deciding where to deploy limited resources next?

## Architecture
- **Foundry** — ontology (Overdose Incident, Harm Reduction Site, Census
  Tract object types + links), one `scoreClusterRisk` AIP Logic function,
  one Workshop heat map screen. (Built directly in Foundry, not in this repo.)
- **Python (`/scripts`)** — the analytical depth layer, config-driven via
  `config.yaml`, shared helpers in `src/utils.py`:
  - `01_generate_tracts_and_sites.py` — notional but realistically
    calibrated SF census tract + harm reduction site reference data
  - `02_simulate_events.py` — a TRUE recursive Hawkes (self-exciting)
    process simulation of overdose events (queue-based cascading, so
    offspring can spawn their own offspring), with a mixed treatment
    effect near new sites (short-term uptick, longer-term decline)
  - `03_spatial_autocorrelation.py` — Global Moran's I + local
    Getis-Ord Gi* hotspot detection, establishing statistically
    significant clustering (p=0.001) before any causal claim
  - `04_event_study_diff_in_diff.py` — event-study diff-in-differences
    around each site's opening date, with matched control tracts
  - `05_hawkes_fit.py` — citywide temporal Hawkes MLE recovering the
    background/contagion branching ratio from event timestamps

## Key findings (on notional data)
- **Spatial clustering is real and significant**: Moran's I = 0.615,
  p = 0.001. Getis-Ord Gi* correctly recovers the seeded hot-corridor
  tracts as statistically significant hotspots.
- **Diff-in-diff**: the pooled estimate shows a statistically significant
  *increase* near new sites (+0.21 events/week, p=0.0015) — taken alone,
  this reads as "harm reduction causes concentration." But the per-site
  early/late breakdown shows this is a short-term uptick (weeks 0-8) that
  fades by weeks 9-26 at the busiest sites — a naive before/after average
  would overstate a sustained concentration effect that isn't actually
  sustained.
- **Hawkes branching ratio**: ~76% of events show temporal contagion
  signature (vs. background/geographic risk alone), suggesting real
  deployments should combine rapid-response alerting (bad-batch spread)
  with permanent infrastructure placement, not rely on placement alone.
  This citywide-pooled estimate likely runs somewhat high, since pooling
  48 tracts into one timeline lets parallel-but-unrelated tract-level
  events look like temporal contagion; a per-tract or multivariate
  (tract-as-dimension) refit would sharpen this.

## Data note
The analytical pipeline still runs on notional data calibrated to match
publicly documented SF overdose geography (Tenderloin/SoMa concentration).
The optional bulk fetcher downloads and profiles candidate real datasets;
mapping their source-specific schemas into the analytical tables remains a
separate step before final submission.

## Setup
Python 3.12 or newer is required by the pinned dependencies (notably
libpysal 4.15).

```
pip install -r requirements.txt
python3 scripts/00_fetch_real_data.py
python3 scripts/00b_profile_real_data.py
python3 scripts/01_generate_tracts_and_sites.py
python3 scripts/02_simulate_events.py
python3 scripts/03_spatial_autocorrelation.py
python3 scripts/04_event_study_diff_in_diff.py
python3 scripts/05_hawkes_fit.py
pytest tests/
```

The optional real-data fetch is a one-time bulk pull: it searches DataSF
and CDC Socrata catalogs, downloads each matching table in pages (up to
100,000 rows per dataset), and downloads daily KSFO weather matching the
2024-01-01 through 2025-12-31 event window. It tries weather.gov first and
uses NOAA GHCN daily summaries for the same station when that historical
window is no longer retained by weather.gov. It does not schedule updates,
perform incremental queries, or write last-run state. Bulk files in
`data/real/` are local inputs and are intentionally excluded from Git.
SAMHSA, CDPH dashboard scraping, and NFLIS remain out of scope because they
do not provide suitable unrestricted APIs for this workflow.

## Status / next steps
Full pipeline runs clean end-to-end, 10/10 tests passing. Not yet done:
frontend/visualization layer, real-source schema integration, Foundry build
itself.
