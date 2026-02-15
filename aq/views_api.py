from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .services.openaq_client import OpenAQClient, OpenAQError, PM25_PARAMETER_ID
from .services.openweather_client import OpenWeatherClient
from .services.risk_engine import compute_all

CITIES = {
    "almaty": {"display": "Almaty", "lat": 43.238949, "lon": 76.889709},
    "astana": {"display": "Astana", "lat": 51.169392, "lon": 71.449074},
}

# Global clients (cheap; requests.Session reuse)
openaq = OpenAQClient(getattr(settings, "OPENAQ_BASE_URL", "https://api.openaq.org"), getattr(settings, "OPENAQ_API_KEY", ""))
ow = OpenWeatherClient(settings.OPENWEATHER_BASE_URL, settings.OPENWEATHER_API_KEY) if getattr(settings, "OPENWEATHER_API_KEY", "") else None


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


def _parse_iso(dt_str: str):
    if not dt_str:
        return None
    s = dt_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _extract_hour_point(row: dict):
    """
    OpenAQ /measurements/hourly returns rows with:
      - value
      - period.datetimeFrom.{utc, local}
      - coverage.percentCoverage
    We normalize it into (dt_str, value, coverage_ratio_0_1).
    """
    val = row.get("value")
    if val is None:
        return None

    period = row.get("period") or {}
    dt_from = period.get("datetimeFrom") or {}
    dt_str = dt_from.get("local") or dt_from.get("utc") or ""

    cov = row.get("coverage") or {}
    pct = cov.get("percentCoverage")
    cov_ratio = None
    if pct is not None:
        try:
            cov_ratio = float(pct) / 100.0
        except Exception:
            cov_ratio = None

    return dt_str, float(val), cov_ratio


def _current_snapshot(city: str, *, lat: float = None, lon: float = None):
    """
    Return (data, error). If OpenAQ fails but cached data exists, return cached data (stale=True).
    """
    city = (city or "almaty").lower()
    meta = CITIES.get(city, CITIES["almaty"])

    use_lat = float(lat) if lat is not None else float(meta["lat"])
    use_lon = float(lon) if lon is not None else float(meta["lon"])

    cache_key = f"aq:current:{city}:{round(use_lat,4)}:{round(use_lon,4)}"

    try:
        snap = openaq.pm25_latest_near(use_lat, use_lon, radius_m=9000, limit_locations=25)

        pm25 = float(snap["pm25_ug_m3"])
        sensor_id = snap.get("sensor_id")

        # Try to fetch 24h series for trend/confidence.
        series_vals = [pm25]
        coverage_ratio = 0.75
        try:
            hourly = openaq.sensor_hourly(int(sensor_id), hours=24) if sensor_id else []
            pts = [_extract_hour_point(r) for r in (hourly or [])]
            pts = [p for p in pts if p is not None]
            if pts:
                series_vals = [p[1] for p in pts]
                covs = [p[2] for p in pts if p[2] is not None]
                if covs:
                    coverage_ratio = sum(covs) / len(covs)
        except Exception:
            # not fatal for snapshot
            pass

        # Weather (optional)
        wind = None
        pres = None
        weather = {}
        if ow:
            try:
                w = ow.onecall(use_lat, use_lon)
                curw = (w.get("current") or {}) if isinstance(w, dict) else {}
                wind = curw.get("wind_speed")
                pres = curw.get("pressure")
                weather = {
                    "temp_c": curw.get("temp"),
                    "wind_m_s": wind,
                    "pressure_hpa": pres,
                }
            except Exception:
                weather = {}

        # Data age (minutes)
        dt_utc = _parse_iso(snap.get("timestamp_utc") or "")
        now_utc = timezone.now()
        data_age_minutes = 60.0
        ts_local = None
        if dt_utc:
            try:
                data_age_minutes = max(0.0, (now_utc - dt_utc).total_seconds() / 60.0)
                ts_local = timezone.localtime(dt_utc).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass

        out = compute_all(
            pm25=pm25,
            wind=wind,
            pressure=pres,
            pm_series=series_vals,
            data_age_minutes=data_age_minutes,
            coverage_ratio=coverage_ratio,
            forecast_stability=0.7,
        )

        data = {
            "city": city,
            "city_display": meta["display"],
            "district": meta.get("district", ""),
            "coords": {"lat": use_lat, "lon": use_lon},

            "pm25_ug_m3": round(pm25, 1),
            "unit": snap.get("unit") or "µg/m³",

            "aqi": out.aqi,
            "category": out.category,
            "risk_score": out.risk_score,
            "confidence": out.confidence,
            "trend": out.trend_label,

            "timestamp_utc": snap.get("timestamp_utc"),
            "timestamp_local": ts_local,

            "sensor_id": sensor_id,
            "location_id": snap.get("location_id"),
            "location_name": snap.get("location_name"),

            "source": "OpenAQ v3",
            "weather": weather,
        }

        cache.set(cache_key, data, 10 * 60)
        return data, None

    except Exception as e:
        cached = cache.get(cache_key)
        if cached:
            cached = dict(cached)
            cached["stale"] = True
            cached["source"] = "OpenAQ (cached)"
            cached["error"] = str(e)
            return cached, None

        hint = "Check OPENAQ_API_KEY (.env) and that you're using OpenAQ v3 endpoints."
        if isinstance(e, OpenAQError) and "missing" in str(e).lower():
            hint = "OPENAQ_API_KEY is missing. Add your OpenAQ v3 API key into .env as OPENAQ_API_KEY=..."
        return None, {"error": "OpenAQ unavailable", "detail": str(e), "hint": hint}


