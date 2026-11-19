"""Sector comparison table for one lap: F1-style purple/green colouring.

Kept independent of the map: `lap_rows` / `sector_status` are plain pandas, and
`render_sector_table` only needs the lap table from wec.laps and returns the clicked car.

Colouring rules (per selected lap):
- purple: fastest time in that column on this lap, per class (or overall).
- green:  the car's personal best so far in the race (and not purple).
- Laps tagged PIT IN / PIT OUT / SC / FCY / RED are greyed out and, unless `include_affected`,
  take no part in either calculation (they get no colour and don't count as a previous best).
- Laps where the car was more than LAPS_DOWN_THRESHOLD laps behind its class leader get a
  "+N L" badge on the car number, are dimmed, and likewise are left out unless `include_lapped`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from wec.config import LAPS_DOWN_THRESHOLD
from wec.laps import TIME_COLS, counted_laps, pb_column

PURPLE = "purple"
GREEN = "green"
NONE = ""

# Table cell styles. Map colours for the same states live in STATUS_MAP_COLORS.
_CELL_CSS = {
    PURPLE: "background-color: #8e3fd1; color: #fff; font-weight: 600",
    GREEN: "background-color: #1f9d55; color: #fff; font-weight: 600",
}
_AFFECTED_CSS = "color: #8a8a8a; font-style: italic"
_LAPPED_CSS = "color: #a3a3a3"
_SELECTED_CSS = "font-weight: 700"

STATUS_MAP_COLORS = {PURPLE: "#b04dff", GREEN: "#22c55e", NONE: "#ffd400", "excluded": "#9a9a9a"}

COLUMN_LABELS = {"s1": "S1", "s2": "S2", "s3": "S3", "lap_time": "Lap time"}


@dataclass
class TableOptions:
    overall: bool = False  # purple across all classes instead of per class
    include_affected: bool = False  # let pit/SC/FCY laps count for purple/green
    classes: list[str] | None = None  # None = all classes
    include_lapped: bool = False  # let laps run far behind the class leader count for purple/green


def lap_numbers(laps: pd.DataFrame) -> list[int]:
    return sorted(int(n) for n in laps["lap"].unique())


def neutralised_laps(laps: pd.DataFrame) -> dict[int, str]:
    """Lap number -> 'SC'/'FCY'/... if any car's lap carried that tag (for the lap picker)."""
    out: dict[int, str] = {}
    for tag in ("RED", "SC", "FCY"):
        for n in laps.loc[laps["tags"].str.contains(tag), "lap"].unique():
            out.setdefault(int(n), tag)
    return out


def sector_status(
    rows: pd.DataFrame, *, overall: bool, include_affected: bool, include_lapped: bool = False
) -> pd.DataFrame:
    """For one lap's rows: a frame of PURPLE/GREEN/NONE per time column (same index as rows)."""
    counted = counted_laps(rows, include_affected, include_lapped)
    status = pd.DataFrame(NONE, index=rows.index, columns=TIME_COLS)
    for c in TIME_COLS:
        v = rows[c].where(counted)
        best = v.min() if overall else v.groupby(rows["car_class"]).transform("min")
        purple = v.notna() & (v == best)
        green = rows[pb_column(c, include_affected, include_lapped)] & ~purple
        status.loc[purple, c] = PURPLE
        status.loc[green, c] = GREEN
    return status


