"""Build auditable operational signals and response options for Foundry.

Numeric risk/confidence scoring and resource constraints are deterministic.
The structured briefs are safe inputs for an AIP explanation layer: they
include source evidence, mandatory caveats, and prohibited claims. No action
is executed automatically; every scenario remains proposed until a human
operator approves it in Foundry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.decision_support import (
    artifact_metadata,
    build_grounded_briefs,
    build_operational_signals,
    build_response_scenarios,
)
from src.utils import REPO_ROOT, get_logger, load_config, outputs_path

log = get_logger(__name__)


def retain_analysis_eligible_periods(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Exclude partial boundary weeks using script 07's persisted sample."""
    weekly = pd.read_csv(outputs_path(cfg, "real_time_series_weekly.csv"), parse_dates=["date"])
    monthly = pd.read_csv(outputs_path(cfg, "real_time_series_monthly.csv"), parse_dates=["date"])
    weekly_dates = set(weekly["date"])
    monthly_dates = set(monthly["date"])
    eligible = (
        ((events["event_type"] == "ems_overdose_911_response") & events["date"].isin(weekly_dates))
        | ((events["event_type"] == "overdose_death") & events["date"].isin(monthly_dates))
    )
    return events.loc[eligible].copy()


def main() -> int:
    cfg = load_config()
    ds_cfg = cfg["decision_support"]
    events_path = REPO_ROOT / cfg["real_data"]["normalized_events_file"]
    resources_path = REPO_ROOT / ds_cfg["resource_inventory_file"]
    events = pd.read_csv(events_path, parse_dates=["date"])
    resources = pd.read_csv(resources_path)
    events = retain_analysis_eligible_periods(events, cfg)

    signals = build_operational_signals(events, ds_cfg["baseline_periods"])
    scenarios = build_response_scenarios(signals, resources, float(ds_cfg["response_budget"]))
    briefs = build_grounded_briefs(signals, scenarios)

    signal_path = outputs_path(cfg, ds_cfg["operational_signals_file"])
    scenario_path = outputs_path(cfg, ds_cfg["response_scenarios_file"])
    brief_path = outputs_path(cfg, ds_cfg["operational_briefs_file"])
    manifest_path = outputs_path(cfg, ds_cfg["artifact_manifest_file"])
    signals.to_csv(signal_path, index=False)
    scenarios.to_csv(scenario_path, index=False)
    brief_path.write_text(json.dumps(briefs, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "pipeline_contract_version": "1.0.0",
        "evidence_window_end": str(signals["signal_date"].max()),
        "artifacts": [
            artifact_metadata(signal_path, rows=len(signals)),
            artifact_metadata(scenario_path, rows=len(scenarios)),
            artifact_metadata(brief_path, rows=len(briefs)),
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    latest = signals.sort_values("signal_date").groupby("event_type", as_index=False).tail(1)
    print("=== Operational decision-support outputs ===")
    print(latest[["signal_date", "event_type", "observed_count", "expected_count", "risk_score", "severity", "confidence_label", "decision_scope"]].to_string(index=False))
    print("\nResponse options (all require human approval):")
    print(scenarios[["scenario_title", "total_cost_usd", "estimated_people_reached", "status"]].to_string(index=False))
    print("\nPublic evidence supports citywide readiness only; resource inputs are notional demo data.")
    log.info("Wrote %d signals, %d scenarios, and %d grounded briefs", len(signals), len(scenarios), len(briefs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
