"""Circuit centreline: load the GeoJSON and describe positions as metres from start/finish.

The track files live in data/tracks/<circuit>.geojson and are made by `npm run fetch-track`.
Each one is a closed LineString ([lon, lat]) whose first point is the start/finish line and
which runs in race direction.

Distances are measured along the centreline, so they come out slightly different from the
published lap length (Imola: 4898 m along the line vs 4909 m published). `scale_to_official`
gives the factor to convert published distances onto this line.
"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

TRACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "tracks"

EARTH_RADIUS_M = 6371008.8


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in metres between two (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@dataclass
class Track:
    circuit: str
    name: str
    coords: list[tuple[float, float]]  # (lon, lat), closed: coords[0] == coords[-1]
    cum_m: list[float] = field(default_factory=list)  # cum_m[i] = metres from S/F to coords[i]
    official_length_m: float | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if self.coords[0] != self.coords[-1]:
            self.coords.append(self.coords[0])
        cum = [0.0]
        for a, b in zip(self.coords, self.coords[1:]):
            cum.append(cum[-1] + haversine_m(a, b))
        self.cum_m = cum

    @property
    def length_m(self) -> float:
        return self.cum_m[-1]

    @property
    def scale_to_official(self) -> float:
        """Multiply a published distance by this to get the distance along our centreline."""
        return self.length_m / self.official_length_m if self.official_length_m else 1.0

    def point_at(self, distance_m: float) -> tuple[float, float]:
        """(lon, lat) of the point `distance_m` metres after start/finish (wraps around the lap)."""
        d = distance_m % self.length_m
        i = max(1, bisect.bisect_right(self.cum_m, d))
        i = min(i, len(self.cum_m) - 1)
        a, b = self.coords[i - 1], self.coords[i]
        seg = self.cum_m[i] - self.cum_m[i - 1]
        t = 0.0 if seg == 0 else (d - self.cum_m[i - 1]) / seg
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    def distance_of(self, lon: float, lat: float) -> tuple[float, float]:
        """Project a point onto the centreline.

        Returns (metres from start/finish, metres off the line).
        """
        # Local flat projection around the point: fine at circuit scale (a few km).
        kx = math.cos(math.radians(lat)) * math.pi / 180 * EARTH_RADIUS_M
        ky = math.pi / 180 * EARTH_RADIUS_M
        best = (0.0, math.inf)
        for i in range(len(self.coords) - 1):
            (ax, ay), (bx, by) = self.coords[i], self.coords[i + 1]
            ax, ay, bx, by = (ax - lon) * kx, (ay - lat) * ky, (bx - lon) * kx, (by - lat) * ky
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
            off = math.hypot(ax + t * dx, ay + t * dy)
            if off < best[1]:
                best = (self.cum_m[i] + t * (self.cum_m[i + 1] - self.cum_m[i]), off)
        return best


def available_circuits() -> list[str]:
    return sorted(p.stem for p in TRACKS_DIR.glob("*.geojson"))


def load_track(circuit: str) -> Track:
    data = json.loads((TRACKS_DIR / f"{circuit}.geojson").read_text(encoding="utf-8"))
    feature = next(f for f in data["features"] if f["geometry"]["type"] == "LineString")
    props = feature.get("properties", {})
    return Track(
        circuit=circuit,
        name=props.get("name", circuit),
        coords=[(float(x), float(y)) for x, y in feature["geometry"]["coordinates"]],
        official_length_m=props.get("official_length_m"),
        source=props.get("source", ""),
    )
