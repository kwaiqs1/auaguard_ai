import requests

class OpenWeatherClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()

    def onecall(self, lat: float, lon: float):
        # One Call 3.0
        url = f"{self.base_url}/data/3.0/onecall"
        params = {
            "lat": lat,
            "lon": lon,
            "appid": self.api_key,
            "units": "metric",
            "exclude": "minutely,daily,alerts",
        }
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
