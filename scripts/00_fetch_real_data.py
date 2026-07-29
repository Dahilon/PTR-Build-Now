"""
One-time bulk download of the best available public real-data inputs.

This intentionally has no incremental update logic, scheduler integration,
or last-run state. Each run discovers matching Socrata datasets, downloads
up to MAX_ROWS_PER_DATASET rows from each one, and overwrites its CSV in
data/real/. It also downloads NWS observations for San Francisco
International Airport and aggregates them to daily weather.
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import certifi

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import REPO_ROOT, get_logger

log = get_logger(__name__)

REAL_DATA_DIR = REPO_ROOT / "data" / "real"
DISCOVERY_URL = "https://api.us.socrata.com/api/catalog/v1"
DISCOVERY_MATCHES_PER_QUERY = 10
SODA_PAGE_SIZE = 10_000
MAX_ROWS_PER_DATASET = 100_000
REQUEST_TIMEOUT_SECONDS = 90
MAX_REQUEST_ATTEMPTS = 3

SOCRATA_SEARCHES = {
    "data.sfgov.org": [
        "overdose",
        "naloxone",
        "drug overdose death",
        "syringe services",
        "EMS response",
    ],
    "data.cdc.gov": [
        "wastewater",
        "NWSS",
    ],
}

WEATHER_STATION = "KSFO"
NOAA_GHCN_STATION = "USW00023234"
WEATHER_START = date(2024, 1, 1)
WEATHER_END = date(2025, 12, 31)
PACIFIC = ZoneInfo("America/Los_Angeles")
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def request_bytes(url: str) -> bytes:
    """Fetch a URL with a descriptive user agent and short retry loop."""
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/csv;q=0.9, */*;q=0.8",
            "User-Agent": (
                "OverdoseClusterIntelligence/1.0 "
                "(public-health research; one-time bulk download)"
            ),
        },
    )
    for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
        try:
            with urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
                context=SSL_CONTEXT,
            ) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == MAX_REQUEST_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1)
            log.warning(
                "Request failed (%s); retrying in %ss: %s",
                exc,
                delay,
                url,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def request_json(url: str) -> dict:
    return json.loads(request_bytes(url))


def safe_slug(value: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "dataset")[:max_length].rstrip("-")


def discover_socrata_datasets(domain: str, keywords: list[str]) -> list[dict]:
    """Return deduplicated, tabular dataset matches for all search terms."""
    matches: dict[str, dict] = {}
    for keyword in keywords:
        query = urlencode(
            {
                "search_context": domain,
                "q": keyword,
                "limit": DISCOVERY_MATCHES_PER_QUERY,
            }
        )
        payload = request_json(f"{DISCOVERY_URL}?{query}")
        for result in payload.get("results", []):
            resource = result.get("resource", {})
            dataset_id = resource.get("id")
            if resource.get("type") == "dataset" and dataset_id:
                matches.setdefault(dataset_id, result)
        log.info(
            "%s discovery %r: %d result(s), %d unique tabular dataset(s) so far",
            domain,
            keyword,
            len(payload.get("results", [])),
            len(matches),
        )
    return list(matches.values())


def parse_csv_page(payload: bytes) -> pd.DataFrame:
    text = payload.decode("utf-8-sig")
    if not text.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(text), dtype=str)


def download_socrata_dataset(domain: str, result: dict) -> dict:
    """Download a Socrata dataset in pages, capped to prevent runaway pulls."""
    resource = result["resource"]
    dataset_id = resource["id"]
    name = resource.get("name") or dataset_id
    source_prefix = "datasf" if domain == "data.sfgov.org" else "cdc"
    destination = REAL_DATA_DIR / (
        f"{source_prefix}_{dataset_id}_{safe_slug(name)}.csv"
    )

    pages: list[pd.DataFrame] = []
    total_rows = 0
    while total_rows < MAX_ROWS_PER_DATASET:
        limit = min(SODA_PAGE_SIZE, MAX_ROWS_PER_DATASET - total_rows)
        query = urlencode({"$limit": limit, "$offset": total_rows})
        url = f"https://{domain}/resource/{dataset_id}.csv?{query}"
        page = parse_csv_page(request_bytes(url))
        if page.empty:
            break
        pages.append(page)
        total_rows += len(page)
        if len(page) < limit:
            break

    if pages:
        frame = pd.concat(pages, ignore_index=True)
        frame.to_csv(destination, index=False)
        columns = len(frame.columns)
    else:
        destination.write_text("", encoding="utf-8")
        columns = 0

    capped = total_rows == MAX_ROWS_PER_DATASET
    log.info(
        "Saved %s: %s rows x %d columns%s",
        destination.name,
        f"{total_rows:,}",
        columns,
        " (100k cap reached)" if capped else "",
    )
    return {
        "source": domain,
        "dataset_id": dataset_id,
        "name": name,
        "file": destination.name,
        "rows": total_rows,
        "columns": columns,
        "capped": capped,
        "status": "ok",
    }


def nws_observation_rows(station: str, start: date, end: date) -> list[dict]:
    """Fetch NWS observations for the full window, following pagination."""
    observations: list[dict] = []
    start_at = datetime.combine(start, datetime.min.time(), tzinfo=PACIFIC)
    end_at = datetime.combine(
        end,
        datetime.max.time().replace(microsecond=0),
        tzinfo=PACIFIC,
    )
    query = urlencode(
        {
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "limit": 500,
        }
    )
    url = f"https://api.weather.gov/stations/{station}/observations?{query}"
    while url:
        payload = request_json(url)
        features = payload.get("features", [])
        observations.extend(features)
        url = payload.get("pagination", {}).get("next")
    log.info(
        "NWS %s %s to %s: %s observations",
        station,
        start,
        end,
        f"{len(observations):,}",
    )
    return observations


def measurement(properties: dict, key: str) -> float:
    value = (properties.get(key) or {}).get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def aggregate_daily_weather(
    observations: list[dict],
    start: date,
    end: date,
) -> pd.DataFrame:
    """Aggregate NWS observation features into daily SF weather metrics."""
    rows = []
    for feature in observations:
        properties = feature.get("properties", {})
        timestamp = properties.get("timestamp")
        if not timestamp:
            continue
        observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        local_date = observed_at.astimezone(PACIFIC).date()
        if not start <= local_date <= end:
            continue
        rows.append(
            {
                "date": local_date.isoformat(),
                "temperature_c": measurement(properties, "temperature"),
                "precipitation_mm": measurement(
                    properties, "precipitationLastHour"
                )
                * 1000,
            }
        )

    columns = [
        "date",
        "temperature_min_c",
        "temperature_mean_c",
        "temperature_max_c",
        "precipitation_mm",
        "observation_count",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    hourly = pd.DataFrame(rows)
    daily = hourly.groupby("date", as_index=False).agg(
        temperature_min_c=("temperature_c", "min"),
        temperature_mean_c=("temperature_c", "mean"),
        temperature_max_c=("temperature_c", "max"),
        precipitation_mm=("precipitation_mm", lambda values: values.sum(min_count=1)),
        observation_count=("date", "size"),
    )

    full_dates = pd.DataFrame(
        {
            "date": pd.date_range(start, end, freq="D").strftime("%Y-%m-%d"),
        }
    )
    return full_dates.merge(daily, on="date", how="left")[columns]


def download_noaa_ghcn_daily(
    station: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Use NOAA GHCN daily summaries when weather.gov history is unavailable."""
    url = (
        "https://www.ncei.noaa.gov/data/"
        "global-historical-climatology-network-daily/access/"
        f"{station}.csv"
    )
    raw = pd.read_csv(io.BytesIO(request_bytes(url)), low_memory=False)
    raw["DATE"] = pd.to_datetime(raw["DATE"], errors="coerce")
    raw = raw[
        raw["DATE"].between(pd.Timestamp(start), pd.Timestamp(end))
    ].copy()

    for column in ("TMIN", "TAVG", "TMAX", "PRCP"):
        raw[column] = pd.to_numeric(raw.get(column), errors="coerce") / 10.0
    temperature_mean = raw["TAVG"].fillna(
        (raw["TMIN"] + raw["TMAX"]) / 2.0
    )
    daily = pd.DataFrame(
        {
            "date": raw["DATE"].dt.strftime("%Y-%m-%d"),
            "temperature_min_c": raw["TMIN"],
            "temperature_mean_c": temperature_mean,
            "temperature_max_c": raw["TMAX"],
            "precipitation_mm": raw["PRCP"],
            "observation_count": 1,
        }
    )
    log.info(
        "NOAA GHCN %s fallback: %s daily summaries",
        station,
        f"{len(daily):,}",
    )
    return daily


