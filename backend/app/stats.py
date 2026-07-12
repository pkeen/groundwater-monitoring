"""Pure statistics helpers used by the nightly refresh job. No I/O here -
everything takes plain values/dates in and returns plain dicts out, so it's
easy to unit-test and reason about independently of the database layer.
"""
import statistics
from datetime import datetime, timezone

import pymannkendall as mk

MIN_TREND_POINTS = 8
STALE_DAYS_QUALITY = 730  # ~2 years with no new chemistry sample
STALE_DAYS_LEVEL = 120  # level stations are usually monitored much more often
SPARSE_COUNT_THRESHOLD = 5


def _parse_date(d: str) -> datetime:
    dt = datetime.fromisoformat(d)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summarize(values: list[float]) -> dict:
    if not values:
        return {
            "count": 0,
            "min_value": None,
            "max_value": None,
            "mean_value": None,
            "median_value": None,
            "stddev_value": None,
        }
    return {
        "count": len(values),
        "min_value": min(values),
        "max_value": max(values),
        "mean_value": statistics.fmean(values),
        "median_value": statistics.median(values),
        "stddev_value": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def sen_slope_per_year(dates: list[str], values: list[float]) -> float | None:
    """Sen's slope generalized to irregularly-spaced samples: median of the
    pairwise rate of change (value/year) across all point pairs. Standard
    Mann-Kendall implementations assume evenly-spaced samples, which water
    quality/level data never is, so this is computed by hand rather than
    trusting a library's built-in slope."""
    n = len(values)
    if n < 2:
        return None
    years = [_parse_date(d).timestamp() / (365.25 * 86400) for d in dates]
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            dt = years[j] - years[i]
            if dt > 0:
                slopes.append((values[j] - values[i]) / dt)
    if not slopes:
        return None
    return statistics.median(slopes)


def trend(dates: list[str], values: list[float]) -> dict:
    """Mann-Kendall trend test (direction + significance) plus a Sen's-slope
    based rate of change per year. This is the standard technique EA/CEH
    hydrogeologists use for borehole trend detection."""
    if len(values) < MIN_TREND_POINTS:
        return {"trend_direction": "insufficient_data", "trend_slope_per_year": None, "trend_p_value": None}

    paired = sorted(zip(dates, values), key=lambda p: p[0])
    sorted_dates = [p[0] for p in paired]
    sorted_values = [p[1] for p in paired]

    result = mk.original_test(sorted_values)
    slope = sen_slope_per_year(sorted_dates, sorted_values)
    return {
        "trend_direction": result.trend,
        "trend_slope_per_year": slope,
        "trend_p_value": float(result.p),
    }


def modified_z_scores(values: list[float]) -> list[float]:
    """Median-Absolute-Deviation based z-score: robust to the skewed,
    non-detect-heavy distributions typical of water quality data (unlike a
    mean/stddev z-score, which skewed data with a few genuine extreme values
    can wash out)."""
    if not values:
        return []
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    if mad == 0:
        return [0.0] * len(values)
    return [0.6745 * (v - med) / mad for v in values]


def detect_outliers(values: list[float], threshold: float = 3.5) -> list[bool]:
    scores = modified_z_scores(values)
    return [abs(s) > threshold for s in scores]


def data_quality_flags(
    *,
    count: int,
    censored_count: int,
    latest_date: str | None,
    stale_days_threshold: int,
) -> dict:
    flags = {
        "is_sparse": count < SPARSE_COUNT_THRESHOLD,
        "is_stale": False,
        "censored_fraction": (censored_count / count) if count else 0.0,
    }

    if latest_date:
        days_since = (datetime.now(timezone.utc) - _parse_date(latest_date)).days
        flags["is_stale"] = days_since > stale_days_threshold
        flags["days_since_latest"] = days_since
    else:
        flags["days_since_latest"] = None

    if count == 0:
        label = "No data"
    elif flags["is_stale"]:
        label = "Stale"
    elif flags["is_sparse"]:
        label = "Limited data"
    elif flags["censored_fraction"] > 0.5:
        label = "Mostly non-detect"
    else:
        label = "Good"

    flags["label"] = label
    return flags
