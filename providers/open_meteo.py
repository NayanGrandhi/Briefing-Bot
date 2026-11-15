"""Open-Meteo forecast + geocoding (free, no API key)."""

from __future__ import annotations

from dataclasses import dataclass, field

from core import http

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# (label, first hour, last hour) of the local day
BUCKETS = (("Morning", 6, 11), ("Afternoon", 12, 16), ("Evening", 17, 20), ("Night", 21, 23))

WMO = {
    0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
}  # fmt: skip


@dataclass
class Forecast:
    high: float
    low: float
    feels_high: float
    rain_max: int
    uv: float
    wind: float
    code: int
    buckets: dict[str, int | None] = field(default_factory=dict)

    @property
    def description(self) -> str:
        return WMO.get(self.code, "Mixed conditions")


def geocode(city: str) -> tuple[float, float]:
    data = http.get_json(GEOCODE_URL, params={"name": city, "count": 1, "format": "json"})
    results = data.get("results") or []
    if not results:
        raise LookupError(f"Could not geocode city {city!r}")
    return float(results[0]["latitude"]), float(results[0]["longitude"])


def _first(values: list | None, default=0):
    return values[0] if values and values[0] is not None else default


def get_forecast(lat: float, lon: float, timezone: str) -> Forecast:
    data = http.get_json(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": timezone,
            "forecast_days": 1,
            "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
            "precipitation_probability_max,weather_code,uv_index_max,wind_speed_10m_max",
            "hourly": "precipitation_probability",
        },
    )
    d, h = data["daily"], data["hourly"]
    probs: dict[int, int] = {}
    for stamp, p in zip(h["time"], h["precipitation_probability"], strict=False):
        if p is not None:
            probs[int(stamp[11:13])] = int(p)
    buckets: dict[str, int | None] = {}
    for label, start, end in BUCKETS:
        vals = [probs[hr] for hr in range(start, end + 1) if hr in probs]
        buckets[label] = max(vals) if vals else None
    return Forecast(
        high=float(_first(d["temperature_2m_max"])),
        low=float(_first(d["temperature_2m_min"])),
        feels_high=float(
            _first(d.get("apparent_temperature_max"), _first(d["temperature_2m_max"]))
        ),
        rain_max=int(_first(d.get("precipitation_probability_max"))),
        uv=float(_first(d.get("uv_index_max"))),
        wind=float(_first(d.get("wind_speed_10m_max"))),
        code=int(_first(d.get("weather_code"))),
        buckets=buckets,
    )
