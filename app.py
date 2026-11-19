"""WEC dashboard: entry list, track map, sector comparison and head-to-heads, one tab each.

Only the active tab is drawn on each run. Tabs: Entry List, Track Map, Sectors, plus one
closable tab per head-to-head. Open tabs and the active tab are kept in the URL query string.

Run with:  streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st
import fitz

from wec.parsing import ANALYSIS, cars_from_analysis, detect_file_type, merge_entries, read_alkamel_csv
from wec.races import find_races
from wec.config import LAPS_DOWN_THRESHOLD
from wec.entry_list import entry_table, render_entry_list
from wec.h2h_view import StateKeys, render_head_to_head
from wec.head_to_head import SAME_LAP
from wec.tab_bar import tab_bar
from wec.lap_keys import lap_keys
from wec.laps import laps_from_analysis
from wec.sector_table import (
    TableOptions,
    lap_numbers,
    lap_rows,
    map_sector_colors,
    neutralised_laps,
    render_sector_table,
)
from wec.sectors import SECTOR_COLORS, SECTOR_NAMES, load_sector_config, validate_boundaries
from wec.track import available_circuits, load_track
from wec.track_map import track_map

st.set_page_config(page_title="WEC Dashboard", layout="wide")


@st.cache_data
def cached_analysis(path: str, mtime: float) -> pd.DataFrame:
    """Analysis CSV as text columns; `mtime` makes the cache notice a replaced file."""
    return read_alkamel_csv(Path(path).read_bytes())


@st.cache_data
def cached_laps(path: str, mtime: float) -> pd.DataFrame:
    return laps_from_analysis(cached_analysis(path, mtime))


@st.cache_data
def cached_track(circuit: str):
    return load_track(circuit)


@st.cache_data
def pdf_to_image(data: bytes) -> bytes:
    """Convert the first page of a PDF into a PNG image."""
    doc = fitz.open(stream=data, filetype="pdf")
    page = doc.load_page(0)

    # Increase resolution for better image quality
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)

    return pix.tobytes("png")


# --- Sidebar: race (files come from data/<season>/<NN>_<circuit>/, see wec/races.py) ------

RACE = "race"
races = {r.id: r for r in find_races()}
if RACE not in st.session_state and st.query_params.get("race") in races:
    st.session_state[RACE] = st.query_params["race"]  # a shared link picks its race
with_data = [rid for rid, r in races.items() if r.analysis]


def race_changed() -> None:
    """Cars and laps differ between races: close head-to-heads and clear the car / lap choice."""
    for tab_id in list(st.session_state.get("h2h_tabs", [])):
        for key in StateKeys(tab_id).persistent:
            st.session_state.pop(key, None)
    st.session_state["h2h_tabs"] = []
    for key in ("sc_cars", "sc_lap", "sc_classes"):
        st.session_state.pop(key, None)
    if st.session_state.get("active_tab", "").startswith("h2h"):
        st.session_state["active_tab"] = "sectors"


with st.sidebar:
    st.header("Race")
    race_id = st.selectbox(
        "Race",
        with_data,
        key=RACE,
        format_func=lambda rid: races[rid].label,
        on_change=race_changed,
        label_visibility="collapsed",
        placeholder="No race data yet",
    )
race = races.get(race_id) if race_id else None
circuit = race.circuit if race and race.circuit in available_circuits() else None

# --- Read the race's files ----------------------------------------------------------

problems: list[str] = []

analysis_cars = None
laps = None  # one row per car per lap (wec.laps), for the sector comparison
if race and race.analysis:
    mtime = race.analysis.stat().st_mtime
    df = cached_analysis(str(race.analysis), mtime)
    if detect_file_type(df) == ANALYSIS:
        analysis_cars = cars_from_analysis(df)
        laps = cached_laps(str(race.analysis), mtime)
    else:
        problems.append(f"**{race.analysis.name}** doesn't look like an Analysis file (no LAP_NUMBER column).")

with st.sidebar:
    if race:
        folder = race.folder.relative_to(Path.cwd()) if race.folder.is_relative_to(Path.cwd()) else race.folder
        st.caption(
            f"Folder: `{folder.as_posix()}`  \n"
            f"Analysis: {race.analysis.name if race.analysis else '–'}  \n"
            f"Circuit map: {race.circuit_map.name if race.circuit_map else '–'}  \n"
            f"Centreline: {'data/tracks/' + race.circuit + '.geojson' if circuit else 'none for ' + race.circuit}"
        )
    pending = [r.label for r in races.values() if not r.analysis]
    if pending:
        st.caption("No Analysis file yet: " + ", ".join(pending))
    if not races:
        st.info("Add a race folder: data/<season>/<NN>_<circuit>/ with its Analysis CSV and circuit map.")

# --- Page layout: compact header so each tab fits in the viewport ---------------------

st.html(
    """<style>
    [data-testid="stMainBlockContainer"] { padding-top: 3.4rem; padding-bottom: 1rem; }
    [data-testid="stMainBlockContainer"] h1 { padding: 0 0 .4rem 0; font-size: 1.9rem; }
    </style>"""
)
st.title(f"{race.season} {race.name}" if race else "WEC Dashboard")

for p in problems:
    st.error(p)


# --- Shared state -------------------------------------------------------------------

SC_LAP, SC_CARS, SC_OVERALL, SC_INCLUDE, SC_CLASSES = "sc_lap", "sc_cars", "sc_overall", "sc_include", "sc_classes"
SC_INCLUDE_LAPPED = "sc_include_lapped"
COLOR_WORDS = {"#b04dff": "purple", "#22c55e": "green", "#ffd400": "yellow", "#9a9a9a": "grey (excluded lap)"}

ENTRY_TAB, MAP_TAB, SECTORS_TAB = "entry", "map", "sectors"
FIXED_TABS = {ENTRY_TAB: "Entry List", MAP_TAB: "Track Map", SECTORS_TAB: "Sectors"}
ACTIVE_TAB = "active_tab"
H2H_TABS = "h2h_tabs"  # ids of the open head-to-head tabs, in order; each id is its StateKeys prefix
H2H_COUNTER = "h2h_counter"
MAP_KEY = "track_map"

st.session_state.setdefault(ACTIVE_TAB, SECTORS_TAB)
st.session_state.setdefault(H2H_TABS, [])
st.session_state.setdefault(H2H_COUNTER, 0)


def h2h_pair(tab_id: str) -> tuple[str, str]:
    k = StateKeys(tab_id)
    return st.session_state.get(k.a, ""), st.session_state.get(k.b, "")


def open_h2h(a: str, b: str, *, switch: bool = True) -> None:
    """Open a head-to-head tab for a and b, or switch to the one already showing that pair."""
    for tab_id in st.session_state[H2H_TABS]:
        if set(h2h_pair(tab_id)) == {a, b}:
            if switch:
                st.session_state[ACTIVE_TAB] = tab_id
            return
    st.session_state[H2H_COUNTER] += 1
    tab_id = f"h2h{st.session_state[H2H_COUNTER]}"
    k = StateKeys(tab_id)
    st.session_state[k.a], st.session_state[k.b], st.session_state[k.mode] = a, b, SAME_LAP
    st.session_state[H2H_TABS] = [*st.session_state[H2H_TABS], tab_id]
    if switch:
        st.session_state[ACTIVE_TAB] = tab_id


def close_tab(tab_id: str) -> None:
    tabs = st.session_state[H2H_TABS]
    if tab_id not in tabs:
        return
    order = [*FIXED_TABS, *tabs]
    if st.session_state[ACTIVE_TAB] == tab_id:
        st.session_state[ACTIVE_TAB] = order[order.index(tab_id) - 1]  # the tab to its left
    st.session_state[H2H_TABS] = [t for t in tabs if t != tab_id]
    for key in StateKeys(tab_id).persistent:
        st.session_state.pop(key, None)


def select_tab(tab_id: str) -> None:
    if tab_id in FIXED_TABS or tab_id in st.session_state[H2H_TABS]:
        st.session_state[ACTIVE_TAB] = tab_id


# Restore the open tabs from the URL once per session: ?tab=sectors&h2h=50v8,51v7
# (an open head-to-head is written as "AvB"; tab= names the active one the same way).
if "url_restored" not in st.session_state:
    st.session_state["url_restored"] = True
    for spec in filter(None, st.query_params.get("h2h", "").split(",")):
        a, _, b = spec.partition("v")
        if a and b and a != b:
            open_h2h(a, b, switch=False)
    wanted = st.query_params.get("tab", SECTORS_TAB)
    if wanted in FIXED_TABS:
        st.session_state[ACTIVE_TAB] = wanted
    else:
        a, _, b = wanted.partition("v")
        for tab_id in st.session_state[H2H_TABS]:
            if h2h_pair(tab_id) == (a, b):
                st.session_state[ACTIVE_TAB] = tab_id

if st.session_state[ACTIVE_TAB] not in (*FIXED_TABS, *st.session_state[H2H_TABS]):
    st.session_state[ACTIVE_TAB] = SECTORS_TAB

# Streamlit drops a widget's state when the widget isn't drawn in a run, and only the active
# tab is drawn. Re-assigning keeps the shared lap, toggles and each head-to-head's settings.
for _k in (
    SC_LAP, SC_INCLUDE, SC_CLASSES, SC_OVERALL, SC_INCLUDE_LAPPED,
    *(key for tab_id in st.session_state[H2H_TABS] for key in StateKeys(tab_id).persistent),
):
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]


def write_url() -> None:
    def spec(tab_id: str) -> str:
        a, b = h2h_pair(tab_id)
        return f"{a}v{b}"

    active = st.session_state[ACTIVE_TAB]
    want = {"race": race.id} if race else {}
    want["tab"] = active if active in FIXED_TABS else spec(active)
    if st.session_state[H2H_TABS]:
        want["h2h"] = ",".join(spec(t) for t in st.session_state[H2H_TABS])
    if st.query_params.to_dict() != want:
        st.query_params.from_dict(want)


def set_lap(n: int) -> None:
    st.session_state[SC_LAP] = n


def select_cars(numbers: list[str]) -> None:
    st.session_state[SC_CARS] = numbers


def lap_controls(laps: pd.DataFrame, *, key_suffix: str = "") -> None:
    """◀ lap ▶, the include toggle and ← → keys. The lap (SC_LAP) is shared by every tab."""
    numbers = lap_numbers(laps)

    def step(delta: int) -> None:
        i = numbers.index(st.session_state[SC_LAP])
        st.session_state[SC_LAP] = numbers[min(max(i + delta, 0), len(numbers) - 1)]

    neutral = neutralised_laps(laps)
    prev_col, lap_col, next_col, inc_col = st.columns([0.6, 1.8, 0.6, 2.6], vertical_alignment="bottom")
    prev_col.button("◀", on_click=step, args=(-1,), help="Previous lap (← key)", width="stretch", key=f"lap_prev{key_suffix}")
    lap_col.selectbox(
        "Lap",
        numbers,
        key=SC_LAP,
        format_func=lambda n: f"Lap {n}" + (f"  ·  {neutral[n]}" if n in neutral else ""),
    )
    next_col.button("▶", on_click=step, args=(1,), help="Next lap (→ key)", width="stretch", key=f"lap_next{key_suffix}")
    inc_col.toggle(
        "Include pit / SC / FCY laps",
        key=SC_INCLUDE,
        help="Start, pit in/out and neutralised laps are left out of bests and averages unless this is on.",
    )
    lap_keys(step)


def ensure_lap(laps: pd.DataFrame) -> None:
    numbers = lap_numbers(laps)
    if st.session_state.get(SC_LAP) not in numbers:
        # Start on the first lap with clean times (lap 1 is a START lap for everyone).
        clean = laps.loc[~laps["affected"], "lap"]
        st.session_state[SC_LAP] = int(clean.min()) if not clean.empty else numbers[0]


def sector_draft(circuit: str):
    """(track, sector config, draft boundaries) – drafts live in session state until saved."""
    track = cached_track(circuit)
    sector_cfg = load_sector_config(circuit)
    draft_key = f"sector_draft_{circuit}"
    if sector_cfg and draft_key not in st.session_state:
        st.session_state[draft_key] = list(sector_cfg.boundaries_m)
    return track, sector_cfg, draft_key


def need_analysis() -> None:
    st.info("No Analysis file for this race: put the Al Kamel Analysis CSV in its data folder.")


# --- Tab: Entry List ------------------------------------------------------------------


def show_entry_tab() -> None:
    if analysis_cars is None:
        need_analysis()
        return
    cars = merge_entries(analysis_cars, {})
    selected = st.session_state.get(SC_CARS) or []

    def picked(numbers: list[str]) -> None:
        select_cars(numbers)
        if len(numbers) == 2:
            open_h2h(*numbers)

    note = (
        f"Selected: {', '.join('#' + n for n in selected)}. " if selected else ""
    ) + "Tick a car to select it across the dashboard (map colours, sector table); tick two to open a head-to-head."
    st.caption(note)
    render_entry_list(entry_table(cars, laps), selected=selected, on_select=picked, key="entry")


# --- Tab: Track Map -------------------------------------------------------------------


def show_reference_map(name: str, data: bytes) -> None:
    if name.lower().endswith(".pdf"):
        try:
            data = pdf_to_image(data)
        except Exception as e:
            st.error(f"Could not display PDF: {e}")
            return
    st.image(data, caption=name, width="stretch")


def reference_map_file(sector_cfg) -> tuple[str, bytes] | None:
    """The circuit map in the race folder if there is one, else the PDF named in the sector config."""
    if race and race.circuit_map:
        return race.circuit_map.name, race.circuit_map.read_bytes()
    pdf = (sector_cfg.published.get("pdf") if sector_cfg else None) or ""
    path = Path(pdf)
    if pdf and path.exists():
        return path.name, path.read_bytes()
    return None


def map_highlight() -> dict | None:
    """One car selected (entry list or sector table) -> colour the map's sectors for that car."""
    cars, lap = st.session_state.get(SC_CARS) or [], st.session_state.get(SC_LAP)
    if laps is None or len(cars) != 1 or lap is None:
        return None
    car = cars[0]
    colors = map_sector_colors(
        laps, lap, car, st.session_state.get(SC_INCLUDE, False), st.session_state.get(SC_INCLUDE_LAPPED, False)
    )
    if colors is None:
        return {"colors": SECTOR_COLORS, "caption": f"Car **#{car}** has no lap {lap}; showing the plain sectors."}
    words = " · ".join(f"{n} {COLOR_WORDS[c]}" for n, c in zip(SECTOR_NAMES, colors))
    return {"colors": colors, "caption": f"Map: **#{car}**, lap {lap}, against its class: {words}."}


