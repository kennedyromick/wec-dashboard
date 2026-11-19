"""Head-to-head comparison of two cars: pure pandas on the lap table from wec.laps.

Sign convention everywhere: delta = B - A (seconds), so a positive delta means car A was
faster / ahead. Laps tagged PIT IN / PIT OUT / SC / FCY / RED are left out of every
aggregate unless include_affected is set (same rule as the sector table).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from wec.laps import TIME_COLS

SAME_LAP, BEST_LAP, THEORETICAL, LAP_RANGE = "Same lap", "Best laps", "Theoretical best", "Lap range / stint"
MODES = [SAME_LAP, BEST_LAP, THEORETICAL, LAP_RANGE]


@dataclass
class CarTimes:
    number: str
    s1: float | None = None
    s2: float | None = None
    s3: float | None = None
    lap_time: float | None = None
    source: str = ""  # where the times come from, e.g. "Lap 47 (official best)"
    driver: str = ""
    notes: list[str] = field(default_factory=list)

    def get(self, col: str) -> float | None:
        return getattr(self, col)


def counted(laps: pd.DataFrame, include_affected: bool) -> pd.Series:
    return pd.Series(True, index=laps.index) if include_affected else ~laps["affected"]


def car_laps(laps: pd.DataFrame, number: str) -> pd.DataFrame:
    return laps[laps["number"] == number].sort_values("lap")


def car_info(laps: pd.DataFrame) -> pd.DataFrame:
    """One row per car: number, team, car_class, drivers (in order of first stint), final position."""
    last = laps.sort_values("lap").groupby("number").tail(1).set_index("number")
    final = laps.sort_values(["lap", "pos"]).groupby("number").tail(1)
    order = final.sort_values(["lap", "elapsed"], ascending=[False, True])["number"].tolist()
    drivers = laps.sort_values("lap").groupby("number")["driver"].agg(lambda s: list(dict.fromkeys(d for d in s if d)))
    info = pd.DataFrame(
        {
            "number": order,
            "team": [last.at[n, "team"] for n in order],
            "car_class": [last.at[n, "car_class"] for n in order],
            "drivers": [drivers[n] for n in order],
        }
    )
    info["final_pos"] = range(1, len(info) + 1)
    return info


def _from_row(number: str, row: pd.Series, source: str) -> CarTimes:
    return CarTimes(number, *(None if pd.isna(row[c]) else float(row[c]) for c in TIME_COLS), source=source, driver=row["driver"])


def times_same_lap(laps: pd.DataFrame, number: str, lap: int, include_affected: bool) -> CarTimes:
    rows = car_laps(laps, number)
    rows = rows[rows["lap"] == lap]
    if rows.empty:
        return CarTimes(number, source=f"Lap {lap}", notes=[f"#{number} has no lap {lap}"])
    row = rows.iloc[0]
    t = _from_row(number, row, f"Lap {lap}")
    if row["affected"]:
        t.notes.append(f"#{number} lap {lap} is {row['tags']}" + ("" if include_affected else " (normally left out)"))
    return t


def times_best_lap(laps: pd.DataFrame, number: str, include_affected: bool) -> CarTimes:
    rows = car_laps(laps, number)
    rows = rows[counted(rows, include_affected) & rows["lap_time"].notna()]
    if rows.empty:
        return CarTimes(number, source="Best lap", notes=[f"#{number} has no counted laps"])
    official = rows[rows["official_best"]]
    if not official.empty:
        row = official.iloc[0]
        return _from_row(number, row, f"Lap {row['lap']} (official best)")
    row = rows.loc[rows["lap_time"].idxmin()]
    return _from_row(number, row, f"Lap {row['lap']} (fastest counted lap)")


def times_theoretical(laps: pd.DataFrame, number: str, include_affected: bool) -> CarTimes:
    rows = car_laps(laps, number)
    rows = rows[counted(rows, include_affected)]
    t = CarTimes(number)
    parts = []
    for c in ("s1", "s2", "s3"):
        valid = rows[rows[c].notna()]
        if valid.empty:
            t.notes.append(f"#{number} has no counted {c.upper()}")
            continue
        best = valid.loc[valid[c].idxmin()]
        setattr(t, c, float(best[c]))
        parts.append(f"{c.upper()} L{best['lap']}")
    if all(t.get(c) is not None for c in ("s1", "s2", "s3")):
        t.lap_time = round(t.s1 + t.s2 + t.s3, 3)
    t.source = "Best sectors: " + " · ".join(parts)
    return t


def times_range(laps: pd.DataFrame, number: str, first: int, last: int, include_affected: bool) -> CarTimes:
    rows = car_laps(laps, number)
    rows = rows[(rows["lap"] >= first) & (rows["lap"] <= last)]
    total = len(rows)
    rows = rows[counted(rows, include_affected)].dropna(subset=TIME_COLS)
    if rows.empty:
        return CarTimes(number, source=f"Laps {first}–{last}", notes=[f"#{number} has no counted laps in {first}–{last}"])
    t = CarTimes(number, *(round(float(rows[c].mean()), 3) for c in TIME_COLS))
    t.source = f"Average of {len(rows)} of {total} laps ({first}–{last})"
    t.driver = " / ".join(dict.fromkeys(rows["driver"]))
    return t


def compare(
    laps: pd.DataFrame,
    a: str,
    b: str,
    mode: str,
    *,
    lap: int,
    lap_range: tuple[int, int],
    include_affected: bool,
) -> tuple[CarTimes, CarTimes]:
    def one(n: str) -> CarTimes:
        if mode == SAME_LAP:
            return times_same_lap(laps, n, lap, include_affected)
        if mode == BEST_LAP:
            return times_best_lap(laps, n, include_affected)
        if mode == THEORETICAL:
            return times_theoretical(laps, n, include_affected)
        return times_range(laps, n, *lap_range, include_affected)

    return one(a), one(b)


def deltas(ta: CarTimes, tb: CarTimes) -> dict[str, float | None]:
    """B - A per time column (positive = A faster)."""
    out = {}
    for c in TIME_COLS:
        va, vb = ta.get(c), tb.get(c)
        out[c] = None if va is None or vb is None else round(vb - va, 3)
    return out


def stints(laps: pd.DataFrame, number: str) -> list[tuple[int, int]]:
    """(first lap, last lap) of each stint: a new stint starts on every out-lap."""
    rows = car_laps(laps, number)
    out: list[tuple[int, int]] = []
    start = None
    prev = None
    for lap, pit_out in zip(rows["lap"], rows["pit_out"]):
        if start is None:
            start = lap
        elif pit_out:
            out.append((start, prev))
            start = lap
        prev = lap
    if start is not None:
        out.append((start, prev))
    return out


def gap_by_lap(laps: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    """Gap at the line at the end of each lap both cars completed: elapsed B - elapsed A.

    Positive = A ahead. Also returns pit-in flags and whether either car's lap was neutralised.
    """
    cols = ["lap", "elapsed", "pit_in", "tags"]
    ra = car_laps(laps, a)[cols].set_index("lap")
    rb = car_laps(laps, b)[cols].set_index("lap")
    m = ra.join(rb, lsuffix="_a", rsuffix="_b", how="inner").reset_index()
    neutral_tag = lambda s: s.str.contains("SC|FCY|RED", regex=True)
    return pd.DataFrame(
        {
            "lap": m["lap"],
            "gap": (m["elapsed_b"] - m["elapsed_a"]).round(3),
            "pit_in_a": m["pit_in_a"],
            "pit_in_b": m["pit_in_b"],
            "neutral": neutral_tag(m["tags_a"]) | neutral_tag(m["tags_b"]),
            "neutral_kind": m["tags_a"].str.extract(r"(SC|FCY|RED)")[0].fillna(m["tags_b"].str.extract(r"(SC|FCY|RED)")[0]),
        }
    )


def neutral_periods(gap: pd.DataFrame) -> pd.DataFrame:
    """Contiguous runs of neutralised laps -> start, end, kind (for shading a chart)."""
    runs = []
    start = prev = kind = None
    for lap, neutral, k in zip(gap["lap"], gap["neutral"], gap["neutral_kind"]):
        if neutral and start is not None and lap == prev + 1:
            prev = lap
            continue
        if start is not None:
            runs.append((start, prev, kind))
            start = None
        if neutral:
            start = prev = lap
            kind = k if isinstance(k, str) else "SC"
    if start is not None:
        runs.append((start, prev, kind))
    return pd.DataFrame(runs, columns=["start", "end", "kind"])


def sector_deltas_by_lap(laps: pd.DataFrame, a: str, b: str, include_affected: bool) -> pd.DataFrame:
    """Long table: lap, sector (S1/S2/S3), delta (B - A), for laps where both cars' laps count."""
    keep = ["lap", "s1", "s2", "s3", "affected"]
    ra = car_laps(laps, a)[keep].set_index("lap")
    rb = car_laps(laps, b)[keep].set_index("lap")
    m = ra.join(rb, lsuffix="_a", rsuffix="_b", how="inner")
    if not include_affected:
        m = m[~m["affected_a"] & ~m["affected_b"]]
    out = pd.DataFrame(
        {c.upper(): (m[f"{c}_b"] - m[f"{c}_a"]).round(3) for c in ("s1", "s2", "s3")}, index=m.index
    ).reset_index()
    return out.melt(id_vars="lap", var_name="sector", value_name="delta").dropna()
