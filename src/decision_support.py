"""Auditable decision-support logic for the operational Foundry workflow.

This module deliberately keeps three concepts separate:

* severity: how unusual the observed count is relative to its own history;
* confidence: whether the available data can support a precise decision; and
* response scenarios: feasible options under explicit inventory and budget.

The public data is citywide and aggregate. The code therefore never invents a
neighborhood, an incident timestamp, or a causal claim. Foundry/AIP can explain
these outputs, but the numeric scores remain deterministic and testable here.
"""
from __future__ import annotations

import hashlib
import json
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_EVENT_COLUMNS = {
    "date",
    "event_count",
    "event_type",
    "source_granularity",
    "source_file",
    "neighborhood",
}

STRATEGY_WEIGHTS = {
    "Rapid stabilization": {
        "mobile_outreach_team_shift": 1.35,
        "naloxone_distribution_batch": 1.20,
        "care_navigation_team_shift": 0.80,
        "public_health_analyst_shift": 0.55,
    },
    "Balanced response": {
        "mobile_outreach_team_shift": 1.00,
        "naloxone_distribution_batch": 1.00,
        "care_navigation_team_shift": 1.00,
        "public_health_analyst_shift": 1.00,
    },
    "Validate before deployment": {
        "mobile_outreach_team_shift": 0.45,
        "naloxone_distribution_batch": 0.75,
        "care_navigation_team_shift": 0.55,
        "public_health_analyst_shift": 1.65,
    },
}

STRATEGY_MINIMUMS = {
    "Rapid stabilization": {"mobile_outreach_team_shift": 1},
    "Balanced response": {
        "mobile_outreach_team_shift": 1,
        "naloxone_distribution_batch": 1,
        "care_navigation_team_shift": 1,
        "public_health_analyst_shift": 1,
    },
    "Validate before deployment": {"public_health_analyst_shift": 2},
}


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _robust_scale(history: pd.Series, expected: float) -> float:
    median = float(history.median())
    mad = float(np.median(np.abs(history.to_numpy(dtype=float) - median)))
    robust_sigma = 1.4826 * mad
    standard_sigma = float(history.std(ddof=1)) if len(history) > 1 else 0.0
    # Poisson-like floor prevents tiny historical variance from creating an
    # implausibly extreme alert score.
    return max(robust_sigma, standard_sigma, np.sqrt(max(expected, 1.0)), 1.0)


