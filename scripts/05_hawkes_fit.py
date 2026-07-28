"""
Fit a TEMPORAL (citywide-aggregate) Hawkes process to recover the
branching ratio (background vs. contagion split) from event timestamps.

DESIGN NOTE: an earlier version of this script used a marked
spatio-temporal kernel (time decay x normalized 2D spatial decay). Despite
fixing two real bugs along the way (asymmetric distance function, a
non-recursive event generator that made the simulated data not actually
match a stationary Hawkes process), the spatio-temporal MLE still showed
an unresolved bias toward alpha=0 even on a clean, favorable synthetic
validation case -- confirmed to be a problem with that specific
implementation, not a fundamental identifiability wall (a pure temporal
version of the exact same validation case recovers the true parameters
well). Rather than keep hand-debugging 2D kernel normalization, this
version drops the spatial mark and fits contagion vs. background from
timestamps alone, citywide. Space is already handled separately by the
Moran's I / Getis-Ord Gi* and diff-in-differences steps -- this script
answers a narrower, cleaner question: is there real temporal contagion
in the citywide arrival process at all?
"""
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import load_config, get_logger, load_events, outputs_path

log = get_logger(__name__)
cfg = load_config()
sim = cfg["simulation"]
hf_cfg = cfg["hawkes_fit"]

events = load_events(cfg).sort_values("date").reset_index(drop=True)
t0 = events.date.min()
t = ((events.date - t0).dt.total_seconds() / 86400.0).values
T = t.max()
n_events = len(t)


def neg_log_likelihood(free_params):
    log_mu, logit_n, log_beta = free_params
    mu = np.exp(log_mu)
    n = 1 / (1 + np.exp(-logit_n))
    beta = np.exp(log_beta)
    alpha = n * beta

    # recursive intensity computation (standard efficient exponential-kernel trick)
    R = np.zeros(n_events)
    for i in range(1, n_events):
        R[i] = np.exp(-beta * (t[i] - t[i - 1])) * (R[i - 1] + 1)

    lam = np.clip(mu + alpha * R, 1e-10, None)
    log_lik_sum = np.sum(np.log(lam))
    compensator = mu * T + alpha * np.sum((1 - np.exp(-beta * (T - t))) / beta)
    return -(log_lik_sum - compensator)


bounds = [(-3, 3), (-8, 8), (-4, 3)]
init = [np.log(n_events / T * 0.6), np.log(0.4 / 0.6), np.log(0.5)]
rng_opt = np.random.default_rng(hf_cfg.get("n_multistart_restarts", 8))
starts = [init] + [
    [np.log(rng_opt.uniform(0.1, 5)),
     np.log(rng_opt.uniform(0.05, 0.9) / (1 - rng_opt.uniform(0.05, 0.9))),
     np.log(rng_opt.uniform(0.05, 3))]
    for _ in range(hf_cfg["n_multistart_restarts"])
]

best_result, best_nll = None, np.inf
for s in starts:
    r = minimize(neg_log_likelihood, s, method="L-BFGS-B", bounds=bounds,
                 options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-10})
    if r.fun < best_nll:
        best_nll, best_result = r.fun, r

result = best_result
log_mu_hat, logit_n_hat, log_beta_hat = result.x
mu_hat = np.exp(log_mu_hat)
branching_ratio = 1 / (1 + np.exp(-logit_n_hat))
beta_hat = np.exp(log_beta_hat)
alpha_hat = branching_ratio * beta_hat

log.info("=== Hawkes Process MLE Fit (citywide temporal, multi-start) ===")
log.info(f"Converged: {result.success}, best NLL: {best_nll:.2f}")
log.info(f"mu={mu_hat:.4f}/day  alpha={alpha_hat:.4f}  beta={beta_hat:.4f}/day")
log.info(f"Implied excitation half-life: {np.log(2)/beta_hat:.2f} days")
log.info(f"Branching ratio n = alpha/beta: {branching_ratio:.3f}")

expected_total = (mu_hat * T) / (1 - branching_ratio) if branching_ratio < 1 else np.nan
log.info(f"Estimated total events (mu*T/(1-n)): {expected_total:.0f} vs observed {n_events}")

true_n = sim["branching_ratio"]
log.info(f"True simulation branching ratio: {true_n} | Recovered: {branching_ratio:.3f} | "
         f"Error: {abs(branching_ratio - true_n):.3f}")

if branching_ratio > 0.35:
    log.info(f"POLICY READ: ~{branching_ratio*100:.0f}% of events plausibly contagion-driven -- "
             "argues for rapid-response alerting alongside permanent infrastructure.")
else:
    log.info(f"POLICY READ: contagion effect modest (~{branching_ratio*100:.0f}%) -- "
             "most events track background/geographic risk, favoring infrastructure placement decisions.")

with open(outputs_path(cfg, "hawkes_fit_results.txt"), "w") as f:
    f.write(f"mu={mu_hat:.6f}\nalpha={alpha_hat:.6f}\nbeta={beta_hat:.6f}\n")
    f.write(f"branching_ratio={branching_ratio:.6f}\ntrue_branching_ratio={true_n:.6f}\n")
    f.write("model=citywide_temporal_only (spatial mark dropped -- see script docstring)\n")
