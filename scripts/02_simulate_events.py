"""
Simulates overdose events as a TRUE recursive Hawkes (self-exciting) point
process: background events spawn offspring, and CRITICALLY those offspring
can spawn their own offspring too, cascading indefinitely (subject to the
stability condition branching_ratio < 1). Uses a queue-based branching
algorithm (Ogata's thinning / cluster method), the standard way to
simulate Hawkes processes correctly.

BUG THIS FIXES: an earlier version of this script generated exactly one
generation of offspring from background events only -- children never had
children of their own. That silently changes the data-generating process
from a real recursive Hawkes process (total expected event rate =
background_rate / (1 - branching_ratio)) into a truncated two-generation
cluster process (total rate = background_rate * (1 + branching_ratio)).
The Hawkes MLE fit assumes the former, so it was fitting the wrong model
class to the data -- which is the actual reason branching ratio recovery
kept collapsing toward 0 no matter how the fit itself was tuned.
"""
import sys
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import load_config, get_logger, data_path, dist_km

log = get_logger(__name__)
cfg = load_config()
sim = cfg["simulation"]

rng = np.random.default_rng(sim["seed_events"])

tracts = pd.read_csv(data_path(cfg, "tracts.csv"), dtype={"tract_id": str})
sites = pd.read_csv(data_path(cfg, "harm_reduction_sites.csv"))
sites["open_date"] = pd.to_datetime(sites["open_date"])

START = datetime.fromisoformat(sim["start_date"])
END = datetime.fromisoformat(sim["end_date"])
N_DAYS = (END - START).days

BRANCHING_RATIO = sim["branching_ratio"]     # expected offspring per parent (ANY generation)
BETA = sim["temporal_decay_rate"]            # rate parameter for offspring delay
SPATIAL_BANDWIDTH_KM = sim["spatial_bandwidth_km"]

tract_lookup = tracts.set_index("tract_id")[["lat", "lon"]]

# nearest harm-reduction site + distance for every tract (for treatment effect)
tract_site_dist, tract_nearest_site = [], []
for _, t in tracts.iterrows():
    dists = dist_km(t.lat, t.lon, sites.lat.values, sites.lon.values)
    idx = np.argmin(dists)
    tract_site_dist.append(dists[idx])
    tract_nearest_site.append(sites.site_id.iloc[idx])
tracts["nearest_site_id"] = tract_nearest_site
tracts["nearest_site_km"] = tract_site_dist
tracts = tracts.merge(sites[["site_id", "open_date"]], left_on="nearest_site_id", right_on="site_id", suffixes=("", "_site"))


def background_rate(row):
    pop_factor = row.population / 5000
    dep_factor = sim["deprivation_base"] + sim["deprivation_slope"] * row.deprivation_index
    corridor_factor = sim["hot_corridor_multiplier"] if row.is_hot_corridor else 1.0
    return sim["background_scale"] * pop_factor * dep_factor * corridor_factor


def treatment_multiplier(row, day_offset_from_open):
    if pd.isna(day_offset_from_open) or day_offset_from_open < 0:
        return 1.0
    proximity_weight = np.exp(-row.nearest_site_km / sim["treatment_proximity_decay_km"])
    if day_offset_from_open <= sim["treatment_short_term_days"]:
        return 1.0 + sim["treatment_short_term_boost"] * proximity_weight
    else:
        return 1.0 - sim["treatment_long_term_decline"] * proximity_weight


# --- Generate background events (generation 0) ---
background_events = []
for _, row in tracts.iterrows():
    bg = background_rate(row)
    for d in range(N_DAYS):
        day = START + timedelta(days=d)
        offset = (day - row.open_date).days
        mult = treatment_multiplier(row, offset)
        n = rng.poisson(bg * mult)
        for _ in range(n):
            background_events.append({
                "tract_id": row.tract_id,
                "date": day,
                "lat": row.lat + rng.normal(0, 0.0015),
                "lon": row.lon + rng.normal(0, 0.0015),
                "generation": 0,
            })

log.info(f"Background (generation 0) events: {len(background_events)}")

# --- Recursive branching: process a queue, each event can spawn its own
# children (any generation), children go back on the queue ---
all_events = list(background_events)
queue = deque(background_events)
max_generation_seen = 0

while queue:
    parent = queue.popleft()
    n_children = rng.poisson(BRANCHING_RATIO)
    for _ in range(n_children):
        delay = rng.exponential(1 / BETA)
        child_date = parent["date"] + timedelta(days=delay)
        if child_date > END:
            continue
        child_lat = parent["lat"] + rng.normal(0, SPATIAL_BANDWIDTH_KM / 111)
        child_lon = parent["lon"] + rng.normal(0, SPATIAL_BANDWIDTH_KM / 111)
        d = dist_km(child_lat, child_lon, tract_lookup.lat.values, tract_lookup.lon.values)
        nearest_tract = tract_lookup.index[np.argmin(d)]
        child = {
            "tract_id": nearest_tract,
            "date": child_date,
            "lat": child_lat,
            "lon": child_lon,
            "generation": parent["generation"] + 1,
        }
        all_events.append(child)
        queue.append(child)  # THE FIX: child goes back on the queue and can itself spawn offspring
        max_generation_seen = max(max_generation_seen, child["generation"])

all_events_df = pd.DataFrame(all_events).sort_values("date").reset_index(drop=True)
all_events_df["event_id"] = [f"OD{str(i).zfill(6)}" for i in range(len(all_events_df))]
all_events_df["date"] = pd.to_datetime(all_events_df["date"]).dt.date

all_events_df.to_csv(data_path(cfg, "overdose_events.csv"), index=False)
tracts.to_csv(data_path(cfg, "tracts_enriched.csv"), index=False)

n_background = (all_events_df.generation == 0).sum()
n_offspring = (all_events_df.generation > 0).sum()
log.info(f"Total events: {len(all_events_df)} ({n_background} background + {n_offspring} offspring)")
log.info(f"Max generation depth observed: {max_generation_seen} (>1 confirms recursive cascading is working)")
log.info(f"Empirical branching ratio (offspring/background): {n_offspring/n_background:.3f} (target: {BRANCHING_RATIO})")
theoretical_total = len(background_events) / (1 - BRANCHING_RATIO)
log.info(f"Theoretical expected total (background/(1-n)): {theoretical_total:.0f} vs observed {len(all_events_df)} "
         f"(should be reasonably close now that recursion is fixed, modulo end-of-window truncation)")
