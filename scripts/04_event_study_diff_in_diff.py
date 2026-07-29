"""
Event-study diff-in-differences around each harm-reduction site's opening.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import load_config, get_logger, load_tracts, load_events, load_sites, dist_km, outputs_path

log = get_logger(__name__)
cfg = load_config()
es_cfg = cfg["event_study"]

tracts = load_tracts(cfg).set_index("tract_id")
events = load_events(cfg)
sites = load_sites(cfg)

site_dist_matrix = pd.DataFrame(
    {s.site_id: dist_km(tracts.lat, tracts.lon, s.lat, s.lon) for _, s in sites.iterrows()},
    index=tracts.index
)
min_dist_to_any_site = site_dist_matrix.min(axis=1)

all_panels, event_study_summary = [], []

for _, site in sites.iterrows():
    site_id = site.site_id
    open_date = site.open_date
    treated_tracts = site_dist_matrix.index[site_dist_matrix[site_id] <= es_cfg["treatment_radius_km"]].tolist()
    candidate_controls = tracts.index[(min_dist_to_any_site > es_cfg["control_min_distance_km"]) &
                                       (~tracts.index.isin(treated_tracts))]
    if len(candidate_controls) == 0 or len(treated_tracts) == 0:
        continue

    control_tracts = set()
    for tt in treated_tracts:
        dep_t = tracts.loc[tt, "deprivation_index"]
        diffs = (tracts.loc[candidate_controls, "deprivation_index"] - dep_t).abs()
        control_tracts.add(diffs.idxmin())
    control_tracts = list(control_tracts)

    panel_tracts = treated_tracts + control_tracts
    ev = events[events.tract_id.isin(panel_tracts)].copy()
    ev["week_offset"] = ((ev.date - open_date).dt.days // 7)
    ev = ev[(ev.week_offset >= -es_cfg["window_weeks_pre"]) & (ev.week_offset <= es_cfg["window_weeks_post"])]
    ev["treated"] = ev.tract_id.isin(treated_tracts).astype(int)

    weekly = ev.groupby(["tract_id", "week_offset", "treated"]).size().rename("n_events").reset_index()
    full_idx = pd.MultiIndex.from_product(
        [panel_tracts, range(-es_cfg["window_weeks_pre"], es_cfg["window_weeks_post"] + 1)],
        names=["tract_id", "week_offset"]
    )
    weekly = weekly.set_index(["tract_id", "week_offset"]).reindex(full_idx, fill_value=0).reset_index()
    weekly["treated"] = weekly.tract_id.isin(treated_tracts).astype(int)
    weekly["post"] = (weekly.week_offset >= 0).astype(int)
    weekly["site_id"] = site_id
    all_panels.append(weekly)

    means = weekly.groupby(["week_offset", "treated"])["n_events"].mean().unstack()
    cutoff = es_cfg["early_post_cutoff_weeks"]
    pre = means.loc[means.index < 0]
    early_post = means.loc[(means.index >= 0) & (means.index <= cutoff)]
    late_post = means.loc[means.index > cutoff]

    def safe_mean(df, col):
        return df.get(col, pd.Series(dtype=float)).mean()

    site_label = site["name"]
    log.info(f"=== {site_label} ({site_id}), opened {open_date.date()} ===")
    log.info(f"Treated tracts (<={es_cfg['treatment_radius_km']}km): {treated_tracts}")
    log.info(f"Matched control tracts: {control_tracts}")
    log.info(f"Pre avg weekly events -- treated: {safe_mean(pre,1):.2f}, control: {safe_mean(pre,0):.2f}")
    log.info(f"Early post avg weekly events -- treated: {safe_mean(early_post,1):.2f}, control: {safe_mean(early_post,0):.2f}")
    log.info(f"Late post avg weekly events -- treated: {safe_mean(late_post,1):.2f}, control: {safe_mean(late_post,0):.2f}")

    event_study_summary.append({
        "site_id": site_id, "site_name": site_label,
        "pre_treated": safe_mean(pre, 1), "pre_control": safe_mean(pre, 0),
        "early_post_treated": safe_mean(early_post, 1), "early_post_control": safe_mean(early_post, 0),
        "late_post_treated": safe_mean(late_post, 1), "late_post_control": safe_mean(late_post, 0),
    })

panel = pd.concat(all_panels, ignore_index=True)
panel.to_csv(outputs_path(cfg, "event_study_panel.csv"), index=False)
pd.DataFrame(event_study_summary).to_csv(outputs_path(cfg, "event_study_summary.csv"), index=False)

panel["tract_id"] = panel.tract_id.astype(str)
panel["week_offset"] = panel.week_offset.astype(str)
# Site fixed effects account for level differences across the stacked site
# panels. Controls can still be reused across panels, so this pooled model is
# suggestive rather than a fully rigorous causal estimate; a cleaner analysis
# would use non-overlapping control pools or a stacked-event-study estimator
# designed to handle that overlap.
model = smf.ols(
    "n_events ~ treated:post + C(tract_id) + C(week_offset) + C(site_id)",
    data=panel,
).fit(
    cov_type="cluster", cov_kwds={"groups": panel["tract_id"]}
)

log.info("=== Pooled Fixed Effects DiD (tract, week-offset, and site FE; clustered SE by tract) ===")
coef = model.params.get("treated:post", np.nan)
se = model.bse.get("treated:post", np.nan)
pval = model.pvalues.get("treated:post", np.nan)
log.info(f"DiD estimate: {coef:+.4f} events/week, SE={se:.4f}, p={pval:.4f}")
direction = "INCREASE (concentration)" if coef > 0 else "DECREASE (dispersal)"
sig = "statistically significant" if pval < 0.05 else "NOT statistically significant"
log.info(f"-> Pooled effect: {direction}, {sig}")
log.info("NOTE: pooled average can mask a mixed short-term-up/long-term-down pattern -- see per-site breakdown above.")
log.warning(
    "Overlapping control-tract reuse across site panels makes this pooled estimate suggestive, "
    "not a fully rigorous causal estimate. Prefer non-overlapping control pools per site or a "
    "stacked-event-study design that properly handles overlap."
)
