"""Race folders: data/<season>/<NN>_<circuit>/ holding that race's files.

Each folder can contain:
- the Al Kamel Analysis CSV (any CSV with "analysis" in its name; the highest "Hour N" wins),
- the official circuit map (a PDF or image with "map" in its name, else the only PDF),
- optionally race.json: {"name": "6 Hours of Imola", "circuit": "imola"} to override the
  display name and which centreline in data/tracks/ to use.

Without race.json the circuit is the folder name after the round number, lower-cased
("01_imola" -> "imola", "03_leMans" -> "lemans"), and the name is made from it ("Le Mans").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from wec.parsing import hour_from_filename

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
_FOLDER = re.compile(r"(\d+)_(.+)")


@dataclass(frozen=True)
class Race:
    id: str  # "2026/01_imola" – also used in the URL
    season: str
    round: int
    name: str
    circuit: str
    folder: Path
    analysis: Path | None
    circuit_map: Path | None

    @property
    def label(self) -> str:
        return f"{self.season} · R{self.round} · {self.name}"


# Folder names whose display name can't be guessed from the spelling (race.json still wins).
KNOWN_NAMES = {"lemans": "Le Mans", "saopaulo": "São Paulo", "usa": "USA"}


def _pretty(slug: str) -> str:
    if slug.lower() in KNOWN_NAMES:
        return KNOWN_NAMES[slug.lower()]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug.replace("_", " ").replace("-", " ")).split()
    return " ".join(w[0].upper() + w[1:] for w in words)


def _analysis_file(folder: Path) -> Path | None:
    files = [p for p in folder.iterdir() if p.suffix.lower() == ".csv" and "analysis" in p.name.lower()]
    return max(files, key=lambda p: (hour_from_filename(p.name) or 0, p.name), default=None)


def _map_file(folder: Path) -> Path | None:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in MAP_SUFFIXES)
    named = [p for p in files if "map" in p.name.lower()]
    if named:
        return named[0]
    pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
    return pdfs[0] if len(pdfs) == 1 else None


def _race(season_dir: Path, folder: Path) -> Race | None:
    m = _FOLDER.fullmatch(folder.name)
    if not m:
        return None
    meta: dict = {}
    meta_file = folder / "race.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    slug = m.group(2)
    return Race(
        id=f"{season_dir.name}/{folder.name}",
        season=season_dir.name,
        round=int(m.group(1)),
        name=meta.get("name") or _pretty(slug),
        circuit=(meta.get("circuit") or slug).lower(),
        folder=folder,
        analysis=_analysis_file(folder),
        circuit_map=_map_file(folder),
    )


def find_races(data_dir: Path = DATA_DIR) -> list[Race]:
    """Every race folder, newest season first, then by round."""
    races = []
    if not data_dir.exists():
        return races
    for season_dir in sorted((d for d in data_dir.iterdir() if d.is_dir() and d.name.isdigit()), reverse=True):
        for folder in sorted(d for d in season_dir.iterdir() if d.is_dir()):
            race = _race(season_dir, folder)
            if race:
                races.append(race)
    return races
