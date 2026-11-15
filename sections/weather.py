"""Weather: Open-Meteo daily high/low, rain probability by time of day, what to wear/carry."""

from __future__ import annotations

import html

from core.models import Section
from providers import open_meteo
from providers.open_meteo import Forecast
from sections.base import SectionPlugin, register

_WHEN = {"Morning": "this morning", "Afternoon": "this afternoon", "Evening": "this evening",
         "Night": "tonight"}  # fmt: skip


def advise(fc: Forecast) -> str:
    """Deterministic one-line 'what to wear / carry' suggestion (no LLM needed)."""
    known = {k: v for k, v in fc.buckets.items() if v is not None}
    peak = max([fc.rain_max, *known.values()])
    wet = [k for k, v in known.items() if v >= 50]
    bits: list[str] = []
    if peak >= 60:
        when = _WHEN[wet[0]] if wet else "later"
        bits.append(f"carry an umbrella (rain likely {when})")
    elif peak >= 30:
        bits.append("pack a compact umbrella just in case")
    if fc.high >= 35:
        bits.append("wear light cotton and carry water")
    elif fc.high >= 30:
        bits.append("wear light, breathable clothes")
    elif fc.high <= 15:
        bits.append("wear a warm jacket")
    elif fc.high <= 22:
        bits.append("bring a light layer")
    elif not bits:
        bits.append("regular clothes are fine")
    if fc.uv >= 8:
        bits.append("use sunscreen and sunglasses")
    if fc.wind >= 35:
        bits.append("expect strong wind, so secure loose items")
    text = ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1]
    return text[0].upper() + text[1:] + "."


@register
class WeatherSection(SectionPlugin):
    name = "weather"
    title = "Weather"
    emoji = "🌤"

    def fetch(self) -> Section:
        loc = self.ctx.config.location
        lat, lon = loc.latitude, loc.longitude
        if lat is None or lon is None:
            lat, lon = open_meteo.geocode(loc.city)
        fc = open_meteo.get_forecast(lat, lon, self.ctx.config.timezone)

        rain = " · ".join(
            f"{label} {v}%" if v is not None else f"{label} –" for label, v in fc.buckets.items()
        )
        feels = f" (feels {fc.feels_high:.0f}°)" if abs(fc.feels_high - fc.high) >= 2 else ""
        lines = [
            f"{fc.description} · ↑{fc.high:.0f}° ↓{fc.low:.0f}°{feels}",
            f"🌧 {rain}",
            f"👕 {html.escape(advise(fc))}",
        ]
        peak_label = max(
            (k for k, v in fc.buckets.items() if v is not None),
            key=lambda k: fc.buckets[k] or 0,
            default=None,
        )
        digest = f"{loc.city}: {fc.description.lower()}, high {fc.high:.0f}°C, low {fc.low:.0f}°C"
        if peak_label and (fc.buckets[peak_label] or 0) >= 30:
            digest += f", rain chance up to {fc.buckets[peak_label]}% in the {peak_label.lower()}"
        return self.make(title=f"Weather · {loc.city}", lines=lines, digest=digest)
