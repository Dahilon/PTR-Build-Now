"""
Global Moran's I + local Getis-Ord Gi* -- establishes statistically
significant spatial clustering exists before any causal claim.
"""
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from libpysal.weights import KNN
from esda.moran import Moran
from esda.getisord import G_Local

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import load_config, get_logger, load_tracts, load_events, outputs_path

log = get_logger(__name__)
cfg = load_config()
sa_cfg = cfg["spatial_analysis"]

tracts = load_tracts(cfg)
events = load_events(cfg)

counts = events.groupby("tract_id").size().reindex(tracts.tract_id, fill_value=0)
tracts = tracts.set_index("tract_id")
tracts["event_count"] = counts
tracts["rate_per_1k"] = tracts.event_count / (tracts.population / 1000)

gdf = gpd.GeoDataFrame(
    tracts,
    geometry=[Point(xy) for xy in zip(tracts.lon, tracts.lat)],
    crs="EPSG:4326"
)

w = KNN.from_dataframe(gdf, k=sa_cfg["knn_k"])
w.transform = "r"

moran = Moran(gdf["rate_per_1k"].values, w, permutations=sa_cfg["moran_permutations"])
log.info("=== Global Moran's I (overdose rate per 1k residents) ===")
log.info(f"Moran's I: {moran.I:.4f}  Expected(H0): {moran.EI:.4f}  p={moran.p_sim:.4f}  z={moran.z_sim:.3f}")
verdict = "SIGNIFICANT positive spatial clustering" if (moran.p_sim < 0.05 and moran.I > moran.EI) else \
          "no significant clustering detected" if moran.p_sim >= 0.05 else "significant dispersion (unusual)"
log.info(f"-> {verdict}")

gi = G_Local(gdf["rate_per_1k"].values, w, transform="r",
             permutations=sa_cfg["gi_permutations"], alternative=sa_cfg["gi_alternative"])
gdf["gi_zscore"] = gi.Zs
gdf["gi_pvalue"] = gi.p_sim
gdf["hotspot_type"] = np.select(
    [(gdf.gi_zscore > 0) & (gdf.gi_pvalue < 0.05),
     (gdf.gi_zscore < 0) & (gdf.gi_pvalue < 0.05)],
    ["HOT SPOT (p<0.05, perm test)", "COLD SPOT (p<0.05, perm test)"],
    default="not significant"
)

log.info("=== Local Getis-Ord Gi* top hot spots ===")
top = gdf.sort_values("gi_zscore", ascending=False)[
    ["event_count", "rate_per_1k", "is_hot_corridor", "gi_zscore", "gi_pvalue", "hotspot_type"]
].head(10)
for tract_id, row in top.iterrows():
    log.info(f"  {tract_id}: count={row.event_count} rate={row.rate_per_1k:.2f} "
              f"hot_corridor={row.is_hot_corridor} z={row.gi_zscore:.3f} p={row.gi_pvalue:.4f} -> {row.hotspot_type}")

hotspot_tracts = gdf[gdf.hotspot_type.str.contains("HOT")].index.tolist()
n_matching = sum(gdf.loc[hotspot_tracts, "is_hot_corridor"]) if hotspot_tracts else 0
log.info(f"Significant hot spot tracts: {len(hotspot_tracts)}, of which {n_matching} match the seeded hot corridor")

gdf.drop(columns="geometry").to_csv(outputs_path(cfg, "tract_hotspot_results.csv"))
