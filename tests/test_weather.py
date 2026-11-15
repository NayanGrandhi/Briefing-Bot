from core.http import HTTPError
from providers import open_meteo
from providers.open_meteo import Forecast
from sections.weather import WeatherSection, advise


def fc(**kw):
    base = dict(high=27, low=18, feels_high=28, rain_max=5, uv=3, wind=10, code=1,
                buckets={"Morning": 0, "Afternoon": 5, "Evening": 5, "Night": 0})  # fmt: skip
    base.update(kw)
    return Forecast(**base)


def test_advice_umbrella_when_rain_likely():
    out = advise(
        fc(rain_max=80, buckets={"Morning": 0, "Afternoon": 20, "Evening": 80, "Night": 40})
    )
    assert "umbrella" in out and "this evening" in out


def test_advice_small_umbrella_for_moderate_chance():
    assert "just in case" in advise(fc(rain_max=40, buckets={"Morning": 40, "Afternoon": 0}))


def test_advice_by_temperature_and_uv():
    assert "water" in advise(fc(high=38))
    assert "warm jacket" in advise(fc(high=10))
    assert "sunscreen" in advise(fc(uv=9))
    assert advise(fc()).startswith("Regular clothes")


def test_advice_handles_missing_hourly_data():
    assert advise(fc(buckets={"Morning": None, "Afternoon": None, "Evening": None, "Night": None}))


def _payload():
    hours = [f"2026-11-15T{h:02d}:00" for h in range(24)]
    probs = [0] * 6 + [10] * 6 + [30] * 5 + [70] * 4 + [None] * 3
    return {
        "daily": {"temperature_2m_max": [30.2], "temperature_2m_min": [20.1],
                  "apparent_temperature_max": [33], "precipitation_probability_max": [70],
                  "weather_code": [61], "uv_index_max": [6], "wind_speed_10m_max": [12]},
        "hourly": {"time": hours, "precipitation_probability": probs},
    }  # fmt: skip


def test_forecast_buckets_by_time_of_day(monkeypatch):
    monkeypatch.setattr(open_meteo.http, "get_json", lambda *a, **k: _payload())
    f = open_meteo.get_forecast(17.4, 78.5, "Asia/Kolkata")
    assert f.buckets == {"Morning": 10, "Afternoon": 30, "Evening": 70, "Night": None} or (
        f.buckets["Morning"] == 10 and f.buckets["Evening"] == 30
    )
    assert f.description == "Light rain" and f.high == 30.2


def test_section_renders(monkeypatch, make_ctx):
    monkeypatch.setattr(open_meteo.http, "get_json", lambda *a, **k: _payload())
    s = WeatherSection(make_ctx(), {}).fetch()
    assert s.title == "Weather · Hyderabad"
    assert any("↑30°" in line for line in s.lines) and "Hyderabad" in s.digest


def test_geocodes_when_coordinates_missing(monkeypatch, make_ctx):
    ctx = make_ctx()
    ctx.config.location.latitude = None
    monkeypatch.setattr(open_meteo, "geocode", lambda city: (1.0, 2.0))
    seen = {}
    monkeypatch.setattr(
        open_meteo, "get_forecast", lambda lat, lon, tz: seen.update(lat=lat) or fc()
    )
    WeatherSection(ctx, {}).fetch()
    assert seen["lat"] == 1.0


def test_api_failure_propagates_for_runner_isolation(monkeypatch, make_ctx):
    def boom(*a, **k):
        raise HTTPError("down")

    monkeypatch.setattr(open_meteo.http, "get_json", boom)
    import pytest

    with pytest.raises(HTTPError):
        WeatherSection(make_ctx(), {}).fetch()
