"""Interactive Leaflet track map as a Streamlit v2 component (JS lives in track_map.js)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st

from wec.track import Track

_JS = (Path(__file__).with_name("track_map.js")).read_text(encoding="utf-8")

# Not style-isolated: Leaflet expects its CSS and DOM events at document level.
_component = st.components.v2.component("wec_track_map", js=_JS, isolate_styles=False)


def track_map(
    track: Track,
    *,
    height: int = 600,
    sectors: dict | None = None,
    on_boundaries_change: Callable[[], None] | None = None,
    key: str = "track_map",
):
    """Show the circuit on a satellite/street/dark basemap with S/F line and km marks.

    `sectors` (optional): {"names": [...], "colors": [...], "boundaries_m": [b1, b2], "editable": bool}.
    When editable, the boundaries can be dragged; on release the new list is sent as the
    `boundaries` trigger (read it in `on_boundaries_change` via st.session_state[key].boundaries).
    """
    data = {
        "circuit": track.circuit,
        "height": height,
        "track": {
            "coords": [[lat, lon] for lon, lat in track.coords],
            "cum": [round(d, 2) for d in track.cum_m],
        },
        "sectors": sectors,
    }
    return _component(
        data=data,
        key=key,
        height=height,
        on_boundaries_change=on_boundaries_change or (lambda: None),
    )
