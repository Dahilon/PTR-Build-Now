"""
Generates notional (but realistically calibrated) reference data:
  - SF census tracts with centroids, population, and a deprivation index
  - Harm reduction site locations + real-world-plausible opening dates

Driven by config.yaml instead of hardcoded constants, using shared
src.utils path helpers instead of hardcoded absolute paths.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import load_config, get_logger, data_path

log = get_logger(__name__)
cfg = load_config()
sim = cfg["simulation"]

rng = np.random.default_rng(sim["seed_tracts_sites"])

N_TRACTS = sim["n_tracts"]
per_site = sim["tracts_per_site_cluster"]
lat_center, lon_center = 37.7749, -122.4194

sites_cfg = cfg["harm_reduction_sites"]
site_coords = [(s["lat"], s["lon"]) for s in sites_cfg]

lats_list, lons_list = [], []
for (slat, slon) in site_coords:
    lats_list.append(rng.normal(slat, 0.006, per_site))
    lons_list.append(rng.normal(slon, 0.006, per_site))

n_seeded = per_site * len(site_coords)
n_rest = N_TRACTS - n_seeded
rest_lats = rng.normal(lat_center, 0.035, n_rest)
rest_lons = rng.normal(lon_center, 0.035, n_rest)

lats = np.concatenate(lats_list + [rest_lats])
lons = np.concatenate(lons_list + [rest_lons])

n_hot = per_site * 2  # Tenderloin + SoMa clusters (first two sites in config)
tract_ids = [f"06075{str(i).zfill(6)}" for i in range(1, N_TRACTS + 1)]

population = rng.integers(2500, 8000, N_TRACTS)
deprivation = np.concatenate([
    rng.uniform(0.65, 0.95, n_hot),
    rng.uniform(0.35, 0.65, per_site * 2),
    rng.uniform(0.15, 0.55, n_rest),
])

tracts = pd.DataFrame({
    "tract_id": tract_ids,
    "lat": lats,
    "lon": lons,
    "population": population,
    "deprivation_index": deprivation.round(3),
    "is_hot_corridor": [True] * n_hot + [False] * (N_TRACTS - n_hot),
})
tracts.to_csv(data_path(cfg, "tracts.csv"), index=False)

sites = pd.DataFrame([
    {"site_id": s["site_id"], "name": s["name"], "lat": s["lat"],
     "lon": s["lon"], "open_date": s["open_date"]}
    for s in sites_cfg
])
sites.to_csv(data_path(cfg, "harm_reduction_sites.csv"), index=False)

log.info(f"tracts: {len(tracts)} rows -> data/tracts.csv")
log.info(f"sites:  {len(sites)} rows -> data/harm_reduction_sites.csv")