def _severity(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 45:
        return "watch"
    return "routine"


def _action(severity: str, confidence: float) -> str:
    if confidence < 0.50:
        return "Validate with internal incident data before geographic deployment"
    if severity in {"critical", "high"}:
        return "Open response plan and route for operator approval"
    if severity == "watch":
        return "Monitor next reporting period and pre-stage resources"
    return "Continue routine monitoring"


def build_operational_signals(
    events: pd.DataFrame,
    baseline_periods: dict[str, int],
) -> pd.DataFrame:
    """Score each aggregate observation against only its preceding history."""
    require_columns(events, REQUIRED_EVENT_COLUMNS, "normalized events")
    frame = events.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["event_count"] = pd.to_numeric(frame["event_count"], errors="coerce")
    if frame[["date", "event_count"]].isna().any().any():
        raise ValueError("normalized events contain invalid dates or counts")
    if (frame["event_count"] < 0).any():
        raise ValueError("normalized events contain negative counts")

    output: list[dict[str, Any]] = []
    for event_type, group in frame.groupby("event_type", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        granularity = str(group["source_granularity"].iloc[0])
        window = int(baseline_periods.get(granularity, 8))
        if window < 2:
            raise ValueError("baseline periods must be at least 2")

        for index, row in group.iterrows():
            history = group.loc[max(0, index - window): index - 1, "event_count"]
            history_n = len(history)
            expected = float(history.median()) if history_n else float(row["event_count"])
            scale = _robust_scale(history, expected) if history_n else np.sqrt(max(expected, 1.0))
            z_score = (float(row["event_count"]) - expected) / scale
            risk_score = float(np.clip(50.0 + 15.0 * z_score, 0.0, 100.0))

            # Even with ample history, citywide aggregate public data cannot
            # justify neighborhood targeting. Confidence is capped accordingly.
            history_coverage = min(history_n / window, 1.0)
            aggregate_cap = 0.45 if str(row["neighborhood"]) == "Citywide" else 0.75
            confidence = aggregate_cap * (0.40 + 0.60 * history_coverage)
            confidence_label = "low" if confidence < 0.50 else "moderate" if confidence < 0.75 else "high"
            severity = _severity(risk_score)
            date_text = row["date"].strftime("%Y-%m-%d")
            limitations = (
                "Citywide aggregate; no incident coordinates or exact timestamps. "
                "Do not infer neighborhood-level risk or causality."
            )
            output.append(
                {
                    "signal_id": f"OPS-{event_type.upper().replace('_', '-')}-{date_text.replace('-', '')}",
                    "signal_date": date_text,
                    "zone_id": str(row["neighborhood"]),
                    "event_type": event_type,
                    "observed_count": int(row["event_count"]),
                    "expected_count": round(expected, 3),
                    "excess_count": round(float(row["event_count"]) - expected, 3),
                    "z_score": round(z_score, 4),
                    "risk_score": round(risk_score, 2),
                    "severity": severity,
                    "confidence_score": round(confidence, 3),
                    "confidence_label": confidence_label,
                    "evidence_class": "real_public_aggregate",
                    "source_granularity": granularity,
                    "source_file": str(row["source_file"]),
                    "decision_scope": "citywide_readiness_only",
                    "recommended_action": _action(severity, confidence),
                    "data_limitations": limitations,
                }
            )
    return pd.DataFrame(output).sort_values(["signal_date", "event_type"]).reset_index(drop=True)


def _best_allocation(
    resources: pd.DataFrame,
    budget: float,
    weights: dict[str, float],
    minimums: dict[str, int],
) -> tuple[list[int], float]:
    ranges = [range(int(value) + 1) for value in resources["available_units"]]
    best_quantities: list[int] | None = None
    best_key: tuple[float, float, int] | None = None
    for quantities in product(*ranges):
        if any(
            quantity < int(minimums.get(resource.resource_type, 0))
            for quantity, resource in zip(quantities, resources.itertuples())
        ):
            continue
        cost = float(np.dot(quantities, resources["unit_cost_usd"]))
        if cost > budget or sum(quantities) == 0:
            continue
        utility = sum(
            quantity
            * float(resource.coverage_per_unit)
            * float(weights.get(resource.resource_type, 1.0))
            for quantity, resource in zip(quantities, resources.itertuples())
        )
        # Stable tie-breaking: utility, then lower cost, then fewer units.
        key = (round(utility, 8), -cost, -sum(quantities))
        if best_key is None or key > best_key:
            best_key = key
            best_quantities = list(quantities)
    if best_quantities is None or best_key is None:
        raise ValueError("budget cannot fund any available response resource")
    return best_quantities, best_key[0]


def build_response_scenarios(
    signals: pd.DataFrame,
    resources: pd.DataFrame,
    budget: float,
) -> pd.DataFrame:
    """Generate three feasible, operator-reviewable resource allocations."""
    require_columns(
        resources,
        {"resource_id", "resource_type", "available_units", "unit_cost_usd", "coverage_per_unit", "evidence_class"},
        "resource inventory",
    )
    require_columns(signals, {"signal_id", "signal_date", "risk_score", "confidence_score", "zone_id"}, "signals")
    if budget <= 0:
        raise ValueError("response budget must be positive")
    inventory = resources.copy().sort_values("resource_id").reset_index(drop=True)
    for column in ["available_units", "unit_cost_usd", "coverage_per_unit"]:
        inventory[column] = pd.to_numeric(inventory[column], errors="raise")
    if (inventory[["available_units", "unit_cost_usd", "coverage_per_unit"]] < 0).any().any():
        raise ValueError("resource constraints cannot be negative")

    # A routine current period does not warrant a new deployment. For the demo
    # workflow, build options against the most recent high/critical signal in
    # the evidence window; production would run this at alert time.
    actionable = signals[signals["severity"].isin(["high", "critical"])]
    target_pool = actionable if not actionable.empty else signals
    latest = target_pool.sort_values(["signal_date", "risk_score"]).iloc[-1]
    scenarios: list[dict[str, Any]] = []
    for rank, (title, weights) in enumerate(STRATEGY_WEIGHTS.items(), start=1):
        quantities, utility = _best_allocation(
            inventory,
            budget,
            weights,
            STRATEGY_MINIMUMS[title],
        )
        plan = []
        total_cost = 0.0
        raw_coverage = 0.0
        for quantity, resource in zip(quantities, inventory.itertuples()):
            if not quantity:
                continue
            line_cost = quantity * float(resource.unit_cost_usd)
            total_cost += line_cost
            raw_coverage += quantity * float(resource.coverage_per_unit)
            plan.append(
                {
                    "resource_id": resource.resource_id,
                    "resource_type": resource.resource_type,
                    "quantity": int(quantity),
                    "line_cost_usd": round(line_cost, 2),
                }
            )
        scenarios.append(
            {
                "scenario_id": f"SCN-{str(latest['signal_date']).replace('-', '')}-{rank}",
                "scenario_title": title,
                "target_signal_id": latest["signal_id"],
                "target_zone_id": latest["zone_id"],
                "budget_limit_usd": round(float(budget), 2),
                "total_cost_usd": round(total_cost, 2),
                "unspent_budget_usd": round(float(budget) - total_cost, 2),
                "estimated_people_reached": int(raw_coverage),
                "strategy_utility": round(float(utility), 2),
                "resource_plan_json": json.dumps(plan, separators=(",", ":")),
                "status": "proposed",
                "approval_required": True,
                "evidence_class": "notional_demo_resources",
                "rationale": f"Feasible {title.lower()} option for the most recent actionable signal; no automatic deployment.",
                "limitations": "Inventory, costs, and reach are clearly labeled notional demo inputs; replace with governed internal operations data.",
            }
        )
    return pd.DataFrame(scenarios)


def build_grounded_briefs(signals: pd.DataFrame, scenarios: pd.DataFrame) -> list[dict[str, Any]]:
    """Create structured evidence packets suitable for a grounded AIP prompt."""
    briefs = []
    latest_by_type = signals.sort_values("signal_date").groupby("event_type", as_index=False).tail(1)
    scenario_targets = signals[
        signals["signal_id"].isin(scenarios["target_signal_id"])
    ]
    briefing_signals = pd.concat(
        [scenario_targets, latest_by_type],
        ignore_index=True,
    ).drop_duplicates("signal_id")
    for signal in briefing_signals.itertuples():
        options = scenarios.loc[
            scenarios["target_signal_id"] == signal.signal_id,
            ["scenario_id", "scenario_title", "total_cost_usd", "status"],
        ].to_dict("records")
        briefs.append(
            {
                "brief_id": f"BRIEF-{signal.signal_id}",
                "signal_id": signal.signal_id,
                "headline": f"{signal.severity.title()} {signal.event_type.replace('_', ' ')} signal with {signal.confidence_label} confidence",
                "observed_evidence": {
                    "date": signal.signal_date,
                    "zone": signal.zone_id,
                    "observed_count": int(signal.observed_count),
                    "historical_expected_count": float(signal.expected_count),
                    "risk_score": float(signal.risk_score),
                    "confidence_score": float(signal.confidence_score),
                    "source_file": signal.source_file,
                },
                "operator_next_step": signal.recommended_action,
                "response_options": options,
                "mandatory_caveat": signal.data_limitations,
                "prohibited_claims": [
                    "Do not name a high-risk neighborhood from citywide data.",
                    "Do not claim temperature causes overdose events.",
                    "Do not treat notional resource estimates as observed outcomes.",
                ],
            }
        )
    return briefs


def artifact_metadata(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        portable_path = path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        portable_path = path
    result: dict[str, Any] = {
        "path": str(portable_path),
        "sha256": digest,
        "bytes": path.stat().st_size,
    }
    if rows is not None:
        result["rows"] = int(rows)
    return result
