// Leaflet track map, mounted as a Streamlit v2 component (see wec/track_map.py).
//
// Streamlit calls this function again with new `data` on every rerun, so the map is created
// once per container and only its layers are updated afterwards (keeps zoom/pan/basemap).

const LEAFLET_URL = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/";

function loadLeaflet() {
  if (!window.__wecLeaflet) {
    if (!document.querySelector("link[data-wec-leaflet]")) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = LEAFLET_URL + "leaflet.css";
      link.dataset.wecLeaflet = "1";
      document.head.appendChild(link);
    }
    window.__wecLeaflet = import(LEAFLET_URL + "leaflet-src.esm.js");
  }
  return window.__wecLeaflet;
}

const instances = (window.__wecTrackMaps ??= new WeakMap());

const BASEMAPS = {
  Satellite: [
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxNativeZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics, GIS User Community" },
  ],
  Street: [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxNativeZoom: 19, attribution: "&copy; OpenStreetMap contributors" },
  ],
  Dark: [
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    { maxNativeZoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors" },
  ],
};

const CSS = `
.wec-map { width: 100%; border-radius: 0.5rem; overflow: hidden; }
.wec-map .leaflet-container { font: 12px/1.4 var(--st-font, sans-serif); }
.wec-readout {
  background: rgba(20, 20, 24, 0.85); color: #fff; padding: 4px 8px; border-radius: 4px;
  font-variant-numeric: tabular-nums; min-width: 9em; pointer-events: none;
}
.wec-label {
  background: rgba(20, 20, 24, 0.8); color: #fff; border: 0; box-shadow: none;
  padding: 1px 5px; font-size: 11px; font-weight: 600;
}
.wec-label::before { display: none; }
.wec-sector-label { font-size: 12px; padding: 2px 7px; border-radius: 4px; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.5); }
.wec-handle {
  background: #fff; border: 3px solid #111; border-radius: 50%;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.6); cursor: grab;
}
.wec-handle:active { cursor: grabbing; }
`;

// --- geometry: flat metres around the track centre (fine at circuit scale) --------

function makeProjector(coords) {
  const lat0 = coords.reduce((s, c) => s + c[0], 0) / coords.length;
  const R = 6371008.8;
  const ky = (Math.PI / 180) * R;
  const kx = ky * Math.cos((lat0 * Math.PI) / 180);
  return {
    toXY: ([lat, lon]) => [lon * kx, lat * ky],
    toLatLng: ([x, y]) => [y / ky, x / kx],
  };
}

function makeTrackGeometry(track) {
  const proj = makeProjector(track.coords);
  const xy = track.coords.map(proj.toXY);
  const cum = track.cum;
  const length = cum[cum.length - 1];

  function pointAt(d) {
    d = ((d % length) + length) % length;
    let lo = 1, hi = cum.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] < d) lo = mid + 1; else hi = mid;
    }
    const i = lo;
    const seg = cum[i] - cum[i - 1];
    const t = seg === 0 ? 0 : (d - cum[i - 1]) / seg;
    const [ax, ay] = xy[i - 1], [bx, by] = xy[i];
    const dx = bx - ax, dy = by - ay, n = Math.hypot(dx, dy) || 1;
    return { xy: [ax + t * dx, ay + t * dy], dir: [dx / n, dy / n], index: i };
  }

  function project(latlng) {
    const [px, py] = proj.toXY(latlng);
    let best = { d: 0, off: Infinity };
    for (let i = 0; i + 1 < xy.length; i++) {
      const [ax, ay] = xy[i], [bx, by] = xy[i + 1];
      const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
      const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L2));
      const off = Math.hypot(px - ax - t * dx, py - ay - t * dy);
      if (off < best.off) best = { d: cum[i] + t * (cum[i + 1] - cum[i]), off };
    }
    return best;
  }

  /** Lat/lng points of the centreline between two distances (from < to, no wrap). */
  function slice(from, to) {
    const a = pointAt(from), b = pointAt(to);
    const pts = [proj.toLatLng(a.xy)];
    for (let i = a.index; i < cum.length && cum[i] < to; i++) pts.push(track.coords[i]);
    pts.push(to >= length ? track.coords[track.coords.length - 1] : proj.toLatLng(b.xy));
    return pts;
  }

  /** Lat/lng of a point at distance d, shifted `side` metres to the left (+) / right (-). */
  function offsetPoint(d, side, along = 0) {
    const p = pointAt(d);
    const [ux, uy] = p.dir;
    return proj.toLatLng([p.xy[0] + ux * along - uy * side, p.xy[1] + uy * along + ux * side]);
  }

  return { length, pointAt, project, slice, offsetPoint, toLatLng: proj.toLatLng };
}