def show_map_tab() -> None:
    if not circuit:
        name = race.circuit if race else "<circuit>"
        st.info(f"No centreline for {name} in data/tracks/. Run `npm run fetch-track -- {name}` to add one.")
        return
    track, sector_cfg, draft_key = sector_draft(circuit)
    highlight = map_highlight()

    def on_drag() -> None:
        dragged = st.session_state[MAP_KEY].get("boundaries")
        if dragged:
            st.session_state[draft_key] = validate_boundaries(dragged, track)

    ctrl_col, name_col, pdf_col = st.columns([6, 3, 2], vertical_alignment="bottom")
    if laps is not None:
        ensure_lap(laps)
        with ctrl_col:
            lap_controls(laps, key_suffix="_map")
    name_col.markdown(f"**{track.name}**")
    reference = reference_map_file(sector_cfg)
    show_pdf = pdf_col.toggle(
        "Show track PDF",
        key="show_track_pdf",
        disabled=reference is None,
        help=None if reference else "Put the circuit map (PDF or image, 'map' in the name) in the race folder.",
    )

    sectors = None
    if sector_cfg:
        sectors = {
            "names": SECTOR_NAMES,
            "colors": highlight["colors"] if highlight else SECTOR_COLORS,
            "boundaries_m": st.session_state[draft_key],
            "editable": True,
        }

    map_col, side = st.columns([7, 3])
    with map_col:
        track_map(track, height=600, sectors=sectors, on_boundaries_change=on_drag, key=MAP_KEY)
    with side:
        if show_pdf and reference:
            show_reference_map(*reference)
        if highlight:
            st.markdown(highlight["caption"])
            st.button("Clear car", on_click=select_cars, args=([],), key="map_clear_car")
        elif laps is not None:
            st.caption("Select one car in the Entry List or Sectors tab to colour the sectors by its times on this lap.")
        st.caption(
            f"Centreline: {track.source.split(' (')[0]} · {track.length_m:,.1f} m along the line"
            + (f" (published lap length {track.official_length_m:,.0f} m)" if track.official_length_m else "")
        )
        if sector_cfg:
            show_sector_editor(track, sector_cfg, draft_key)
        else:
            st.info(f"No sector boundaries for {circuit} yet (data/tracks/{circuit}.sectors.json).")


