"""Lap-by-lap timing from the Al Kamel Analysis file (Python port of src/data/parseAnalysis.ts).

One row per car per lap, with sector and lap times in seconds, pit and track-status tags,
running positions and personal-best flags. Pure pandas, no Streamlit.

Track status: Al Kamel's FLAG_AT_FL is the status when the car crossed the finish line.
The 2026 Imola file only contains GF (green), SF (safety car) and FF (chequered). A lap is
treated as affected if it *ends* under SC/FCY/red, or *starts* under one (the previous lap
ended under it), which covers the restart lap. "FCY" and "RF" are guesses at the codes,
not yet seen in a file.

FCY is not in FLAG_AT_FL at all: at Imola 2026 every car lapped ~2.3x slower for two laps
before the SC came out while the file still says GF. So neutralised periods are also
detected from the timing (see detect_neutral_periods) and laps overlapping one are tagged
FCY unless already SC.

Lap 1 is tagged START: timing starts at the start line, 218 m after the finish line at
Imola, so lap 1's S1 and lap time are short and must not count as bests.

Laps down: for each lap, how many laps the car's class leader had completed by the time the
car finished it, minus the lap number (see add_laps_down). A car far behind (e.g. after a long
garage stop) runs its lap N at a very different moment from the rest of its class, so those
laps are flagged "lapped" and, like affected laps, can be left out of purple/green.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from wec.config import LAPS_DOWN_THRESHOLD

TIME_COLS = ["s1", "s2", "s3", "lap_time"]
SECTOR_COLS = ["s1", "s2", "s3"]

FLAG_CODES = {"GF": "green", "FF": "finish", "SF": "sc", "SC": "sc", "FCY": "fcy", "RF": "red"}
NEUTRALISED = {"sc": "SC", "fcy": "FCY", "red": "RED"}

# Neutralisation detection (see detect_neutral_periods).
SLOW_RATIO = 1.4  # a sector this much slower than the car's own median counts as slow
SLOW_SHARE = 0.5  # ...and at least this share of the cars on track must be slow at once
MIN_CARS = 5
SAMPLE_S = 5.0
MIN_OVERLAP_S = 5.0  # a lap must overlap a neutral period by this much to be tagged
LAP_SLOW_RATIO = 1.1  # ...and have at least one sector this much slower than the car's median


def parse_duration(raw: str) -> float | None:
    """'21.542', '1:39.473', '4:44:55.345', "1'32.952" -> seconds (None if empty/unreadable)."""
    s = (raw or "").strip().replace("'", ":")
    if not s or not re.fullmatch(r"\d+(:\d{1,2})*(\.\d+)?", s):
        return None
    total = 0.0
    for part in s.split(":"):
        total = total * 60 + float(part)
    return round(total, 3)


def _first_col(df: pd.DataFrame, *names: str) -> str | None:
    return next((n for n in names if n in df.columns), None)


def laps_from_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Analysis rows (as read by wec.parsing.read_alkamel_csv) -> tidy lap table.

    Columns: number, car_class, team, lap, driver, s1, s2, s3, lap_time, elapsed, hour,
    pit_in, pit_out, official_best, flag, flag_raw, tags, affected, pos, class_pos,
    leader_lap, laps_down, lapped and, for each time column c, the personal-best-so-far flags
    named by pb_column(c, include_affected, include_lapped).

    `number` stays text throughout: "007" and "7" are different cars.
    """
    def col(*names: str) -> pd.Series:
        name = _first_col(df, *names)
        return df[name] if name else pd.Series([""] * len(df), index=df.index)

    out = pd.DataFrame(
        {
            "number": col("NUMBER"),
            "car_class": col("CLASS"),
            "team": col("TEAM"),
            "lap": pd.to_numeric(col("LAP_NUMBER"), errors="coerce"),
            "driver": col("DRIVER_NAME", "DRIVER"),
            # *_SECONDS are plain seconds; S1/S1_LARGE can be "m:ss.sss". parse_duration reads all.
            "s1": col("S1_SECONDS", "S1", "S1_LARGE").map(parse_duration),
            "s2": col("S2_SECONDS", "S2", "S2_LARGE").map(parse_duration),
            "s3": col("S3_SECONDS", "S3", "S3_LARGE").map(parse_duration),
            "lap_time": col("LAP_TIME").map(parse_duration),
            "elapsed": col("ELAPSED", "ELAPSED_TIME").map(parse_duration),
            "hour": col("HOUR"),  # time of day at the line, e.g. "13:45:12.345"
            "pit_in": col("CROSSING_FINISH_LINE_IN_PIT") != "",
            "pit_time": col("PIT_TIME").map(parse_duration),
            "flag_raw": col("FLAG_AT_FL", "FLAG").str.upper(),
            # LAP_IMPROVEMENT > 0 marks the car's official fastest lap (one per car). A raw-faster
            # lap without the mark was deleted (e.g. track limits), so prefer this for "best lap".
            "official_best": pd.to_numeric(col("LAP_IMPROVEMENT"), errors="coerce").fillna(0) > 0,
        }
    )
    out = out.dropna(subset=["lap"])
    out = out[out["number"] != ""]
    out["lap"] = out["lap"].astype(int)
    for c in TIME_COLS + ["elapsed", "pit_time"]:
        out[c] = out[c].astype("float64")
    out = out.sort_values(["number", "lap"]).reset_index(drop=True)
    out["flag"] = out["flag_raw"].map(FLAG_CODES).fillna("unknown")

    by_car = out.groupby("number", sort=False)
    # Out-lap: the lap after an in-lap. PIT_TIME also sits on the out-lap, so either signal counts.
    prev_pit_in = by_car["pit_in"].shift(fill_value=False).astype(bool)
    out["pit_out"] = prev_pit_in | out["pit_time"].notna()
    prev_flag = by_car["flag"].shift().fillna("green")

    periods = detect_neutral_periods(out)
    lap_start = out["elapsed"] - out["lap_time"]
    in_neutral = pd.Series(False, index=out.index)
    for start, end in periods:
        overlap = np.minimum(out["elapsed"], end) - np.maximum(lap_start, start)
        in_neutral |= overlap >= MIN_OVERLAP_S
    # ...and was actually slowed by it: a lap that only grazes the start or end of a period at
    # racing speed (e.g. the first green lap after a restart) is left untagged.
    own_slow = pd.Series(False, index=out.index)
    for c in SECTOR_COLS:
        own_slow |= out[c] > LAP_SLOW_RATIO * out.groupby("number")[c].transform("median")
    out["detected_neutral"] = in_neutral & own_slow

    def tags(row) -> str:
        t = []
        if row.lap == 1:
            t.append("START")
        if row.pit_in:
            t.append("PIT IN")
        if row.pit_out:
            t.append("PIT OUT")
        for status in dict.fromkeys([row.flag, row.prev_flag]):  # end status first, no duplicates
            if status in NEUTRALISED:
                t.append(NEUTRALISED[status])
        if row.detected_neutral and not any(x in t for x in ("SC", "FCY", "RED")):
            t.append("FCY")
        return " · ".join(t)

    out["prev_flag"] = prev_flag
    out["tags"] = out.apply(tags, axis=1)
    out["affected"] = out["tags"] != ""
    out = out.drop(columns=["prev_flag"])

    # Running order at the end of each lap: order in which the cars completed that lap.
    out["pos"] = out.groupby("lap")["elapsed"].rank(method="first").astype("Int64")
    out["class_pos"] = out.groupby(["lap", "car_class"])["elapsed"].rank(method="first").astype("Int64")
    add_laps_down(out)

    # Personal best so far (F1 green): strictly faster than every earlier counted lap of that car.
    for include_affected in (False, True):
        for include_lapped in (False, True):
            counted = counted_laps(out, include_affected, include_lapped)
            for c in TIME_COLS:
                v = out[c].where(counted)
                prev_best = v.groupby(out["number"]).transform(lambda s: s.cummin().ffill().shift())
                out[pb_column(c, include_affected, include_lapped)] = (
                    counted & v.notna() & (prev_best.isna() | (v < prev_best))
                )
    out.attrs["neutral_periods"] = periods
    return out


