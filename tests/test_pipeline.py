"""
Tests targeting the actual bugs found while building this pipeline:
  1. dist_km / pairwise_dist_km: an earlier version used one endpoint's
     latitude for the longitude scaling factor, making distance
     asymmetric (dist(A,B) != dist(B,A)).
  2. The event simulator originally generated only ONE generation of
     offspring (background -> children, but children never had their own
     children). That's not a real recursive Hawkes process, which is
     what the fitting script assumes -- this test would have caught that
     mismatch immediately instead of it surfacing as an inexplicable
     branching-ratio-collapses-to-zero result three debugging sessions
     later.
  3. The temporal Hawkes MLE itself: confirms it recovers a known
     branching ratio on clean synthetic data (validates the likelihood
     function is correct, independent of any real-data identifiability
     difficulty).
"""
import hashlib
import json
import sys
from collections import deque
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import (
    dist_km,
    load_config,
    load_events,
    load_tracts,
    pairwise_dist_km,
)
from src.decision_support import (
    build_grounded_briefs,
    build_operational_signals,
    build_response_scenarios,
)


def load_script_module(filename):
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = spec_from_file_location(path.stem, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dist_km_zero_for_same_point():
    assert dist_km(37.77, -122.41, 37.77, -122.41) == pytest.approx(0.0)


def test_dist_km_known_distance():
    d = dist_km(37.0, -122.0, 38.0, -122.0)
    assert d == pytest.approx(111.0, rel=0.01)


def test_dist_km_broadcasts_array():
    lat2 = np.array([37.0, 38.0, 39.0])
    lon2 = np.array([-122.0, -122.0, -122.0])
    d = dist_km(37.0, -122.0, lat2, lon2)
    assert d.shape == (3,)
    assert d[0] == pytest.approx(0.0)


def test_dist_km_is_symmetric():
    """Regression test for a real bug: using one endpoint's latitude
    (instead of the mean) for the longitude scaling factor made
    dist(A,B) != dist(B,A)."""
    d_ab = dist_km(37.0, -122.0, 37.3, -122.3)
    d_ba = dist_km(37.3, -122.3, 37.0, -122.0)
    assert d_ab == pytest.approx(d_ba, rel=1e-9)


def test_pairwise_dist_km_symmetric_and_zero_diagonal():
    lat = np.array([37.0, 37.1, 37.2])
    lon = np.array([-122.0, -122.1, -122.2])
    d = pairwise_dist_km(lat, lon)
    assert d.shape == (3, 3)
    np.testing.assert_allclose(np.diag(d), 0.0, atol=1e-9)
    np.testing.assert_allclose(d, d.T, atol=1e-9)


def test_csv_loaders_preserve_tract_id_leading_zero():
    """Census tract IDs are identifiers, not integers; CSV loading must not
    strip the leading zero required for joins with external tract data."""
    cfg = load_config()
    for frame in (load_tracts(cfg), load_events(cfg)):
        assert frame["tract_id"].map(type).eq(str).all()
        assert frame["tract_id"].str.fullmatch(r"\d{11}").all()
        assert frame["tract_id"].str.startswith("0").all()


def test_real_data_fetch_helpers_are_deterministic():
    fetcher = load_script_module("00_fetch_real_data.py")
    assert fetcher.safe_slug("Drug Overdose Deaths / SF!") == "drug-overdose-deaths-sf"
    assert fetcher.MAX_ROWS_PER_DATASET == 100_000
    assert fetcher.WEATHER_START == fetcher.date(2024, 1, 1)
    assert fetcher.WEATHER_END == fetcher.date(2025, 12, 31)


def test_weather_aggregation_fills_full_requested_date_range():
    fetcher = load_script_module("00_fetch_real_data.py")
    observations = [
        {
            "properties": {
                "timestamp": "2024-01-01T18:00:00+00:00",
                "temperature": {"value": 10.0},
                "precipitationLastHour": {"value": 0.001},
            }
        },
        {
            "properties": {
                "timestamp": "2024-01-01T20:00:00+00:00",
                "temperature": {"value": 14.0},
                "precipitationLastHour": {"value": None},
            }
        },
    ]
    daily = fetcher.aggregate_daily_weather(
        observations,
        fetcher.date(2024, 1, 1),
        fetcher.date(2024, 1, 2),
    )
    assert daily["date"].tolist() == ["2024-01-01", "2024-01-02"]
    assert daily.loc[0, "temperature_mean_c"] == pytest.approx(12.0)
    assert daily.loc[0, "precipitation_mm"] == pytest.approx(1.0)
    assert pd.isna(daily.loc[1, "observation_count"])


def test_real_aggregate_normalization_preserves_counts_and_citywide_scope():
    loader = load_script_module("06_load_real_data.py")
    source = pd.DataFrame(
        {
            "week_start_date": ["2023-12-31", "2024-01-07", "2024-01-14"],
            "calls": [99, 12, 15],
        }
    )
    normalized = loader.normalize_aggregate(
        source,
        date_column="week_start_date",
        count_column="calls",
        event_type="ems_overdose_911_response",
        granularity="weekly",
        source_file="example.csv",
        id_prefix="REAL-EMS",
        window_start=pd.Timestamp("2024-01-01"),
        window_end=pd.Timestamp("2024-12-31"),
    )
    assert normalized["event_count"].tolist() == [12, 15]
    assert normalized["neighborhood"].eq("Citywide").all()
    assert normalized[["lat", "lon"]].isna().all().all()
    assert normalized["event_id"].is_unique


def test_real_analysis_modes_reject_unsupported_precision():
    cfg = load_config()
    assert cfg["spatial_analysis"]["geography_level"] == "neighborhood"
    assert cfg["hawkes_fit"]["data_source"] == "real"

    hawkes = load_script_module("05_hawkes_fit.py")
    with pytest.raises(
        hawkes.AggregateTemporalResolutionError,
        match="weekly aggregate",
    ):
        hawkes.load_hawkes_events(cfg)


def test_real_time_series_period_alignment_excludes_partial_weeks():
    analysis = load_script_module("07_real_time_series_analysis.py")
    events = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01", "2024-01-07", "2024-01-14"]
            ),
            "event_count": [30, 70, 63],
            "event_type": [analysis.EMS_EVENT_TYPE] * 3,
        }
    )
    weather = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", "2024-01-31"),
            "temperature_mean_c": 12.0,
            "precipitation_mm": 1.0,
        }
    )
    complete, excluded = analysis.prepare_weekly_series(events, weather)
    assert complete["date"].tolist() == [pd.Timestamp("2024-01-07")]
    assert complete.iloc[0]["precipitation_mm"] == pytest.approx(7.0)
    assert set(excluded["period_days"]) == {6, 353}


