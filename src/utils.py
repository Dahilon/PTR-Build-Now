"""
Shared utilities for the Overdose Cluster Intelligence pipeline.
Previously, dist_km() and file paths were redefined/hardcoded in every
script; this module is the single source of truth so scripts can't drift
out of sync with each other.
"""
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(config_path: str = None) -> dict:
    """Load the pipeline config.yaml. Every tunable parameter lives there
    instead of being hardcoded inline in individual scripts."""
    path = Path(config_path) if config_path else REPO_ROOT / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def get_logger(name: str) -> logging.Logger:
    """Standard logger config -- replaces ad-hoc print() calls so output
    is leveled (INFO/WARNING/ERROR) and consistently formatted, and can be
    redirected/filtered when this graduates from exploratory scripts to
    something closer to production."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def dist_km(lat1, lon1, lat2, lon2):
    """Approximate haversine distance in km for small local lat/lon deltas
    (fine at city scale; would need a proper haversine or projected CRS
    for anything spanning enough latitude for the flat-earth approximation
    to break down).

    Broadcasts over numpy arrays -- pass scalars, 1-D arrays, or a mix
    (e.g. one point vs. an array of points) and it returns the matching
    shape.

    Uses the MEAN of the two latitudes for the longitude scaling factor.
    Using either endpoint's latitude alone (as an earlier version did)
    makes the result asymmetric -- dist(A,B) != dist(B,A) -- which a unit
    test caught directly.
    """
    lat1, lon1, lat2, lon2 = map(np.asarray, (lat1, lon1, lat2, lon2))
    mean_lat = (lat1 + lat2) / 2.0
    return np.sqrt(
        ((lat1 - lat2) * 111.0) ** 2
        + ((lon1 - lon2) * 111.0 * np.cos(np.radians(mean_lat))) ** 2
    )


def pairwise_dist_km(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Full pairwise distance matrix in km for an array of points."""
    mean_lat_r = np.radians((lat[:, None] + lat[None, :]) / 2.0)
    dlat = (lat[:, None] - lat[None, :]) * 111.0
    dlon = (lon[:, None] - lon[None, :]) * 111.0 * np.cos(mean_lat_r)
    return np.sqrt(dlat ** 2 + dlon ** 2)


def data_path(config: dict, filename: str) -> Path:
    return REPO_ROOT / config["paths"]["data_dir"] / filename


def outputs_path(config: dict, filename: str) -> Path:
    out_dir = REPO_ROOT / config["paths"]["outputs_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / filename


def load_tracts(config: dict) -> pd.DataFrame:
    return pd.read_csv(
        data_path(config, "tracts_enriched.csv"),
        dtype={"tract_id": str},
    )


def load_sites(config: dict) -> pd.DataFrame:
    return pd.read_csv(data_path(config, "harm_reduction_sites.csv"), parse_dates=["open_date"])


def load_events(config: dict) -> pd.DataFrame:
    return pd.read_csv(
        data_path(config, "overdose_events.csv"),
        parse_dates=["date"],
        dtype={"tract_id": str},
    )
