from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from datetime import datetime, timedelta
import requests

from .services.openaq_client import OpenAQClient, pick_pm25_sensor_from_latest
from .services.openweather_client import OpenWeatherClient
from .services.risk_engine import compute_all

CITIES = {
    "almaty": {"display": "Almaty", "lat": 43.238949, "lon": 76.889709},
    "astana": {"display": "Astana", "lat": 51.169392, "lon": 71.449074},
}

openaq = OpenAQClient(settings.OPENAQ_BASE_URL, settings.OPENAQ_API_KEY)
ow = OpenWeatherClient(settings.OPENWEATHER_BASE_URL, settings.OPENWEATHER_API_KEY) if getattr(settings, "OPENWEATHER_API_KEY", None) else None


def _user_profile_payload(request):
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        p = request.user.profile
        return {
            "sensitivity": p.sensitivity,
            "age_group": p.age_group,
            "activity": p.activity,
            "asthma_flag": p.asthma_flag,
            "other_resp_flag": p.other_resp_flag,
            "language": p.language,
        }
    return {"sensitivity": "normal", "age_group": "adult", "activity": "commute", "language": "ru"}


def _current_snapshot(city: str, lat: float | None = None, lon: float | None = None):
    city = (city or "almaty").lower()

    if lat is not None and lon is not None:
        city_display = "Selected point"
        _lat, _lon = float(lat), float(lon)
    else:
        c = CITIES.get(city, CITIES["almaty"])
        city_display = c["display"]
        _lat, _lon = c["lat"], c["lon"]

    # 1) nearest locations
    locations = openaq.locations_near(_lat, _lon, radius_m=12000, limit=10)
    if not locations:
        return None, {"error": "No OpenAQ locations nearby"}

    best_loc = locations[0]
    location_id = int(best_loc.get("id"))
    # location label
    loc_name = best_loc.get("name") or ""

    # 2) latest by location
    latest = openaq.location_latest(location_id)
    picked = pick_pm25_sensor_from_latest(latest)
    if not picked:
        return None, {"error": "No PM2.5 latest data for nearest location"}

    pm25_value, unit, sensor_id, dt_utc, picked_loc_name = picked
    if picked_loc_name:
        loc_name = picked_loc_name

    # 3) weather context
    weather = {"wind_m_s": None, "pressure_hpa": None, "temp_c": None}
    forecast_stability = 0.65
    if ow:
        w = ow.onecall(_lat, _lon)
        curw = (w or {}).get("current", {}) if isinstance(w, dict) else {}
        weather = {
            "wind_m_s": curw.get("wind_speed"),
            "pressure_hpa": curw.get("pressure"),
            "temp_c": curw.get("temp"),
        }
        ws = curw.get("wind_speed") or 0
        forecast_stability = 0.85 if ws < 6 else 0.65

    # 4) last 24h series for trend+coverage
    series = []
    coverage_ratio = 0.3
    try:
        hourly = openaq.sensor_hourly(int(sensor_id), hours=24) if sensor_id else []
        series = [float(x.get("value")) for x in hourly if x.get("value") is not None]
        coverage_ratio = min(1.0, len(series) / 24.0)
    except Exception:
        series = []
        coverage_ratio = 0.0

    # 5) data age (minutes) + local timestamp
    age_min = 180.0
    ts_local = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    try:
        if dt_utc:
            parsed = datetime.fromisoformat(dt_utc.replace("Z", "+00:00"))
            age_min = (timezone.now() - parsed).total_seconds() / 60.0
            ts_local = timezone.localtime(parsed).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    out = compute_all(
        pm25=float(pm25_value) if pm25_value is not None else 0.0,
        wind=weather["wind_m_s"],
        pressure=weather["pressure_hpa"],
        pm_series=series[-12:] if len(series) >= 12 else series,
        data_age_minutes=age_min,
        coverage_ratio=coverage_ratio,
        forecast_stability=forecast_stability,
    )

    payload = {
        "city": city,
        "city_display": city_display,
        "district": loc_name,
        "pm25_ug_m3": round(float(pm25_value), 1) if pm25_value is not None else None,
        "aqi": out.aqi,
        "category": out.category,
        "risk_score": out.risk_score,
        "trend_label": out.trend_label,
        "confidence": out.confidence,
        "timestamp_local": ts_local,
        "source": "OpenAQ" + (" + OpenWeather" if ow else ""),
        "sensor_id": sensor_id,
        "weather": weather,
    }
    return payload, None


@require_GET
def cities(request):
    return JsonResponse({"results": [{"id": k, "name": v["display"]} for k, v in CITIES.items()]})


@require_GET
def stations_near(request):
    lat = float(request.GET.get("lat"))
    lon = float(request.GET.get("lon"))
    locations = openaq.locations_near(lat, lon, radius_m=10000, limit=12)

    out = []
    for loc in locations:
        coords = (loc.get("coordinates") or {})
        out.append({
            "id": loc.get("id"),
            "name": loc.get("name"),
            "lat": coords.get("latitude"),
            "lon": coords.get("longitude"),
        })
    return JsonResponse({"results": out})


@require_GET
def aq_series24h(request):
    sensor_id = int(request.GET.get("sensor_id"))
    results = openaq.sensor_hourly(sensor_id, hours=24)

    labels, values = [], []
    for r in results:
        dt_obj = r.get("datetimeFrom") or r.get("datetime") or {}
        dt = None
        if isinstance(dt_obj, dict):
            dt = dt_obj.get("local") or dt_obj.get("utc")
        if not dt and isinstance(r.get("date"), str):
            dt = r.get("date")

        if not dt or not isinstance(dt, str) or len(dt) < 16:
            continue

        labels.append(dt[11:16])  # HH:MM
        values.append(float(r.get("value") or 0))

    return JsonResponse({"labels": labels, "values": values})


