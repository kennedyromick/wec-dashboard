"""Left/right arrow keys -> previous/next lap (an invisible Streamlit v2 component).

Keys are ignored while typing in an input, inside a selectbox, or while the table grid has
focus (it uses the arrow keys to move between cells), and with modifier keys held.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

_JS = """
export default function (component) {
  const { setTriggerValue } = component;
  // One document listener for the page; re-pointed at the latest setTriggerValue each rerun.
  window.__wecLapKeysTrigger = setTriggerValue;
  if (!window.__wecLapKeysInstalled) {
    window.__wecLapKeysInstalled = true;
    document.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const t = e.target;
      if (t instanceof Element && (
        t.closest("input, textarea, select, [contenteditable=true], [role=combobox], [role=listbox]") ||
        t.closest("[data-testid=stDataFrame], [data-testid=stDataFrameResizable]")
      )) return;
      e.preventDefault();
      window.__wecLapKeysTrigger?.("step", e.key === "ArrowLeft" ? -1 : 1);
    });
  }
}
"""

_component = st.components.v2.component("wec_lap_keys", js=_JS, isolate_styles=False)


def lap_keys(on_step: Callable[[int], None], *, key: str = "lap_keys") -> None:
    """Call on_step(-1 / +1) when the user presses Left / Right."""

    def handle() -> None:
        step = st.session_state[key].get("step")
        if step:
            on_step(int(step))

    _component(key=key, height="content", on_step_change=handle)