// --- map instance ---------------------------------------------------------------

function createInstance(L, parentElement, height) {
  if (!document.querySelector("style[data-wec-map]")) {
    const style = document.createElement("style");
    style.dataset.wecMap = "1";
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  const el = document.createElement("div");
  el.className = "wec-map";
  el.style.height = `${height}px`;
  parentElement.appendChild(el);

  // Whole zoom levels only: fractional zoom leaves hairline seams between tiles.
  const map = L.map(el, { zoomSnap: 1, zoomDelta: 1, maxZoom: 20 });
  const bases = Object.fromEntries(
    Object.entries(BASEMAPS).map(([name, [url, opts]]) => [name, L.tileLayer(url, { maxZoom: 20, ...opts })]),
  );
  bases.Satellite.addTo(map);
  L.control.layers(bases, null, { position: "topright", collapsed: false }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  // Panes keep the stacking fixed however often each layer is redrawn.
  map.createPane("wecCasing").style.zIndex = 401;
  map.createPane("wecSectors").style.zIndex = 402;
  map.createPane("wecMarks").style.zIndex = 403;
  const casingLayer = L.layerGroup().addTo(map);
  const sectorLayer = L.layerGroup().addTo(map);
  const marksLayer = L.layerGroup().addTo(map);
  const hoverMarker = L.circleMarker([0, 0], { radius: 6, color: "#fff", weight: 2, fillColor: "#111", fillOpacity: 1, interactive: false, pane: "wecMarks" });

  const Readout = L.Control.extend({
    onAdd() {
      const div = L.DomUtil.create("div", "wec-readout");
      div.textContent = "Hover the track";
      return div;
    },
  });
  const readout = new Readout({ position: "bottomleft" }).addTo(map);

  const inst = {
    L, el, map, casingLayer, sectorLayer, marksLayer, hoverMarker, readout,
    circuit: null, geom: null, bounds: null, userMoved: false,
    sectors: null, boundaries: [], handles: [], dragging: false, setTriggerValue: null,
  };

  // The container can be laid out after the map is created (or resized later): keep Leaflet's
  // size in sync, and keep the track fitted until the user pans/zooms themselves.
  inst.fit = () => {
    if (!inst.bounds) return;
    inst.fitting = true;
    map.fitBounds(inst.bounds, { padding: [20, 20], animate: false });
    inst.fitting = false;
  };
  inst.resizeObserver = new ResizeObserver(() => {
    if (!el.isConnected || inst.removed) return;
    map.invalidateSize({ animate: false });
    if (!inst.userMoved) inst.fit();
  });
  inst.resizeObserver.observe(el);
  // Leaflet measures its container once; inside a tab that was hidden (or not yet laid out)
  // that size is wrong. Re-measure whenever the map becomes visible, and whenever the tab
  // bar announces a tab switch.
  inst.refresh = () => {
    if (!el.isConnected || inst.removed || !el.offsetWidth) return;
    map.invalidateSize({ animate: false });
    if (!inst.userMoved) inst.fit();
  };
  inst.visibilityObserver = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) inst.refresh();
  });
  inst.visibilityObserver.observe(el);
  inst.onTabShown = () => {
    inst.refresh();
    setTimeout(inst.refresh, 150);
  };
  window.addEventListener("wec:tab-shown", inst.onTabShown);
  map.on("dragstart zoomstart", () => {
    if (!inst.fitting) inst.userMoved = true;
  });

  map.on("mousemove", (e) => {
    if (!inst.geom || inst.dragging) return;
    const hit = inst.geom.project([e.latlng.lat, e.latlng.lng]);
    const p = inst.geom.toLatLng(inst.geom.pointAt(hit.d).xy);
    const px = map.latLngToContainerPoint(p).distanceTo(e.containerPoint);
    const div = readout.getContainer();
    if (px > 25) {
      hoverMarker.remove();
      div.textContent = "Hover the track";
      return;
    }
    hoverMarker.setLatLng(p).addTo(map);
    const pct = (100 * hit.d) / inst.geom.length;
    const sector = inst.sectors ? `${inst.sectors.names[sectorIndex(inst.boundaries, hit.d)]} · ` : "";
    div.textContent = `${sector}${fmtM(hit.d)} from S/F · ${pct.toFixed(1)}% of lap`;
  });
  map.on("mouseout", () => {
    hoverMarker.remove();
    readout.getContainer().textContent = "Hover the track";
  });

  return inst;
}

