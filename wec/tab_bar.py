"""Tab bar with closable tabs (a Streamlit v2 component).

Streamlit's st.tabs can't add, close or switch tabs from Python, so this draws the bar and
reports clicks; the app renders only the active tab's content. Each switch also fires a
`wec:tab-shown` window event so Leaflet maps can re-measure themselves (see track_map.js).
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

_JS = r"""
const CSS = `
.wec-tabbar { display: flex; gap: 2px; flex-wrap: wrap; align-items: flex-end;
  border-bottom: 1px solid rgba(128,128,128,.35); font-family: inherit; }
.wec-tab { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px 8px;
  border: 1px solid transparent; border-bottom: none; border-radius: 8px 8px 0 0;
  background: transparent; color: inherit; opacity: .72; font: inherit; font-size: 15px;
  cursor: pointer; margin-bottom: -1px; white-space: nowrap; }
.wec-tab:hover { opacity: 1; background: rgba(128,128,128,.10); }
.wec-tab.active { opacity: 1; font-weight: 600; border-color: rgba(128,128,128,.35);
  background: var(--st-background-color, transparent);
  box-shadow: inset 0 2px 0 var(--st-primary-color, #ff4b4b); }
.wec-tab.h2h { font-variant-numeric: tabular-nums; }
.wec-tab .wec-close { display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 4px; font-size: 15px; line-height: 1; opacity: .6; }
.wec-tab .wec-close:hover { opacity: 1; background: rgba(128,128,128,.25); }
.wec-tab-sep { width: 1px; align-self: stretch; margin: 6px 6px 8px; background: rgba(128,128,128,.35); }
`;

function announce() {
  const fire = () => window.dispatchEvent(new Event("wec:tab-shown"));
  requestAnimationFrame(fire);
  setTimeout(fire, 400);
  setTimeout(fire, 1200);
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  if (!document.getElementById("wec-tabbar-css")) {
    const style = document.createElement("style");
    style.id = "wec-tabbar-css";
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  let bar = parentElement.querySelector(".wec-tabbar");
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "wec-tabbar";
    bar.setAttribute("role", "tablist");
    parentElement.appendChild(bar);
    const activate = (id) => {
      bar.querySelectorAll(".wec-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
    };
    bar.addEventListener("click", (e) => {
      const close = e.target.closest("[data-close]");
      if (close) {
        e.stopPropagation();
        bar.__trigger("close", close.dataset.close);
        return;
      }
      const tab = e.target.closest("[data-tab]");
      if (tab && !tab.classList.contains("active")) {
        activate(tab.dataset.tab); // instant feedback; Python confirms on the rerun
        bar.__trigger("select", tab.dataset.tab);
      }
    });
    bar.addEventListener("auxclick", (e) => {
      const tab = e.target.closest("[data-tab]");
      if (e.button === 1 && tab && tab.dataset.closable === "1") {
        e.preventDefault();
        bar.__trigger("close", tab.dataset.tab);
      }
    });
  }
  bar.__trigger = setTriggerValue;

  bar.replaceChildren();
  let sawDynamic = false;
  for (const t of data.tabs) {
    if (t.closable && !sawDynamic) {
      sawDynamic = true;
      const sep = document.createElement("span");
      sep.className = "wec-tab-sep";
      bar.appendChild(sep);
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "wec-tab" + (t.id === data.active ? " active" : "") + (t.closable ? " h2h" : "");
    b.dataset.tab = t.id;
    b.dataset.closable = t.closable ? "1" : "0";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(t.id === data.active));
    const label = document.createElement("span");
    label.textContent = t.label;
    b.appendChild(label);
    if (t.closable) {
      const x = document.createElement("span");
      x.className = "wec-close";
      x.dataset.close = t.id;
      x.title = "Close tab";
      x.textContent = "×";
      b.appendChild(x);
    }
    bar.appendChild(b);
  }
  if (bar.__active !== data.active) {
    bar.__active = data.active;
    announce();
  }
}
"""

_component = st.components.v2.component("wec_tab_bar", js=_JS, isolate_styles=False)


def tab_bar(
    tabs: list[dict],
    active: str,
    *,
    on_select: Callable[[str], None],
    on_close: Callable[[str], None],
    key: str = "tab_bar",
) -> None:
    """`tabs` = [{"id", "label", "closable"}]; calls on_select(id) / on_close(id) on clicks."""

    def handle_select() -> None:
        tab = st.session_state[key].get("select")
        if tab:
            on_select(tab)

    def handle_close() -> None:
        tab = st.session_state[key].get("close")
        if tab:
            on_close(tab)

    _component(
        data={"tabs": tabs, "active": active},
        key=key,
        height="content",
        on_select_change=handle_select,
        on_close_change=handle_close,
    )
