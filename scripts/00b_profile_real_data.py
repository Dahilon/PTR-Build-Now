"""
Profile every CSV in data/real/ after a one-time bulk download.

The summary emphasizes row/column counts, date coverage, geographic fields,
and missingness so obviously empty or unsuitable datasets stand out quickly.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import REPO_ROOT

REAL_DATA_DIR = REPO_ROOT / "data" / "real"
DATE_HINTS = ("date", "time", "year", "month", "week")
GEO_HINTS = (
    "latitude",
    "longitude",
    "_lat",
    "lat_",
    "_lon",
    "lon_",
    "location",
    "address",
    "zip",
    "tract",
    "neighborhood",
    "county",
    "geograph",
)


def matching_columns(columns, hints) -> list[str]:
    return [
        str(column)
        for column in columns
        if any(hint in str(column).lower() for hint in hints)
    ]


def matching_geo_columns(columns) -> list[str]:
    exact_names = {"lat", "lon", "lng", "x", "y"}
    return [
        str(column)
        for column in columns
        if str(column).lower() in exact_names
        or any(hint in str(column).lower() for hint in GEO_HINTS)
    ]


def date_coverage(frame: pd.DataFrame, candidates: list[str]) -> str:
    ordered_candidates = sorted(
        candidates,
        key=lambda column: (
            "date" not in column.lower(),
            column.lower() == "year" or column.lower().endswith("_year"),
            column,
        ),
    )
    for column in ordered_candidates:
        normalized_name = column.lower()
        if normalized_name == "year" or normalized_name.endswith("_year"):
            years = pd.to_numeric(frame[column], errors="coerce")
            years = years[years.between(1900, 2100)]
            if not years.empty:
                return f"{column}: {int(years.min())} to {int(years.max())}"
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if parsed.notna().any():
            return (
                f"{column}: {parsed.min().date().isoformat()} "
                f"to {parsed.max().date().isoformat()}"
            )
    return "none detected"


def profile_file(path: Path) -> dict:
    try:
        frame = pd.read_csv(path, low_memory=False)
    except (pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        return {
            "file": path.name,
            "rows": 0,
            "columns": 0,
            "date_coverage": "unreadable/empty",
            "geo_columns": "",
            "missing_pct": 100.0,
            "usable": "NO",
            "error": str(exc),
        }

    date_columns = matching_columns(frame.columns, DATE_HINTS)
    geo_columns = matching_geo_columns(frame.columns)
    cell_count = frame.shape[0] * frame.shape[1]
    missing_pct = (
        float(frame.isna().sum().sum() / cell_count * 100)
        if cell_count
        else 100.0
    )
    has_time = date_coverage(frame, date_columns)
    usable = (
        "YES"
        if len(frame) > 0 and (has_time != "none detected" or geo_columns)
        else "REVIEW"
        if len(frame) > 0
        else "NO"
    )
    return {
        "file": path.name,
        "rows": len(frame),
        "columns": len(frame.columns),
        "date_coverage": has_time,
        "geo_columns": ", ".join(geo_columns[:6]),
        "missing_pct": round(missing_pct, 1),
        "usable": usable,
        "error": "",
    }


def main() -> int:
    paths = sorted(REAL_DATA_DIR.glob("*.csv"))
    print("=== Real-data profile ===")
    print(f"Directory: {REAL_DATA_DIR}")
    if not paths:
        print("No CSV files found. Run scripts/00_fetch_real_data.py first.")
        return 1

    profiles = pd.DataFrame(profile_file(path) for path in paths)
    display = profiles[
        [
            "file",
            "rows",
            "columns",
            "date_coverage",
            "geo_columns",
            "missing_pct",
            "usable",
        ]
    ]
    with pd.option_context(
        "display.max_colwidth",
        70,
        "display.width",
        220,
    ):
        print(display.to_string(index=False))

    print(
        f"\nFiles: {len(profiles)} | Rows: {profiles['rows'].sum():,} | "
        f"Usable: {(profiles['usable'] == 'YES').sum()} | "
        f"Review: {(profiles['usable'] == 'REVIEW').sum()} | "
        f"Empty/unreadable: {(profiles['usable'] == 'NO').sum()}"
    )
    errors = profiles[profiles["error"] != ""]
    if not errors.empty:
        print("\nRead errors:")
        for row in errors.itertuples():
            print(f"- {row.file}: {row.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