const sectorIndex = (boundaries, d) => boundaries.filter((b) => d >= b).length;
const fmtM = (d) => `${Math.round(d).toLocaleString()} m`;
const MIN_SECTOR_M = 50;

/** Casing, km marks and the start/finish line: only change with the track. */
function drawStatic(inst, data) {
  const { L, casingLayer, marksLayer, geom } = inst;
  casingLayer.clearLayers();
  marksLayer.clearLayers();

  L.polyline(data.track.coords, { color: "#000", weight: 9, opacity: 0.55, interactive: false, pane: "wecCasing" }).addTo(casingLayer);

  for (let d = 1000; d < geom.length - 100; d += 1000) {
    L.circleMarker(geom.toLatLng(geom.pointAt(d).xy), { radius: 3, color: "#000", weight: 1, fillColor: "#fff", fillOpacity: 1, interactive: false, pane: "wecMarks" })
      .bindTooltip(`${d / 1000} km`, { permanent: true, direction: "right", offset: [6, 0], className: "wec-label" })
      .addTo(marksLayer);
  }

  // Start/finish line: a chequered bar across the track, plus a race-direction chevron.
  const half = 14;
  const bar = [geom.offsetPoint(0, half), geom.offsetPoint(0, -half)];
  L.polyline(bar, { color: "#fff", weight: 6, opacity: 1, interactive: false, pane: "wecMarks" }).addTo(marksLayer);
  L.polyline(bar, { color: "#000", weight: 6, dashArray: "4 4", opacity: 1, interactive: false, pane: "wecMarks" })
    .bindTooltip("S/F", { permanent: true, direction: "top", offset: [0, -6], className: "wec-label" })
    .addTo(marksLayer);
  const tip = 70, back = 25, wing = 9;
  L.polyline(
    [geom.offsetPoint(tip - back, wing), geom.offsetPoint(tip, 0), geom.offsetPoint(tip - back, -wing)],
    { color: "#fff", weight: 3, opacity: 0.95, interactive: false, pane: "wecMarks" },
  ).addTo(marksLayer);
}

/** The coloured line (one colour per sector) and a white bar at each sector boundary. */
function drawSectors(inst) {
  const { L, sectorLayer, geom, sectors, boundaries } = inst;
  sectorLayer.clearLayers();
  if (!sectors) {
    L.polyline(geom.slice(0, geom.length), { color: "#ffd400", weight: 5, interactive: false, pane: "wecSectors" }).addTo(sectorLayer);
    return;
  }
  const edges = [0, ...boundaries, geom.length];
  for (let i = 0; i + 1 < edges.length; i++) {
    L.polyline(geom.slice(edges[i], edges[i + 1]), { color: sectors.colors[i], weight: 5, interactive: false, pane: "wecSectors" }).addTo(sectorLayer);
  }
  for (const b of boundaries) {
    L.polyline([geom.offsetPoint(b, 13), geom.offsetPoint(b, -13)], { color: "#fff", weight: 4, interactive: false, pane: "wecSectors" }).addTo(sectorLayer);
  }
  // Optional label per sector (e.g. head-to-head delta), at the sector's midpoint.
  (sectors.labels ?? []).forEach((label, i) => {
    if (!label || i + 1 >= edges.length) return;
    const mid = geom.toLatLng(geom.pointAt((edges[i] + edges[i + 1]) / 2).xy);
    const tip = L.tooltip({ permanent: true, direction: "center", className: "wec-label wec-sector-label", interactive: false })
      .setLatLng(mid)
      .setContent(label.text);
    sectorLayer.addLayer(tip);
    const el = tip.getElement();
    if (el && label.color) {
      el.style.background = label.color;
      el.style.color = label.textColor ?? "#111";
    }
  });
}

