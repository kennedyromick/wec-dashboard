"""Per-circuit sector boundaries: data/tracks/<circuit>.sectors.json.

Boundaries are stored as metres from start/finish *along our centreline* (data/tracks/<circuit>.geojson),
alongside the published values they came from, so they can be re-derived if the centreline changes.

Published timing distances are measured on the organiser's centreline (e.g. Imola 4,909 m), which
is a little longer or shorter than ours, so they are scaled by our_length / published_length.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wec.track import TRACKS_DIR, Track

SECTOR_NAMES = ["S1", "S2", "S3"]
# Same colours as the official track maps: S1 blue, S2 yellow, S3 magenta.
SECTOR_COLORS = ["#2f6bff", "#ffd400", "#ff2d78"]


@dataclass
class SectorConfig:
    circuit: str
    boundaries_m: list[float]  # [S1/S2, S2/S3] on our centreline
    placement: str  # how boundaries_m was set: "published" or "manual"
    published: dict = field(default_factory=dict)  # values copied from the track PDF
    updated: str = ""

    def path(self) -> Path:
        return sector_config_path(self.circuit)

    def published_boundaries(self, track: Track) -> list[float] | None:
        """Boundaries from the published distances, scaled onto our centreline."""
        pub = self.published
        if not all(k in pub for k in ("centerline_m", "s1_s2_m", "s2_s3_m")):
            return None
        k = track.length_m / pub["centerline_m"]
        return [round(pub["s1_s2_m"] * k, 1), round(pub["s2_s3_m"] * k, 1)]

    def segments(self, track: Track) -> list[dict]:
        """Coloured sector ranges for the map."""
        edges = [0.0, *self.boundaries_m, track.length_m]
        return [
            {"from_m": edges[i], "to_m": edges[i + 1], "color": SECTOR_COLORS[i], "label": SECTOR_NAMES[i]}
            for i in range(3)
        ]

    def save(self) -> None:
        self.updated = date.today().isoformat()
        data = {
            "circuit": self.circuit,
            "boundaries_m": [round(b, 1) for b in self.boundaries_m],
            "placement": self.placement,
            "updated": self.updated,
            "published": self.published,
        }
        self.path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sector_config_path(circuit: str) -> Path:
    return TRACKS_DIR / f"{circuit}.sectors.json"


def load_sector_config(circuit: str) -> SectorConfig | None:
    path = sector_config_path(circuit)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SectorConfig(
        circuit=data["circuit"],
        boundaries_m=[float(b) for b in data["boundaries_m"]],
        placement=data.get("placement", ""),
        published=data.get("published", {}),
        updated=data.get("updated", ""),
    )


def validate_boundaries(boundaries: list[float], track: Track, min_gap_m: float = 50.0) -> list[float]:
    """Clamp to (0, lap) and keep S1/S2 before S2/S3 with a minimum gap."""
    b1, b2 = boundaries
    b1 = min(max(b1, min_gap_m), track.length_m - 2 * min_gap_m)
    b2 = min(max(b2, b1 + min_gap_m), track.length_m - min_gap_m)
    return [round(b1, 1), round(b2, 1)]
