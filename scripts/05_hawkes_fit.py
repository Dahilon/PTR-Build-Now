"""
Fit a citywide temporal Hawkes process to exact event timestamps.

``hawkes_fit.data_source = simulated`` reproduces the existing temporal fit.
``real`` loads ``data/real_events_normalized.csv`` and selects the configured
event type. The current DataSF EMS source is weekly aggregate counts, not
individual timestamps, so real mode records a scientifically honest
``not_fitted`` result rather than manufacturing event times. A future exact
incident feed using the normalized schema can be fitted without changing the
model path.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import (
    REPO_ROOT,
    get_logger,
    load_config,
    load_events,
    outputs_path,
)

log = get_logger(__name__)


class AggregateTemporalResolutionError(ValueError):
    """Raised when aggregate counts cannot support point-process fitting."""


def load_hawkes_events(cfg: dict) -> tuple[pd.DataFrame, str]:
    data_source = cfg["hawkes_fit"]["data_source"]
    if data_source == "simulated":
        events = load_events(cfg)[["date"]].copy()
        events["event_count"] = 1
        return events, data_source
    if data_source != "real":
        raise ValueError(
            "hawkes_fit.data_source must be simulated or real, "
            f"got {data_source!r}"
        )

    path = REPO_ROOT / cfg["real_data"]["normalized_events_file"]
    events = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "event_count", "event_type"}
    missing = sorted(required - set(events.columns))
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {missing}")
    events = events[
        events["event_type"] == cfg["hawkes_fit"]["real_event_type"]
    ].copy()
    events["event_count"] = pd.to_numeric(
        events["event_count"],
        errors="coerce",
    )
    events = events.dropna(subset=["date", "event_count"])
    if events.empty:
        raise ValueError("No real rows match hawkes_fit.real_event_type")
    if not events["event_count"].eq(1).all():
        raise AggregateTemporalResolutionError(
            "real EMS data contains weekly aggregate event_count weights, "
            "not exact individual event timestamps; continuous-time Hawkes "
            "MLE would be misleading"
        )
    return events[["date", "event_count"]], data_source


def fit_hawkes(t: np.ndarray, config: dict):
    n_events = len(t)
    horizon = t.max()

    def neg_log_likelihood(free_params):
        log_mu, logit_n, log_beta = free_params
        mu = np.exp(log_mu)
        n = 1 / (1 + np.exp(-logit_n))
        beta = np.exp(log_beta)
        alpha = n * beta

        recursive_intensity = np.zeros(n_events)
        for i in range(1, n_events):
            recursive_intensity[i] = np.exp(
                -beta * (t[i] - t[i - 1])
            ) * (recursive_intensity[i - 1] + 1)

        intensity = np.clip(
            mu + alpha * recursive_intensity,
            1e-10,
            None,
        )
        log_likelihood_sum = np.sum(np.log(intensity))
        compensator = mu * horizon + alpha * np.sum(
            (1 - np.exp(-beta * (horizon - t))) / beta
        )
        return -(log_likelihood_sum - compensator)

    bounds = [
        tuple(config["optimizer_bounds"][name])
        for name in ("log_mu", "logit_n", "log_beta")
    ]
    init = [
        np.log(n_events / horizon * 0.6),
        np.log(0.4 / 0.6),
        np.log(0.5),
    ]
    rng_opt = np.random.default_rng(config["optimizer_seed"])
    starts = [init] + [
        [
            np.log(rng_opt.uniform(0.1, 5)),
            np.log(
                rng_opt.uniform(0.05, 0.9)
                / (1 - rng_opt.uniform(0.05, 0.9))
            ),
            np.log(rng_opt.uniform(0.05, 3)),
        ]
        for _ in range(config["n_multistart_restarts"])
    ]

    best_result, best_nll = None, np.inf
    for start in starts:
        result = minimize(
            neg_log_likelihood,
            start,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-10},
        )
        if result.fun < best_nll:
            best_nll, best_result = result.fun, result
    return best_result, best_nll, horizon


def write_not_fitted(cfg: dict, reason: str) -> None:
    path = outputs_path(cfg, "hawkes_fit_results_real.txt")
    path.write_text(
        "data_source=real\n"
        "status=not_fitted\n"
        f"event_type={cfg['hawkes_fit']['real_event_type']}\n"
        f"reason={reason}\n",
        encoding="utf-8",
    )
    log.warning("Real Hawkes fit not run: %s", reason)
    log.info("Wrote limitation record to %s", path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-source",
        choices=["simulated", "real"],
        help="Override hawkes_fit.data_source for this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config()
    if args.data_source:
        cfg["hawkes_fit"]["data_source"] = args.data_source
    hawkes_cfg = cfg["hawkes_fit"]
    try:
        events, data_source = load_hawkes_events(cfg)
    except AggregateTemporalResolutionError as exc:
        write_not_fitted(cfg, str(exc))
        return 0

    events = events.sort_values("date").reset_index(drop=True)
    start = events.date.min()
    t = ((events.date - start).dt.total_seconds() / 86400.0).to_numpy()
    if len(t) < 2 or t.max() <= 0:
        raise ValueError("At least two distinct event times are required")

    result, best_nll, horizon = fit_hawkes(t, hawkes_cfg)
    log_mu_hat, logit_n_hat, log_beta_hat = result.x
    mu_hat = np.exp(log_mu_hat)
    branching_ratio = 1 / (1 + np.exp(-logit_n_hat))
    beta_hat = np.exp(log_beta_hat)
    alpha_hat = branching_ratio * beta_hat

    log.info(
        "=== Hawkes Process MLE Fit (%s citywide temporal, multi-start) ===",
        data_source,
    )
    log.info("Converged: %s, best NLL: %.2f", result.success, best_nll)
    log.info(
        "mu=%.4f/day alpha=%.4f beta=%.4f/day",
        mu_hat,
        alpha_hat,
        beta_hat,
    )
    log.info(
        "Implied excitation half-life: %.2f days",
        np.log(2) / beta_hat,
    )
    log.info("Branching ratio n = alpha/beta: %.3f", branching_ratio)

    expected_total = (
        (mu_hat * horizon) / (1 - branching_ratio)
        if branching_ratio < 1
        else np.nan
    )
    log.info(
        "Estimated total events (mu*T/(1-n)): %.0f vs observed %d",
        expected_total,
        len(t),
    )

    output_name = (
        "hawkes_fit_results.txt"
        if data_source == "simulated"
        else "hawkes_fit_results_real.txt"
    )
    output = outputs_path(cfg, output_name)
    with output.open("w", encoding="utf-8") as handle:
        handle.write(f"data_source={data_source}\nstatus=fitted\n")
        handle.write(
            f"mu={mu_hat:.6f}\nalpha={alpha_hat:.6f}\n"
            f"beta={beta_hat:.6f}\n"
        )
        handle.write(f"branching_ratio={branching_ratio:.6f}\n")
        if data_source == "simulated":
            true_n = cfg["simulation"]["branching_ratio"]
            handle.write(f"true_branching_ratio={true_n:.6f}\n")
            handle.write(
                "model=citywide_temporal_only "
                "(spatial mark dropped -- see script docstring)\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