def counted_laps(laps: pd.DataFrame, include_affected: bool, include_lapped: bool) -> pd.Series:
    """Laps that take part in purple/green: not affected and not lapped, unless included."""
    keep = pd.Series(True, index=laps.index)
    if not include_affected:
        keep &= ~laps["affected"]
    if not include_lapped:
        keep &= ~laps["lapped"]
    return keep


def pb_column(col: str, include_affected: bool, include_lapped: bool) -> str:
    """Name of the personal-best-so-far flag column for these counting options."""
    return f"{col}_pb_{'all' if include_affected else 'clean'}{'' if include_lapped else '_unlapped'}"


def add_laps_down(laps: pd.DataFrame, threshold: int = LAPS_DOWN_THRESHOLD) -> None:
    """Add leader_lap, laps_down and lapped columns (in place), per class.

    For a car's lap N ending at elapsed time T: leader_lap = the most laps any car in the same
    class had completed by T (i.e. the class leader's lap count then), laps_down = leader_lap - N.
    Done per class because Hypercars naturally lap the LMGT3 cars.
    """
    leader = pd.Series(np.nan, index=laps.index)
    for _, g in laps.groupby("car_class"):
        g = g[g["elapsed"].notna()]
        if g.empty:
            continue
        done = g.sort_values("elapsed")
        t = done["elapsed"].to_numpy()
        most_laps = np.maximum.accumulate(done["lap"].to_numpy())
        idx = np.searchsorted(t, g["elapsed"].to_numpy(), side="right") - 1
        leader[g.index] = most_laps[idx]
    laps["leader_lap"] = leader.astype("Int64")
    laps["laps_down"] = (leader - laps["lap"]).astype("Int64")
    laps["lapped"] = (laps["laps_down"] > threshold).fillna(False).astype(bool)


