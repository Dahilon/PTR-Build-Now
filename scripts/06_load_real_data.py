"""
Normalize the triaged real DataSF aggregates and join daily weather.

The relevant public tables do not contain incident-level records or any
within-city geography. This script therefore preserves one row per published
weekly/monthly aggregate with an explicit event_count, assigns the honest
placeholder neighborhood="Citywide", and leaves lat/lon null. It does not
manufacture individual event times or locations.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import REPO_ROOT, get_logger, load_config

log = get_logger(__name__)

NORMALIZED_COLUMNS = [
    "event_id",
    "neighborhood",
    "date",
    "lat",
    "lon",
    "event_count",
    "event_type",
    "source_granularity",
    "source_file",
]


def require_columns(frame: pd.DataFrame, columns: set[str], source: str) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ValueError(f"{source} is missing required columns: {missing}")


def normalize_aggregate(
    frame: pd.DataFrame,
    *,
    date_column: str,
    count_column: str,
    event_type: str,
    granularity: str,
    source_file: str,
    id_prefix: str,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
) -> pd.DataFrame:
    """Normalize one citywide aggregate source without inventing incidents."""
    require_columns(frame, {date_column, count_column}, source_file)
    normalized = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_column], errors="coerce"),
            "event_count": pd.to_numeric(frame[count_column], errors="coerce"),
        }
    )
    normalized = normalized.dropna(subset=["date", "event_count"])
    normalized = normalized[
        normalized["date"].between(window_start, window_end)
    ].copy()
    normalized["event_count"] = normalized["event_count"].astype(int)
    normalized = normalized[normalized["event_count"] >= 0]
    normalized["event_id"] = (
        id_prefix + "-" + normalized["date"].dt.strftime("%Y%m%d")
    )
    normalized["neighborhood"] = "Citywide"
    normalized["lat"] = np.nan
    normalized["lon"] = np.nan
    normalized["event_type"] = event_type
    normalized["source_granularity"] = granularity
    normalized["source_file"] = source_file
    return normalized[NORMALIZED_COLUMNS]


def load_weather(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    weather = pd.read_csv(path)
    require_columns(
        weather,
        {
            "date",
            "temperature_min_c",
            "temperature_mean_c",
            "temperature_max_c",
            "precipitation_mm",
        },
        path.name,
    )
    weather["date"] = pd.to_datetime(weather["date"], errors="coerce")
    weather = weather[weather["date"].between(start, end)].copy()
    if weather["date"].duplicated().any():
        raise ValueError(f"{path.name} contains duplicate dates")
    return weather


def print_summary(events: pd.DataFrame, weather_columns: list[str]) -> None:
    print("=== Normalized real-data summary ===")
    print(f"Aggregate observation rows: {len(events):,}")
    print(
        "Date range: "
        f"{events['date'].min().date()} to {events['date'].max().date()}"
    )
    print("\nBy event type:")
    by_type = events.groupby("event_type").agg(
        aggregate_rows=("event_id", "size"),
        represented_events=("event_count", "sum"),
        first_date=("date", "min"),
        last_date=("date", "max"),
    )
    print(by_type.to_string())
    print("\nNeighborhood breakdown:")
    neighborhoods = events.groupby(["neighborhood", "event_type"]).agg(
        aggregate_rows=("event_id", "size"),
        represented_events=("event_count", "sum"),
    )
    print(neighborhoods.to_string())
    joined = events[weather_columns].notna().all(axis=1).sum()
    print(
        f"\nWeather join: {joined:,}/{len(events):,} rows "
        f"({joined / len(events):.1%}) have complete daily weather"
    )
    located = events[["lat", "lon"]].notna().all(axis=1).sum()
    print(f"Coordinate coverage: {located:,}/{len(events):,} rows")
    print(
        "Harm-reduction sites: no site-level file or opening dates were "
        "present in the relevant downloads; manual sourcing is required."
    )
    print(
        "IMPORTANT: rows are published weekly/monthly aggregates with "
        "event_count weights, not individual incidents."
    )


def main() -> int:
    cfg = load_config()
    real_cfg = cfg["real_data"]
    input_dir = REPO_ROOT / real_cfg["input_dir"]
    output_path = REPO_ROOT / real_cfg["normalized_events_file"]
    start = pd.Timestamp(real_cfg["event_window_start"])
    end = pd.Timestamp(real_cfg["event_window_end"])

    ems_path = input_dir / real_cfg["ems_file"]
    deaths_path = input_dir / real_cfg["deaths_file"]
    services_path = input_dir / real_cfg["services_file"]
    weather_path = input_dir / real_cfg["weather_file"]

    ems = pd.read_csv(ems_path)
    deaths = pd.read_csv(deaths_path)
    services = pd.read_csv(services_path)

    normalized_ems = normalize_aggregate(
        ems,
        date_column="week_start_date",
        count_column="total_overdose_related_911_calls",
        event_type="ems_overdose_911_response",
        granularity="weekly",
        source_file=ems_path.name,
        id_prefix="REAL-EMS",
        window_start=start,
        window_end=end,
    )
    normalized_deaths = normalize_aggregate(
        deaths,
        date_column="month_start_date",
        count_column="total_deaths",
        event_type="overdose_death",
        granularity="monthly",
        source_file=deaths_path.name,
        id_prefix="REAL-DEATH",
        window_start=start,
        window_end=end,
    )
    events = pd.concat(
        [normalized_ems, normalized_deaths],
        ignore_index=True,
    ).sort_values(["date", "event_type"])

    require_columns(
        services,
        {
            "reporting_period_start_date",
            "service_category",
            "metric",
            "metric_value",
        },
        services_path.name,
    )
    site_columns = {
        "site_id",
        "site_name",
        "address",
        "lat",
        "lon",
        "open_date",
    }
    if site_columns.intersection(services.columns):
        log.warning(
            "Service metrics unexpectedly contain possible site columns; "
            "review before treating them as site records."
        )
    else:
        log.info(
            "Substance-use service metrics are citywide aggregates and "
            "contain no site locations or opening dates."
        )

    weather = load_weather(weather_path, start, end)
    weather_columns = [
        "temperature_min_c",
        "temperature_mean_c",
        "temperature_max_c",
        "precipitation_mm",
    ]
    events = events.merge(
        weather[["date", *weather_columns]],
        on="date",
        how="left",
        validate="many_to_one",
    )
    events["date"] = events["date"].dt.strftime("%Y-%m-%d")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_path, index=False)
    log.info("Saved %d normalized aggregate rows to %s", len(events), output_path)

    events["date"] = pd.to_datetime(events["date"])
    print_summary(events, weather_columns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