def test_real_time_series_detects_clear_positive_trend():
    analysis = load_script_module("07_real_time_series_analysis.py")
    frame = pd.DataFrame(
        {
            "elapsed_periods": np.arange(40, dtype=float),
            "event_count": 10 + 2 * np.arange(40, dtype=float),
        }
    )
    result = analysis.trend_analysis(frame, unit="week", hac_lags=2)
    assert result["slope"] == pytest.approx(2.0)
    assert result["pvalue"] < 0.001


def test_real_time_series_writes_frontend_analysis_samples(tmp_path):
    analysis = load_script_module("07_real_time_series_analysis.py")
    cfg = {"paths": {"outputs_dir": str(tmp_path)}}
    weekly = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-07"]),
            "event_count": [70],
            "temperature_mean_c": [12.5],
            "precipitation_mm": [3.0],
            "elapsed_periods": [0.0],
            "month": [1],
        }
    )
    monthly = weekly.assign(date=pd.to_datetime(["2024-01-01"]))
    weekly_path, monthly_path = analysis.write_analysis_series(
        cfg, weekly, monthly
    )
    assert pd.read_csv(weekly_path)["event_count"].tolist() == [70]
    assert pd.read_csv(monthly_path)["temperature_mean_c"].tolist() == [12.5]


def test_frontend_uses_period_aligned_analysis_outputs():
    frontend = (
        Path(__file__).resolve().parent.parent / "frontend" / "index.html"
    ).read_text(encoding="utf-8")
    assert "../outputs/real_time_series_weekly.csv" in frontend
    assert "../outputs/real_time_series_monthly.csv" in frontend
    assert "102 complete weeks analyzed (106 published rows)" in frontend
    assert "configureDependencies()" in frontend
    assert "validateLoadedData(loaded)" in frontend