def detect_neutral_periods(laps: pd.DataFrame) -> list[tuple[float, float]]:
    """Race-time windows (elapsed seconds) where most cars on track were lapping slowly at once.

    Each sector is an interval in race time; it is "slow" when it took SLOW_RATIO x the car's
    own median for that sector. Pit sectors (S3 of an in-lap, S1 of an out-lap) are ignored.
    Sampling every SAMPLE_S seconds, a moment is neutralised when at least SLOW_SHARE of the
    cars with a known sector at that moment are slow.
    """
    rows = laps.dropna(subset=["elapsed", "s1", "s2", "s3"])
    start = rows["elapsed"] - rows["s1"] - rows["s2"] - rows["s3"]
    bounds = [start, start + rows["s1"], start + rows["s1"] + rows["s2"], rows["elapsed"]]
    medians = {c: rows.groupby("number")[c].transform("median") for c in SECTOR_COLS}
    pit_sector = {"s1": rows["pit_out"], "s2": pd.Series(False, index=rows.index), "s3": rows["pit_in"]}

    intervals = []
    for i, c in enumerate(SECTOR_COLS):
        intervals.append(
            pd.DataFrame(
                {
                    "number": rows["number"],
                    "t0": bounds[i],
                    "t1": bounds[i + 1],
                    "slow": rows[c] > SLOW_RATIO * medians[c],
                    "skip": pit_sector[c] | (rows["lap"] == 1),
                }
            )
        )
    iv = pd.concat(intervals).sort_values(["number", "t0"])
    iv = iv[~iv["skip"]]
    if iv.empty:
        return []

    t = np.arange(0.0, float(rows["elapsed"].max()), SAMPLE_S)
    slow_count = np.zeros_like(t)
    known_count = np.zeros_like(t)
    for _, car in iv.groupby("number"):
        t0, t1, slow = car["t0"].to_numpy(), car["t1"].to_numpy(), car["slow"].to_numpy()
        idx = np.searchsorted(t0, t, side="right") - 1
        valid = (idx >= 0) & (t < t1[np.clip(idx, 0, None)])
        known_count += valid
        slow_count += valid & slow[np.clip(idx, 0, None)]
    neutral = (known_count >= MIN_CARS) & (slow_count >= SLOW_SHARE * np.maximum(known_count, 1))

    periods: list[tuple[float, float]] = []
    run_start = None
    for ti, n in zip(t, neutral):
        if n and run_start is None:
            run_start = ti
        elif not n and run_start is not None:
            periods.append((run_start, ti))
            run_start = None
    if run_start is not None:
        periods.append((run_start, float(t[-1])))
    return periods
