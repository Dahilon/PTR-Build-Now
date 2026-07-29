# Real-data triage

Triage date: 2026-07-28

The 19 bulk-downloaded CSVs were inspected using the profiler plus direct
schema, sample-row, value, and keyword checks. The active directory retains
four relevant source tables and one intentionally downloaded weather
covariate. Fourteen false positives were moved to `data/real/discarded/`.

## RELEVANT

### `datasf_ed3a-sn39_overdose-related-911-responses-by-emergency-medical-services.csv`

- Subject: overdose-related EMS 911 responses.
- Rows: 191. Each row is a citywide weekly aggregate, not an incident.
- Timestamp: `week_start_date`, 2022-08-28 through 2026-03-29.
- Location: **No usable location field.** The table is citywide only.
- Measure: `total_overdose_related_911_calls` (12,995 calls summed across
  the downloaded weekly rows).
- Pipeline use: this is the best available real overdose-event proxy, but
  counts must remain weighted weekly observations; they must not be
  presented as individually timestamped or geolocated incidents.

### `datasf_jxrr-bmra_preliminary-unintentional-drug-overdose-deaths.csv`

- Subject: preliminary unintentional drug-overdose deaths.
- Rows: 78. Each row is a citywide month with a total death count.
- Timestamp: `month_start_date`, 2020-01-01 through 2026-06-01.
- Location: **No usable location field.** The table is citywide only.
- Measure: `total_deaths` (4,346 deaths summed across the downloaded
  monthly rows).
- Pipeline use: useful for citywide trend validation, but not for spatial
  clustering and not interchangeable with EMS responses.

### `datasf_k4g8-b3sf_unintentional-drug-overdose-death-rate-by-race-ethnicity.csv`

- Subject: annual overdose death counts and rates by race/ethnicity.
- Rows: 25. Each row is one race/ethnicity-by-year aggregate; the five
  categories include an `All races` rollup.
- Timestamp: `year`, 2020 through 2024.
- Location: **No usable location field.** The table is citywide only.
- Pipeline use: useful for equity/descriptive follow-up, not incident-level
  temporal or spatial modeling.

### `datasf_ubf6-e57x_san-francisco-department-of-public-health-substance-use-serv.csv`

- Subject: substance-use treatment, medications for opioid use disorder,
  and naloxone-distribution metrics.
- Rows: 76. Each row is one citywide service metric for a reporting period,
  not a service location or individual client.
- Timestamp: `reporting_period_start_date`, 2020-01-01 through 2026-01-01;
  `data_through_date` runs through 2026-05-31.
- Location: **No usable location field.** The table is citywide only.
- Site limitation: it contains no site name, address, coordinates, or
  opening date. Harm-reduction site opening dates must be sourced manually
  from city press releases, provider announcements, or news archives before
  a real event-study design is possible.

## FALSE_POSITIVE

The following files matched broad discovery keywords but are not about
overdose incidents, naloxone response, or harm-reduction sites. They are
retained under `data/real/discarded/` for reference.

| File | Why it is not active |
|---|---|
| `datasf_nuek-vuh3_fire-department-and-emergency-medical-services-dispatched-ca.csv` | General fire/EMS dispatches. It has excellent `address`, `neighborhoods_analysis_boundaries`, and `case_location` fields, but zero rows contain “overdose,” “naloxone,” or “Narcan”; generic medical incidents cannot validly be relabeled as overdoses. |
| `datasf_pnbj-y63g_fire-department-30-day-calls.csv` | Station-level 30-day fire/EMS workload totals with no overdose classification. |
| `datasf_6x9q-izga_ambulance-patient-offload-times.csv` | Hospital ambulance offload-delay performance metrics, not overdose responses. |
| `datasf_enwt-3u8m_2024-high-injury-network.csv` | Traffic high-injury street network geometry, unrelated to overdose or harm reduction. |
| `cdc_2ew6-ywp6_nwss-public-sars-cov-2-wastewater-metric-data.csv` | SARS-CoV-2 wastewater surveillance. |
| `cdc_3nnm-4jni_united-states-covid-19-community-levels-by-county.csv` | COVID-19 community levels, not wastewater drug surveillance. |
| `cdc_45cq-cw4i_cdc-wastewater-data-for-rsv.csv` | RSV wastewater surveillance. |
| `cdc_akvg-8vrb_cdc-wastewater-data-for-measles.csv` | Measles wastewater surveillance. |
| `cdc_atcp-73re_cdc-wastewater-viral-activity-level-for-sars-cov-2-influenza.csv` | SARS-CoV-2/influenza wastewater activity. |
| `cdc_g653-rqe2_nwss-public-sars-cov-2-concentration-in-wastewater-data.csv` | SARS-CoV-2 wastewater concentration. |
| `cdc_j9g8-acpt_cdc-wastewater-data-for-sars-cov-2.csv` | SARS-CoV-2 wastewater laboratory records. |
| `cdc_mtpu-urpp_cdc-wastewater-data-for-avian-influenza-a-h5.csv` | Avian influenza wastewater surveillance. |
| `cdc_xpxn-rzgz_cdc-wastewater-data-for-mpox.csv` | Mpox wastewater surveillance. |
| `cdc_ymmh-divb_cdc-wastewater-data-for-influenza-a.csv` | Influenza A wastewater surveillance. |

None of the downloaded CDC/NWSS matches measures drugs, overdose, naloxone,
or harm-reduction activity.

## UNCLEAR

### `nws_ksfo_daily_weather_2024_2025.csv`

- Subject: daily KSFO temperature and precipitation from NOAA GHCN after
  weather.gov returned no retained observations for the historical window.
- Rows: 731, one row per day from 2024-01-01 through 2025-12-31.
- Timestamp: `date`; complete requested date coverage.
- Location: fixed station location (KSFO), not an event geography.
- Classification rationale: this is an intentionally requested contextual
  covariate rather than an overdose/EMS/site dataset. It remains active for
  the weather join and possible confounding/sensitivity analysis.

## Geography decision

Pending Stage 2.