def download_weather() -> dict:
    observations = nws_observation_rows(
        WEATHER_STATION,
        WEATHER_START,
        WEATHER_END,
    )
    if observations:
        daily = aggregate_daily_weather(
            observations,
            WEATHER_START,
            WEATHER_END,
        )
        source = "api.weather.gov"
    else:
        log.warning(
            "weather.gov has no observations for the historical window; "
            "falling back to NOAA GHCN daily summaries for the same station."
        )
        daily = download_noaa_ghcn_daily(
            NOAA_GHCN_STATION,
            WEATHER_START,
            WEATHER_END,
        )
        source = "NOAA GHCN"
    destination = REAL_DATA_DIR / "nws_ksfo_daily_weather_2024_2025.csv"
    daily.to_csv(destination, index=False, quoting=csv.QUOTE_MINIMAL)
    populated = int(daily["observation_count"].notna().sum())
    log.info(
        "Saved %s: %s days (%d with observations)",
        destination.name,
        f"{len(daily):,}",
        populated,
    )
    return {
        "source": source,
        "dataset_id": WEATHER_STATION,
        "name": "KSFO daily weather",
        "file": destination.name,
        "rows": len(daily),
        "columns": len(daily.columns),
        "capped": False,
        "status": "ok" if populated else "empty",
    }