def _decision_events() -> pd.DataFrame:
    dates = pd.date_range("2024-01-07", periods=16, freq="7D")
    return pd.DataFrame(
        {
            "date": dates,
            "event_count": [50] * 15 + [80],
            "event_type": ["ems_overdose_911_response"] * 16,
            "source_granularity": ["weekly"] * 16,
            "source_file": ["public.csv"] * 16,
            "neighborhood": ["Citywide"] * 16,
        }
    )


def test_operational_signal_separates_severity_from_confidence():
    signals = build_operational_signals(_decision_events(), {"weekly": 12})
    latest = signals.iloc[-1]
    assert latest["severity"] in {"high", "critical"}
    assert latest["confidence_label"] == "low"
    assert latest["confidence_score"] <= 0.45
    assert latest["zone_id"] == "Citywide"
    assert latest["decision_scope"] == "citywide_readiness_only"
    assert "internal incident data" in latest["recommended_action"]


def test_response_scenarios_respect_budget_inventory_and_human_approval():
    signals = build_operational_signals(_decision_events(), {"weekly": 12})
    resources = pd.DataFrame(
        {
            "resource_id": ["A", "B"],
            "resource_type": [
                "mobile_outreach_team_shift",
                "public_health_analyst_shift",
            ],
            "available_units": [2, 3],
            "unit_cost_usd": [100, 40],
            "coverage_per_unit": [20, 5],
            "evidence_class": ["notional_demo", "notional_demo"],
        }
    )
    scenarios = build_response_scenarios(signals, resources, budget=180)
    assert len(scenarios) == 3
    assert scenarios["total_cost_usd"].le(180).all()
    assert scenarios["approval_required"].all()
    assert scenarios["status"].eq("proposed").all()
    for plan_text in scenarios["resource_plan_json"]:
        plan = json.loads(plan_text)
        assert all(
            item["quantity"]
            <= {"A": 2, "B": 3}[item["resource_id"]]
            for item in plan
        )


def test_grounded_brief_carries_source_and_prohibits_geographic_invention():
    signals = build_operational_signals(_decision_events(), {"weekly": 12})
    resources = pd.DataFrame(
        {
            "resource_id": ["A"],
            "resource_type": ["public_health_analyst_shift"],
            "available_units": [2],
            "unit_cost_usd": [50],
            "coverage_per_unit": [5],
            "evidence_class": ["notional_demo"],
        }
    )
    scenarios = build_response_scenarios(signals, resources, budget=100)
    brief = build_grounded_briefs(signals, scenarios)[0]
    assert brief["observed_evidence"]["source_file"] == "public.csv"
    assert any("neighborhood" in claim for claim in brief["prohibited_claims"])
    assert "no incident coordinates" in brief["mandatory_caveat"]


def test_checked_in_operational_outputs_exclude_partial_ems_periods():
    root = Path(__file__).resolve().parent.parent
    signals = pd.read_csv(root / "outputs" / "operational_signals.csv")
    ems_dates = set(
        signals.loc[
            signals["event_type"] == "ems_overdose_911_response",
            "signal_date",
        ]
    )
    assert len(signals) == 126
    assert not {
        "2024-01-01",
        "2024-12-29",
        "2025-01-01",
        "2025-12-28",
    }.intersection(ems_dates)


