"""Head-to-head view (Streamlit): two cars compared by sector, on the map and through the race.

Logic lives in wec.head_to_head; this module only draws. Car A is always CAR_COLORS[0] (blue),
car B CAR_COLORS[1] (orange), in the selectors, breakdown, map and both charts.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable

import altair as alt
import pandas as pd
import streamlit as st

from wec.head_to_head import (
    LAP_RANGE,
    MODES,
    SAME_LAP,
    car_info,
    compare,
    deltas,
    gap_by_lap,
    neutral_periods,
    sector_deltas_by_lap,
    stints,
)
from wec.sector_table import fmt_lap, fmt_sector
from wec.track import Track
from wec.track_map import track_map

CAR_COLORS = ("#3b9eff", "#ff8a1f")
TIE_COLOR = "#9a9a9a"
NEUTRAL_FILL = "#f5c518"

H2H_A, H2H_B, H2H_MODE, H2H_RANGE, H2H_STINT = "h2h_a", "h2h_b", "h2h_mode", "h2h_range", "h2h_stint"
PERSISTENT_KEYS = (H2H_A, H2H_B, H2H_MODE, H2H_RANGE)
GAP_CHART, TREND_CHART = "Gap through the race", "Sector gains lap by lap"


@dataclass(frozen=True)
class StateKeys:
    """Session-state keys of one head-to-head view, so several can be open at once."""

    prefix: str = "h2h"

    @property
    def a(self) -> str:
        return f"{self.prefix}_a"

    @property
    def b(self) -> str:
        return f"{self.prefix}_b"

    @property
    def mode(self) -> str:
        return f"{self.prefix}_mode"

    @property
    def range(self) -> str:
        return f"{self.prefix}_range"

    @property
    def stint(self) -> str:
        return f"{self.prefix}_stint"

    @property
    def chart(self) -> str:
        return f"{self.prefix}_chart"

    @property
    def persistent(self) -> tuple[str, ...]:
        """Keys to keep while the view isn't drawn (Streamlit drops undrawn widgets' state)."""
        return (self.a, self.b, self.mode, self.range, self.stint, self.chart)


def _surname(name: str) -> str:
    caps = [w for w in name.split() if w.isupper() and len(w) > 1]
    return " ".join(caps).title() if caps else name


def _swatch(i: int) -> str:
    return f'<span style="color:{CAR_COLORS[i]};font-size:1.1em">■</span>'


def _fmt(col: str, v: float | None) -> str:
    return fmt_lap(v) if col == "lap_time" else fmt_sector(v)


def _breakdown_html(a: str, b: str, ta, tb, d: dict) -> str:
    rows = [("S1", "s1"), ("S2", "s2"), ("S3", "s3"), ("Lap", "lap_time")]
    biggest = max([abs(v) for v in d.values() if v is not None] or [1.0]) or 1.0
    body = []
    for label, col in rows:
        delta = d[col]
        if delta is None:
            gap, bar = "–", ""
        else:
            if abs(delta) < 0.0005:
                gap, side, color = "level", None, TIE_COLOR
            else:
                winner, side = (a, "left") if delta > 0 else (b, "right")
                color = CAR_COLORS[0] if delta > 0 else CAR_COLORS[1]
                gap = f'<span style="color:{color};font-weight:700">#{html.escape(winner)} {abs(delta):.3f}</span>'
            width = 0 if side is None else max(2.0, 50 * abs(delta) / biggest)
            fill = (
                f'<div style="position:absolute;top:3px;bottom:3px;{"right:50%" if side == "left" else "left:50%"};'
                f'width:{width:.1f}%;background:{color};border-radius:3px"></div>'
                if side
                else ""
            )
            bar = f'<div style="position:relative;height:22px">{fill}<div style="position:absolute;left:50%;top:0;bottom:0;border-left:1px solid rgba(128,128,128,.6)"></div></div>'
        strong = "font-weight:700;" if col == "lap_time" else ""
        body.append(
            f'<tr style="{strong}border-top:1px solid rgba(128,128,128,.25)">'
            f'<td style="padding:6px 8px">{label}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-variant-numeric:tabular-nums">{_fmt(col, ta.get(col)) or "–"}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-variant-numeric:tabular-nums">{_fmt(col, tb.get(col)) or "–"}</td>'
            f'<td style="padding:6px 8px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums">{gap}</td>'
            f'<td style="padding:6px 8px;width:38%">{bar}</td></tr>'
        )
    head = (
        '<tr style="opacity:.75;font-size:.85em">'
        '<th style="text-align:left;padding:4px 8px"></th>'
        f'<th style="text-align:right;padding:4px 8px">{_swatch(0)} #{html.escape(a)}</th>'
        f'<th style="text-align:right;padding:4px 8px">{_swatch(1)} #{html.escape(b)}</th>'
        '<th style="text-align:right;padding:4px 8px">Gap</th>'
        f'<th style="padding:4px 8px"><div style="display:flex;justify-content:space-between">'
        f'<span>◀ #{html.escape(a)} gains</span><span>#{html.escape(b)} gains ▶</span></div></th></tr>'
    )
    return f'<table style="width:100%;border-collapse:collapse;font-size:15px">{head}{"".join(body)}</table>'


def _car_scale(a: str, b: str) -> alt.Scale:
    return alt.Scale(domain=[f"#{a}", f"#{b}"], range=list(CAR_COLORS))


def _gap_chart(laps: pd.DataFrame, a: str, b: str, lap: int) -> alt.Chart | None:
    gap = gap_by_lap(laps, a, b)
    if gap.empty:
        return None
    gap["ahead"] = gap["gap"].map(lambda g: f"#{a}" if g > 0 else f"#{b}")
    gap["gap_abs"] = gap["gap"].abs()
    scale = _car_scale(a, b)
    pick = alt.selection_point(name="gap_lap", fields=["lap"], on="click", nearest=True, clear=False)
    x = alt.X("lap:Q", title="Lap", scale=alt.Scale(domain=[gap["lap"].min(), gap["lap"].max()], nice=False))

    periods = neutral_periods(gap)
    layers = []
    if not periods.empty:
        periods = periods.assign(x0=periods["start"] - 0.5, x1=periods["end"] + 0.5)
        layers.append(
            alt.Chart(periods)
            .mark_rect(color=NEUTRAL_FILL, opacity=0.18)
            .encode(x=alt.X("x0:Q"), x2="x1:Q", tooltip=[alt.Tooltip("kind:N", title="Neutralised"), "start:Q", "end:Q"])
        )
    layers += [
        alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="gray", opacity=0.6).encode(y="y:Q"),
        alt.Chart(gap).mark_line(color="#888", strokeWidth=1.5).encode(x=x, y=alt.Y("gap:Q")),
        alt.Chart(gap)
        .mark_circle(size=34, opacity=0.95)
        .encode(
            x=x,
            y=alt.Y("gap:Q", title=f"Gap (s), above 0 = #{a} ahead"),
            color=alt.Color("ahead:N", scale=scale, title="Ahead"),
            tooltip=[
                alt.Tooltip("lap:Q", title="Lap"),
                alt.Tooltip("ahead:N", title="Ahead"),
                alt.Tooltip("gap_abs:Q", title="Gap (s)", format=".3f"),
            ],
        )
        .add_params(pick),
    ]
    pits = pd.concat(
        [
            gap.loc[gap["pit_in_a"], ["lap", "gap"]].assign(car=f"#{a}"),
            gap.loc[gap["pit_in_b"], ["lap", "gap"]].assign(car=f"#{b}"),
        ]
    )
    if not pits.empty:
        layers.append(
            alt.Chart(pits)
            .mark_point(shape="triangle-down", size=150, filled=True, stroke="#000", strokeWidth=0.6, yOffset=-12)
            .encode(x=x, y="gap:Q", color=alt.Color("car:N", scale=scale, legend=None), tooltip=[alt.Tooltip("car:N", title="Pit stop"), "lap:Q"])
        )
    layers.append(alt.Chart(pd.DataFrame({"lap": [lap]})).mark_rule(color="#e5e5e5", strokeDash=[4, 3]).encode(x="lap:Q"))
    return alt.layer(*layers).properties(height=420)


def _trend_chart(laps: pd.DataFrame, a: str, b: str, include: bool, lap: int) -> tuple[alt.Chart | None, list[str]]:
    d = sector_deltas_by_lap(laps, a, b, include)
    if d.empty:
        return None, []
    d["gainer"] = d["delta"].map(lambda v: f"#{a}" if v > 0 else f"#{b}")
    d["gain"] = d["delta"].abs()
    d = d.sort_values(["sector", "lap"])
    d["trend"] = d.groupby("sector")["delta"].transform(lambda s: s.rolling(5, center=True, min_periods=2).median())
    q = max(0.3, float(d["delta"].abs().quantile(0.95)) * 1.4)
    scale = _car_scale(a, b)
    pit_laps = pd.concat(
        [
            laps.loc[(laps["number"] == a) & laps["pit_in"], ["lap"]].assign(car=f"#{a}"),
            laps.loc[(laps["number"] == b) & laps["pit_in"], ["lap"]].assign(car=f"#{b}"),
        ]
    )
    x = alt.X("lap:Q", title="Lap", scale=alt.Scale(domain=[int(d["lap"].min()), int(d["lap"].max())], nice=False))

    summary = []
    panels = []
    for sector in ("S1", "S2", "S3"):
        s = d[d["sector"] == sector]
        if s.empty:
            continue
        share = (s["delta"] > 0).mean()
        med = s["delta"].median()
        leader = a if med > 0 else b
        summary.append(
            f"**{sector}**: {_swatch(0 if med > 0 else 1)} #{leader} faster by a median "
            f"{abs(med):.3f} s; #{a} faster on {share:.0%} of {len(s)} laps"
        )
        layers = [
            alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="gray", opacity=0.6).encode(y="y:Q"),
            alt.Chart(s)
            .mark_bar(opacity=0.85)
            .encode(
                x=x,
                y=alt.Y("delta:Q", title=f"{sector} Δ (s)", scale=alt.Scale(domain=[-q, q], clamp=True)),
                color=alt.Color("gainer:N", scale=scale, title="Gained"),
                tooltip=[alt.Tooltip("lap:Q", title="Lap"), alt.Tooltip("gainer:N", title="Gained"), alt.Tooltip("gain:Q", title="By (s)", format=".3f")],
            ),
            alt.Chart(s).mark_line(color="#e5e5e5", strokeWidth=1.5).encode(x=x, y=alt.Y("trend:Q", scale=alt.Scale(domain=[-q, q], clamp=True))),
        ]
        if not pit_laps.empty:
            layers.append(
                alt.Chart(pit_laps).mark_rule(strokeDash=[2, 3], opacity=0.7).encode(x="lap:Q", color=alt.Color("car:N", scale=scale, legend=None))
            )
        layers.append(alt.Chart(pd.DataFrame({"lap": [lap]})).mark_rule(color="#e5e5e5", strokeDash=[4, 3]).encode(x="lap:Q"))
        panels.append(alt.layer(*layers).properties(height=100))
    return alt.vconcat(*panels).resolve_scale(color="shared"), summary


def render_head_to_head(
    laps: pd.DataFrame,
    *,
    lap: int,
    include_affected: bool,
    set_lap: Callable[[int], None],
    track: Track | None,
    sector_boundaries: list[float] | None,
    prefix: str = "h2h",
    lap_controls: Callable[[], None] | None = None,
) -> None:
    """Draw one head-to-head view. `prefix` namespaces its widget state (see StateKeys);
    `lap_controls`, if given, draws the shared lap selector in the view's header row."""
    k = StateKeys(prefix)
    info = car_info(laps).set_index("number")
    numbers = info.index.tolist()
    if len(numbers) < 2:
        st.info("Need at least two cars to compare.")
        return
    if st.session_state.get(k.a) not in numbers:
        st.session_state[k.a] = numbers[0]
    if st.session_state.get(k.b) not in numbers:
        st.session_state[k.b] = next(n for n in numbers if n != st.session_state[k.a])
    if st.session_state.get(k.mode) not in MODES:
        st.session_state[k.mode] = SAME_LAP

    def label(n: str) -> str:
        r = info.loc[n]
        return f"#{n} · {r['team']} · {r['car_class']} · {' / '.join(_surname(d) for d in r['drivers'])}"

    def swap() -> None:
        st.session_state[k.a], st.session_state[k.b] = st.session_state[k.b], st.session_state[k.a]

    col_a, col_swap, col_b = st.columns([10, 1, 10], vertical_alignment="bottom")
    with col_a:
        st.markdown(f"{_swatch(0)} **Car A**", unsafe_allow_html=True)
        st.selectbox("Car A", numbers, key=k.a, format_func=label, label_visibility="collapsed")
    col_swap.button("⇄", on_click=swap, help="Swap A and B", width="stretch")
    with col_b:
        st.markdown(f"{_swatch(1)} **Car B**", unsafe_allow_html=True)
        st.selectbox("Car B", numbers, key=k.b, format_func=label, label_visibility="collapsed")

    a, b = st.session_state[k.a], st.session_state[k.b]
    if a == b:
        st.warning("Pick two different cars.")
        return
    cls_a, cls_b = info.at[a, "car_class"], info.at[b, "car_class"]
    if cls_a != cls_b:
        st.caption(f":material/info: Different classes ({cls_a} vs {cls_b}): the gaps include the difference in class performance.")

    mode_col, lap_col = st.columns([5, 6], vertical_alignment="bottom")
    mode = mode_col.radio("Compare", MODES, key=k.mode, horizontal=True)
    if lap_controls:
        with lap_col:
            lap_controls()

    lap_numbers = sorted(int(n) for n in laps["lap"].unique())
    lo, hi = lap_numbers[0], lap_numbers[-1]
    lap_range = (lo, hi)
    if mode == LAP_RANGE:
        current = st.session_state.get(k.range)
        if not (isinstance(current, (tuple, list)) and len(current) == 2 and lo <= current[0] <= current[1] <= hi):
            st.session_state[k.range] = (lo, hi)
        options = {"": None}
        for i, car in enumerate((a, b)):
            for k, (s0, s1) in enumerate(stints(laps, car), start=1):
                options[f"#{car} stint {k}: laps {s0}–{s1}"] = (s0, s1)

        def pick_stint() -> None:
            chosen = options.get(st.session_state[k.stint])
            if chosen:
                st.session_state[k.range] = chosen

        stint_col, range_col = st.columns([2, 5])
        stint_col.selectbox("Stint", list(options), key=k.stint, on_change=pick_stint, format_func=lambda s: s or "Choose a stint…")
        lap_range = range_col.slider("Laps", lo, hi, key=k.range)
    elif mode == SAME_LAP:
        st.caption(f"Lap {lap}: change it with the lap selector, ← →, or by clicking the gap chart.")

    ta, tb = compare(laps, a, b, mode, lap=lap, lap_range=tuple(lap_range), include_affected=include_affected)
    d = deltas(ta, tb)

    left, right = st.columns([10, 11])
    with left:
        st.html(_breakdown_html(a, b, ta, tb, d))
        for i, t in enumerate((ta, tb)):
            driver = f" · {t.driver}" if t.driver else ""
            st.markdown(f"{_swatch(i)} #{t.number}: {t.source}{driver}", unsafe_allow_html=True)
        for note in ta.notes + tb.notes:
            st.caption(f":material/warning: {note}")
        if track and sector_boundaries:
            colors, labels = [], []
            for col in ("s1", "s2", "s3"):
                v = d[col]
                if v is None or abs(v) < 0.0005:
                    colors.append(TIE_COLOR)
                    labels.append({"text": "level" if v is not None else "–", "color": TIE_COLOR})
                else:
                    i = 0 if v > 0 else 1
                    colors.append(CAR_COLORS[i])
                    labels.append({"text": f"#{(a, b)[i]} −{abs(v):.3f}", "color": CAR_COLORS[i]})
            track_map(
                track,
                height=330,
                sectors={
                    "names": ["S1", "S2", "S3"],
                    "colors": colors,
                    "boundaries_m": sector_boundaries,
                    "editable": False,
                    "labels": labels,
                },
                key=f"{prefix}_map",
            )
            st.caption("Each sector is coloured by the faster car; the label is its margin.")

    with right:
        if st.session_state.get(k.chart) not in (GAP_CHART, TREND_CHART):
            st.session_state[k.chart] = GAP_CHART
        chart = st.segmented_control("Chart", [GAP_CHART, TREND_CHART], key=k.chart, label_visibility="collapsed")
        if chart == TREND_CHART:
            _show_trend(laps, a, b, include_affected, lap)
        else:
            _show_gap(laps, a, b, lap, set_lap, prefix)


