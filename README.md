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
  - `00_fetch_real_data.py` / `00b_profile_real_data.py` — one-time public
    bulk downloads and structural profiling
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
  - `06_load_real_data.py` — preserves real weekly/monthly aggregate counts,
    normalizes the geography contract, and joins daily weather
  - `07_real_time_series_analysis.py` — trend, seasonality, and period-aligned
    weather tests suited to the real aggregate resolution

## Simulated data findings
- **Simulated spatial clustering is strong and significant**: Moran's I = 0.615,
  p = 0.001. Getis-Ord Gi* correctly recovers the seeded hot-corridor
  tracts as statistically significant hotspots.
- **Diff-in-diff**: the pooled estimate shows a statistically significant
  *increase* near new sites (+0.21 events/week, p=0.0015) — taken alone,
  this reads as "harm reduction causes concentration." But the per-site
  early/late breakdown shows this is a short-term uptick (weeks 0-8) that
  fades by weeks 9-26 at the busiest sites — a naive before/after average
  would overstate a sustained concentration effect that isn't actually
  sustained.
- **Simulated Hawkes branching ratio**: ~76% of events show temporal contagion
  signature (vs. background/geographic risk alone), suggesting real
  deployments should combine rapid-response alerting (bad-batch spread)
  with permanent infrastructure placement, not rely on placement alone.
  This citywide-pooled estimate likely runs somewhat high, since pooling
  48 tracts into one timeline lets parallel-but-unrelated tract-level
  events look like temporal contagion; a per-tract or multivariate
  (tract-as-dimension) refit would sharpen this.

## Real-data findings

The bulk-download triage found four genuinely relevant DataSF tables, all
published as citywide aggregates rather than geolocated incidents:

- For the shared 2024-2025 window, the EMS table contains 106 weekly rows
  representing 6,468 overdose-related 911 calls. Four partial calendar-
  boundary periods were excluded from weekly inference, leaving 102 complete
  weeks.
- The preliminary death table contains 24 monthly rows representing 1,261
  overdose deaths over the same window.
- **Trend:** complete-week EMS calls decreased by an estimated 0.134 calls
  per week (95% CI -0.233 to -0.034, p=0.0085). Monthly deaths decreased by
  0.730 deaths per month (95% CI -1.230 to -0.230, p=0.0042).
- **Seasonality:** month-of-year differences were detected for weekly EMS
  calls (p<0.0001) and monthly deaths (p=0.0002). The death comparison is
  exploratory because it has only two observations per calendar month.
- **Weather:** weekly EMS calls had a weak negative temperature correlation
  (r=-0.244, p=0.0136) and no precipitation correlation (r=-0.011,
  p=0.9137). The joint weekly weather model was weak overall (R²=0.073,
  p=0.0830), so it does not support a strong weather claim.
- Monthly deaths had a stronger negative temperature correlation (r=-0.612,
  p=0.0015); precipitation was not significant (r=+0.382, p=0.0653). With
  only 24 months and clear seasonality, this remains associational and may
  reflect seasonal or other omitted factors rather than a causal effect.
- The substance-use-services table includes naloxone and opioid-treatment
  metrics but no site names, locations, or opening dates.
- No relevant table contains a tract, neighborhood, address, or coordinate.
  Normalized real rows therefore use `neighborhood="Citywide"` and cannot
  support a real spatial-clustering estimate yet.
- All 130 normalized EMS/death aggregate rows join successfully to the KSFO
  daily weather record.
- Real neighborhood spatial statistics are currently not estimable: the
  source provides only one `Citywide` geography and no centroid.
- A continuous-time real Hawkes fit is intentionally not reported: weekly
  EMS count weights are not exact event timestamps. The pipeline records
  this limitation instead of fabricating within-week incidents.

These are published aggregate counts, not individual event records. Real and
simulated results must not be interpreted as equivalent evidence.
The aggregate resolution of the public overdose exports is itself an
operational finding: neighborhood cluster detection, exact-time contagion
modeling, and site-linked response analysis require internal city incident
and site data access—for example through a governed Foundry ontology—not
additional modeling of the public aggregates.

## Data note
The spatial, event-study, and Hawkes validation methods run on notional data
calibrated to match publicly documented SF overdose geography
(Tenderloin/SoMa concentration). The real-data branch downloads, profiles,
and normalizes the usable citywide aggregates, then applies aggregate-suited
time-series tests in script 07. Exact incident geography/timestamps and
verified site opening dates remain unavailable from these public exports.

## Setup
Python 3.12 or newer is required by the pinned dependencies (notably
libpysal 4.15).

```
pip install -r requirements.txt
python3 scripts/00_fetch_real_data.py
python3 scripts/00b_profile_real_data.py
python3 scripts/06_load_real_data.py
python3 scripts/07_real_time_series_analysis.py
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

Analysis mode is config-driven:

- `spatial_analysis.geography_level: neighborhood` uses normalized real
  geography; `tract` runs the simulated tract analysis.
- `hawkes_fit.data_source: real` validates the normalized real timestamps;
  `simulated` runs the fitted temporal Hawkes model.
- The event-study DiD remains simulated-only until real site opening dates
  and treatment geography are manually sourced.

## Status / next steps
Full pipeline runs clean end-to-end, 14/14 tests passing. Not yet done:
frontend/visualization layer, exact incident/site source acquisition,
Foundry build itself.