@require_GET
def aq_current(request):
    city = (request.GET.get("city") or "almaty").lower()
    lat = request.GET.get("lat")
    lon = request.GET.get("lon")

    if lat and lon:
        payload, err = _current_snapshot(city, float(lat), float(lon))
    else:
        payload, err = _current_snapshot(city)

    if err:
        return JsonResponse(err, status=502)
    return JsonResponse(payload)


@require_GET
def aq_outlook(request):
    city = (request.GET.get("city") or "almaty").lower()
    hours = int(request.GET.get("hours") or 72)
    hours = max(1, min(hours, 72))

    snap, err = _current_snapshot(city)
    if err:
        return JsonResponse(err, status=502)

    c = CITIES.get(city, CITIES["almaty"])
    lat, lon = c["lat"], c["lon"]

    pm_now = float(snap.get("pm25_ug_m3") or 0.0)

    if ow:
        w = ow.onecall(lat, lon)
        hourly = (w or {}).get("hourly", [])[:hours] if isinstance(w, dict) else []
    else:
        hourly = [{"wind_speed": None, "pressure": None, "temp": None} for _ in range(hours)]

    results = []
    pm = pm_now

    for i, h in enumerate(hourly):
        wind = h.get("wind_speed")
        pres = h.get("pressure")
        temp = h.get("temp")

        # persistence + stagnation adjustment
        if wind is not None and pres is not None:
            if wind < 1.5 and pres > 1020:
                pm *= 1.03
            elif wind > 5.5:
                pm *= 0.90
            else:
                pm *= 0.99
        else:
            pm *= 0.995

        out = compute_all(
            pm25=pm,
            wind=wind,
            pressure=pres,
            pm_series=[pm_now, pm],
            data_age_minutes=30.0 + i * 5.0,
            coverage_ratio=0.7,
            forecast_stability=0.75 if wind is not None else 0.55,
        )

        ts = timezone.localtime(timezone.now() + timedelta(hours=i)).strftime("%m-%d %H:%M")

        results.append({
            "t": ts,
            "pm25": round(pm, 1),
            "aqi": out.aqi,
            "category": out.category,
            "risk": out.risk_score,
            "confidence": out.confidence,
            "weather": {"wind_m_s": wind, "pressure_hpa": pres, "temp_c": temp},
        })

    return JsonResponse({"city": city, "hours": hours, "results": results})


@require_GET
def geocode(request):
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"results": []})

    url = "https://nominatim.openstreetmap.org/search"
    params = {"format": "json", "q": q, "limit": 6}
    r = requests.get(url, params=params, headers={"User-Agent": "AuaGuardAI/1.0"}, timeout=15)
    r.raise_for_status()

    res = []
    for item in r.json():
        res.append({
            "display_name": item.get("display_name"),
            "lat": float(item.get("lat")),
            "lon": float(item.get("lon")),
        })
    return JsonResponse({"results": res})


@require_GET
def recommendation(request):
    from llm.client import LLMClient

    city = (request.GET.get("city") or "almaty").lower()
    profile = _user_profile_payload(request)

    cur, err = _current_snapshot(city)
    if err:
        return JsonResponse(err, status=502)

    payload = {
        "city": cur.get("city_display"),
        "district": cur.get("district") or "",
        "timestamp_local": cur.get("timestamp_local"),
        "pm25_ug_m3": cur.get("pm25_ug_m3"),
        "aqi": cur.get("aqi"),
        "category": cur.get("category"),
        "risk_score": cur.get("risk_score"),
        "confidence": cur.get("confidence"),
        "weather": cur.get("weather"),
        "profile": {
            "sensitivity": profile["sensitivity"],
            "age_group": profile["age_group"],
            "activity": profile["activity"],
        },
        "language": profile.get("language", "ru"),
    }

    llm = LLMClient.from_settings()
    text = llm.generate_recommendation(payload, mode="today")
    return JsonResponse({"text": text, "payload_used": payload})


@require_GET
def school_decision(request):
    from llm.client import LLMClient

    city = (request.GET.get("city") or "almaty").lower()
    profile = _user_profile_payload(request)
    profile["activity"] = "outdoor_sport"

    out = requests.get(f"http://127.0.0.1:8000/api/v1/aq/outlook?city={city}&hours=36", timeout=15).json()
    slots = out.get("results", [])

    pick = None
    for s in slots:
        t = s.get("t", "")
        if "07:" in t or "08:" in t or "06:" in t:
            pick = s
            break
    if not pick and slots:
        pick = slots[0]

    payload = {
        "city": CITIES.get(city, CITIES["almaty"])["display"],
        "district": "",
        "timestamp_local": pick.get("t") if pick else "",
        "pm25_ug_m3": pick.get("pm25") if pick else None,
        "aqi": pick.get("aqi") if pick else None,
        "category": pick.get("category") if pick else None,
        "risk_score": pick.get("risk") if pick else None,
        "confidence": pick.get("confidence") if pick else 0.5,
        "weather": pick.get("weather") if pick else {},
        "profile": {
            "sensitivity": profile["sensitivity"],
            "age_group": profile["age_group"],
            "activity": "outdoor_sport"
        },
        "language": profile.get("language", "ru"),
    }

    llm = LLMClient.from_settings()
    text = llm.generate_recommendation(payload, mode="school")

    decision = "Indoors"
    cat = (payload.get("category") or "").lower()
    risk = payload.get("risk_score") or 0

    if ("good" in cat or "moderate" in cat) and risk < 45:
        decision = "Outdoor OK"
    elif risk < 70:
        decision = "Caution"

    return JsonResponse({"decision": decision, "text": text, "payload_used": payload})