def show_sector_editor(track, sector_cfg, draft_key: str) -> None:
    draft = st.session_state[draft_key]
    published = sector_cfg.published_boundaries(track)
    edges = [0.0, *draft, track.length_m]
    pub = sector_cfg.published
    ends_at = [
        f"{pub.get('s1_s2_label', 'S1/S2')} (PDF: {pub['s1_s2_m']:,} m)" if "s1_s2_m" in pub else "S1/S2",
        f"{pub.get('s2_s3_label', 'S2/S3')} (PDF: {pub['s2_s3_m']:,} m)" if "s2_s3_m" in pub else "S2/S3",
        "S/F",
    ]
    st.dataframe(
        pd.DataFrame(
            {
                "Sector": SECTOR_NAMES,
                "From (m)": [round(edges[i]) for i in range(3)],
                "To (m)": [round(edges[i + 1]) for i in range(3)],
                "Length (m)": [round(edges[i + 1] - edges[i]) for i in range(3)],
                "Ends at": ends_at,
            }
        ),
        hide_index=True,
    )

    def save() -> None:
        sector_cfg.boundaries_m = list(st.session_state[draft_key])
        sector_cfg.placement = "published" if sector_cfg.boundaries_m == published else "manual"
        sector_cfg.save()
        st.toast(f"Saved {sector_cfg.path().name}")

    def revert() -> None:
        st.session_state[draft_key] = list(sector_cfg.boundaries_m)

    def reset_to_published() -> None:
        st.session_state[draft_key] = list(published)

    unsaved = draft != sector_cfg.boundaries_m
    status = (
        f"Unsaved: S1/S2 {draft[0]:,.1f} m, S2/S3 {draft[1]:,.1f} m "
        f"(saved: {sector_cfg.boundaries_m[0]:,.1f} m, {sector_cfg.boundaries_m[1]:,.1f} m)"
        if unsaved
        else f"Saved in data/tracks/{sector_cfg.path().name} · placement: {sector_cfg.placement}"
    )
    st.caption("Drag the white handles on the map to move a sector line; it snaps to the centreline. " + status)
    b1, b2 = st.columns(2)
    b1.button("Save", on_click=save, disabled=not unsaved, type="primary", width="stretch")
    b2.button("Revert", on_click=revert, disabled=not unsaved, width="stretch")
    st.button("Reset to PDF distances", on_click=reset_to_published, disabled=published is None or draft == published, width="stretch")


