"""Entry list: every car grouped by class, with laps completed and best lap.

`entry_table` is plain pandas; `render_entry_list` draws one selectable table per class and
reports the ticked cars (car numbers stay text: "007" and "7" are different cars).
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from wec.parsing import Car
from wec.sector_table import fmt_lap

CLASS_ORDER = ["HYPERCAR", "LMGT3"]
CLASS_NAMES = {"HYPERCAR": "Hypercar"}


def class_sort_key(cls: str) -> tuple[int, str]:
    up = cls.upper()
    return (CLASS_ORDER.index(up) if up in CLASS_ORDER else len(CLASS_ORDER), up)


def _drivers(car: Car) -> str:
    """'(1) Mike CONWAY, (2) Kamui KOBAYASHI, Nyck DE VRIES' – numbered drivers first, by number."""
    def order(d: str) -> tuple[int, int]:
        n = car.driver_numbers.get(d, "")
        return (0, int(n)) if n.isdigit() else (1, car.drivers.index(d))

    return ", ".join(
        f"({car.driver_numbers[d]}) {d}" if d in car.driver_numbers else d for d in sorted(car.drivers, key=order)
    )


def _best_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Per car: laps completed, best lap (Al Kamel's official best, else fastest clean lap) and who set it."""
    rows = []
    for number, g in laps.groupby("number", sort=False):
        best = g[g["official_best"] & g["lap_time"].notna()]
        if best.empty:
            clean = g[~g["affected"] & g["lap_time"].notna()]
            best = clean.loc[[clean["lap_time"].idxmin()]] if not clean.empty else best
        r = best.iloc[0] if not best.empty else None
        rows.append(
            {
                "number": number,
                "laps": int(g["lap"].max()),
                "best": None if r is None else float(r["lap_time"]),
                "best_lap": None if r is None else int(r["lap"]),
                "best_by": "" if r is None else r["driver"],
            }
        )
    return pd.DataFrame(rows).set_index("number")


def entry_table(cars: list[Car], laps: pd.DataFrame | None) -> pd.DataFrame:
    """One row per car: number, class, team, manufacturer, drivers, laps, best, best_lap, best_by."""
    stats = _best_laps(laps) if laps is not None and not laps.empty else None
    rows = []
    for c in cars:
        s = stats.loc[c.number] if stats is not None and c.number in stats.index else None
        rows.append(
            {
                "number": c.number,
                "car_class": c.car_class,
                "team": c.team,
                "manufacturer": c.manufacturer or c.vehicle,
                "drivers": _drivers(c),
                "laps": None if s is None else s["laps"],
                "best": None if s is None else s["best"],
                "best_lap": None if s is None else s["best_lap"],
                "best_by": "" if s is None else s["best_by"],
            }
        )
    return pd.DataFrame(rows)


def render_entry_list(
    table: pd.DataFrame,
    *,
    selected: list[str],
    on_select: Callable[[list[str]], None],
    key: str = "entry",
    max_height: int = 380,
) -> None:
    """One table per class. Ticking rows (in any of the tables) calls on_select([car numbers])."""
    classes = sorted(table["car_class"].unique(), key=class_sort_key)
    shown: dict[str, list[str]] = {}

    def changed() -> None:
        picked: list[str] = []
        for cls, numbers in shown.items():
            state = st.session_state.get(f"{key}_{cls}")
            if state is not None:
                picked += [numbers[i] for i in state.selection.rows if i < len(numbers)]
        on_select(picked)

    for cls in classes:
        rows = table[table["car_class"] == cls].reset_index(drop=True)
        shown[cls] = rows["number"].tolist()
        st.markdown(f"**{CLASS_NAMES.get(cls.upper(), cls)}** · {len(rows)} cars")
        view = pd.DataFrame(
            {
                "Car": rows["number"],
                "Team": rows["team"],
                "Manufacturer": rows["manufacturer"],
                "Drivers": rows["drivers"],
                "Laps": rows["laps"],
                "Best lap": rows["best"],
                "On lap": rows["best_lap"],
                "Set by": rows["best_by"],
            }
        )
        bold = rows["number"].isin(selected).to_numpy()
        styler = view.style.apply(
            lambda r: ["font-weight: 700" if bold[r.name] else "" for _ in r], axis=1
        ).format({"Best lap": fmt_lap, "Laps": lambda v: "" if pd.isna(v) else f"{int(v)}",
                  "On lap": lambda v: "" if pd.isna(v) else f"{int(v)}"})
        st.dataframe(
            styler,
            key=f"{key}_{cls}",
            on_select=changed,
            selection_mode="multi-row",
            hide_index=True,
            width="stretch",
            height=min(35 * (len(view) + 1) + 3, max_height),
            column_config={
                "Car": st.column_config.TextColumn(width=55),
                "Team": st.column_config.TextColumn(width=180),
                "Manufacturer": st.column_config.TextColumn(width=110),
                "Drivers": st.column_config.TextColumn(width=400),
                "Laps": st.column_config.Column(width=55),
                "Best lap": st.column_config.Column(width=85, help="Al Kamel's official best lap (else the fastest clean lap)"),
                "On lap": st.column_config.Column(width=60),
                "Set by": st.column_config.TextColumn(width=170),
            },
        )
