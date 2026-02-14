from dataclasses import dataclass
from datetime import datetime


# EPA-style breakpoints for PM2.5 (µg/m³) -> AQI (0..500)
_PM25_AQI = [
    (0.0, 12.0, 0, 50, "Good"),
    (12.1, 35.4, 51, 100, "Moderate"),
    (35.5, 55.4, 101, 150, "Unhealthy for Sensitive Groups"),
    (55.5, 150.4, 151, 200, "Unhealthy"),
    (150.5, 250.4, 201, 300, "Very Unhealthy"),
    (250.5, 500.4, 301, 500, "Hazardous"),
]


@dataclass
class RiskOutput:
    aqi: int
    category: str
    risk_score: int
    confidence: float   # 0..1
    trend_label: str    # rising/falling/stable


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def aqi_from_pm25(pm: float):
    if pm is None:
        return 0, "—"
    pm = float(pm)

    for c_low, c_high, i_low, i_high, label in _PM25_AQI:
        if c_low <= pm <= c_high:
            aqi = (i_high - i_low) / (c_high - c_low) * (pm - c_low) + i_low
            return int(round(aqi)), label

    # clamp above top breakpoint
    return 500, "Hazardous"


def pm_norm(pm25: float):
    """
    Normalize relative to WHO 24h guideline ~15 µg/m³.
    We cap at 4x WHO => 1.0
    """
    if pm25 is None:
        return 0.0
    x = float(pm25) / 15.0          # 1.0 means WHO guideline
    return _clamp01(x / 4.0)        # 4x WHO => 1.0


def stagnation_score(wind_m_s: float | None, pressure_hpa: float | None):
    """
    Higher when wind is low AND pressure is high/stable.
    Returns 0..1
    """
    if wind_m_s is None or pressure_hpa is None:
        return 0.4

    wind = float(wind_m_s)
    pres = float(pressure_hpa)

    wind_component = _clamp01((2.5 - wind) / 2.5)     # wind <=0 =>1, wind>=2.5 =>0
    pres_component = _clamp01((pres - 1012) / 18.0)   # ~1012=>0, ~1030=>1

    return _clamp01(0.65 * wind_component + 0.35 * pres_component)


def trend_score(pm_series: list[float]):
    """
    Slope proxy over last N points: (last-first)/max(10, |first|+10)
    Returns (score 0..1, label)
    """
    if not pm_series or len(pm_series) < 2:
        return 0.5, "stable"

    first = float(pm_series[0])
    last = float(pm_series[-1])
    delta = last - first

    denom = max(10.0, abs(first) + 10.0)
    raw = delta / denom                 # can be negative/positive
    score = _clamp01((raw + 0.7) / 1.4) # map roughly [-0.7..+0.7] to [0..1]

    if delta > 3:
        label = "rising"
    elif delta < -3:
        label = "falling"
    else:
        label = "stable"

    return score, label


def seasonality(month: int):
    # winter prior (Nov–Mar)
    return 0.75 if month in (11, 12, 1, 2, 3) else 0.25


def confidence_score(data_age_minutes: float, coverage_ratio: float, forecast_stability: float):
    """
    freshness + coverage + forecast stability (0..1)
    """
    freshness = _clamp01(1.0 - (float(data_age_minutes) / 180.0))  # 0 after 3h
    coverage = _clamp01(float(coverage_ratio))
    stability = _clamp01(float(forecast_stability))

    return _clamp01(0.5 * freshness + 0.35 * coverage + 0.15 * stability)


def risk_index(pm25: float, stag: float, trend: float, seas: float):
    """
    Explainable formula:
    R = w1*PM_norm + w2*Stagnation + w3*Trend + w4*Seasonality
    """
    w1, w2, w3, w4 = 0.55, 0.20, 0.15, 0.10
    r = (w1 * pm_norm(pm25) + w2 * _clamp01(stag) + w3 * _clamp01(trend) + w4 * _clamp01(seas))
    return int(round(_clamp01(r) * 100))


def compute_all(
    pm25: float,
    wind: float | None,
    pressure: float | None,
    pm_series: list[float],
    data_age_minutes: float,
    coverage_ratio: float,
    forecast_stability: float
) -> RiskOutput:
    aqi, cat = aqi_from_pm25(pm25)
    t_score, t_label = trend_score(pm_series)
    stag = stagnation_score(wind, pressure)
    seas = seasonality(datetime.now().month)
    risk = risk_index(pm25, stag, t_score, seas)
    conf = confidence_score(data_age_minutes, coverage_ratio, forecast_stability)

    return RiskOutput(
        aqi=aqi,
        category=cat,
        risk_score=risk,
        confidence=conf,
        trend_label=t_label,
    )