# --- Tab: Sectors -----------------------------------------------------------------------


def show_sectors_tab() -> None:
    if laps is None:
        need_analysis()
        return
    ensure_lap(laps)
    classes = sorted(laps["car_class"].unique())
    if SC_CLASSES not in st.session_state:
        st.session_state[SC_CLASSES] = classes

    ctrl_col, _ = st.columns([6, 5], vertical_alignment="bottom")
    with ctrl_col:
        lap_controls(laps, key_suffix="_sectors")
    lap = st.session_state[SC_LAP]
    include = st.session_state.get(SC_INCLUDE, False)

    class_col, opt_col, lapped_col, cmp_col = st.columns([3, 2, 2, 2], vertical_alignment="bottom")
    chosen = class_col.multiselect("Classes", classes, key=SC_CLASSES)
    overall = opt_col.toggle("Purple: overall (not per class)", key=SC_OVERALL)
    include_lapped = lapped_col.toggle(
        f"Include cars {LAPS_DOWN_THRESHOLD}+ laps down",
        key=SC_INCLUDE_LAPPED,
        help=(
            f"Laps where a car was more than {LAPS_DOWN_THRESHOLD} laps behind its class leader happened at a "
            "very different time from the rest of its class. They are left out of purple/green unless this is on."
        ),
    )
    selected = st.session_state.get(SC_CARS) or []
    if len(selected) == 2:
        cmp_col.button(
            f"Compare #{selected[0]} vs #{selected[1]} →",
            on_click=open_h2h,
            args=tuple(selected),
            type="primary",
            width="stretch",
        )
    else:
        cmp_col.caption("Tick two rows to compare them head-to-head.")

    rows, status = lap_rows(
        laps,
        lap,
        TableOptions(overall=overall, include_affected=include, classes=chosen, include_lapped=include_lapped),
    )
    if rows.empty:
        st.info("No cars match the class filter on this lap.")
        return
    render_sector_table(
        rows,
        status,
        selected=selected,
        on_select=select_cars,
        # A fresh key per lap / filter, so a stale row selection never points at a different car.
        key=f"sector_table_{lap}_{'-'.join(chosen)}_{overall}_{include}_{include_lapped}",
        height=min(36 * (len(rows) + 1) + 3, 540),  # scrolls inside its panel; the header stays put
    )
    st.caption(
        "Purple: fastest on this lap " + ("overall" if overall else "in class")
        + " · Green: the car's personal best so far · Grey italic: start, pit in/out or neutralised lap"
        + (" (counted)" if include else " (left out of purple/green)")
        + f" · Light grey, +N L: more than {LAPS_DOWN_THRESHOLD} laps behind the class leader"
        + (" (counted)" if include_lapped else " (left out)")
        + ". Click a column header to sort. Tick one row to show its sectors on the map, two to compare."
    )


