"""Read Al Kamel CSV files (Analysis and Classification) into car entry information.

Only entry data for now: car number, class, team, manufacturer/vehicle and drivers.
No timing data yet.

Al Kamel quirks handled here:
- Files start with a UTF-8 BOM, so we read them with the "utf-8-sig" encoding.
- Headers can have stray spaces (" LAP_NUMBER") and every line ends with ";",
  which adds an empty extra column.
- Car numbers must stay text: "007" and "7" are different cars.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

ANALYSIS = "analysis"
CLASSIFICATION = "classification"


@dataclass
class Car:
    number: str
    car_class: str = ""
    team: str = ""
    manufacturer: str = ""
    vehicle: str = ""
    drivers: list[str] = field(default_factory=list)
    driver_numbers: dict[str, str] = field(default_factory=dict)  # driver name -> DRIVER_NUMBER (Analysis only)


def read_alkamel_csv(data: bytes) -> pd.DataFrame:
    """Read a semicolon-separated Al Kamel file with every value kept as text."""
    df = pd.read_csv(
        io.BytesIO(data),
        sep=";",
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,  # empty cells stay "" instead of NaN
    )
    # Drop the empty column produced by the trailing ";" (pandas calls it "Unnamed: N").
    df = df.loc[:, [not c.strip().startswith("Unnamed") and c.strip() != "" for c in df.columns]]
    df.columns = [re.sub(r"[\s-]+", "_", c.strip().upper()) for c in df.columns]
    return df.apply(lambda col: col.str.strip())


def detect_file_type(df: pd.DataFrame) -> str | None:
    """Tell an Analysis file (one row per lap) from a Classification file (one row per car)."""
    cols = set(df.columns)
    if {"NUMBER", "LAP_NUMBER"} <= cols:
        return ANALYSIS
    if {"POSITION", "NUMBER", "DRIVER_1"} <= cols:
        return CLASSIFICATION
    return None


def hour_from_filename(name: str) -> int | None:
    """'03_Classification_Race_Hour 6.CSV' -> 6."""
    m = re.search(r"hour[\s_]*(\d+)", name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _first_col(df: pd.DataFrame, *names: str) -> str | None:
    """First of `names` present in df (column names differ slightly between seasons)."""
    return next((n for n in names if n in df.columns), None)


def cars_from_analysis(df: pd.DataFrame) -> dict[str, Car]:
    """Entry info from the Analysis file. Drivers are those who actually drove, by driver number."""
    team_col = _first_col(df, "TEAM")
    class_col = _first_col(df, "CLASS")
    manu_col = _first_col(df, "MANUFACTURER")
    driver_col = _first_col(df, "DRIVER_NAME", "DRIVER")
    drv_no_col = _first_col(df, "DRIVER_NUMBER", "DRIVER_NO")

    cars: dict[str, Car] = {}
    for number, rows in df.groupby("NUMBER", sort=False):
        first = rows.iloc[0]
        car = Car(
            number=str(number),
            car_class=first[class_col] if class_col else "",
            team=first[team_col] if team_col else "",
            manufacturer=first[manu_col] if manu_col else "",
        )
        if driver_col:
            stints = rows[[driver_col] + ([drv_no_col] if drv_no_col else [])].drop_duplicates(driver_col)
            if drv_no_col:
                stints = stints.sort_values(drv_no_col, key=lambda s: pd.to_numeric(s, errors="coerce"))
            car.drivers = [d for d in stints[driver_col] if d]
            if drv_no_col:
                car.driver_numbers = {d: n for d, n in zip(stints[driver_col], stints[drv_no_col]) if d and n}
        cars[car.number] = car
    return cars


def cars_from_classification(df: pd.DataFrame) -> dict[str, Car]:
    """Entry info from a Classification file. Drivers are the full nominated line-up."""
    # DRIVER_1..DRIVER_5, ordered by number (DRIVER_5 sits at the end of the file, not after DRIVER_4).
    driver_cols = sorted(
        (c for c in df.columns if re.fullmatch(r"DRIVER_\d+", c)),
        key=lambda c: int(c.split("_")[1]),
    )
    cars: dict[str, Car] = {}
    for _, row in df.iterrows():
        car = Car(
            number=row["NUMBER"],
            car_class=row.get("CLASS", ""),
            team=row.get("TEAM", ""),
            vehicle=row.get("VEHICLE", ""),
            drivers=[row[c] for c in driver_cols if row[c]],
        )
        cars[car.number] = car
    return cars


def merge_entries(analysis: dict[str, Car] | None, classifications: dict[int, dict[str, Car]]) -> list[Car]:
    """Combine both sources into one entry per car.

    Line-up order comes from the latest classification hour; any driver seen only in
    the Analysis file is appended. Missing fields in one source are filled from the other.
    """
    latest = classifications[max(classifications)] if classifications else {}
    numbers = set(latest) | set(analysis or {})

    merged: list[Car] = []
    for number in numbers:
        a = (analysis or {}).get(number)
        c = latest.get(number)
        drivers = list(c.drivers) if c else []
        for d in a.drivers if a else []:
            if d not in drivers:
                drivers.append(d)
        merged.append(
            Car(
                number=number,
                car_class=(c and c.car_class) or (a and a.car_class) or "",
                team=(c and c.team) or (a and a.team) or "",
                manufacturer=(a and a.manufacturer) or "",
                vehicle=(c and c.vehicle) or "",
                drivers=drivers,
                driver_numbers=dict(a.driver_numbers) if a else {},
            )
        )
    return sorted(merged, key=car_sort_key)


def car_sort_key(car: Car) -> tuple[int, int]:
    """Numeric order, with '007' placed just after '7' rather than treated as equal."""
    return (int(car.number) if car.number.isdigit() else 10_000, len(car.number))