def print_download_summary(results: list[dict]) -> None:
    print("\n=== One-time real-data download summary ===")
    if not results:
        print("No datasets were downloaded.")
        return
    summary = pd.DataFrame(results)
    display_columns = [
        "source",
        "dataset_id",
        "rows",
        "columns",
        "capped",
        "status",
        "file",
    ]
    print(summary[display_columns].to_string(index=False))
    print(
        f"\nFiles: {len(summary)} | Rows: {summary['rows'].sum():,} | "
        f"Errors: {(summary['status'] == 'error').sum()}"
    )


def main() -> int:
    REAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for domain, keywords in SOCRATA_SEARCHES.items():
        try:
            datasets = discover_socrata_datasets(domain, keywords)
        except Exception as exc:
            log.error("Discovery failed for %s: %s", domain, exc)
            results.append(
                {
                    "source": domain,
                    "dataset_id": "discovery",
                    "name": "Discovery API",
                    "file": "",
                    "rows": 0,
                    "columns": 0,
                    "capped": False,
                    "status": "error",
                }
            )
            continue

        for result in datasets:
            try:
                results.append(download_socrata_dataset(domain, result))
            except Exception as exc:
                resource = result.get("resource", {})
                log.error(
                    "Download failed for %s/%s (%s): %s",
                    domain,
                    resource.get("id", "unknown"),
                    resource.get("name", "unnamed"),
                    exc,
                )
                results.append(
                    {
                        "source": domain,
                        "dataset_id": resource.get("id", "unknown"),
                        "name": resource.get("name", "unnamed"),
                        "file": "",
                        "rows": 0,
                        "columns": 0,
                        "capped": False,
                        "status": "error",
                    }
                )

    try:
        results.append(download_weather())
    except Exception as exc:
        log.error("NWS weather download failed: %s", exc)
        results.append(
            {
                "source": "api.weather.gov / NOAA GHCN",
                "dataset_id": WEATHER_STATION,
                "name": "KSFO daily weather",
                "file": "",
                "rows": 0,
                "columns": 0,
                "capped": False,
                "status": "error",
            }
        )

    print_download_summary(results)
    return 1 if any(result["status"] == "error" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
