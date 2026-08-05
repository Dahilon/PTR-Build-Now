"""Rebuild every checked-in analysis and decision-support artifact.

The public-data download remains an explicit one-time prerequisite because it
uses external APIs. This runner consumes the existing ``data/real`` files and
executes both supported analysis modes without editing config.yaml between
runs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ["scripts/01_generate_tracts_and_sites.py"],
    ["scripts/02_simulate_events.py"],
    ["scripts/03_spatial_autocorrelation.py", "--geography-level", "tract"],
    ["scripts/04_event_study_diff_in_diff.py"],
    ["scripts/05_hawkes_fit.py", "--data-source", "simulated"],
    ["scripts/06_load_real_data.py"],
    ["scripts/07_real_time_series_analysis.py"],
    ["scripts/03_spatial_autocorrelation.py", "--geography-level", "neighborhood"],
    ["scripts/05_hawkes_fit.py", "--data-source", "real"],
    ["scripts/08_build_operational_workflow.py"],
]


def main() -> int:
    for index, arguments in enumerate(STEPS, start=1):
        print(f"\n[{index}/{len(STEPS)}] {' '.join(arguments)}", flush=True)
        subprocess.run(
            [sys.executable, *arguments],
            cwd=REPO_ROOT,
            check=True,
        )
    print("\nPipeline complete: simulated validation, real analysis, and operational outputs rebuilt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