# --- Tab: head-to-head ------------------------------------------------------------------


def show_h2h_tab(tab_id: str) -> None:
    if laps is None:
        need_analysis()
        return
    ensure_lap(laps)
    track, boundaries = None, None
    if circuit:
        track, _, draft_key = sector_draft(circuit)
        boundaries = st.session_state.get(draft_key)
    render_head_to_head(
        laps,
        lap=st.session_state[SC_LAP],
        include_affected=st.session_state.get(SC_INCLUDE, False),
        set_lap=set_lap,
        track=track,
        sector_boundaries=boundaries,
        prefix=tab_id,
        lap_controls=lambda: lap_controls(laps, key_suffix=f"_{tab_id}"),
    )


# --- Tab bar and the active tab -----------------------------------------------------------

def tab_label(tab_id: str) -> str:
    a, b = h2h_pair(tab_id)
    return f"#{a} vs #{b}"


tab_bar(
    [{"id": t, "label": label, "closable": False} for t, label in FIXED_TABS.items()]
    + [{"id": t, "label": tab_label(t), "closable": True} for t in st.session_state[H2H_TABS]],
    st.session_state[ACTIVE_TAB],
    on_select=select_tab,
    on_close=close_tab,
)
write_url()

active = st.session_state[ACTIVE_TAB]
if active == ENTRY_TAB:
    show_entry_tab()
elif active == MAP_TAB:
    show_map_tab()
elif active == SECTORS_TAB:
    show_sectors_tab()
else:
    show_h2h_tab(active)
