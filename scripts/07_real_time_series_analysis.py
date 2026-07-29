"""
Time-series analysis suited to the real public aggregate data.

Unlike point-process and spatial methods, these analyses preserve the source
resolution: weekly citywide EMS counts and monthly citywide death counts.
Weather is aggregated over each complete reporting period. Four partial EMS
periods at calendar-year boundaries are retained in the normalized file but
excluded from inference so 3-6 day counts are not compared with full weeks.
"""
from __future__ import annotations

import calendar
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import pearsonr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import REPO_ROOT, get_logger, load_config, outputs_path

log = get_logger(__name__)

EMS_EVENT_TYPE = "ems_overdose_911_response"
DEATH_EVENT_TYPE = "overdose_death"
MONTH_NAMES = list(calendar.month_abbr)[1:]


def load_inputs(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    real_cfg = cfg["real_data"]
    events_path = REPO_ROOT / real_cfg["normalized_events_file"]
    weather_path = (
        REPO_ROOT / real_cfg["input_dir"] / real_cfg["weather_file"]
    )
    events = pd.read_csv(events_path, parse_dates=["date"])
    weather = pd.read_csv(weather_path, parse_dates=["date"])

    event_columns = {"date", "event_count", "event_type"}
    weather_columns = {
        "date",
        "temperature_mean_c",
        "precipitation_mm",
    }
    missing_events = sorted(event_columns - set(events.columns))
    missing_weather = sorted(weather_columns - set(weather.columns))
    if missing_events:
        raise ValueError(
            f"{events_path.name} is missing required columns: {missing_events}"
        )
    if missing_weather:
        raise ValueError(
            f"{weather_path.name} is missing required columns: "
            f"{missing_weather}"
        )
    return events, weather


def prepare_weekly_series(
    events: pd.DataFrame,
    weather: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return complete seven-day EMS periods and all excluded partial rows."""
    weekly = events[events["event_type"] == EMS_EVENT_TYPE].copy()
    weekly = weekly.sort_values("date").reset_index(drop=True)
    weekly = weekly.drop(
        columns=[
            "temperature_min_c",
            "temperature_mean_c",
            "temperature_max_c",
            "precipitation_mm",
        ],
        errors="ignore",
    )
    weekly["next_date"] = weekly.groupby(weekly["date"].dt.year)[
        "date"
    ].shift(-1)
    year_end = pd.to_datetime(
        (weekly["date"].dt.year + 1).astype(str) + "-01-01"
    )
    weekly["period_end"] = weekly["next_date"].fillna(year_end)
    weekly["period_days"] = (
        weekly["period_end"] - weekly["date"]
    ).dt.days

    weather_rows = []
    for row in weekly.itertuples():
        period = weather[
            (weather["date"] >= row.date)
            & (weather["date"] < row.period_end)
        ]
        weather_rows.append(
            {
                "weather_days": len(period),
                "temperature_mean_c": period["temperature_mean_c"].mean(),
                "precipitation_mm": period["precipitation_mm"].sum(
                    min_count=1
                ),
            }
        )
    weekly = pd.concat(
        [weekly, pd.DataFrame(weather_rows)],
        axis=1,
    )
    weekly["month"] = weekly["date"].dt.month
    weekly["elapsed_periods"] = (
        (weekly["date"] - weekly["date"].min()).dt.days / 7.0
    )

    complete = weekly[
        (weekly["period_days"] == 7)
        & (weekly["weather_days"] == 7)
        & weekly[
            ["event_count", "temperature_mean_c", "precipitation_mm"]
        ].notna().all(axis=1)
    ].copy()
    excluded = weekly.loc[~weekly.index.isin(complete.index)].copy()
    return complete, excluded


def prepare_monthly_series(
    events: pd.DataFrame,
    weather: pd.DataFrame,
) -> pd.DataFrame:
    monthly = events[events["event_type"] == DEATH_EVENT_TYPE].copy()
    monthly = monthly.sort_values("date").reset_index(drop=True)
    monthly["period"] = monthly["date"].dt.to_period("M")

    daily = weather.copy()
    daily["period"] = daily["date"].dt.to_period("M")
    monthly_weather = daily.groupby("period", as_index=False).agg(
        weather_days=("date", "size"),
        temperature_mean_c=("temperature_mean_c", "mean"),
        precipitation_mm=("precipitation_mm", "sum"),
    )
    monthly = monthly.drop(
        columns=["temperature_mean_c", "precipitation_mm"],
        errors="ignore",
    ).merge(monthly_weather, on="period", how="left", validate="one_to_one")
    monthly["month"] = monthly["date"].dt.month
    monthly["elapsed_periods"] = np.arange(len(monthly), dtype=float)
    monthly["expected_days"] = monthly["date"].dt.days_in_month
    return monthly[
        (monthly["weather_days"] == monthly["expected_days"])
        & monthly[
            ["event_count", "temperature_mean_c", "precipitation_mm"]
        ].notna().all(axis=1)
    ].copy()


def trend_analysis(
    frame: pd.DataFrame,
    *,
    unit: str,
    hac_lags: int,
) -> dict:
    design = sm.add_constant(frame["elapsed_periods"])
    model = sm.OLS(frame["event_count"], design).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": hac_lags},
    )
    slope = float(model.params["elapsed_periods"])
    pvalue = float(model.pvalues["elapsed_periods"])
    confidence = model.conf_int().loc["elapsed_periods"]
    return {
        "n": len(frame),
        "unit": unit,
        "slope": slope,
        "pvalue": pvalue,
        "ci_low": float(confidence.iloc[0]),
        "ci_high": float(confidence.iloc[1]),
        "r_squared": float(model.rsquared),
    }


def seasonality_analysis(frame: pd.DataFrame) -> dict:
    model = smf.ols("event_count ~ C(month)", data=frame).fit()
    month_means = frame.groupby("month")["event_count"].mean()
    return {
        "n": len(frame),
        "f_statistic": float(model.fvalue),
        "pvalue": float(model.f_pvalue),
        "highest_month": int(month_means.idxmax()),
        "highest_mean": float(month_means.max()),
        "lowest_month": int(month_means.idxmin()),
        "lowest_mean": float(month_means.min()),
    }


def weather_analysis(
    frame: pd.DataFrame,
    *,
    hac_lags: int,
) -> dict:
    temp_r, temp_p = pearsonr(
        frame["event_count"],
        frame["temperature_mean_c"],
    )
    precip_r, precip_p = pearsonr(
        frame["event_count"],
        frame["precipitation_mm"],
    )
    design = sm.add_constant(
        frame[["temperature_mean_c", "precipitation_mm"]]
    )
    model = sm.OLS(frame["event_count"], design).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": hac_lags},
    )
    return {
        "n": len(frame),
        "temperature_r": float(temp_r),
        "temperature_p": float(temp_p),
        "precipitation_r": float(precip_r),
        "precipitation_p": float(precip_p),
        "temperature_coefficient": float(
            model.params["temperature_mean_c"]
        ),
        "temperature_coefficient_p": float(
            model.pvalues["temperature_mean_c"]
        ),
        "precipitation_coefficient": float(
            model.params["precipitation_mm"]
        ),
        "precipitation_coefficient_p": float(
            model.pvalues["precipitation_mm"]
        ),
        "r_squared": float(model.rsquared),
        "model_pvalue": float(model.f_pvalue),
    }


def significance_statement(pvalue: float) -> str:
    return (
        "statistically significant at p<0.05"
        if pvalue < 0.05
        else "not statistically significant at p<0.05"
    )


def pvalue_text(pvalue: float) -> str:
    return "p<0.0001" if pvalue < 0.0001 else f"p={pvalue:.4f}"


def trend_text(label: str, result: dict) -> str:
    direction = "increase" if result["slope"] > 0 else "decrease"
    if result["pvalue"] < 0.05:
        finding = f"a statistically significant {direction}"
    else:
        finding = f"no statistically significant trend ({direction} estimate)"
    return (
        f"{label}: {finding}. Slope={result['slope']:+.3f} counts per "
        f"{result['unit']} (95% CI {result['ci_low']:+.3f} to "
        f"{result['ci_high']:+.3f}), {pvalue_text(result['pvalue'])}, "
        f"R²={result['r_squared']:.3f}, n={result['n']}."
    )


def seasonality_text(label: str, result: dict) -> str:
    evidence = (
        "evidence of month-of-year differences"
        if result["pvalue"] < 0.05
        else "no statistically significant month-of-year pattern"
    )
    return (
        f"{label}: {evidence}; omnibus F={result['f_statistic']:.3f}, "
        f"{pvalue_text(result['pvalue'])}, n={result['n']}. "
        f"Highest raw monthly "
        f"mean={MONTH_NAMES[result['highest_month'] - 1]} "
        f"({result['highest_mean']:.1f}); lowest="
        f"{MONTH_NAMES[result['lowest_month'] - 1]} "
        f"({result['lowest_mean']:.1f})."
    )


def weather_text(label: str, result: dict) -> list[str]:
    return [
        (
            f"{label} temperature correlation: r="
            f"{result['temperature_r']:+.3f}, "
            f"{pvalue_text(result['temperature_p'])}, n={result['n']} "
            f"({significance_statement(result['temperature_p'])})."
        ),
        (
            f"{label} precipitation correlation: r="
            f"{result['precipitation_r']:+.3f}, "
            f"{pvalue_text(result['precipitation_p'])}, n={result['n']} "
            f"({significance_statement(result['precipitation_p'])})."
        ),
        (
            f"{label} joint weather regression: temperature coefficient="
            f"{result['temperature_coefficient']:+.3f} "
            f"({pvalue_text(result['temperature_coefficient_p'])}); "
            f"precipitation coefficient="
            f"{result['precipitation_coefficient']:+.3f} "
            f"({pvalue_text(result['precipitation_coefficient_p'])}); "
            f"R²={result['r_squared']:.3f}, model "
            f"{pvalue_text(result['model_pvalue'])}."
        ),
    ]


def build_report(
    weekly: pd.DataFrame,
    excluded_weekly: pd.DataFrame,
    monthly: pd.DataFrame,
) -> str:
    weekly_trend = trend_analysis(weekly, unit="week", hac_lags=4)
    monthly_trend = trend_analysis(monthly, unit="month", hac_lags=1)
    weekly_seasonality = seasonality_analysis(weekly)
    monthly_seasonality = seasonality_analysis(monthly)
    weekly_weather = weather_analysis(weekly, hac_lags=4)
    monthly_weather = weather_analysis(monthly, hac_lags=1)

    lines = [
        "REAL SF AGGREGATE TIME-SERIES ANALYSIS (2024-2025)",
        "=" * 55,
        "",
        "DATA",
        f"- EMS source rows: {len(weekly) + len(excluded_weekly)} published "
        f"weekly aggregates; {len(weekly)} complete seven-day periods used.",
        f"- Excluded partial EMS periods: {len(excluded_weekly)} "
        f"({', '.join(excluded_weekly['date'].dt.strftime('%Y-%m-%d'))}).",
        f"- Death source rows: {len(monthly)} monthly aggregates used.",
        "- Weather exposure is period-aligned: mean temperature and total "
        "precipitation over each complete week/month.",
        "",
        "TREND",
        trend_text("Weekly overdose-related EMS calls", weekly_trend),
        trend_text("Monthly preliminary overdose deaths", monthly_trend),
        "",
        "SEASONALITY",
        seasonality_text("Weekly EMS calls", weekly_seasonality),
        seasonality_text("Monthly deaths", monthly_seasonality),
        (
            "Day-of-week seasonality was not tested. The EMS dates are "
            "administrative week-start anchors, not incident weekdays."
        ),
        "",
        "WEATHER ASSOCIATION",
        *weather_text("Weekly EMS calls", weekly_weather),
        *weather_text("Monthly deaths", monthly_weather),
        "",
        "INTERPRETATION AND CAVEATS",
        (
            "- These are associations in citywide aggregate counts, not "
            "causal weather effects and not incident-level risk estimates."
        ),
        (
            "- The public data provides only 106 weekly source rows "
            "(102 complete weeks used) and 24 monthly rows. The death "
            "analysis in particular has low statistical power."
        ),
        (
            "- Month-of-year death comparisons have only two observations "
            "per month and are exploratory."
        ),
        (
            "- Temperature, season, and calendar time are related. The "
            "weather coefficients can reflect seasonality or other omitted "
            "changes; statistical significance does not establish causation."
        ),
        (
            "- Linear trend and weather-regression standard errors use HAC "
            "corrections, but two years is too short to characterize "
            "long-run cycles or rule out unmeasured confounding."
        ),
        (
            "- Public aggregate resolution prevents neighborhood cluster "
            "detection, event-level Hawkes fitting, and linkage to site "
            "openings; those require internal city incident/site data."
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    cfg = load_config()
    events, weather = load_inputs(cfg)
    weekly, excluded_weekly = prepare_weekly_series(events, weather)
    monthly = prepare_monthly_series(events, weather)
    if len(weekly) < 24 or len(monthly) < 12:
        raise ValueError(
            "Insufficient complete aggregate periods for time-series analysis"
        )

    report = build_report(weekly, excluded_weekly, monthly)
    output = outputs_path(cfg, "real_time_series_results.txt")
    output.write_text(report, encoding="utf-8")
    print(report, end="")
    log.info("Saved real time-series findings to %s", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
