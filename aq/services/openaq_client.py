import requests
from datetime import datetime, timedelta, timezone

class OpenAQClient:
    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()

    def _headers(self):
        h = {"Accept": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def locations_near(self, lat: float, lon: float, radius_m: int = 8000, limit: int = 10):
        # v3 geospatial queries via coordinates + radius (docs)
        url = f"{self.base_url}/v3/locations"
        params = {
            "coordinates": f"{lat},{lon}",
            "radius": radius_m,
            "limit": limit,
            "page": 1,
        }
        r = requests.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("results", [])

    def location_latest(self, location_id: int):
        url = f"{self.base_url}/v3/locations/{location_id}/latest"
        r = requests.get(url, headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json().get("results", [])

    def sensor_hourly(self, sensor_id: int, hours: int = 24):
        url = f"{self.base_url}/v3/sensors/{sensor_id}/measurements/hourly"
        dt_to = datetime.now(timezone.utc)
        dt_from = dt_to - timedelta(hours=hours)
        params = {
            "datetime_from": dt_from.isoformat(),
            "datetime_to": dt_to.isoformat(),
            "limit": 200,
            "page": 1,
        }
        r = requests.get(url, headers=self._headers(), params=params, timeout=25)
        r.raise_for_status()
        return r.json().get("results", [])

def pick_pm25_sensor_from_latest(latest_results):
    """
    latest_results: list of objects with parameter info (name/units) and 'sensorsId' or 'sensorId' sometimes.
    We'll try to find pm25 and return (pm25_value, unit, sensor_id, captured_utc, location_name)
    """
    pm = None
    for item in latest_results:
        param = (item.get("parameter") or {})
        name = (param.get("name") or "").lower()
        if name in ("pm25", "pm2.5", "pm2_5"):
            pm = item
            break
    if not pm:
        return None

    # fields vary slightly; keep defensive
    value = pm.get("value")
    unit = (pm.get("parameter") or {}).get("units") or pm.get("unit") or "µg/m³"
    sensor_id = pm.get("sensorsId") or pm.get("sensorId") or pm.get("sensor_id")
    dt = (pm.get("datetime") or {}).get("utc") or (pm.get("date") or {}).get("utc")
    location_name = pm.get("location") or pm.get("locationName")
    return value, unit, sensor_id, dt, location_name
