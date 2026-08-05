"""
Spatial autocorrelation at the configured geography level.

``tract`` reproduces the simulated tract-rate Moran's I and local Getis-Ord
analysis. ``neighborhood`` loads normalized real aggregate observations.
The real path reports a structured not-estimable result when the source lacks
multiple located neighborhoods, rather than inventing spatial precision.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from esda.getisord import G_Local
from esda.moran import Moran
from libpysal.weights import KNN
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import (
    REPO_ROOT,
    get_logger,
    load_config,
    load_events,
    load_tracts,
    outputs_path,
)

log = get_logger(__name__)


def calculate_spatial_statistics(
    frame: pd.DataFrame,
    *,
    value_column: str,
    id_column: str,
    lat_column: str,
    lon_column: str,
    config: dict,
) -> tuple[pd.DataFrame, Moran]:
    """Calculate Moran's I and local Gi statistics for located geographies."""
    np.random.seed(config["permutation_seed"])
    gdf = gpd.GeoDataFrame(
        frame.set_index(id_column),
        geometry=[
            Point(xy)
            for xy in zip(frame[lon_column], frame[lat_column], strict=True)
        ],
        crs="EPSG:4326",
    )
    k = min(config["knn_k"], len(gdf) - 1)
    w = KNN.from_dataframe(gdf, k=k)
    w.transform = "r"

    moran = Moran(
        gdf[value_column].values,
        w,
        permutations=config["moran_permutations"],
    )
    gi = G_Local(
        gdf[value_column].values,
        w,
        transform="r",
        permutations=config["gi_permutations"],
        alternative=config["gi_alternative"],
        seed=config["permutation_seed"],
        n_jobs=1,
    )
    gdf["gi_zscore"] = gi.Zs
    gdf["gi_pvalue"] = gi.p_sim
    gdf["hotspot_type"] = np.select(
        [
            (gdf.gi_zscore > 0) & (gdf.gi_pvalue < 0.05),
            (gdf.gi_zscore < 0) & (gdf.gi_pvalue < 0.05),
        ],
        [
            "HOT SPOT (p<0.05, perm test)",
            "COLD SPOT (p<0.05, perm test)",
        ],
        default="not significant",
    )
    return gdf.drop(columns="geometry").reset_index(), moran


def run_tract_analysis(cfg: dict) -> None:
    spatial_cfg = cfg["spatial_analysis"]
    tracts = load_tracts(cfg)
    events = load_events(cfg)

    counts = events.groupby("tract_id").size().reindex(
        tracts.tract_id,
        fill_value=0,
    )
    tracts["event_count"] = counts.to_numpy()
    tracts["rate_per_1k"] = tracts.event_count / (
        tracts.population / 1000
    )

    results, moran = calculate_spatial_statistics(
        tracts,
        value_column="rate_per_1k",
        id_column="tract_id",
        lat_column="lat",
        lon_column="lon",
        config=spatial_cfg,
    )
    log.info("=== Global Moran's I (simulated tract rate per 1k) ===")
    log.info(
        "Moran's I: %.4f Expected(H0): %.4f p=%.4f z=%.3f",
        moran.I,
        moran.EI,
        moran.p_sim,
        moran.z_sim,
    )
    verdict = (
        "SIGNIFICANT positive spatial clustering"
        if moran.p_sim < 0.05 and moran.I > moran.EI
        else "no significant clustering detected"
        if moran.p_sim >= 0.05
        else "significant dispersion (unusual)"
    )
    log.info("-> %s", verdict)

    top = results.sort_values("gi_zscore", ascending=False).head(10)
    log.info("=== Local Getis-Ord Gi* top simulated tract hot spots ===")
    for row in top.itertuples():
        log.info(
            "  %s: count=%d rate=%.2f hot_corridor=%s z=%.3f p=%.4f -> %s",
            row.tract_id,
            row.event_count,
            row.rate_per_1k,
            row.is_hot_corridor,
            row.gi_zscore,
            row.gi_pvalue,
            row.hotspot_type,
        )
    results.set_index("tract_id").to_csv(
        outputs_path(cfg, "tract_hotspot_results.csv")
    )


def neighborhood_not_estimable(
    cfg: dict,
    grouped: pd.DataFrame,
    reason: str,
) -> None:
    output = grouped.copy()
    output["status"] = "not_estimable"
    output["reason"] = reason
    output.to_csv(
        outputs_path(cfg, "neighborhood_spatial_results.csv"),
        index=False,
    )
    log.warning("Neighborhood spatial analysis not estimable: %s", reason)


def run_neighborhood_analysis(cfg: dict) -> None:
    spatial_cfg = cfg["spatial_analysis"]
    real_path = REPO_ROOT / cfg["real_data"]["normalized_events_file"]
    events = pd.read_csv(real_path)
    required = {"neighborhood", "event_count", "event_type", "lat", "lon"}
    missing = sorted(required - set(events.columns))
    if missing:
        raise ValueError(
            f"{real_path.name} is missing required columns: {missing}"
        )

    events = events[
        events["event_type"] == spatial_cfg["real_event_type"]
    ].copy()
    events["event_count"] = pd.to_numeric(
        events["event_count"],
        errors="coerce",
    )
    events["lat"] = pd.to_numeric(events["lat"], errors="coerce")
    events["lon"] = pd.to_numeric(events["lon"], errors="coerce")
    grouped = events.groupby("neighborhood", as_index=False).agg(
        event_count=("event_count", "sum"),
        aggregate_rows=("event_count", "size"),
        lat=("lat", "mean"),
        lon=("lon", "mean"),
    )

    if len(grouped) < 3:
        neighborhood_not_estimable(
            cfg,
            grouped,
            (
                f"only {len(grouped)} distinct neighborhood is available; "
                "at least 3 are required"
            ),
        )
        return
    if grouped[["lat", "lon"]].isna().any(axis=None):
        neighborhood_not_estimable(
            cfg,
            grouped,
            "one or more neighborhoods lack a usable centroid",
        )
        return

    results, moran = calculate_spatial_statistics(
        grouped,
        value_column="event_count",
        id_column="neighborhood",
        lat_column="lat",
        lon_column="lon",
        config=spatial_cfg,
    )
    results["status"] = "estimated"
    results["reason"] = ""
    results.to_csv(
        outputs_path(cfg, "neighborhood_spatial_results.csv"),
        index=False,
    )
    log.info("=== Global Moran's I (real neighborhood event counts) ===")
    log.info(
        "Moran's I: %.4f Expected(H0): %.4f p=%.4f z=%.3f",
        moran.I,
        moran.EI,
        moran.p_sim,
        moran.z_sim,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--geography-level",
        choices=["tract", "neighborhood"],
        help="Override spatial_analysis.geography_level for this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config()
    geography_level = (
        args.geography_level
        or cfg["spatial_analysis"]["geography_level"]
    )
    if geography_level == "tract":
        run_tract_analysis(cfg)
    elif geography_level == "neighborhood":
        run_neighborhood_analysis(cfg)
    else:
        raise ValueError(
            "spatial_analysis.geography_level must be tract or neighborhood, "
            f"got {geography_level!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