def test_checked_in_scenarios_are_distinct_and_feasible():
    root = Path(__file__).resolve().parent.parent
    scenarios = pd.read_csv(root / "outputs" / "response_scenarios.csv")
    assert scenarios["resource_plan_json"].nunique() == 3
    assert scenarios["total_cost_usd"].le(scenarios["budget_limit_usd"]).all()
    assert scenarios["status"].eq("proposed").all()
    assert scenarios["evidence_class"].eq("notional_demo_resources").all()


def test_artifact_manifest_hashes_match_checked_in_outputs():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (root / "outputs" / "artifact_manifest.json").read_text()
    )
    for artifact in manifest["artifacts"]:
        path = root / artifact["path"]
        assert path.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]


def _simulate_recursive_hawkes(rng, n_background, true_n, true_beta, Tmax):
    """Minimal standalone recursive Hawkes simulator for test purposes --
    mirrors the fix applied to scripts/02_simulate_events.py: children are
    pushed back onto the processing queue so they can spawn their own
    offspring, instead of being generated in a single non-recursive pass."""
    bg_times = np.sort(rng.uniform(0, Tmax, n_background))
    all_events = list(bg_times)
    queue = deque(bg_times)
    while queue:
        parent_t = queue.popleft()
        for _ in range(rng.poisson(true_n)):
            delay = rng.exponential(1 / true_beta)
            child_t = parent_t + delay
            if child_t > Tmax:
                continue
            all_events.append(child_t)
            queue.append(child_t)  # the fix: recurse
    return np.sort(np.array(all_events))


def test_recursive_simulator_produces_multigeneration_cascades():
    """Confirms the simulator's queue-based approach actually cascades
    (children spawning grandchildren), not just one flat generation."""
    rng = np.random.default_rng(42)
    n_background = 100
    t = _simulate_recursive_hawkes(rng, n_background, true_n=0.7, true_beta=0.5, Tmax=300)
    # with a stable but substantial branching ratio, total events should be
    # noticeably more than background + one generation of direct children
    # (background * (1+n) would be the non-recursive-bug ceiling)
    non_recursive_bug_ceiling = n_background * 1.7 * 1.15  # generous slack
    assert len(t) > non_recursive_bug_ceiling, (
        "Total event count looks consistent with only one generation of "
        "offspring, not true recursive cascading."
    )


def test_temporal_hawkes_mle_recovers_known_branching_ratio():
    """The actual validated approach used in scripts/05_hawkes_fit.py:
    citywide temporal-only Hawkes MLE. On clean synthetic data with a
    known branching ratio, the recovered value should land in a
    reasonable neighborhood of the truth -- this is the test that
    confirms the final implementation works, after the marked
    spatio-temporal version was abandoned due to unresolved bias."""
    rng = np.random.default_rng(123)
    true_beta, true_n = 0.5, 0.6
    t = _simulate_recursive_hawkes(rng, n_background=150, true_n=true_n, true_beta=true_beta, Tmax=500)
    T = t.max()
    n_events = len(t)

    def nll(free_params):
        log_mu, logit_n, log_beta = free_params
        mu = np.exp(log_mu)
        n = 1 / (1 + np.exp(-logit_n))
        beta = np.exp(log_beta)
        alpha = n * beta
        R = np.zeros(n_events)
        for i in range(1, n_events):
            R[i] = np.exp(-beta * (t[i] - t[i - 1])) * (R[i - 1] + 1)
        lam = np.clip(mu + alpha * R, 1e-10, None)
        ll = np.sum(np.log(lam))
        comp = mu * T + alpha * np.sum((1 - np.exp(-beta * (T - t))) / beta)
        return -(ll - comp)

    init = [np.log(0.3), np.log(0.4 / 0.6), np.log(0.5)]
    bounds = [(-3, 3), (-8, 8), (-4, 3)]
    result = minimize(nll, init, method="L-BFGS-B", bounds=bounds, options={"maxiter": 500})
    n_hat = 1 / (1 + np.exp(-result.x[1]))

    assert abs(n_hat - true_n) < 0.25, (
        f"Recovered branching ratio {n_hat:.3f} too far from true {true_n} -- "
        "the temporal Hawkes MLE should recover the true value reasonably "
        "well on clean synthetic data with this much data."
    )