const handleLabel = (inst, i) => `${inst.sectors.names[i]}/${inst.sectors.names[i + 1]} · ${fmtM(inst.boundaries[i])}`;

/** Draggable handles on the sector boundaries; they snap to the centreline. */
function syncHandles(inst) {
  const { L, map, geom, sectors } = inst;
  const wanted = sectors?.editable ? inst.boundaries.length : 0;
  while (inst.handles.length > wanted) inst.handles.pop().remove();
  while (inst.handles.length < wanted) {
    const i = inst.handles.length;
    const handle = L.marker([0, 0], {
      draggable: true,
      autoPan: true,
      icon: L.divIcon({ className: "wec-handle", iconSize: [18, 18] }),
      title: "Drag along the track to move this sector line",
    })
      .bindTooltip("", { permanent: true, direction: "right", offset: [10, 0], className: "wec-label" })
      .addTo(map);
    handle.on("dragstart", () => {
      inst.dragging = true;
      inst.hoverMarker.remove();
    });
    handle.on("drag", () => {
      const ll = handle.getLatLng();
      const hit = inst.geom.project([ll.lat, ll.lng]);
      const lo = i === 0 ? MIN_SECTOR_M : inst.boundaries[i - 1] + MIN_SECTOR_M;
      const hi = i === inst.boundaries.length - 1 ? inst.geom.length - MIN_SECTOR_M : inst.boundaries[i + 1] - MIN_SECTOR_M;
      inst.boundaries[i] = Math.min(hi, Math.max(lo, hit.d));
      handle.setLatLng(inst.geom.toLatLng(inst.geom.pointAt(inst.boundaries[i]).xy));
      handle.setTooltipContent(handleLabel(inst, i));
      inst.readout.getContainer().textContent = handleLabel(inst, i);
      drawSectors(inst);
    });
    handle.on("dragend", () => {
      inst.dragging = false;
      inst.setTriggerValue?.("boundaries", inst.boundaries.map((b) => Math.round(b * 10) / 10));
    });
    inst.handles.push(handle);
  }
  inst.handles.forEach((h, i) => {
    h.setLatLng(geom.toLatLng(geom.pointAt(inst.boundaries[i]).xy));
    h.setTooltipContent(handleLabel(inst, i));
  });
}

export default async function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const L = await loadLeaflet();

  let inst = instances.get(parentElement);
  if (!inst) {
    inst = createInstance(L, parentElement, data.height);
    instances.set(parentElement, inst);
  }
  inst.setTriggerValue = setTriggerValue;
  inst.el.style.height = `${data.height}px`;
  inst.map.invalidateSize();

  inst.geom = makeTrackGeometry(data.track);
  inst.sectors = data.sectors ?? null;
  // Python owns the boundaries; ignore its (stale) copy while the user is mid-drag.
  if (!inst.dragging) {
    inst.boundaries = [...(data.sectors?.boundaries_m ?? [])];
    inst.readout.getContainer().textContent = "Hover the track";
  }

  drawStatic(inst, data);
  drawSectors(inst);
  syncHandles(inst);

  if (inst.circuit !== data.circuit) {
    inst.circuit = data.circuit;
    inst.bounds = L.latLngBounds(data.track.coords);
    inst.userMoved = false;
    inst.fit();
  }

  // Streamlit only calls this on unmount, but a stale call can also land here while a
  // newer render is in flight, so only tear down once the container has really gone.
  return () => {
    setTimeout(() => {
      if (!parentElement.isConnected && instances.get(parentElement) === inst) {
        inst.removed = true;
        inst.resizeObserver.disconnect();
        inst.visibilityObserver.disconnect();
        window.removeEventListener("wec:tab-shown", inst.onTabShown);
        inst.map.remove();
        instances.delete(parentElement);
      }
    }, 0);
  };
}
