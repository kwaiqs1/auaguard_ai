import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


PM25_PARAMETER_ID = 2
PM25_PARAMETER_NAMES = {"pm25", "pm2.5", "pm2_5"}


class OpenAQError(RuntimeError):
    pass


class OpenAQClient:
    """
    Minimal OpenAQ v3 client (requests-based).

    Key design choice for MVP:
    - Use /v3/locations?coordinates=...&radius=...&parameters_id=2 to find nearby locations that *have PM2.5*.
    - Use /v3/locations/{id}/sensors to get the PM2.5 sensor + its latest value (this endpoint includes `latest`).
    - Use /v3/sensors/{id}/measurements/hourly for 24h series (aggregated).
    """

    def __init__(self, base_url: str, api_key: str = "", timeout_s: int = 20):
        self.base_url = (base_url or "").rstrip("/") or "https://api.openaq.org"
        self.api_key = (api_key or "").strip()
        self.timeout_s = int(timeout_s)

        # A small session improves performance and allows re-use of connections.
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.api_key:
            # OpenAQ v3 auth header
            h["X-API-Key"] = self.api_key
        return h

    def _get_json(self, path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        timeout = self.timeout_s if timeout_s is None else int(timeout_s)

        if not self.api_key:
            raise OpenAQError("OPENAQ_API_KEY is missing (OpenAQ v3 requires an API key).")

        # Basic retry for transient issues (429/5xx/timeouts)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self._headers(), params=params, timeout=timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = OpenAQError(f"OpenAQ temporary error HTTP {r.status_code}: {r.text[:300]}")
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
        raise OpenAQError(str(last_err) if last_err else "OpenAQ request failed")

    def locations_near(
        self,
        lat: float,
        lon: float,
        *,
        radius_m: int = 8000,
        limit: int = 10,
        parameters_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find locations near a point. Coordinates are in (latitude, longitude) order per OpenAQ geospatial docs.
        """
        params: Dict[str, Any] = {
            "coordinates": f"{lat},{lon}",
            "radius": int(radius_m),
            "limit": int(limit),
            "page": 1,
        }
        if parameters_id is not None:
            params["parameters_id"] = int(parameters_id)

        data = self._get_json("/v3/locations", params=params)
        return data.get("results", []) or []

    def location_sensors(self, location_id: int) -> List[Dict[str, Any]]:
        """
        Returns sensors for a location. This endpoint includes `parameter` and `latest` objects in response schema.
        """
        data = self._get_json(f"/v3/locations/{int(location_id)}/sensors", timeout_s=25)
        return data.get("results", []) or []

    def sensor_hourly(self, sensor_id: int, *, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Hourly aggregated measurements for a sensor.
        Response includes `period.datetimeFrom`/`period.datetimeTo` objects.
        """
        dt_to = datetime.now(timezone.utc)
        dt_from = dt_to - timedelta(hours=int(hours))

        params = {
            "datetime_from": dt_from.isoformat(),
            "datetime_to": dt_to.isoformat(),
            "limit": 300,  # 24h => <= 24 rows; leave headroom
            "page": 1,
        }
        data = self._get_json(f"/v3/sensors/{int(sensor_id)}/measurements/hourly", params=params, timeout_s=30)
        return data.get("results", []) or []

    # ---------- Convenience helpers ----------

    @staticmethod
    def _pick_best_pm25_sensor(sensors: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Pick the best PM2.5 sensor from a location sensors list.
        Prefer:
        - parameter.id == 2 (PM2.5) OR parameter.name in {pm25, pm2.5, ...}
        - has latest.value not None
        - most recent latest.datetime.utc
        """
        candidates: List[Dict[str, Any]] = []
        for s in sensors or []:
            param = s.get("parameter") or {}
            pid = param.get("id")
            pname = (param.get("name") or "").lower()

            if pid == PM25_PARAMETER_ID or pname in PM25_PARAMETER_NAMES:
                candidates.append(s)

        if not candidates:
            return None

        def score(sensor: Dict[str, Any]) -> Tuple[int, str]:
            latest = sensor.get("latest") or {}
            dt_utc = (latest.get("datetime") or {}).get("utc") or ""
            has_value = 1 if latest.get("value") is not None else 0
            # has_value first, then datetime desc
            return (has_value, dt_utc)

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    def pm25_latest_near(
        self,
        lat: float,
        lon: float,
        *,
        radius_m: int = 9000,
        limit_locations: int = 25,
        max_location_checks: int = 6,
    ) -> Dict[str, Any]:
        """
        Returns PM2.5 latest snapshot near (lat, lon), plus location + sensor IDs.

        Strategy:
        1) Find nearby locations that have PM2.5 (parameters_id=2).
        2) For the closest few locations, fetch sensors and select PM2.5 sensor with a latest value.
        """
        locations = self.locations_near(
            lat, lon,
            radius_m=radius_m,
            limit=limit_locations,
            parameters_id=PM25_PARAMETER_ID,
        )
        if not locations:
            raise OpenAQError("No PM2.5 locations found near this point (OpenAQ returned 0 results).")

        # Prefer closest if distance exists
        if any("distance" in (loc or {}) for loc in locations):
            locations = sorted(locations, key=lambda x: (x or {}).get("distance") or 10**9)

        last_err: Optional[str] = None
        for loc in locations[: max_location_checks]:
            loc_id = (loc or {}).get("id")
            if not loc_id:
                continue

            try:
                sensors = self.location_sensors(int(loc_id))
                pm25_sensor = self._pick_best_pm25_sensor(sensors)
                if not pm25_sensor:
                    last_err = f"Location {loc_id} has no PM2.5 sensor in sensors list."
                    continue

                latest = pm25_sensor.get("latest") or {}
                value = latest.get("value")
                dt_utc = (latest.get("datetime") or {}).get("utc") or ""

                if value is None:
                    last_err = f"PM2.5 sensor at location {loc_id} has no latest value."
                    continue

                unit = (pm25_sensor.get("parameter") or {}).get("units") or "µg/m³"
                coords = (loc or {}).get("coordinates") or {}
                return {
                    "pm25_ug_m3": float(value),
                    "unit": unit,
                    "timestamp_utc": dt_utc,
                    "sensor_id": pm25_sensor.get("id"),
                    "location_id": int(loc_id),
                    "location_name": (loc or {}).get("name") or "",
                    "location_coords": {
                        "lat": coords.get("latitude"),
                        "lon": coords.get("longitude"),
                    },
                    "provider": (loc or {}).get("provider") or None,
                    "owner": (loc or {}).get("owner") or None,
                }
            except Exception as e:
                last_err = str(e)

        raise OpenAQError(last_err or "Unable to resolve PM2.5 latest value near this point.")