@require_GET
def cities(request):
    return JsonResponse({"results": [{"key": k, "display": v["display"]} for k, v in CITIES.items()]})


@require_GET
def stations_near(request):
    lat = request.GET.get("lat")
    lon = request.GET.get("lon")
    if not lat or not lon:
        return JsonResponse({"error": "lat and lon are required"}, status=400)

    radius = int(request.GET.get("radius") or 9000)
    limit = int(request.GET.get("limit") or 25)

    try:
        locations = openaq.locations_near(
            lat=float(lat),
            lon=float(lon),
            radius_m=radius,
            limit=limit,
            parameters_id=PM25_PARAMETER_ID,  # only stations that have PM2.5
        )
    except Exception as e:
        return JsonResponse({"error": "OpenAQ unavailable", "detail": str(e)}, status=502)

    out = []
    for loc in locations:
        coords = (loc.get("coordinates") or {})
        out.append({
            "id": loc.get("id"),
            "name": loc.get("name"),
            "distance_m": loc.get("distance"),
            "coords": {"lat": coords.get("latitude"), "lon": coords.get("longitude")},
        })

    return JsonResponse({"count": len(out), "results": out})


@require_GET
def aq_current(request):
    city = (request.GET.get("city") or "almaty").lower()
    lat = request.GET.get("lat")
    lon = request.GET.get("lon")

    if lat and lon:
        payload, err = _current_snapshot(city, lat=float(lat), lon=float(lon))
    else:
        payload, err = _current_snapshot(city)

    if err:
        return JsonResponse(err, status=502)
    return JsonResponse(payload)


@require_GET
def aq_series24h(request):
    sensor_id = request.GET.get("sensor_id")
    if not sensor_id:
        return JsonResponse({"labels": [], "values": []})

    try:
        results = openaq.sensor_hourly(int(sensor_id), hours=24)
    except Exception as e:
        return JsonResponse({"error": "OpenAQ unavailable", "detail": str(e)}, status=502)

    points = []
    for r in (results or []):
        p = _extract_hour_point(r)
        if not p:
            continue
        dt_str, val, _cov = p
        if not dt_str or len(dt_str) < 16:
            continue
        dt_obj = _parse_iso(dt_str)
        points.append((dt_obj or dt_str, dt_str, val))

    # sort by datetime
    points.sort(key=lambda x: x[0])

    labels, values = [], []
    for _key, dt_str, val in points[-24:]:
        # label HH:MM (local)
        labels.append(dt_str[11:16])
        values.append(float(val))

    return JsonResponse({"labels": labels, "values": values})


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

    # Use internal outlook endpoint
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
            "activity": "outdoor_sport",
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