def _show_gap(laps: pd.DataFrame, a: str, b: str, lap: int, set_lap: Callable[[int], None], prefix: str) -> None:
    gap_chart = _gap_chart(laps, a, b, lap)
    if gap_chart is None:
        st.info("These two cars have no laps in common.")
    else:
        gap_key = f"{prefix}_gap_{a}_{b}"

        def on_gap_click() -> None:
            sel = st.session_state[gap_key].selection.get("gap_lap") or []
            if isinstance(sel, dict):
                sel = [sel]
            if sel and "lap" in sel[0]:
                set_lap(int(sel[0]["lap"]))

        st.altair_chart(gap_chart, key=gap_key, on_select=on_gap_click, selection_mode="gap_lap", width="stretch")
        st.caption(
            "Gap at the line at the end of each lap (elapsed time). ▼ = pit stop, shaded = SC / FCY. "
            "Click a point to jump the lap selector to that lap."
        )


def _show_trend(laps: pd.DataFrame, a: str, b: str, include_affected: bool, lap: int) -> None:
    trend, summary = _trend_chart(laps, a, b, include_affected, lap)
    if trend is None:
        st.info("No laps where both cars have counted sector times.")
    else:
        st.markdown(" · ".join(summary), unsafe_allow_html=True)
        st.altair_chart(trend, width="stretch")
        st.caption(
            "Bars: per-lap sector delta, coloured by the car that gained (clipped at the extremes). "
            "Line: 5-lap rolling median. Dashed lines: pit stops (stint changes)."
            + ("" if include_affected else " Pit / SC / FCY laps are left out.")
        )