def lap_rows(laps: pd.DataFrame, lap: int, opts: TableOptions) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(rows, status) for one lap, sorted by position and filtered by class.

    Purple is worked out before the class filter, so 'overall' still means all cars.
    """
    rows = laps[laps["lap"] == lap].sort_values("pos")
    status = sector_status(
        rows, overall=opts.overall, include_affected=opts.include_affected, include_lapped=opts.include_lapped
    )
    if opts.classes is not None:
        keep = rows["car_class"].isin(opts.classes)
        rows, status = rows[keep], status[keep]
    return rows, status


def map_sector_colors(
    laps: pd.DataFrame, lap: int, number: str, include_affected: bool, include_lapped: bool = False
) -> list[str] | None:
    """S1/S2/S3 colours for one car on one lap, compared with its class (for the track map)."""
    rows = laps[laps["lap"] == lap]
    if number not in set(rows["number"]):
        return None
    status = sector_status(rows, overall=False, include_affected=include_affected, include_lapped=include_lapped)
    i = rows.index[rows["number"] == number][0]
    if not counted_laps(rows.loc[[i]], include_affected, include_lapped).iloc[0]:
        return [STATUS_MAP_COLORS["excluded"]] * 3
    return [STATUS_MAP_COLORS[status.at[i, c]] for c in ("s1", "s2", "s3")]


# --- formatting -------------------------------------------------------------------


def fmt_lap(seconds: float | None) -> str:
    """93.958 -> '1:33.958'."""
    if seconds is None or pd.isna(seconds):
        return ""
    ms = round(seconds * 1000)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def laps_down_badge(laps_down) -> str:
    """22 -> '+22 L' when over the threshold, else ''."""
    if pd.isna(laps_down) or laps_down <= LAPS_DOWN_THRESHOLD:
        return ""
    return f"+{int(laps_down)} L"


def laps_down_tooltip(row: pd.Series) -> str:
    """Explanation for the badge, e.g. "...Its lap 60 was completed at 14:48:03, when..."."""
    if not laps_down_badge(row["laps_down"]):
        return ""
    when = str(row.get("hour") or "").split(".")[0] or "an unknown time"
    n = int(row["laps_down"])
    return (
        f"This car was {n} lap{'s' if n != 1 else ''} behind its class leader. "
        f"Its lap {row['lap']} was completed at {when}, when the class leader was on lap {row['leader_lap']}."
    )


def fmt_sector(seconds: float | None) -> str:
    """19.221 -> '19.221' (seconds only, also past a minute: '75.130')."""
    return "" if seconds is None or pd.isna(seconds) else f"{seconds:.3f}"


# --- rendering --------------------------------------------------------------------


def render_sector_table(
    rows: pd.DataFrame,
    status: pd.DataFrame,
    *,
    selected: list[str],
    on_select,
    key: str,
    height: int | None = None,
    multi: bool = True,
):
    """Show the table; selecting rows calls `on_select([car numbers])` (empty list = none).

    `selected` cars are shown in bold. With `multi`, several rows can be ticked (e.g. two to compare).

    Sorting is done by clicking a column header (Streamlit sorts on the underlying numbers,
    so times sort correctly even though they are displayed as text-like m:ss.sss).
    """
    view = pd.DataFrame(
        {
            "Pos": rows["pos"].to_numpy(),
            "Class pos": rows["class_pos"].to_numpy(),
            # Car number stays text ("007" != "7"); the badge is appended for far-behind laps.
            "Car": [
                f"{n}  {b}" if b else n for n, b in zip(rows["number"], rows["laps_down"].map(laps_down_badge))
            ],
            "Team": rows["team"].to_numpy(),
            "Class": rows["car_class"].to_numpy(),
            "Driver": rows["driver"].to_numpy(),
            "S1": rows["s1"].to_numpy(),
            "S2": rows["s2"].to_numpy(),
            "S3": rows["s3"].to_numpy(),
            "Lap time": rows["lap_time"].to_numpy(),
            "Tags": rows["tags"].to_numpy(),
        }
    )
    st_status = status.reset_index(drop=True)
    affected = rows["affected"].to_numpy()
    lapped = rows["lapped"].to_numpy()
    numbers = rows["number"].tolist()

    def style_row(r: pd.Series) -> list[str]:
        i = r.name
        base = _AFFECTED_CSS if affected[i] else _LAPPED_CSS if lapped[i] else ""
        if numbers[i] in selected:
            base = f"{base}; {_SELECTED_CSS}".strip("; ")
        css = []
        for col in view.columns:
            src = next((k for k, lbl in COLUMN_LABELS.items() if lbl == col), None)
            cell = _CELL_CSS.get(st_status.at[i, src], "") if src else ""
            css.append("; ".join(x for x in (base, cell) if x))
        return css

    # st.dataframe can't show per-cell tooltips (Styler.set_tooltips leaks <span> markup into
    # every cell), so the badge explanation is listed under the table instead.
    styler = (
        view.style.apply(style_row, axis=1)
        .format({"S1": fmt_sector, "S2": fmt_sector, "S3": fmt_sector, "Lap time": fmt_lap})
    )

    def handle_select() -> None:
        on_select([numbers[r] for r in st.session_state[key].selection.rows])

    st.dataframe(
        styler,
        key=key,
        on_select=handle_select,
        selection_mode="multi-row" if multi else "single-row",
        hide_index=True,
        height=height or min(36 * (len(view) + 1) + 3, 900),
        column_config={
            "Pos": st.column_config.NumberColumn(width=50),
            "Class pos": st.column_config.NumberColumn(width=75),
            "Car": st.column_config.TextColumn(
                width=90, help=f"+N L: more than {LAPS_DOWN_THRESHOLD} laps behind the class leader (details below the table)"
            ),
            "Class": st.column_config.TextColumn(width=95),
        },
    )
    notes = [
        f"**#{r['number']} +{int(r['laps_down'])} L:** {laps_down_tooltip(r)}"
        for _, r in rows[rows["lapped"]].iterrows()
    ]
    if notes:
        st.caption("  \n".join(notes))
