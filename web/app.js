/* Onward UI: polls the local server and draws everything. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let S = null, prefs = null, tab = "fly", keys = {}, dismissed = null;

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}
async function post(path, body = {}) {
  try {
    const r = await api(path, body);
    if (r && r.error) toast(r.error);
    await poll();
    return r;
  } catch (e) { toast("Can't reach Onward. Is the window with the server still open?"); }
}
function toast(msg, info) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast on" + (info ? " info" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), 5000);
}
const fmtMin = (m) => { m = Math.round(m || 0); return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`; };
const fmtSec = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h} h ${m} min` : m ? `${m} min ${s % 60}s` : `${s}s`; };
const stars = (n, why) => `<span class="stars" title="Difficulty ${n}/5${why && why.length ? ": " + esc(why.join(", ")) : ""}">${"★".repeat(n)}<em>${"★".repeat(5 - n)}</em></span>`;
const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty"];
const word = (n) => WORDS[n] || String(n);
const muteFor = (m, name) => m.getContainer().classList.toggle("map-muted", !/Satellite|FAA/.test(name || ""));
const changed = (name, v) => { const k = JSON.stringify(v); if (keys[name] === k) return false; keys[name] = k; return true; };
const SYS = { engine: "Engine", airframe: "Airframe", gear: "Landing gear", prop: "Propeller" };
const STATUS = { ok: "Airworthy", worn: "Worn", damaged: "Damaged", grounded: "Grounded", in_repair: "In repair" };

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  tab = b.dataset.tab;
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x.id === "tab-" + tab));
  keys = {};
  if (tab === "fly") setTimeout(() => map.invalidateSize(), 50);
  if (tab === "journal") renderJournal();
  if (tab === "world") renderWorld();
  render();
});

/* ---------------- map ---------------- */
const map = L.map("map", { zoomControl: true, worldCopyJump: true }).setView([41.58, -8.44], 8);
map.createPane("history"); map.getPane("history").style.zIndex = 390;
const historyLayer = L.layerGroup();
let historyLegs = -1;
async function loadHistory() {
  try {
    const r = await api("/api/history");
    historyLayer.clearLayers();
    const fields = {};
    r.legs.forEach((l) => {
      L.polyline(l.track, { pane: "history", color: "#f4f2ec", weight: 6, opacity: 0.7, interactive: false }).addTo(historyLayer);
      L.polyline(l.track, { pane: "history", color: "#3d5a2c", weight: 2.2, opacity: 0.75, interactive: true })
        .bindTooltip(`${esc(l.label)} · ${esc(l.date)}`, { sticky: true }).addTo(historyLayer);
      const a = l.track[0], b = l.track[l.track.length - 1];
      fields[a.join()] = a; fields[b.join()] = b;
    });
    Object.values(fields).forEach((p) => L.circleMarker(p, { pane: "history", radius: 3, color: "#f4f2ec", weight: 1.5, fillColor: "#3d5a2c", fillOpacity: 1, interactive: false }).addTo(historyLayer));
  } catch (e) {}
}
const BASEMAPS = {
  "Terrain": () => L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)" }),
  // EOxCloudless (Sentinel-2): free for non-commercial use with this credit shown on the map (CC BY-NC-SA 4.0)
  "Satellite": () => L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/default/g/{z}/{y}/{x}.jpg", { maxZoom: 18, maxNativeZoom: 14,
    attribution: '<a href="https://cloudless.eox.at" target="_blank" rel="noopener">EOxCloudless</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2023)' }),
  "Streets": () => L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
  "FAA VFR sectional (US)": () => L.tileLayer("https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}", { maxZoom: 13, maxNativeZoom: 12, attribution: "Charts: FAA. Not for real-world navigation" }),
  "FAA IFR low enroute (US)": () => L.tileLayer("https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/IFR_AreaLow/MapServer/tile/{z}/{y}/{x}", { maxZoom: 12, maxNativeZoom: 11, attribution: "Charts: FAA. Not for real-world navigation" }),
};
/* ---------------- weather overlays ---------------- */
const CAT_COL = { VFR: "#3d8f4a", MVFR: "#2f6fb5", IFR: "#c0392b", LIFR: "#9b2fae" };
const HAZ_COL = (h) => /TS|CONV/.test(h) ? "#c0392b" : /ICE|ICING|FZ/.test(h) ? "#2f6fb5" : /TURB|LLWS|WS/.test(h) ? "#d27d1e" : /VA|ASH/.test(h) ? "#7b3fa0" : /MTW|MT OBSC|MTN/.test(h) ? "#7a5c3a" : "#5d6458";
const zTime = (t) => { const d = new Date(t * 1000); return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}Z`; };
const COND_RGBA = [[0, 0, 0, 0], [47, 111, 181, 0.30], [192, 57, 43, 0.40], [155, 47, 174, 0.48]];
const COND_NAME = ["VFR", "MVFR", "IFR", "LIFR"];
function windTick(m) {
  if (m.wspd == null || m.wdir == null || m.vrb) return "";
  const len = 10 + Math.min(16, m.wspd);
  return `<span class="wtick" style="height:${len}px;transform:rotate(${m.wdir}deg)"></span>`;
}
/* One set of weather overlays for a map. The Fly map and the Plan a trip map each get their own.
   opts.legend(): redraw that map's legend; opts.bar: id of its time slider box; opts.alt(): altitude for winds aloft */
function makeWx(m, opts) {
  const w = { radar: L.layerGroup(), metars: L.layerGroup(), sigmets: L.layerGroup(), grid: L.layerGroup(), sat: L.layerGroup(), cond: L.layerGroup(),
    radarTime: null, satTime: null, cond_data: null, condHour: 0, timer: null, loaded: {}, map: m };
  const legend = () => opts.legend && opts.legend();
  m.createPane("sat"); m.getPane("sat").style.zIndex = 330;       // satellite clouds, blended over the base map
  m.createPane("cond"); m.getPane("cond").style.zIndex = 340;     // forecast flying conditions
  m.createPane("radar"); m.getPane("radar").style.zIndex = 350;   // rain on top of both
  m.createPane("wxsig"); m.getPane("wxsig").style.zIndex = 440;   // SIGMET areas
  w.loadRadar = async () => {
    try {
      const r = await (await fetch("https://api.rainviewer.com/public/weather-maps.json")).json();
      const f = r.radar && r.radar.past && r.radar.past[r.radar.past.length - 1];
      if (!f) return;
      w.radar.clearLayers();
      L.tileLayer(`${r.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`, { opacity: 0.6, maxNativeZoom: 7, maxZoom: 18, pane: "radar", attribution: "Radar &copy; RainViewer" }).addTo(w.radar);
      w.radarTime = f.time;
      legend();
    } catch (e) { toast("The radar isn't available right now."); }
  };
  /* Satellite clouds: Meteosat (EUMETSAT) over Europe, Africa and the Atlantic, GOES over the Americas, Himawari over Asia and the Pacific.
     The pictures include the ground, so the pane blends them in "screen" mode with the ground darkened away: only the cloud shows. */
  const GIBS = (layer, tms, maxNat, bounds) => L.tileLayer(`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${layer}/default/default/${tms}/{z}/{y}/{x}.png`,
    { pane: "sat", bounds, maxNativeZoom: maxNat, maxZoom: 18, className: "sat-tiles", attribution: "Clouds: NASA GIBS" });
  w.loadSat = async () => {
    try {
      const r = await api("/api/wx/sat");
      if (r.eumetsat && r.eumetsat === w.satTime && w.sat.getLayers().length) return;
      w.sat.clearLayers();
      w.satTime = r.eumetsat;
      if (r.eumetsat) L.tileLayer.wms("https://view.eumetsat.int/geoserver/wms", { layers: "mtg_fd:rgb_geocolour", format: "image/jpeg", version: "1.3.0", time: r.eumetsat,
        pane: "sat", bounds: [[-78, -50], [78, 72]], maxNativeZoom: 9, maxZoom: 18, className: "sat-tiles", attribution: "Clouds &copy; EUMETSAT" }).addTo(w.sat);
      GIBS("GOES-East_ABI_GeoColor", "GoogleMapsCompatible_Level7", 7, [[-78, -115], [78, -50]]).addTo(w.sat);
      GIBS("GOES-West_ABI_GeoColor", "GoogleMapsCompatible_Level7", 7, [[-78, -180], [78, -115]]).addTo(w.sat);
      GIBS("Himawari_AHI_Band13_Clean_Infrared", "GoogleMapsCompatible_Level6", 6, [[-78, 72], [78, 180]]).addTo(w.sat);
      legend();
    } catch (e) { toast("The satellite picture isn't available right now."); }
  };
  /* Forecast flying conditions: VFR / MVFR / IFR / LIFR from the forecast ceiling and visibility, drawn as a soft wash */
  w.loadCond = async () => {
    const b = m.getBounds();
    try {
      const r = await api(`/api/wx/cond?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}`);
      w.cond_data = r;
      w.condError = r.error || null;
      w.drawCond();
    } catch (e) {}
  };
  w.drawCond = () => {
    const r = w.cond_data;
    w.cond.clearLayers();
    w.renderBar();
    legend();
    if (!r || !r.lats || !r.lats.length || !r.grid.some(Boolean)) return;
    const H = w.condHour, nl = r.lats.length, nc = r.lons.length;
    const cell = (i, j) => { const g = r.grid[i * nc + j]; return g && g[H] ? g[H] : null; };
    const half = r.step / 2;
    const s = r.lats[0] - half, n = r.lats[nl - 1] + half, wl = r.lons[0] - half, e = r.lons[nc - 1] + half;
    // paint in map pixels, so rows follow the Mercator stretch of the map
    const p0 = m.project([n, wl], 0), p1 = m.project([s, e], 0);
    const Wc = 360, Hc = Math.max(40, Math.min(600, Math.round(Wc * (p1.y - p0.y) / (p1.x - p0.x))));
    const cv = document.createElement("canvas"); cv.width = Wc; cv.height = Hc;
    const ctx = cv.getContext("2d"), img = ctx.createImageData(Wc, Hc);
    const fi = (lat) => (lat - r.lats[0]) / r.step, fj = (lon) => (lon - r.lons[0]) / r.step;
    for (let y = 0; y < Hc; y++) {
      const lat = m.unproject([0, p0.y + (y + 0.5) / Hc * (p1.y - p0.y)], 0).lat;
      const ii = Math.max(0, Math.min(nl - 1.001, fi(lat))), i0 = Math.floor(ii), ti = ii - i0;
      for (let x = 0; x < Wc; x++) {
        const jj = Math.max(0, Math.min(nc - 1.001, fj(wl + (x + 0.5) / Wc * (e - wl)))), j0 = Math.floor(jj), tj = jj - j0;
        let acc = [0, 0, 0, 0], wsum = 0;
        [[i0, j0, (1 - ti) * (1 - tj)], [i0 + 1, j0, ti * (1 - tj)], [i0, j0 + 1, (1 - ti) * tj], [i0 + 1, j0 + 1, ti * tj]].forEach(([a, c, wt]) => {
          const v = cell(Math.min(nl - 1, a), Math.min(nc - 1, c));
          if (!v || !wt) return;
          const col = COND_RGBA[v[0]];
          acc[0] += col[0] * col[3] * wt; acc[1] += col[1] * col[3] * wt; acc[2] += col[2] * col[3] * wt; acc[3] += col[3] * wt; wsum += wt;
        });
        if (!wsum) continue;
        const al = acc[3] / wsum, k = (y * Wc + x) * 4;
        if (al <= 0.01) continue;
        img.data[k] = acc[0] / acc[3]; img.data[k + 1] = acc[1] / acc[3]; img.data[k + 2] = acc[2] / acc[3]; img.data[k + 3] = Math.round(al * 255);
      }
    }
    ctx.putImageData(img, 0, 0);
    L.imageOverlay(cv.toDataURL(), [[s, wl], [n, e]], { pane: "cond", interactive: false }).addTo(w.cond);
  };
  w.when = (i) => {
    const t = w.cond_data && w.cond_data.times && w.cond_data.times[i];
    if (!t) return i ? `+${i} h` : "now";
    return `${t.slice(11, 13)}Z${i ? ` · +${i} h` : " · now"}`;
  };
  w.renderBar = () => {
    const el = document.getElementById(opts.bar);
    if (!el) return;
    const on = m.hasLayer(w.cond) && w.cond_data && (w.cond_data.times || []).length;
    el.style.display = on ? "" : "none";
    if (!on) return;
    const n = w.cond_data.times.length - 1;
    if (!el.dataset.built) {
      el.dataset.built = "1";
      el.innerHTML = `<span class="label" style="margin:0">Flying conditions</span><input type="range" min="0" step="1" aria-label="Forecast hour"><b></b>`;
      el.querySelector("input").addEventListener("input", (ev) => { w.condHour = +ev.target.value; el.querySelector("b").textContent = w.when(w.condHour); w.drawCond(); });
    }
    const sl = el.querySelector("input"); sl.max = n; sl.value = Math.min(w.condHour, n);
    el.querySelector("b").textContent = w.when(+sl.value);
  };
  w.loadMetars = async () => {
    if (m.getZoom() < 5) { w.metars.clearLayers(); return; }
    const b = m.getBounds();
    try {
      const r = await api(`/api/wx/metars?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}`);
      w.metars.clearLayers();
      (r.metars || []).forEach((x) => {
        const col = CAT_COL[x.cat] || "#5d6458";
        const wind = x.vrb ? `VRB ${x.wspd} kt` : x.wdir == null ? (x.wspd ? `${x.wspd} kt` : "calm") : `${String(Math.round(x.wdir)).padStart(3, "0")}° ${x.wspd} kt${x.wgst ? " G" + x.wgst : ""}`;
        L.marker([x.lat, x.lon], { icon: L.divIcon({ className: "", html: `<div class="metar-dot" style="--c:${col}">${windTick(x)}<i></i><b>${esc(x.station)}</b></div>`, iconSize: [16, 16], iconAnchor: [8, 8] }), zIndexOffset: -200 })
          .bindTooltip(`<b>${esc(x.station)} · ${esc(x.cat || "")}</b> · ${esc(wind)}${x.ceiling != null ? ` · ceiling ${x.ceiling.toLocaleString()} ft` : ""}${x.vis != null ? ` · vis ${x.vis} sm` : ""}<br><small style="font-family:var(--mono)">${esc(x.raw)}</small>`, { direction: "top", offset: [0, -8], className: "ttwx" })
          .addTo(w.metars);
      });
    } catch (e) {}
  };
  w.loadSigmets = async () => {
    try {
      const r = await api("/api/wx/sigmets");
      w.sigmets.clearLayers();
      (r.sigmets || []).forEach((x) => {
        const col = HAZ_COL(`${x.hazard} ${x.qualifier}`);
        const lv = (v) => v == null ? "" : v >= 18000 ? `FL${Math.round(v / 100)}` : `${Number(v).toLocaleString()} ft`;
        L.polygon(x.coords, { color: col, weight: 2, dashArray: x.kind === "AIRMET" ? "4 5" : null, fillColor: col, fillOpacity: 0.12, pane: "wxsig" })
          .bindTooltip(`<b>${esc(x.kind)} · ${esc([x.hazard, x.qualifier].filter(Boolean).join(" "))}</b><br>${x.base != null || x.top != null ? `${lv(x.base) || "SFC"} to ${lv(x.top) || "?"} · ` : ""}${x.to ? "until " + zTime(x.to) : ""}${x.fir ? ` · ${esc(x.fir)}` : ""}`, { sticky: true, className: "ttwx" })
          .bindPopup(`<pre style="white-space:pre-wrap;font:11px var(--mono);margin:0">${esc(x.raw)}</pre>`, { maxWidth: 420 })
          .addTo(w.sigmets);
      });
    } catch (e) {}
  };
  w.loadGrid = async () => {
    const b = m.getBounds();
    const alt = (opts.alt && opts.alt()) || 5000;
    try {
      const r = await api(`/api/wx/grid?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}&alt=${alt}`);
      w.grid.clearLayers();
      w.gridLevel = r.level_ft;
      w.gridError = r.error || null;
      (r.points || []).forEach((p) => {
        const cloud = p.base != null ? `<small>${p.low >= 75 ? "BKN" : "SCT"} ${p.base.toLocaleString()}</small>` : "";
        if (p.kt == null) return;
        L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="windarrow"><span style="transform:rotate(${(p.dir + 180) % 360}deg)">↑</span><b>${Math.round(p.kt)}</b>${cloud}</div>`, iconSize: [60, 44], iconAnchor: [30, 22] }), interactive: false, zIndexOffset: -300 }).addTo(w.grid);
      });
      legend();
    } catch (e) {}
  };
  w.refresh = (force) => {
    const on = (l) => m.hasLayer(l);
    if (on(w.radar) && (force === "radar" || !w.radarTime || Date.now() / 1000 - w.radarTime > 900)) w.loadRadar();
    if (on(w.metars)) w.loadMetars();
    if (on(w.sigmets) && (force || !w.loaded.sig || Date.now() - w.loaded.sig > 600000)) { w.loaded.sig = Date.now(); w.loadSigmets(); }
    if (on(w.grid)) w.loadGrid();
    if (on(w.sat)) w.loadSat();
    if (on(w.cond)) w.loadCond();
  };
  w.legendItems = () => {
    const out = [];
    if (m.hasLayer(w.radar)) out.push(`Radar${w.radarTime ? " " + zTime(w.radarTime) : ""}`);
    if (m.hasLayer(w.sat)) out.push(`Satellite clouds${w.satTime ? " " + w.satTime.slice(11, 16) + "Z" : ""}`);
    if (m.hasLayer(w.cond)) out.push(w.condError ? `Flying conditions: ${esc(w.condError)}` : `Forecast ${w.when(w.condHour)}: ${[1, 2, 3].map((k) => `<i style="background:rgb(${COND_RGBA[k].slice(0, 3)})"></i>${COND_NAME[k]}`).join(" ")}`);
    if (m.hasLayer(w.metars)) out.push(`METAR <i style="background:${CAT_COL.VFR}"></i>VFR <i style="background:${CAT_COL.MVFR}"></i>MVFR <i style="background:${CAT_COL.IFR}"></i>IFR <i style="background:${CAT_COL.LIFR}"></i>LIFR`);
    if (m.hasLayer(w.grid)) out.push(w.gridError ? `Winds aloft: ${esc(w.gridError)}` : `Winds at ${(w.gridLevel || 5000).toLocaleString()} ft, cloud base`);
    if (m.hasLayer(w.sigmets)) out.push(`SIGMETs: <span style="color:#c0392b">storms</span> <span style="color:#2f6fb5">icing</span> <span style="color:#d27d1e">turbulence</span>`);
    return out;
  };
  w.layers = () => ({ "Weather radar (RainViewer)": w.radar, "Satellite clouds (live)": w.sat, "Flying conditions (forecast)": w.cond,
    "Airport weather (METAR)": w.metars, "SIGMETs and AIRMETs": w.sigmets, "Winds aloft and low cloud": w.grid });
  const mine = [w.radar, w.metars, w.sigmets, w.grid, w.sat, w.cond];
  m.on("overlayadd", (e) => { if (mine.includes(e.layer)) { w.refresh(e.layer === w.radar ? "radar" : true); legend(); } });
  m.on("overlayremove", () => { w.renderBar(); legend(); });
  m.on("moveend", () => { clearTimeout(w.timer); w.timer = setTimeout(() => { if (m.hasLayer(w.metars)) w.loadMetars(); if (m.hasLayer(w.grid)) w.loadGrid(); if (m.hasLayer(w.cond)) w.loadCond(); }, 700); });
  setInterval(() => w.refresh(), 600000);
  return w;
}
const wx = makeWx(map, { legend: () => drawLegend(), bar: "wxtime", alt: () => { const t = editTarget(); return t ? t.plan.cruise_alt : 5000; } });
const refreshWx = (f) => wx.refresh(f);

const OVERLAYS = {
  "VFR chart (open flightmaps, parts of Europe)": () => L.tileLayer("https://nwy-tiles-api.prod.newaydata.com/tiles/{z}/{x}/{y}.png?path=latest/aero/latest", { tileSize: 512, zoomOffset: -1, minZoom: 6, maxZoom: 14, maxNativeZoom: 12, opacity: 0.95, attribution: "VFR charts &copy; open flightmaps" }),
};
let overlayLayers = {}, layerControl = null, aipKeyUsed = null;
function buildLayerControl(aipKey) {
  if (layerControl && aipKeyUsed === aipKey) return;
  aipKeyUsed = aipKey;
  if (layerControl) layerControl.remove();
  const fresh = Object.fromEntries(Object.entries(OVERLAYS).map(([k, f]) => [k, overlayLayers[k] || f()]));
  if (aipKey) fresh["Airspace & navaids (openAIP, worldwide)"] = L.tileLayer(`https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=${encodeURIComponent(aipKey)}`, { maxZoom: 14, attribution: "Airspace &copy; openAIP" });
  Object.entries(overlayLayers).forEach(([k, l]) => { if (!fresh[k] || fresh[k] !== l) map.removeLayer(l); });
  fresh["My past flights"] = historyLayer;
  Object.assign(fresh, wx.layers());
  overlayLayers = fresh;
  let on = ["My past flights"];
  try { const saved = localStorage.getItem("onward-overlays"); if (saved) on = JSON.parse(saved); } catch (e) {}
  on.forEach((k) => overlayLayers[k] && overlayLayers[k].addTo(map));
  layerControl = L.control.layers(baseLayers, overlayLayers, { position: "topright" }).addTo(map);
  setTimeout(refreshWx, 300);
}
function saveOverlays() { try { localStorage.setItem("onward-overlays", JSON.stringify(Object.keys(overlayLayers).filter((k) => map.hasLayer(overlayLayers[k])))); } catch (e) {} }
const baseLayers = Object.fromEntries(Object.entries(BASEMAPS).map(([k, f]) => [k, f()]));
let baseName = "Terrain"; try { baseName = localStorage.getItem("onward-base") || "Terrain"; } catch (e) {}
(baseLayers[baseName] || baseLayers.Terrain).addTo(map);
muteFor(map, baseName);
buildLayerControl(null);
map.on("overlayadd overlayremove", saveOverlays);
map.on("baselayerchange", (e) => { muteFor(map, e.name); baseName = e.name; try { localStorage.setItem("onward-base", e.name); } catch (err) {} });
const layers = { sights: L.layerGroup().addTo(map), offers: L.layerGroup().addTo(map), job: L.layerGroup().addTo(map), here: L.layerGroup().addTo(map), pois: L.layerGroup().addTo(map) };
let lmStatus = null, lmMissing = 0, lmTimer = null;
async function loadSights() {
  if (!S || !S.ready || tab !== "fly") return;
  const b = map.getBounds();
  const z = map.getZoom();
  if (z < 5) { layers.sights.clearLayers(); lmMissing = 0; drawLegend("Zoom in to see points of interest"); return; }
  const far = z < 7;  // far out: only the sim's own points of interest, so the map doesn't fill with dots
  try {
    const r = await api(`/api/pois?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}${far ? "&msfs=1" : ""}`);
    lmStatus = r.status; lmMissing = r.missing;
    layers.sights.clearLayers();
    // a light ring around every dot keeps it visible on the satellite map as well as the terrain map
    const size = (p) => { const m = p.source === "msfs"; return z >= 9 ? (m ? 6.5 : 5.5) : z >= 7 ? (m ? 5.5 : 4) : 4.5; };
    r.pois.forEach((p) => L.circleMarker([p.lat, p.lon], { radius: size(p), color: "#f4f2ec", weight: z >= 9 ? 2 : 1.5, opacity: 0.95, fillColor: p.source === "msfs" ? "#d98a1f" : "#4c6b78", fillOpacity: 1 })
      .bindTooltip(`${esc(p.name)}<br><small>${esc(p.label || "")} · click for more</small>`).on("click", () => showSight(p)).addTo(layers.sights));
    far ? drawLegend("Zoom in for landmarks") : drawLegend();
  } catch (e) {}
  clearTimeout(lmTimer);
  if (lmMissing) lmTimer = setTimeout(loadSights, 6000);  // keep filling in while areas download
}
function drawLegend(extra) {
  const st = lmStatus || {};
  let msg = extra || "";
  if (!extra) {
    if (st.error && st.paused) msg = `<span style="color:var(--warn)">${esc(st.error)}</span>`;
    else if (lmMissing) msg = `Downloading landmarks for this area (${lmMissing} to go)…`;
  }
  const wxl = wx.legendItems();
  if (wxl.length) msg = (msg ? msg + "<br>" : "") + wxl.join(" · ");
  $("#legend").innerHTML = `<i style="background:#c7832a"></i>MSFS POI <i style="background:#4c6b78"></i>Landmark <i style="background:#3d5a2c;border-radius:2px;height:3px;border:0;vertical-align:3px"></i>Past flights${msg ? `<div style="margin-top:4px">${msg}</div>` : ""}`;
}
map.on("moveend", () => { clearTimeout(lmTimer); lmTimer = setTimeout(loadSights, 400); });
let planeMarker = null, trail = null, trailPts = [], guideLine = null;
const COLORS = ["#f0a04b", "#7fb3d5", "#b993d6", "#7cc49a"];
const dot = (cls) => L.divIcon({ className: "", html: `<div class="poi-dot big ${cls || ""}"></div>`, iconSize: [14, 14], iconAnchor: [7, 7] });

// A plane drawn pointing north, so rotating it by the true heading lines it up with the map (a font glyph points a different way on every system)
const PLANE_SVG = `<svg width="30" height="30" viewBox="-16 -16 32 32" aria-hidden="true"><path d="M0 -14 L2.2 -4.5 L13 2 L13 4.5 L2.2 1.5 L1.6 9 L5 11.5 L5 13.5 L0 12 L-5 13.5 L-5 11.5 L-1.6 9 L-2.2 1.5 L-13 4.5 L-13 2 L-2.2 -4.5 Z" fill="#1b2119" stroke="#f4f2ec" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
function drawMap() {
  if (!S || !S.ready) return;
  const here = S.here;
  if (changed("map-here", here && here.ident)) {
    layers.here.clearLayers();
    if (here) L.circleMarker([here.lat, here.lon], { radius: 9, color: "#1b2119", weight: 2.5, fillColor: "#cfe98a", fillOpacity: 1 }).bindTooltip(`You are here: ${here.name}`).addTo(layers.here);
  }
  if (changed("map-offers", [S.job && S.job.id, (S.offers || []).map((o) => o.id), S.job && !!S.job.plan, openOffer])) {
    layers.offers.clearLayers(); layers.job.clearLayers(); layers.pois.clearLayers();
    const list = S.job ? [S.job] : S.offers || [];
    const bounds = here ? [[here.lat, here.lon]] : [];
    const keepView = !S.job && keys.mapOffersIds === JSON.stringify(list.map((o) => o.id));
    keys.mapOffersIds = JSON.stringify(list.map((o) => o.id));
    list.forEach((o, i) => {
      const sel = S.job || o.id === openOffer;
      const c = sel ? "#1b2119" : "#3d5a2c";
      const pts = [[o.dep.lat, o.dep.lon], ...o.pois.map((p) => [p.lat, p.lon]), [o.dest.lat, o.dest.lon]];
      const g = S.job ? layers.job : layers.offers;
      const line = o.plan ? o.plan.route.map((r) => [r.lat, r.lon]) : [[o.dep.lat, o.dep.lon], [o.dest.lat, o.dest.lon]];
      L.polyline(line, { color: "#f4f2ec", weight: sel ? 9 : 6, opacity: sel ? 0.9 : 0.6 }).addTo(g);
      L.polyline(line, { color: c, weight: sel ? 4 : 2.5, opacity: sel ? 0.95 : 0.85, dashArray: S.job || sel ? null : "7 5" }).addTo(g);
      const num = L.divIcon({ className: "", html: `<div class="dest-num ${sel ? "sel" : ""}">${S.job ? "✓" : i + 1}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] });
      const m = L.marker([o.dest.lat, o.dest.lon], { icon: num, zIndexOffset: sel ? 2000 : 1000 })
        .bindTooltip(`<b>${esc(o.dest.code || o.dest.ident)}</b> ${esc(o.dest.name)}<br>${o.distance_nm} nm · ${fmtMin(o.ete_min)}`).addTo(g);
      if (!S.job) m.on("click", () => { openOffer = o.id; renderSide(); drawMap(); const el = document.querySelector(`[data-open="${o.id}"]`); if (el) el.scrollIntoView({ behavior: "smooth", block: "center" }); });
      if (sel) o.pois.concat(o.dest_pois || []).forEach((p) => {
        L.marker([p.lat, p.lon], { icon: dot(p.source === "msfs" ? "msfs" : ""), zIndexOffset: 500 }).bindTooltip(`<b>${esc(p.name)}</b><br><small>${esc(p.label || "")}</small>`, { permanent: !!S.job, direction: "right", offset: [8, 0] }).on("click", () => showSight(p)).addTo(layers.pois);
      });
      if (S.job && o.alternate) L.circleMarker([o.alternate.lat, o.alternate.lon], { radius: 6, color: "#5d6458", weight: 2, dashArray: "3 3", fillOpacity: 0 }).bindTooltip(`Alternate ${esc(o.alternate.code)} ${esc(o.alternate.name)}`).addTo(g);
      pts.forEach((p) => bounds.push(p));
    });
    if (keepView) { /* just a selection change: keep the view */ }
    else if (bounds.length > 1) map.fitBounds(bounds, { padding: [50, 50], maxZoom: 10 });
    else if (here) map.setView([here.lat, here.lon], 9);
  }
  const smp = S.live && S.live.sample;
  if (smp && smp.lat != null) {
    const ll = [smp.lat, smp.lon];
    const icon = L.divIcon({ className: "", html: `<div class="plane-ico" style="transform:rotate(${Math.round(smp.hdg || 0)}deg)">${PLANE_SVG}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
    if (!planeMarker) planeMarker = L.marker(ll, { icon, zIndexOffset: 1000 }).addTo(map); else { planeMarker.setLatLng(ll); planeMarker.setIcon(icon); }
    const gd = S.live.flight && S.live.flight.guide;
    if (gd && S.live.flight.phase === "air") {
      const pts = [ll, [gd.lat, gd.lon]];
      if (!guideLine) guideLine = L.polyline(pts, { color: "#3d5a2c", weight: 3, dashArray: "4 6" }).addTo(map); else guideLine.setLatLngs(pts);
    } else if (guideLine) { guideLine.remove(); guideLine = null; }
    if (S.live.flight && S.live.flight.phase !== "preflight") {
      const last = trailPts[trailPts.length - 1];
      if (!last || Math.abs(last[0] - ll[0]) + Math.abs(last[1] - ll[1]) > 0.003) { trailPts.push(ll); if (!trail) trail = L.polyline(trailPts, { color: "#9c3a24", weight: 3, opacity: 0.9 }).addTo(map); else trail.setLatLngs(trailPts); }
    } else if (trailPts.length && !S.job) { trailPts = []; if (trail) { trail.remove(); trail = null; } }
  }
  if (!keys.sightsOnce) { keys.sightsOnce = "1"; loadSights(); }
}

/* ---------------- route editing on the map ---------------- */
map.createPane("edit"); map.getPane("edit").style.zIndex = 450;
const editLayer = L.layerGroup().addTo(map);
const hintLayer = L.layerGroup().addTo(map);   // detour previews and the side-view cursor
let detourPinned = null, cursorDot = null;
function showDetour(id, k, fly) {
  hintLayer.eachLayer((l) => { if (l !== cursorDot) hintLayer.removeLayer(l); });
  const p = planFor(id);
  if (!p || !p.detours || !p.detours[k]) return;
  const d = p.detours[k], r = p.route;
  const at = Math.max(1, Math.min(r.length - 1, d.at || cheapestAt(r, d) + 1));
  const a = r[at - 1], b = r[at];
  L.polyline([[a.lat, a.lon], [d.lat, d.lon], [b.lat, b.lon]], { pane: "edit", color: "#3d5a2c", weight: 3, dashArray: "6 6", opacity: 0.95, interactive: false }).addTo(hintLayer);
  L.circleMarker([d.lat, d.lon], { pane: "edit", radius: 11, color: "#1b2119", weight: 2.5, fillColor: "#cfe98a", fillOpacity: 1, interactive: false }).addTo(hintLayer);
  L.marker([d.lat, d.lon], { icon: L.divIcon({ className: "", html: `<div class="det-tag">+${Math.max(1, Math.round(d.extra_nm))} nm · ${esc(d.name)}</div>`, iconSize: [0, 0], iconAnchor: [-16, 12] }), interactive: false, zIndexOffset: 3000 }).addTo(hintLayer);
  if (fly) map.fitBounds([[a.lat, a.lon], [d.lat, d.lon], [b.lat, b.lon]], { padding: [80, 80], maxZoom: 11 });
  else if (!map.getBounds().pad(-0.05).contains([d.lat, d.lon])) map.panTo([d.lat, d.lon]);
}
function clearDetour() {
  if (detourPinned) { showDetour(detourPinned.id, detourPinned.k, false); return; }
  hintLayer.eachLayer((l) => { if (l !== cursorDot) hintLayer.removeLayer(l); });
}
// position along the route, nm from the start -> lat/lon
function routeAt(route, nm, total) {
  const cum = [0];
  for (let i = 1; i < route.length; i++) cum.push(cum[i - 1] + distNm(route[i - 1], route[i]));
  const want = Math.max(0, Math.min(cum[cum.length - 1], nm * (cum[cum.length - 1] / (total || cum[cum.length - 1] || 1))));
  for (let i = 1; i < route.length; i++) {
    if (want <= cum[i] || i === route.length - 1) {
      const f = cum[i] - cum[i - 1] ? (want - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
      return { lat: route[i - 1].lat + (route[i].lat - route[i - 1].lat) * f, lon: route[i - 1].lon + (route[i].lon - route[i - 1].lon) * f };
    }
  }
  return route[0];
}
function nmAlong(route, latlng, total) {
  const ll = route.map((w) => [w.lat, w.lon]);
  const i = segIndex(ll, latlng);
  const a = route[i], b = route[i + 1];
  const pa = map.latLngToLayerPoint([a.lat, a.lon]), pb = map.latLngToLayerPoint([b.lat, b.lon]), pc = map.latLngToLayerPoint(latlng);
  const dx = pb.x - pa.x, dy = pb.y - pa.y;
  const t = Math.max(0, Math.min(1, ((pc.x - pa.x) * dx + (pc.y - pa.y) * dy) / ((dx * dx + dy * dy) || 1)));
  let cum = 0, all = 0;
  for (let k = 1; k < route.length; k++) { const d = distNm(route[k - 1], route[k]); if (k <= i) cum += d; all += d; }
  return (cum + t * distNm(a, b)) * ((total || all) / (all || 1));
}
function showCursorOnMap(route, nm, total) {
  const p = routeAt(route, nm, total);
  if (!cursorDot) cursorDot = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div class="cur-dot"></div>`, iconSize: [22, 22], iconAnchor: [11, 11] }), interactive: false, zIndexOffset: 5000 }).addTo(hintLayer);
  else { cursorDot.setLatLng([p.lat, p.lon]); if (!hintLayer.hasLayer(cursorDot)) cursorDot.addTo(hintLayer); }
}
function hideCursorOnMap() { if (cursorDot) { hintLayer.removeLayer(cursorDot); cursorDot = null; } }
let profOpen = true, profDrag = null, profCursor = null;
try { profOpen = localStorage.getItem("onward-profile") !== "0"; } catch (e) {}
function segIndex(ll, latlng) {
  let best = 0, bd = Infinity;
  const P = (p) => map.latLngToLayerPoint(L.latLng(p[0], p[1]));
  const c = map.latLngToLayerPoint(latlng);
  for (let i = 0; i < ll.length - 1; i++) {
    const a = P(ll[i]), b = P(ll[i + 1]);
    const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((c.x - a.x) * dx + (c.y - a.y) * dy) / len));
    const d = Math.hypot(a.x + t * dx - c.x, a.y + t * dy - c.y);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}
const handle = (cls, txt) => L.divIcon({ className: "", html: `<div class="${cls}">${txt || ""}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] });
function flightTarget() {  // the leg you're flying, once it can't be edited any more: the side view stays, read-only
  if (tab !== "fly" || !S || !S.job || !S.job.plan) return null;
  const f = S.live && S.live.flight;
  return f && f.phase !== "preflight" ? { id: S.job.id, plan: S.job.plan } : null;
}
function drawEditor() {
  const t = editTarget();
  const ft = t ? null : flightTarget();
  const key = t ? [t.id, t.plan.built_at, t.plan.route.map((r) => [r.lat, r.lon]), planBusy[t.id]] : ft ? ["ro", ft.id, ft.plan.built_at] : null;
  if (!changed("edit", key)) return;
  editLayer.clearLayers();
  hintLayer.clearLayers(); cursorDot = null;
  drawProfile(t || ft, !t && !!ft);
  if (!t || t.plan.rules === "IFR") return;
  const id = t.id, r = t.plan.route, ll = r.map((w) => [w.lat, w.lon]);
  L.polyline(ll, { pane: "edit", color: "#f4f2ec", weight: 11, opacity: 0.9, interactive: false }).addTo(editLayer);
  const line = L.polyline(ll, { pane: "edit", color: "#1b2119", weight: 4, opacity: planBusy[id] ? 0.4 : 1 }).addTo(editLayer);
  line.bindTooltip("Click to add a point here", { sticky: true });
  line.on("mousemove", (e) => { const pr = t.plan.profile; if (pr && profCursor) profCursor(nmAlong(r, e.latlng, pr.total)); });
  line.on("mouseout", () => { if (profCursor) profCursor(null); });
  line.on("click", (e) => { L.DomEvent.stop(e); const i = segIndex(ll, e.latlng); editWps(id, (w) => w.splice(i, 0, { kind: "user", name: "Your point", lat: e.latlng.lat, lon: e.latlng.lng }), "Added a point"); });
  for (let i = 0; i < r.length - 1; i++) {
    const m = L.marker([(r[i].lat + r[i + 1].lat) / 2, (r[i].lon + r[i + 1].lon) / 2], { icon: handle("mid-h", "+"), draggable: true, zIndexOffset: 900 }).addTo(editLayer);
    m.bindTooltip("Drag to bend the route");
    m.on("dragend", (e) => { const p = e.target.getLatLng(); editWps(id, (w) => w.splice(i, 0, { kind: "user", name: "Your point", lat: p.lat, lon: p.lng }), "Added a point"); });
  }
  r.forEach((w, i) => {
    const fixed = i === 0 || i === r.length - 1;
    const cls = fixed ? "wp-h end" : `wp-h ${w.kind}`;
    const m = L.marker([w.lat, w.lon], { icon: handle(cls), draggable: !fixed, zIndexOffset: 1500 }).addTo(editLayer);
    m.bindTooltip(`<b>${esc(w.ident || "")}</b> ${esc(w.kind === "visual" ? w.name : wpLabel(w))}${fixed ? "" : "<br><small>Drag to move · right-click to remove</small>"}`, { direction: "top", offset: [0, -10] });
    if (fixed) return;
    m.on("dragend", (e) => { const p = e.target.getLatLng(); editWps(id, (x) => { const o = x[i - 1]; const moved = distNm(o, { lat: p.lat, lon: p.lng }) > 0.5; x[i - 1] = moved ? { kind: "user", name: o.kind === "user" ? o.name : `Near ${o.name || o.ident}`, lat: p.lat, lon: p.lng } : o; }, `Moved ${w.ident || w.name}`); });
    m.on("contextmenu", (e) => { L.DomEvent.stop(e); editWps(id, (x) => x.splice(i - 1, 1), `Removed ${w.name || w.ident}`); });
  });
}

/* ---------------- side view (profile) ---------------- */
let profLive = null, profTrace = { id: null, pts: [] };
function drawProfile(t, ro) {
  const box = $("#profile");
  profLive = null;
  if (!box) return;
  const pr = t && t.plan.profile;
  if (!pr || !pr.terrain || !pr.terrain.length) { box.classList.remove("on"); box.innerHTML = ""; return; }
  box.classList.add("on");
  box.classList.toggle("closed", !profOpen);
  const W = Math.max(420, (box.clientWidth || 900) - (profOpen ? 330 : 0)), H = 190, x0 = 54, x1 = W - 14, y1 = H - 28;
  const tot = pr.total || 1;
  const tmax = Math.max(...pr.terrain.map((p) => p[1]));
  const top = Math.max(pr.cruise + 2000, pr.msa + 1500, tmax + 1500, ...pr.clouds.filter((c) => c.base < pr.cruise + 4000).map((c) => Math.min(c.top + 500, pr.cruise + 4000)));
  const aloft = [...(pr.aloft || []), ...pr.clouds.filter((c) => c.base >= top && c.cover >= 30)];
  const y0 = aloft.length ? 26 : 14;
  const X = (nm) => x0 + (nm / tot) * (x1 - x0), Y = (ft) => y1 - (Math.max(0, ft) / top) * (y1 - y0);
  const step = top > 12000 ? 4000 : top > 6000 ? 2000 : 1000;
  let grid = "";
  for (let f = 0; f <= top; f += step) grid += `<line x1="${x0}" x2="${x1}" y1="${Y(f)}" y2="${Y(f)}" class="g"/><text x="${x0 - 6}" y="${Y(f) + 4}" text-anchor="end">${f ? f.toLocaleString() : "0"}</text>`;
  const terr = `M${X(0)},${y1} ` + pr.terrain.map((p) => `L${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ") + ` L${X(tot)},${y1} Z`;
  const cOp = (cv) => cv >= 75 ? 0.85 : cv >= 50 ? 0.6 : 0.32;
  const clouds = pr.clouds.filter((c) => c.base < top).map((c) => { const t2 = Math.min(top, c.top);
    return `<rect x="${X(c.from)}" y="${Y(t2)}" width="${Math.max(1, X(c.to) - X(c.from))}" height="${Math.max(3, Y(c.base) - Y(t2))}" class="cl" opacity="${cOp(c.cover)}"/>`; }).join("");
  // grey sky above the top of the chart
  const aloftSvg = aloft.map((c) => `<rect x="${X(c.from)}" y="2" width="${Math.max(1, X(c.to) - X(c.from))}" height="12" class="cl aloft" opacity="${cOp(c.cover)}"/>`).join("");
  const aBases = aloft.map((c) => c.base).filter((b) => b != null);
  const aloftLbl = aloft.length ? `<text x="${x0 + 6}" y="11" class="aloftt">${aloft.some((c) => c.cover >= 75) ? "OVERCAST" : "CLOUD"} ABOVE${aBases.length ? `, BASE ~${(Math.round(Math.min(...aBases) / 1000) * 1000).toLocaleString()} FT` : ""}</text>` : "";
  const alt = pr.alt.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const bad = pr.issues.filter((i) => i.from != null).map((i) => `<line x1="${X(i.from)}" x2="${X(Math.max(i.to, i.from + tot * 0.01))}" y1="${Y(pr.cruise)}" y2="${Y(pr.cruise)}" class="bad ${i.kind}"/>`).join("");
  let lastX = -99;
  const marks = pr.marks.map((m, i) => { const x = X(m.nm); const show = i === pr.marks.length - 1 ? x - lastX > 60 : x - lastX > 70 && x < x1 - 70; if (show) lastX = x; return { m, i, show }; }).map(({ m, i, show }) => `<line x1="${X(m.nm)}" x2="${X(m.nm)}" y1="${y1}" y2="${y1 + 4}" class="tick"/>${show ? `<text x="${X(m.nm)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === pr.marks.length - 1 ? "end" : "middle"}">${esc(m.ident || "")}</text>` : ""}`).join("");
  const frz = pr.freezing != null && pr.freezing < top ? `<line x1="${x0}" x2="${x1}" y1="${Y(pr.freezing)}" y2="${Y(pr.freezing)}" class="frz"/><text x="${x1}" y="${Y(pr.freezing) - 4}" text-anchor="end" class="frzt">FREEZING ${pr.freezing.toLocaleString()}</text>` : "";
  const hz = pr.hazards || [];
  const HZ = { ice: "Icing", turb: "Turbulence", wave: "Mountain waves", low_turb: "Gusty low down", storm: "Storms" };
  const haz = hz.flatMap((h) => h.parts.map(([a, b, lo, hi]) => {
    if (h.kind === "storm") return `<rect x="${X(a)}" y="${y0}" width="${Math.max(2, X(b) - X(a))}" height="${y1 - y0}" class="hz-storm ${h.level}"/>`;
    const lo2 = Math.max(0, lo), hi2 = Math.min(top, hi);
    if (hi2 <= lo2) return "";
    return `<rect x="${X(a)}" y="${Y(hi2)}" width="${Math.max(2, X(b) - X(a))}" height="${Math.max(2, Y(lo2) - Y(hi2))}" class="hz-${h.kind === "ice" ? "ice" : "turb"}"/>`;
  })).join("");
  const stormLbl = hz.filter((h) => h.kind === "storm").map((h) => `<text x="${(X(h.from) + X(h.to)) / 2}" y="${y0 + 12}" text-anchor="middle" class="hz-st">${h.level === "forecast" ? "STORMS" : "STORMS?"}</text>`).join("");
  const kinds = [...new Set(hz.map((h) => h.kind === "ice" ? "ice" : h.kind === "storm" ? "storm" : "turb"))];
  const legend = kinds.map((k) => `<span class="hzkey"><i class="sw-${k}"></i>${{ ice: "Icing", turb: "Turbulence", storm: "Storms" }[k]}</span>`).join("");
  const hazAt = (nm) => hz.filter((h) => h.parts.some(([a, b]) => nm >= a && nm <= b)).map((h) => {
    const part = h.parts.find(([a, b]) => nm >= a && nm <= b);
    return h.kind === "storm" ? (h.level === "forecast" ? "STORMS FORECAST" : "STORMS POSSIBLE") : `${HZ[h.kind].toUpperCase()} ${Math.max(0, part[2]).toLocaleString()}\u2013${part[3].toLocaleString()}`;
  });
  const hx = (X(pr.alt[1][0]) + X(pr.alt[2][0])) / 2;
  const svg = `<svg id="profsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Side view of the route: terrain, your altitude ${pr.cruise} ft, minimum safe altitude ${pr.msa} ft${kinds.length ? ", with " + kinds.join(", ") + " along the way" : ""}">
    <defs><pattern id="pat-ice" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="pi"/></pattern>
    <pattern id="pat-turb" width="10" height="6" patternUnits="userSpaceOnUse"><path d="M0,4 L2.5,1.5 L5,4 L7.5,1.5 L10,4" class="pt"/></pattern></defs>
    ${grid}${haz}${clouds}${aloftSvg}<path d="${terr}" class="terr"/>${stormLbl}${aloftLbl}
    <line x1="${x0}" x2="${x1}" y1="${Y(pr.msa)}" y2="${Y(pr.msa)}" class="msa"/><text x="${x0 + 6}" y="${Y(pr.msa) - 4}" class="msat">MSA ${pr.msa.toLocaleString()}</text>${frz}
    <polyline points="${alt}" class="altw"/><polyline points="${alt}" class="alt" id="profalt"/>${bad}
    ${ro ? `<polyline id="proftrace" class="trace" points=""/><g id="profme" style="display:none"><circle r="6.5" class="me"/><text y="-12" text-anchor="middle" class="met"></text></g>` : `<g id="profknob" transform="translate(${hx},${Y(pr.cruise)})"><circle r="10" class="knob"/><text y="-16" text-anchor="middle" class="knobt">${pr.cruise.toLocaleString()} FT</text></g>`}${marks}
    <g id="profcur" style="display:none"><line y1="${y0}" y2="${y1}" class="cur"/><circle r="4" class="curdot"/><rect class="curbg" rx="4" height="30"/><text class="curt" y="${y0 + 14}"></text><text class="curt2" y="${y0 + 27}"></text><text class="curt3" y="${y0 + 40}"></text></g>
    <rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="transparent" id="profhit" style="cursor:${ro ? "crosshair" : "ns-resize"}"/></svg>`;
  const iss = pr.issues.map((i) => `<p class="iss ${i.kind}">${esc(i.text)}</p>`).join("");
  const fixes = ro ? "" : pr.fixes.map((f, k) => `<button class="fix" data-fix="${k}"><b>${esc(f.title)}</b><small>${esc(f.detail || "")}</small></button>`).join("");
  box.innerHTML = `<div class="ph"><button class="linkbtn" id="proftoggle">${profOpen ? "Hide" : "Show"} side view</button><span class="label" style="margin:0">${ro ? `In flight · planned cruise ${pr.cruise.toLocaleString()} ft · MSA ${pr.msa.toLocaleString()} ft` : `Drag the line to change altitude · cruise ${pr.cruise.toLocaleString()} ft · MSA ${pr.msa.toLocaleString()} ft`}</span>${legend ? `<span class="hzlegend">${legend}</span>` : ""}</div>
    ${profOpen ? `<div class="pb"><div class="pg">${svg}</div><div class="pside">${(pr.wx === "none" ? `<p class="iss turb">No weather forecast for this route right now, so cloud, icing, turbulence and storms aren't shown. Check the briefing and the map's weather layers.</p>` : "") + (iss || `<p class="iss ok">Clear of terrain${pr.wx === "none" ? "" : `${pr.clouds.length ? " and cloud" : ""}${(pr.hazards || []).length ? "" : ", with no icing, turbulence or storms forecast,"}`} at ${pr.cruise.toLocaleString()} ft.</p>`) + (pr.wx_source === "MET Norway" ? `<p class="hint hzhint">Forecast from MET Norway (Open-Meteo is busy), with simplified cloud layers.</p>` : "") + ((pr.cloud_metars || []).length ? `<p class="hint hzhint">Low cloud near ${pr.cloud_metars.map(esc).join(", ")} from the latest airport reports (METAR), which MSFS live weather also uses. Forecast elsewhere.</p>` : "")}${fixes}${(pr.hazards || []).length ? `<p class="hint hzhint">From the forecast for when you pass each point. MSFS live weather can differ.</p>` : ""}</div></div>` : ""}`;
  $("#proftoggle").onclick = () => { profOpen = !profOpen; try { localStorage.setItem("onward-profile", profOpen ? "1" : "0"); } catch (e) {} keys.edit = null; drawEditor(); };
  box.querySelectorAll("[data-fix]").forEach((b) => b.onclick = () => { const f = pr.fixes[+b.dataset.fix];
    if (f.action === "alt") replan(t.id, { cruise_alt: f.value }, `Cruise ${f.value.toLocaleString()} ft`);
    else if (f.action === "shape") { pushUndo(t.id); replan(t.id, { shape: f.value }, "Following the lower ground"); }
    else if (f.action === "later") replan(t.id, { depart_offset_min: f.value }, `Leaving ${f.value / 60} h later`); });
  const hit = $("#profhit");
  profCursor = null;
  if (!hit) return;
  if (ro) { profLive = { X, Y, tot, top, id: t.id, route: t.plan.route }; updateProfileLive(); }
  const terrAt = (nm) => { let best = pr.terrain[0]; for (const p of pr.terrain) if (Math.abs(p[0] - nm) < Math.abs(best[0] - nm)) best = p; return best[1]; };
  const altAt = (nm) => { const a = pr.alt; for (let i = 1; i < a.length; i++) if (nm <= a[i][0]) { const f = a[i][0] - a[i - 1][0] ? (nm - a[i - 1][0]) / (a[i][0] - a[i - 1][0]) : 0; return a[i - 1][1] + (a[i][1] - a[i - 1][1]) * f; } return a[a.length - 1][1]; };
  profCursor = (nm) => {
    const g = $("#profcur");
    if (!g) return;
    if (nm == null) { g.style.display = "none"; hideCursorOnMap(); return; }
    nm = Math.max(0, Math.min(tot, nm));
    const x = X(nm), te = Math.round(terrAt(nm) / 10) * 10, al = Math.round(altAt(nm) / 10) * 10;
    g.style.display = "";
    g.querySelector("line").setAttribute("x1", x); g.querySelector("line").setAttribute("x2", x);
    g.querySelector("circle").setAttribute("cx", x); g.querySelector("circle").setAttribute("cy", Y(al));
    const left = x > x1 - 150;
    const tx = left ? x - 8 : x + 8, anchor = left ? "end" : "start";
    const t1 = g.querySelector(".curt"), t2 = g.querySelector(".curt2"), t3 = g.querySelector(".curt3");
    const here = hazAt(nm);
    t3.textContent = here.join(" \u00b7 ");
    t3.setAttribute("x", left ? x - 8 : x + 8); t3.setAttribute("text-anchor", left ? "end" : "start");
    t1.textContent = `${nm.toFixed(1)} NM · YOU ${al.toLocaleString()} FT`; t2.textContent = `GROUND ${te.toLocaleString()} FT · CLEARANCE ${(al - te).toLocaleString()}`;
    [t1, t2].forEach((el) => { el.setAttribute("x", tx); el.setAttribute("text-anchor", anchor); el.classList.toggle("low", al - te < 500); });
    const bg = g.querySelector(".curbg"); bg.setAttribute("height", here.length ? 43 : 30); bg.setAttribute("width", Math.max(190, here.join(" \u00b7 ").length * 6.2 + 12)); bg.setAttribute("x", left ? x - 6 - +bg.getAttribute("width") : x + 4); bg.setAttribute("y", y0 + 2);
    showCursorOnMap(t.plan.route, nm, tot);
  };
  const nmFromEv = (ev) => { const rc = hit.getBoundingClientRect(); return ((ev.clientX - rc.left) / rc.width) * tot; };
  hit.addEventListener("pointermove", (ev) => { if (!profDrag) profCursor(nmFromEv(ev)); });
  hit.addEventListener("pointerleave", () => { if (!profDrag) profCursor(null); });
  const toFt = (ev) => { const rc = hit.getBoundingClientRect(); const y = ev.clientY - rc.top + y0; return Math.round(((y1 - y) / (y1 - y0)) * top / 100) * 100; };
  if (ro) return;  // nothing to drag once you're flying
  hit.onpointerdown = (ev) => { profDrag = { ft: pr.cruise }; hit.setPointerCapture(ev.pointerId); };
  hit.onpointermove = (ev) => { if (!profDrag) return; const ft = Math.max(500, Math.min(17500, Math.round(toFt(ev) / 500) * 500)); profDrag.ft = ft;
    const pts = pr.alt.map((p, i) => [p[0], i === 1 || i === 2 ? ft : p[1]]).map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    $("#profalt").setAttribute("points", pts); $("#profknob").setAttribute("transform", `translate(${hx},${Y(ft)})`); $("#profknob text").textContent = ft.toLocaleString() + " FT"; };
  hit.onpointerup = () => { if (!profDrag) return; const ft = profDrag.ft; profDrag = null; if (ft !== pr.cruise) replan(t.id, { cruise_alt: ft }, `Cruise ${ft.toLocaleString()} ft`); };
}

/* in flight: your aircraft and the altitude you've flown, on the side view */
function updateProfileLive() {
  const g = profLive, smp = S && S.live && S.live.sample;
  if (!g || !smp || smp.lat == null || !$("#profme")) return;
  if (profTrace.id !== g.id) profTrace = { id: g.id, pts: [] };
  const nm = Math.max(0, Math.min(g.tot, nmAlong(g.route, L.latLng(smp.lat, smp.lon), g.tot)));
  const alt = Math.round(smp.alt || 0);
  const last = profTrace.pts[profTrace.pts.length - 1];
  if (!last || Math.abs(last[0] - nm) > 0.15 || Math.abs(last[1] - alt) > 40) profTrace.pts.push([nm, alt]);
  if (profTrace.pts.length > 1500) profTrace.pts = profTrace.pts.filter((p, i) => i % 2 === 0);
  $("#proftrace").setAttribute("points", profTrace.pts.map((p) => `${g.X(p[0]).toFixed(1)},${g.Y(p[1]).toFixed(1)}`).join(" "));
  const me = $("#profme");
  me.style.display = "";
  me.setAttribute("transform", `translate(${g.X(nm).toFixed(1)},${g.Y(alt).toFixed(1)})`);
  me.querySelector("text").textContent = `YOU ${alt.toLocaleString()} FT`;
}

/* ---------------- side panel (Fly) ---------------- */
function prefsForm() {
  const p = prefs;
  const seg = (key, opts) => `<div class="seg" data-key="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${String(p[key]) === String(v) ? "on" : ""}">${l}</button>`).join("")}</div>`;
  return `<div class="form">
    <span class="label">Where to next</span><h2>Plan the <em>next leg.</em></h2>
    <label>Distance (nm)</label>
    <div class="range"><input type="number" id="pmin" value="${p.min_nm}" min="0" max="800"> to <input type="number" id="pmax" value="${p.max_nm}" min="10" max="1200">
    <span class="hint" id="pete"></span></div>
    <label>Challenge</label>${seg("difficulty", [["any", "Mixed"], ["easy", "Relaxed"], ["normal", "Normal"], ["hard", "Tough"]])}
    <label>Runway surface</label>${seg("surface", [["any", "Any"], ["paved", "Paved"], ["unpaved", "Grass/dirt"]])}
    <label>Weather (live)</label>${seg("weather", [["vfr", "VFR only"], ["marginal", "Up to MVFR"], ["any", "Anything"]])}
    <label>Darkness</label>${seg("night", [["day", "Daylight arrivals"], ["lit_only", "Night if lit"], ["any", "Anything goes"]])}
    <label class="toggle"><input type="checkbox" id="pbush" ${p.bush ? "checked" : ""}> Bush mood: favor short and unpaved strips</label>
    <label class="toggle"><input type="checkbox" id="punch" ${p.uncharted ? "checked" : ""}> Include strips with unknown runway data or no sim code</label>
    <label class="toggle"><input type="checkbox" id="pnew" ${p.prefer_new ? "checked" : ""}> Prefer places I haven't been</label>
    <div style="margin-top:14px"><button class="btn big" id="find" ${S.gen.running ? "disabled" : ""}>${S.gen.running ? esc(S.gen.progress || "Finding flights…") : "Find adventures"}</button>${S.gen.running ? `<p class="hint">Onward is working on it. The first search in a new area can take up to a minute while it downloads terrain and landmarks; after that it's quick.</p>` : ""}</div>
  </div>`;
}

function wxChips(w, label) {
  if (!w) return `<span class="chip">${label}: no report nearby</span>`;
  const cls = w.cat === "VFR" ? "good" : w.cat === "MVFR" ? "warn" : "bad";
  const wind = w.vrb ? `VRB ${w.wspd} kt` : w.wdir == null ? `${w.wspd} kt` : `${String(Math.round(w.wdir)).padStart(3, "0")}° ${w.wspd} kt`;
  const cig = w.cat !== "VFR" ? `${w.ceiling != null ? ` · cig ${w.ceiling.toLocaleString()} ft` : ""}${w.vis != null ? ` · vis ${w.vis} sm` : ""}` : "";
  return `<span class="chip ${cls}">${label}: ${w.cat} · ${wind}${w.wgst ? " G" + w.wgst : ""}${cig}</span>`;
}
function runwayChips(o) {
  const r = o.runway;
  if (!r || !r.length) return `<span class="chip warn">Runway: unknown</span>`;
  const xw = r.crosswind != null ? `<span class="chip ${r.crosswind >= 12 ? "warn" : ""}">X-wind ${r.crosswind} kt${r.headwind < 0 ? `, tail ${-r.headwind}` : ""}</span>` : "";
  return `<span class="chip">RWY ${esc(r.end || r.id)} · ${r.length.toLocaleString()} ft · ${r.surface}</span>${o.night ? `<span class="chip ${r.lighted ? "" : "bad"}">${r.lighted ? "Lighted" : "Unlit!"}</span>` : ""}${xw}`;
}
let openOffer = null, filtersOpen = true;
const KIND = { scenic: "Scenic", tour: "Wander", challenge: "Challenge", ride: "A ride for a friend", ferry: "Maintenance ferry", trip: "Trip leg", anywhere: "Your pick" };
function offerCard(o, i) {
  const d = o.dest, open = openOffer === o.id;
  const r = o.runway || {};
  const w = o.weather;
  const n = String(i + 1).padStart(2, "0");
  const surf = r.length ? (r.surface === "paved" ? "paved" : r.surface) : "strip";
  const first = (o.highlights || [])[0];
  const head = `<div class="offer-head ox" data-open="${o.id}">
      <span class="n">${n}</span>
      <span class="t">${o.rules === "IFR" ? `<span class="ifr">IFR</span>` : ""}${o.trip_leg ? `<span class="ifr">TRIP ${o.trip_leg.n}/${o.trip_leg.of}</span>` : o.kind === "anywhere" ? `<span class="ifr">YOUR PICK</span>` : ""}${esc(o.title)}</span>
      <span class="d">${o.distance_nm} nm · ${surf}${w ? ` · ${w.cat}` : ""}</span>
      ${open ? "" : first ? `<span class="s">${esc(first.text)}</span>` : ""}
    </div>`;
  if (!open) return `<div class="offer compact">${head}</div>`;
  return `<div class="offer open">${head}
    <div class="row" style="justify-content:space-between;margin:8px 0 0 42px"><span class="kind">${KIND[o.kind] || o.kind} · ${fmtMin(o.ete_min)} · ${esc(d.code || "no code")} ${esc(d.town || d.name)}${d.country !== (S.here || {}).country ? `, ${esc(d.country_name)}` : ""}</span>${stars(o.difficulty, o.difficulty_why)}</div>
    <div style="margin-left:42px">
    ${(o.highlights || []).length ? `<ul class="hl">${o.highlights.map((h) => `<li>${esc(h.text)}</li>`).join("")}</ul>` : ""}
    ${o.lines.map((l) => `<p class="flavor">${esc(l)}</p>`).join("")}
    <div class="facts">
      ${runwayChips(o)} ${wxChips(o.weather, "Dest")}
      <span class="chip">${o.night ? "Arrive after dark" : "Daylight"} · ${o.eta_utc}</span>
      ${d.elev > 3000 ? `<span class="chip warn">Elev ${d.elev.toLocaleString()} ft</span>` : ""}
      ${d.uncharted ? `<span class="chip warn">No sim code: ${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}</span>` : ""}
      ${o.rules === "IFR" ? (o.alternate ? `<span class="chip">Alternate ${esc(o.alternate.code)} · ${o.alternate.dist_nm} nm</span>` : `<span class="chip warn">No good alternate</span>`) : ""}
    </div>
    ${o.pois.length > 2 ? `<p class="hint"><b>Sights:</b> ${o.pois.map(sightLink).join(" · ")}</p>` : ""}
    <div class="row" style="margin-top:14px"><button class="btn" data-accept="${o.id}">Take this leg</button><button class="btn ghost" data-plan="${o.id}">${planOpen[o.id] ? "Hide the plan" : "Read the plan"}</button></div>
    </div>
    <div class="planbox" id="plan-${o.id}">${planOpen[o.id] ? (planOpen[o.id] === "loading" ? `<p class="hint">Working out terrain, winds, weather and fuel…</p>` : planHtml(planOpen[o.id], o.id)) : ""}</div>
  </div>`;
}
function hlLine(o) {
  const hl = (o.highlights || []).slice(0, 2);
  if (!hl.length) return "";
  return `<div class="hl-line">${hl.map((h) => `<span><span class="ico">${h.icon}</span>${esc(h.text)}</span>`).join("")}</div>`;
}
function filterSummary() {
  const p = prefs;
  const lbl = { any: "mixed", easy: "relaxed", normal: "normal", hard: "tough" }[p.difficulty];
  const wx = { vfr: "VFR only", marginal: "up to MVFR", any: "any weather" }[p.weather];
  return `<div class="filters-sum"><span>${p.min_nm} to ${p.max_nm} nm · ${lbl} · ${p.surface === "any" ? "any surface" : p.surface === "paved" ? "paved" : "grass/dirt"} · ${wx}${p.bush ? " · bush" : ""}</span>
    <span class="row"><button class="linkbtn" id="refind">${S.gen.running ? esc(S.gen.progress || "Finding flights…") : "New search"}</button><button class="linkbtn" id="openfilters">Filters</button></span></div>`;
}

const planOpen = {};
const sightAttr = (p) => `data-sight="${esc(JSON.stringify({ name: p.name, lat: p.lat, lon: p.lon, wiki: p.wiki || "", label: p.label || "" }))}"`;
const sightLink = (p) => `<a class="sight" ${sightAttr(p)}>${esc(p.name)}</a>`;
async function showSight(p) {
  const box = $("#sightcard");
  box.innerHTML = `<button class="x" onclick="this.parentNode.classList.remove('on')">×</button><div class="kicker">${esc(p.label || "Sight")}</div><h4>${esc(p.name)}</h4><p class="hint">Looking it up…</p>`;
  box.classList.add("on");
  try {
    const r = await api(`/api/sight?name=${encodeURIComponent(p.name)}&lat=${p.lat}&lon=${p.lon}&wiki=${encodeURIComponent(p.wiki || "")}`);
    if (!box.classList.contains("on")) return;
    let body;
    if (r.error) body = `<p class="hint">${esc(r.error)}</p>`;
    else if (r.none) body = `<p class="hint">No Wikipedia article found for this one. You'll have to see it for yourself.</p>`;
    else body = `${r.thumb ? `<img src="${esc(r.thumb)}" alt="">` : ""}<p>${esc(r.extract)}</p>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Read more on Wikipedia</a>` : ""}`;
    const et = editTarget();
    const inRoute = et && et.plan.route.some((w) => Math.abs(w.lat - p.lat) < 0.001 && Math.abs(w.lon - p.lon) < 0.001);
    box.innerHTML = `<button class="x" onclick="this.parentNode.classList.remove('on')">×</button><div class="kicker">${esc(p.label || "Sight")}</div><h4>${esc(r.title || p.name)}</h4>${et && !inRoute ? `<button class="btn lime" id="addsight" style="margin-bottom:12px">Add to route</button>` : ""}${body}`;
    const ab = $("#addsight"); if (ab) ab.onclick = () => { box.classList.remove("on"); addToRoute(et.id, { kind: "visual", name: p.name, lat: p.lat, lon: p.lon, label: p.label, source: p.source || "osm", wiki: p.wiki }); };
  } catch (e) { box.querySelector(".hint").textContent = "Couldn't look it up right now."; }
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-sight]");
  if (!el) return;
  e.preventDefault(); e.stopPropagation();
  try { showSight(JSON.parse(el.dataset.sight)); } catch (err) {}
});
function arrivalHtml(arr, open) {
  if (!arr) return "";
  const r = arr.runway;
  const fq = (arr.freqs || []).map((f) => `${esc(f.type)} ${f.mhz}`).join(" · ") || "none listed";
  const wind = r && r.headwind != null ? ` · ${r.headwind >= 0 ? `${r.headwind} kt headwind` : `${-r.headwind} kt tailwind`}, ${r.crosswind} kt across` : "";
  return `<details class="arrival" ${open ? "open" : ""}><summary>Arrival briefing: ${esc(arr.field)}</summary>
    <div class="facts"><span class="chip">Field ${arr.elev.toLocaleString()} ft</span><span class="chip good">Circuit ${arr.tpa.toLocaleString()} ft</span>
      ${r ? `<span class="chip">RWY ${esc(r.end)} · ${r.hdg_mag != null ? String(r.hdg_mag).padStart(3, "0") + "°M · " : ""}${r.length.toLocaleString()} ft ${esc(r.surface)}${r.lighted ? " · lit" : ""}</span>` : ""}
      ${arr.sunset ? `<span class="chip">Sunset ${arr.sunset}</span>` : ""}</div>
    ${r ? `<p><b>Runway in use:</b> ${esc(r.end)}${wind}.</p>` : ""}
    ${arr.join ? `<p><b>Joining:</b> ${esc(arr.join)} You'll be inbound on about ${String(arr.inbound_mag).padStart(3, "0")}°M.</p>` : ""}
    ${arr.circuit_note ? `<p><b>Circuit:</b> ${esc(arr.circuit_note)}</p>` : ""}
    ${arr.terrain ? `<p><b>Terrain:</b> highest ground within 5 nm is ${arr.terrain.highest.toLocaleString()} ft (${arr.terrain.above_field >= 0 ? arr.terrain.above_field.toLocaleString() + " ft above the field" : "below the field"}), to the ${esc(arr.terrain.dir)}${arr.sea && arr.sea.length ? `. Water to the ${arr.sea.map(esc).join(", ")}` : ""}.</p>` : ""}
    <p><b>Radio:</b> ${fq}</p>
    ${arr.weather && arr.weather.raw ? `<div class="metar">${esc(arr.weather.raw)} <span style="color:var(--dim)">(${arr.weather.dist} nm away)</span></div>` : ""}
    ${(arr.tips || []).length ? `<ul class="why">${arr.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
  </details>`;
}
/* ---------------- route editor ---------------- */
const SHAPES = [["direct", "Direct"], ["scenic", "Scenic"], ["low", "Low and slow"], ["navaids", "Navaids"]];
const planIdeas = {}, planUndo = {}, planDelta = {}, planBusy = {};
let wpResults = { id: null, list: [] };
function planFor(id) {
  if (S.job && S.job.id === id) return S.job.plan;
  const p = planOpen[id];
  return p && p !== "loading" ? p : null;
}
function canEdit(id) {
  if (S.job && S.job.id === id) { const f = S.live && S.live.flight; return !f || f.phase === "preflight"; }
  return true;
}
function editTarget() {
  if (tab !== "fly" || !S || !S.ready) return null;
  if (S.job) return S.job.plan && canEdit(S.job.id) ? { id: S.job.id, plan: S.job.plan } : null;
  return openOffer && (S.offers || []).some((o) => o.id === openOffer) && planFor(openOffer) ? { id: openOffer, plan: planFor(openOffer) } : null;
}
const wpLabel = (w) => w.kind === "nav" ? (w.name || "").replace((w.ident || "") + " ", "") : w.kind === "visual" ? (w.label || "sight") + (w.detour >= 2 ? ` · +${Math.round(w.detour)} nm` : "") : w.kind === "user" ? (w.name || "Your point") : (w.name || "");
function shapesHtml(p, id) {
  const ideas = planIdeas[id];
  return `<div class="shapes">${SHAPES.map(([k, l]) => { const i = Array.isArray(ideas) && ideas.find((x) => x.shape === k);
    return `<button class="${p.shape === k ? "on" : ""}" data-shape="${k}" data-id="${id}"><b>${l}</b><small>${i ? `${Math.round(i.nm)} nm · ${i.min} min` : ideas === "loading" ? "…" : ""}</small></button>`; }).join("")}</div>
    ${p.shape === "custom" ? `<p class="hint">Your own route. Pick a shape above to start over from one of Onward's.</p>` : ""}`;
}
function routeHtml(p, id) {
  const edit = id && canEdit(id) && p.rules !== "IFR";
  const r = p.route;
  const d = planDelta[id];
  const sgn = (x, u) => `${x > 0 ? "+" : x < 0 ? "−" : "±"}${Math.abs(x)} ${u}`;
  return `<ol class="wplist" data-id="${id}">${r.map((w, i) => { const fixed = i === 0 || i === r.length - 1;
      return `<li class="${fixed ? "fixed" : ""}" ${!fixed && edit ? `draggable="true" data-i="${i}"` : ""}>
        <span class="${!fixed && edit ? "grip" : "pin"}" aria-hidden="true"></span>
        <span class="wn"><b>${w.kind === "visual" ? sightLink(w) : esc(w.ident)}</b><small>${esc(wpLabel(w))}</small></span>
        ${!fixed && edit ? `<button class="x" data-rm="${i}" data-id="${id}" aria-label="Remove ${esc(w.name || w.ident)}">×</button>` : ""}</li>`; }).join("")}</ol>
    ${edit ? `<div class="wpadd"><label class="label" for="wpq-${id}">Add a waypoint</label><input type="text" id="wpq-${id}" data-wpq="${id}" placeholder="Airfield, VOR, NDB or sight" autocomplete="off"><div class="wpres" id="wpres-${id}"></div></div>` : ""}
    ${d ? `<div class="deltabar"><span>${esc(d.what || "Route changed")}</span><b>${sgn(d.nm, "nm")} · ${sgn(d.min, "min")} · ${sgn(d.gal, "gal")}</b></div>` : ""}
    ${edit ? `<div class="row" style="margin:10px 0 4px">${(planUndo[id] || []).length ? `<button class="btn ghost" data-undo="${id}">Undo</button>` : ""}<button class="btn ghost" data-reset="${id}">Reset</button>${planBusy[id] ? `<span class="hint">Replanning…</span>` : `<span class="hint">Drag points on the map, click the line to add one, right-click to remove.</span>`}</div>` : ""}
    ${edit && p.detours && p.detours.length ? `<span class="label" style="margin-top:18px">Detours worth it</span><div class="detours">${p.detours.map((x, k) => `<div class="det" data-detrow="${k}" data-id="${id}" tabindex="0" title="Show on the map"><b>+${Math.max(1, Math.round(x.extra_nm))}</b><span>${sightLink(x)}<small>${esc(x.label || "")}</small></span><button class="btn ghost" data-det="${k}" data-id="${id}">Add</button></div>`).join("")}</div>` : ""}`;
}
function cheapestAt(route, pt) {
  let best = null;
  for (let i = 0; i < route.length - 1; i++) {
    const a = route[i], b = route[i + 1];
    const extra = distNm(a, pt) + distNm(pt, b) - distNm(a, b);
    if (!best || extra < best[0]) best = [extra, i];
  }
  return best ? best[1] : 0;
}
function distNm(a, b) {
  const R = 3440.065, toR = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toR, dLon = (b.lon - a.lon) * toR;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * toR) * Math.cos(b.lat * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const mids = (p) => p.route.slice(1, -1).map((w) => ({ ...w }));
async function replan(id, body, what) {
  planBusy[id] = true; keys.side = null; renderSide();
  try {
    const r = await api("/api/replan", { id, ...body });
    if (r.error) { toast(r.error); return; }
    planOpen[id] = r.plan;
    if (S.job && S.job.id === id) S.job.plan = r.plan;
    planDelta[id] = r.delta && what !== false ? { ...r.delta, what } : null;
    planIdeas[id] = planIdeas[id] || null;
  } catch (e) { toast("Couldn't replan right now."); }
  finally { planBusy[id] = false; keys.side = null; keys.edit = null; keys["map-offers"] = null; await poll(); }
}
function pushUndo(id) {
  const p = planFor(id);
  if (!p) return;
  (planUndo[id] = planUndo[id] || []).push(p.shape === "custom" ? { wps: mids(p) } : { shape: p.shape });
  if (planUndo[id].length > 20) planUndo[id].shift();
}
function editWps(id, fn, what) {
  const p = planFor(id);
  if (!p || planBusy[id]) return;
  const w = mids(p);
  fn(w);
  pushUndo(id);
  replan(id, { waypoints: w }, what);
}
function addToRoute(id, pt, what) {
  const p = planFor(id);
  if (!p) return;
  const at = pt.at != null ? pt.at - 1 : cheapestAt(p.route, pt);
  const wp = { kind: pt.kind || "visual", name: pt.name, ident: pt.ident, lat: pt.lat, lon: pt.lon, label: pt.label, source: pt.source, freq: pt.freq, wiki: pt.wiki };
  editWps(id, (w) => w.splice(Math.max(0, Math.min(w.length, at)), 0, wp), what || `Added ${pt.name || pt.ident}`);
}
function loadIdeas(id) {
  if (planIdeas[id]) return;
  planIdeas[id] = "loading";
  api(`/api/ideas?id=${id}`).then((r) => { planIdeas[id] = r.ideas || []; keys.side = null; renderSide(); }).catch(() => { planIdeas[id] = []; });
}

function wxBriefHtml(w) {
  if (!w) return "";
  if (w.verdict === "unknown" || w.unavailable) return `<div class="hint"><b>Weather along the route:</b> forecast not available right now. Check the sim's weather before you go.</div>`;
  const head = { go: "Looks good", care: "Flyable, with care", think_twice: "Think twice" }[w.verdict];
  const li = (arr, cls) => arr.map((x) => `<li class="${cls}">${esc(x)}</li>`).join("");
  const row = (r) => `<tr><td><b>${esc(r.name || "")}</b></td><td>${r.eta}</td><td>${r.base_msl != null ? r.base_msl.toLocaleString() + " ft" : r.low_cloud >= 25 ? "scattered" : "clear"}</td><td>${r.vis_km != null ? (r.vis_km >= 10 ? "10+" : r.vis_km) + " km" : "–"}</td><td>${r.gust_kt != null ? Math.round(r.gust_kt) + " kt" : "–"}</td><td>${esc(r.wx || "")}</td></tr>`;
  return `<details class="wxbrief ${w.verdict}"><summary>Weather along the route <span class="verdict">${head}</span></summary>
    <ul class="why">${li(w.bad, "bad")}${li(w.care, "care")}${li(w.good, "good")}</ul>
    ${w.points && w.points.length ? `<table class="navlog wx"><tr><th>Where</th><th>ETA</th><th>Cloud base</th><th>Vis</th><th>Gusts</th><th></th></tr>${w.points.map(row).join("")}</table>` : ""}
    ${w.taf ? `<div class="metar">${esc(w.taf.raw)} <span style="color:var(--dim)">(${w.taf.dist} nm away, you arrive around ${w.eta_dest})</span></div>` : ""}
    <p class="hint">Model forecast from Open-Meteo (cloud base estimated from temperature and dew point), TAF from aviationweather.gov. The sim's live weather may differ a little.</p>
  </details>`;
}
function perfHtml(perf, prof) {
  if (!perf || (!perf.takeoff && !perf.landing)) return "";
  const one = (d, lbl) => !d ? "" : `<div class="perf ${d.status || ""}"><small>${lbl}</small><b>${d.need_ft.toLocaleString()} ft</b>
    <span>${d.runway_ft ? `of ${d.runway_ft.toLocaleString()} ft runway · ${d.status === "ok" ? "plenty" : d.status === "tight" ? "tight" : "too short"}` : "runway length unknown"}</span>
    <span class="hint">over 50 ft · DA ${d.da_ft.toLocaleString()} ft · ${Math.round(d.temp)}°C</span></div>`;
  return `<div class="perfrow">${one(perf.takeoff, "Takeoff distance")}${one(perf.landing, "Landing distance")}</div>
    <p class="hint">Rough POH-style numbers for a heavy airplane, corrected for density altitude, wind and surface.${prof && prof.vref ? ` Approach speed ${prof.vref} kt.` : ""}</p>`;
}
function planHtml(p, id) {
  const altTxt = (v) => (typeof v === "string" ? v : v.toLocaleString());
  const rw = (r) => (r ? `RWY ${esc(r.end)} · ${r.length.toLocaleString()} ft${r.crosswind != null ? ` · X-wind ${r.crosswind} kt` : ""}` : "pick on arrival");
  const fq = (l) => (l.length ? l.map((f) => `${esc(f.type)} ${f.mhz}`).join(" · ") : "none listed");
  const wind = p.wind ? `${String(p.wind.dir).padStart(3, "0")}° / ${p.wind.kt} kt` : "n/a";
  const var_ = `${Math.abs(p.mag_var)}°${p.mag_var < 0 ? "W" : "E"}`;
  const f = p.fuel;
  const sb = p.simbrief ? `<a class="btn" href="${p.simbrief}" target="_blank" rel="noopener">Plan in SimBrief</a>` : "";
  const isJob = S.job && S.job.id === id;
  const sent = isJob && S.job.plan_sent;
  const w = p.route_weather, ld = p.performance && p.performance.landing, to = p.performance && p.performance.takeoff;
  const verdict = w && { go: "Looks good", care: "Flyable, with care", think_twice: "Think twice", unknown: "Forecast unavailable" }[w.verdict];
  const frz = w && w.points && w.points.map((x) => x.freezing_ft).filter((x) => x != null);
  const said = w ? (w.bad[0] || w.care[0] || w.good[0] || "") : "";
  const tight = [to, ld].filter((x) => x && x.status && x.status !== "ok");
  const before = `<div class="before"><span class="label">Before you go${verdict ? " · " + verdict : ""}</span>
    <div class="nums">
      <div><b>${p.cruise_alt.toLocaleString()}</b><small>Cruise ft · MSA ${p.msa.toLocaleString()}</small></div>
      ${ld ? `<div class="${ld.status || ""}"><b>${ld.need_ft.toLocaleString()}</b><small>Landing ft${ld.runway_ft ? " of " + ld.runway_ft.toLocaleString() : ""}</small></div>` : `<div><b>${fmtMin(p.ete_min)}</b><small>Flight time</small></div>`}
      ${frz && frz.length ? `<div><b>${Math.min(...frz).toLocaleString()}</b><small>Freezing level ft</small></div>` : `<div><b>${p.fuel.total}</b><small>Fuel gal</small></div>`}
    </div>
    ${said ? `<p>${esc(said)}.</p>` : ""}${tight.length ? `<p>${tight.map((x) => `${x.kind === "takeoff" ? "Takeoff" : "Landing"} distance is ${x.status === "no" ? "longer than the runway" : "tight"}.`).join(" ")}</p>` : ""}
  </div>`;
  if (p.rules !== "IFR" && canEdit(id)) loadIdeas(id);
  const off = (p.opts || {}).depart_offset_min;
  return `<span class="label" style="margin-top:20px">The plan · ${p.rules === "IFR" ? "IFR" : "VFR"}${off ? ` · departing ${off / 60} h later` : ""}</span>
    ${p.rules !== "IFR" && canEdit(id) ? shapesHtml(p, id) : ""}
    ${before}
    ${off ? `<div class="row" style="margin:-6px 0 10px"><button class="linkbtn" data-later="0" data-id="${id}">Plan for leaving now instead</button></div>` : ""}
    ${routeHtml(p, id)}
    <div class="facts"><span class="chip good">Cruise ${p.cruise_alt.toLocaleString()} ft</span><span class="chip">MSA ${p.msa.toLocaleString()} ft</span>
      <span class="chip">Wind aloft ${wind}</span><span class="chip">Var ${var_}</span><span class="chip">${p.total_nm} nm · ${fmtMin(p.ete_min)}</span>
      <span class="chip ${f.usable && f.total > f.usable ? "bad" : ""}">Fuel ${f.total} gal${f.usable ? ` of ${f.usable}` : ""}</span></div>
    <table class="navlog"><tr><th>To</th><th>Alt</th><th>MC</th><th>MH</th><th>nm</th><th>GS</th><th>min</th><th>gal</th></tr>
    ${p.legs.map((l) => `<tr class="${l.phase}"><td><b>${esc(l.to)}</b><small>${esc(l.name)}</small></td><td>${altTxt(l.alt)}</td><td>${String(l.mc).padStart(3, "0")}°</td><td>${String(l.mh).padStart(3, "0")}°</td><td>${l.dist}</td><td>${l.gs}</td><td>${Math.round(l.ete)}</td><td>${l.fuel}</td></tr>`).join("")}</table>
    <div class="hint"><b>Departure</b> ${rw(p.dep_runway)} · ${fq(p.freqs.dep)}</div>
    <div class="hint"><b>Arrival</b> ${rw(p.arr_runway)} · ${fq(p.freqs.arr)}</div>
    ${p.navaids.length ? `<div class="hint"><b>Navaids</b> ${p.navaids.map((n) => `${esc(n.ident)} ${esc(n.freq)} (${esc(n.type)}, ${n.at_nm} nm)`).join(" · ")}</div>` : ""}
    <div class="hint"><b>Fuel</b> trip ${f.trip} + taxi ${f.taxi}${f.alternate ? ` + alternate ${f.alternate}` : ""} + reserve ${f.reserve} (${f.reserve_min} min) at ${f.gph} gph</div>
    ${wxBriefHtml(p.route_weather)}
    ${perfHtml(p.performance, S.aircraft)}
    ${p.notes.length ? `<ul class="why">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <div class="row" style="margin-top:10px">${isJob ? `<button class="btn ${sent ? "ghost" : "lime"}" id="sendplan">${sent ? "Sent to the sim ✓ (send again)" : "Send to the sim"}</button>` : ""}${sb}<a class="btn ghost" href="/api/pln?id=${id}">Download .pln for MSFS</a><button class="btn ghost" data-gtn="${id}">Save for the GTNXi</button><a class="btn ghost" href="/api/navlog?id=${id}" target="_blank">Printable navlog</a><button class="btn ghost" data-replan="${id}">Refresh</button></div>
    ${arrivalHtml(p.arrival, false)}
    <p class="hint">Planned ${esc(p.built_at)}. Terrain and winds from Open-Meteo, navaids and frequencies from OurAirports. Real-world data, so double-check against the sim.</p>`;
}

function jobPanel() {
  const j = S.job;
  const f = (S.live && S.live.flight) || { phase: "preflight", msgs: [], events: [], seen: [] };
  const all = j.pois.concat(j.dest_pois || []);
  const ground = f.phase === "preflight";
  const live = `<div class="livebox">
    <div class="row" style="justify-content:space-between;margin-top:18px"><span class="phase" id="phase"></span><span class="label" id="dist" style="margin:0"></span></div>
    <div id="guide"></div>
    <div class="gauges" id="gauges"></div>
    <div id="arrlive"></div>
    <div id="nextpoi" class="hint"></div>
    <div id="fails"></div>
    <div class="msgs" id="msgs"></div>
    <div id="livephotos"></div>
    <div id="dmg"></div>
    <div class="row" style="margin-top:14px">
      <button class="btn" id="finish">Finish leg</button>
      <button class="btn ghost" id="cancel">Cancel leg</button>
    </div>
    ${S.demo ? `<div class="banner" style="margin-top:14px"><b>Demo controls</b><div class="row" style="margin-top:6px">Landing <input type="number" id="dfpm" value="220" style="width:80px"> fpm
      <label class="toggle" style="margin:0"><input type="checkbox" id="drough"> fly rough</label>
      <button class="btn ghost" id="dfly">Fly it</button><button class="btn danger" id="dcrash">Crash</button></div></div>` : ""}
  </div>`;
  return `<div class="jobhead">
    <div class="row" style="justify-content:space-between"><span class="label">${j.rules === "IFR" ? `<span class="ifr">IFR</span> ` : ""}Current leg · ${esc(j.dep.code || j.dep.ident)} → ${esc(j.dest.code || "no code")} · ${j.distance_nm} nm</span>${stars(j.difficulty)}</div>
    <h2>${esc(j.title)}</h2>
    <div class="hint">${esc(j.dest.name)} · cruise ${j.cruise_alt.toLocaleString()} ft</div>
    ${j.dest.uncharted ? `<div class="banner" style="margin-top:10px">This strip has no code the sim knows. In the MSFS world map, search near <b>${j.dest.lat.toFixed(4)}, ${j.dest.lon.toFixed(4)}</b>, or load the .pln, which ends on a user waypoint there.</div>` : ""}
  </div>
  ${live}
  <details ${ground ? "open" : ""}><summary>About this leg</summary>
    ${j.lines.map((l) => `<p class="flavor">${esc(l)}</p>`).join("")}
    <div class="facts">${runwayChips(j)} ${wxChips(j.dep_weather, "Dep")} ${wxChips(j.weather, "Dest")}</div>
    ${j.weather ? `<div class="metar">${esc(j.weather.raw)} <span style="color:var(--dim)">(${j.weather.dist} nm from field)</span></div>` : ""}
  </details>
  ${all.length ? `<details open><summary>Sights <span class="label" style="margin:0">${all.length}</span></summary><ul class="pois" id="poilist">${all.map((p) => `<li data-id="${esc(p.id || p.name)}"><span class="tick">○</span><span>${sightLink(p)} <small>${esc(p.label || "")}${p.off_nm != null ? ` · ${p.off_nm} nm off track` : p.frac === 0 ? " · on the planned route" : " · near destination"}</small></span></li>`).join("")}</ul></details>` : ""}
  <details ${ground ? "open" : ""}><summary>The plan</summary>
    ${j.plan ? planHtml(j.plan, j.id) : `<div class="row" style="margin-top:10px"><button class="btn ghost" data-plan="${j.id}">Build flight plan</button></div><div id="plan-${j.id}"></div>`}
  </details>`;
}

function updateLive() {
  const f = S.live && S.live.flight, s = (S.live && S.live.sample) || {};
  if (!$("#gauges")) return;
  const phase = { preflight: "On the ground: ready when you are", air: "Flying", rollout: "Rolling out", taxi_in: "Landed: taxi in and shut down", done: "Leg complete: press Finish leg" };
  $("#phase").textContent = f ? phase[f.phase] || f.phase : "Waiting for the sim";
  $("#dist").textContent = f && f.to_dest_nm != null ? `${f.to_dest_nm} nm to go` : "";
  const ex = (f && f.exceed) || [];
  const gauge = (lbl, v, hot) => `<div class="gauge ${hot ? "hot" : ""}"><small>${lbl}</small><b>${v}</b></div>`;
  const n = (v, d = 0) => (v == null ? "–" : Number(v).toFixed(d));
  $("#gauges").innerHTML = gauge("IAS kt", n(s.ias), ex.some((e) => ["vne", "vne_lite", "vfe", "vle"].includes(e))) + gauge("ALT ft", n(s.alt)) + gauge("V/S fpm", n(s.vs))
    + gauge("G", n(s.g, 2)) + gauge("RPM", n(s.rpm), ex.includes("rpm")) + gauge("CHT °F", n(s.cht), ex.includes("cht"));
  if (!f) return;
  const g = f.guide;
  $("#guide").innerHTML = g ? `<div class="guide">
      <div class="g-top"><span class="g-step">NEXT ${g.index}/${g.count}</span><span class="g-to">→ <b>${esc(g.ident || "")}</b> ${g.kind !== "airport" && g.name ? `<small>${esc(g.kind === "nav" ? g.name.replace((g.ident || "") + " ", "") : g.name)}</small>` : ""}</span></div>
      <div class="g-nums"><span><b>${String(g.brg_mag).padStart(3, "0")}°</b>BEARING °M</span><span><b>${g.nm}</b>NM TO GO</span><span><b>${g.ete_min != null ? Math.round(g.ete_min) : "–"}</b>MINUTES</span></div>
      ${g.xtk != null && g.xtk >= 0.3 ? `<div class="g-xtk ${g.xtk >= 1.5 ? "far" : ""}">${g.xtk} nm ${g.xtk_side} of course ${String(g.course_mag).padStart(3, "0")}°M</div>` : ""}
      ${g.then ? `<div class="hint">Then ${esc(g.then.ident || "")}${g.then.kind === "visual" && g.then.name ? " · " + esc(g.then.name) : ""}</div>` : ""}
    </div>` : "";
  const arr = S.job && S.job.plan && S.job.plan.arrival;
  if (f.near_dest && arr) { if (!$("#arrlive").dataset.on) { $("#arrlive").innerHTML = arrivalHtml(arr, true); $("#arrlive").dataset.on = "1"; } }
  else if ($("#arrlive").dataset.on) { $("#arrlive").innerHTML = ""; delete $("#arrlive").dataset.on; }
  $("#nextpoi").innerHTML = f.next_poi ? `Next sight: <b>${esc(f.next_poi.name)}</b>, ${f.next_poi.nm} nm, bearing ${String(f.next_poi.brg).padStart(3, "0")}°` : "";
  $("#fails").innerHTML = f.active_failures && f.active_failures.length ? `<div class="banner bad">Active failure: ${f.active_failures.map(esc).join(", ")}</div>` : "";
  $("#msgs").innerHTML = f.msgs.slice().reverse().map((m) => `<div class="${m.level}"><time>${m.t}</time>${esc(m.text)}</div>`).join("");
  const ph = ((S.job && S.job.photos) || []).filter((p) => !p.skip);
  if (changed("livephotos", ph.length)) $("#livephotos").innerHTML = ph.length ? `<h3>Photos this leg</h3>${photoStrip(ph, "this leg")}` : "";
  const real = f.events || [];
  $("#dmg").innerHTML = !real.length ? "" : f.accusim
    ? `<h3>Noted this leg</h3><p class="hint">A2A AccuSim decides the actual damage.</p>` + real.map((e) => `<div class="hint">${SYS[e.system]}: ${esc(e.reason)}</div>`).join("")
    : `<h3>Wear and tear this leg</h3>` + real.map((e) => `<div class="hint">${SYS[e.system]} −${e.amount}%: ${esc(e.reason)}</div>`).join("");
  document.querySelectorAll("#poilist li").forEach((li) => { const seen = f.seen.includes(li.dataset.id); li.classList.toggle("seen", seen); li.querySelector(".tick").textContent = seen ? "●" : "○"; });
}

function statusBanner() {
  const p = S.plane;
  if (p.accusim && p.status !== "in_repair" && p.status !== "ok") {
    const w = (p.accusim.worst || []).slice(0, 2).map((x) => `${esc(x.label.toLowerCase())} ${Math.round(x.pct)}%`).join(", ");
    if (p.status === "grounded") return `<div class="banner bad">The ${esc(S.aircraft.name)} is grounded (${w}). Do the maintenance in the A2A tablet before flying.</div>`;
    if (p.status === "damaged") return `<div class="banner bad">AccuSim reports damage (${w}). Until it's fixed, you'll only be offered ferry flights to a maintenance shop, where you can do the work in the A2A tablet.${S.shop_here ? " There's a shop right here." : ""}</div>`;
    return `<div class="banner">AccuSim says some parts need attention (${w}).${S.shop_here ? " There's a shop here, so it's a good place to do the work in the A2A tablet." : ""}</div>`;
  }
  if (p.status === "in_repair") return `<div class="banner bad">${p.repair.kind === "replacement" ? "Waiting for a replacement aircraft" : "The plane is in the shop"}: ready in <b>${fmtSec(p.repair.left_s)}</b>.</div>`;
  if (p.status === "grounded") return `<div class="banner bad">The ${esc(S.aircraft.name)} is grounded. Arrange a field repair in the Hangar before flying.</div>`;
  if (p.status === "damaged") return `<div class="banner bad">The plane is damaged. Until it's fixed, you'll only be offered ferry flights to a maintenance shop.${S.shop_here ? " There's a shop right here, see the Hangar." : ""}</div>`;
  if (p.status === "worn") return `<div class="banner">The plane is showing its age. Worn systems can fail in flight. A shop visit would help.${S.shop_here ? " There's one here." : ""}</div>`;
  return "";
}

function renderSide() {
  const side = $("#side");
  const key = [JSON.stringify(planBusy), Object.keys(planIdeas).map((k) => k + (Array.isArray(planIdeas[k]) ? planIdeas[k].length : planIdeas[k])), S.job && S.job.id, S.job && S.job.plan && S.job.plan.built_at, S.job && S.job.plan_sent, !!(S.live && S.live.flight && S.live.flight.phase !== "preflight"), Object.keys(planOpen).map((k) => k + (planOpen[k] === "loading" ? "L" : planOpen[k].built_at)), (S.offers || []).map((o) => o.id), openOffer, filtersOpen, S.gen, S.plane.status, S.plane.status === "in_repair" ? Math.floor(S.plane.repair.left_s / 10) : 0, S.pending_friend && S.pending_friend.name, S.aircraft.id];
  if (changed("side", key)) {
    if (!prefs) prefs = { ...S.prefs };
    let h = statusBanner();
    if (openOffer && !(S.offers || []).some((o) => o.id === openOffer)) openOffer = null;
    if (S.job) h += jobPanel();
    else {
      if (S.pending_friend) h += `<div class="banner good">${esc(S.pending_friend.story)} <div class="row" style="margin-top:6px"><button class="btn ghost" id="nofriend">Not this time</button></div></div>`;
      const offers = S.offers || [];
      if (!["in_repair", "grounded"].includes(S.plane.status)) h += (filtersOpen || !offers.length) ? prefsForm() : filterSummary();
      if (S.gen.error) h += `<div class="banner bad">${esc(S.gen.error)}</div>`;
      if (offers.length) h += `<div class="intro"><h2>${word(offers.length)} ways <em>out of ${esc((S.here && (S.here.town || S.here.name)) || "here")}.</em></h2></div>`;
      if (offers.length) h += `<div class="offers">${offers.map(offerCard).join("")}</div>`;
    }
    side.innerHTML = h;
    bindSide();
  }
  updateLive();
  const ete = $("#pete");
  if (ete && prefs) ete.textContent = `≈ ${fmtMin((+prefs.min_nm / S.aircraft.cruise_kt) * 60 + 10)} to ${fmtMin((+prefs.max_nm / S.aircraft.cruise_kt) * 60 + 10)} in the ${S.aircraft.name.split(" ").slice(-1)[0]}`;
}

function bindSide() {
  document.querySelectorAll(".seg").forEach((sg) => sg.querySelectorAll("button").forEach((b) => b.onclick = () => {
    prefs[sg.dataset.key] = b.dataset.v; sg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  }));
  const num = (id, k) => { const el = $(id); if (el) el.oninput = () => { prefs[k] = +el.value; renderSide(); }; };
  num("#pmin", "min_nm"); num("#pmax", "max_nm");
  const chk = (id, k) => { const el = $(id); if (el) el.onchange = () => (prefs[k] = el.checked); };
  chk("#pbush", "bush"); chk("#punch", "uncharted"); chk("#pnew", "prefer_new");
  document.querySelectorAll("[data-open]").forEach((el) => el.onclick = () => { openOffer = openOffer === el.dataset.open ? null : el.dataset.open; renderSide(); drawMap(); });
  const of = $("#openfilters"); if (of) of.onclick = () => { filtersOpen = true; renderSide(); };
  const rf2 = $("#refind"); if (rf2) rf2.onclick = () => { if (!S.gen.running) { openOffer = null; post("/api/generate", { prefs }); } };
  const find = $("#find"); if (find) find.onclick = () => { if (+prefs.max_nm <= +prefs.min_nm) return toast("The maximum distance has to be bigger than the minimum."); filtersOpen = false; openOffer = null; post("/api/generate", { prefs }); };
  document.querySelectorAll("[data-accept]").forEach((b) => b.onclick = () => { trailPts = []; b.disabled = true; b.textContent = "Planning the flight…"; post("/api/accept", { id: b.dataset.accept }); });
  const loadPlan = async (id, rebuild) => {
    planOpen[id] = "loading"; keys.side = null; renderSide();
    try {
      const r = await api(`/api/plan?id=${id}${rebuild ? "&rebuild=1" : ""}`);
      if (r.error) { delete planOpen[id]; toast(r.error); } else planOpen[id] = r.plan;
    } catch (e) { delete planOpen[id]; toast("Couldn't build the plan."); }
    keys.side = null; keys["map-offers"] = null; await poll();
  };
  document.querySelectorAll("[data-plan]").forEach((b) => b.onclick = () => {
    const id = b.dataset.plan;
    if (planOpen[id] && planOpen[id] !== "loading" && !(S.job && S.job.id === id)) { delete planOpen[id]; keys.side = null; renderSide(); return; }
    loadPlan(id);
  });
  document.querySelectorAll("[data-replan]").forEach((b) => b.onclick = () => loadPlan(b.dataset.replan, true));
  const fin = $("#finish"); if (fin) fin.onclick = async () => {
    const f = S.live && S.live.flight;
    if (f && f.phase === "air") { if (!confirm("You're still in the air. Ending now counts as a crash. Really end the leg?")) return; return post("/api/finish", { force: true }); }
    post("/api/finish");
  };
  const can = $("#cancel"); if (can) can.onclick = () => confirm("Cancel this leg? Nothing will be logged.") && post("/api/cancel");
  document.querySelectorAll("[data-shape]").forEach((b) => b.onclick = () => { const id = b.dataset.id; const p = planFor(id); if (p && p.shape === b.dataset.shape) return; pushUndo(id); replan(id, { shape: b.dataset.shape }, `Switched to ${b.textContent.replace(/\d.*$/, "").trim()}`); });
  document.querySelectorAll("[data-rm]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); const i = +b.dataset.rm; const p = planFor(b.dataset.id); const w = p.route[i]; editWps(b.dataset.id, (x) => x.splice(i - 1, 1), `Removed ${w.name || w.ident}`); });
  document.querySelectorAll("[data-undo]").forEach((b) => b.onclick = () => { const id = b.dataset.undo; const u = (planUndo[id] || []).pop(); if (!u) return; replan(id, u.shape ? { shape: u.shape } : { waypoints: u.wps }, "Undone"); });
  document.querySelectorAll("[data-reset]").forEach((b) => b.onclick = () => { const id = b.dataset.reset; pushUndo(id); replan(id, { reset: true }, "Back to Onward's plan"); });
  document.querySelectorAll("[data-later]").forEach((b) => b.onclick = () => replan(b.dataset.id, { depart_offset_min: +b.dataset.later }, +b.dataset.later ? `Leaving ${+b.dataset.later / 60} h later` : "Leaving now"));
  detourPinned = null;
  document.querySelectorAll("[data-detrow]").forEach((row) => {
    const id = row.dataset.id, k = +row.dataset.detrow;
    row.onmouseenter = row.onfocus = () => showDetour(id, k, false);
    row.onmouseleave = row.onblur = () => clearDetour();
    row.onclick = (e) => { if (e.target.closest("button, a")) return; const same = detourPinned && detourPinned.k === k && detourPinned.id === id; detourPinned = same ? null : { id, k };
      document.querySelectorAll("[data-detrow]").forEach((x) => x.classList.toggle("pinned", !same && x === row)); if (same) clearDetour(); else showDetour(id, k, true); };
  });
  document.querySelectorAll("[data-det]").forEach((b) => b.onclick = () => { const id = b.dataset.id; const d = planFor(id).detours[+b.dataset.det]; addToRoute(id, { ...d, kind: "visual" }, `Added ${d.name}`); });
  document.querySelectorAll("[data-wpq]").forEach((inp) => {
    const id = inp.dataset.wpq;
    let t = null;
    inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
      const q = inp.value.trim(); const box = $(`#wpres-${id}`);
      if (q.length < 2) { box.innerHTML = ""; return; }
      const p = planFor(id); const c = p.route[Math.floor(p.route.length / 2)];
      const r = await api(`/api/wpsearch?q=${encodeURIComponent(q)}&lat=${c.lat}&lon=${c.lon}`);
      wpResults = { id, list: r.results || [] };
      box.innerHTML = wpResults.list.map((x, k) => { const extra = Math.max(0, distNm(p.route[cheapestAt(p.route, x)], x) + distNm(x, p.route[cheapestAt(p.route, x) + 1]) - distNm(p.route[cheapestAt(p.route, x)], p.route[cheapestAt(p.route, x) + 1]));
        return `<button data-wpr="${k}"><span><b>${esc(x.ident || x.name)}</b> ${esc(x.ident ? x.name : x.label || "")}</span><small>+${Math.round(extra)} nm</small></button>`; }).join("") || `<p class="hint" style="padding:8px 12px">Nothing by that name nearby.</p>`;
      box.querySelectorAll("[data-wpr]").forEach((b) => b.onclick = () => { const x = wpResults.list[+b.dataset.wpr]; addToRoute(id, x, `Added ${x.ident || x.name}`); });
    }, 250); };
  });
  const wl = document.querySelector(".wplist[data-id]");
  if (wl) {
    let from = null;
    wl.querySelectorAll("li[draggable]").forEach((li) => {
      li.ondragstart = (e) => { from = +li.dataset.i; li.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; };
      li.ondragend = () => li.classList.remove("dragging");
      li.ondragover = (e) => { e.preventDefault(); li.classList.add("over"); };
      li.ondragleave = () => li.classList.remove("over");
      li.ondrop = (e) => { e.preventDefault(); li.classList.remove("over"); const to = +li.dataset.i; if (from == null || from === to) return;
        const id = wl.dataset.id; editWps(id, (w) => { const [m] = w.splice(from - 1, 1); w.splice(to - 1, 0, m); }, "Reordered the route"); };
    });
  }
  document.querySelectorAll("[data-gtn]").forEach((g) => g.onclick = async () => {
    g.disabled = true;
    const r = await post("/api/gtn", { id: g.dataset.gtn });
    g.disabled = false;
    if (r && r.ok) toast(`Saved ${r.name} for the GTNXi. On the GTN: Flight Plan, Menu, Catalog, Menu, Import.`, true);
    else if (r && r.error) toast(r.error);
  });
  const sp = $("#sendplan"); if (sp) sp.onclick = async () => { sp.disabled = true; const r = await post("/api/load_plan"); if (r && r.ok) toast("Flight plan sent to the sim.", true); };
  const nf = $("#nofriend"); if (nf) nf.onclick = () => post("/api/decline_friend");
  const df = $("#dfly"); if (df) df.onclick = () => post("/api/demo", { action: "fly", fpm: +$("#dfpm").value, rough: $("#drough").checked, duration: 45 });
  const dc = $("#dcrash"); if (dc) dc.onclick = () => post("/api/demo", { action: "crash" });
}

/* ---------------- plan a trip ---------------- */
let tmap = null, tripLayer = null, tripBusy = false, tripLeg = null, apResults = [];
let tripName = null, tripDest = null, tripVia = [], tripRound = false, tripPreview = null, tripErr = null, tripHome = null, tripHomeKey = null, ideaHover = null;
const T = { marks: null, pois: null, rings: null, past: null, hover: null, timer: null };
function tripLegsHtml(legs, idx) {
  let h = "", day = 0;
  legs.forEach((l, i) => {
    if (l.day !== day) { day = l.day; h += `<span class="label" style="margin:14px 0 4px;color:var(--moss)">Day ${day}</span>`; }
    const state = idx == null ? "" : i < idx ? "done" : i === idx ? "next" : "";
    const sights = (l.sights || []).map((p) => p.name);
    h += `<div class="tleg ${state}"><span class="n">${String(i + 1).padStart(2, "0")}</span><span class="t">${esc(l.from_name)} to ${esc(l.to_name)}${sights.length ? `<small class="tvia">over ${esc(sights.join(", "))}</small>` : ""}</span><span class="d">${state === "done" ? "flown" : state === "next" ? "next up · " : ""}${l.via ? "via · " : ""}${l.nm} nm${l.stop === "overnight" ? " · overnight" : l.stop === "fuel" ? " · fuel stop" : ""}</span></div>`;
  });
  return h;
}
function tripStats(t) {
  return `<div class="tstats"><div><b>${t.legs.length}</b><small>legs</small></div><div><b>${t.total_nm}</b><small>nm</small></div><div><b>${fmtMin(t.total_min)}</b><small>flying</small></div><div><b>${t.days}</b><small>day${t.days > 1 ? "s" : ""}</small></div></div>`;
}
const viaKey = (v) => v.ident || `${(+v.lat).toFixed(3)},${(+v.lon).toFixed(3)}`;
function tripAsk() {  // what the server needs to plan the current choice
  return { name: tripName || undefined, ident: tripDest && tripDest.ident, via: tripVia.map((v) => v.ident ? { ident: v.ident } : { name: v.name, lat: v.lat, lon: v.lon, label: v.label, source: v.source }), round_trip: tripRound, leg_nm: tripLeg };
}
async function tripReplan(keepName) {
  if (!keepName) tripName = null;
  if (!tripDest && !tripVia.length) { tripPreview = null; tripErr = null; keys.trip = null; renderTrip(); return; }
  tripBusy = true; keys.trip = null; renderTrip();
  try {
    const r = await api("/api/trip", Object.assign({ action: "preview" }, tripAsk()));
    tripPreview = r.error ? null : r.trip; tripErr = r.error || null;
    if (tripPreview && !tripLeg) tripLeg = tripPreview.max_leg;
  } finally { tripBusy = false; keys.trip = null; renderTrip(); }
}
async function loadTripHome(force) {
  const key = [S.here && S.here.ident, (S.trips_n || 0), S.aircraft && S.aircraft.name].join("|");
  if (!force && tripHomeKey === key && tripHome) return;
  tripHomeKey = key;
  try { tripHome = await api("/api/trip/home"); } catch (e) { tripHome = null; }
  keys.trip = null; renderTrip(); drawTripRings();
}
function setDest(p) { tripDest = p; tripVia = tripVia.filter((v) => v.ident !== p.ident); tripPreview = null; tmap && tmap.closePopup(); tripReplan(); }
function addVia(v) { if (!tripVia.some((x) => viaKey(x) === viaKey(v)) && !(tripDest && v.ident === tripDest.ident)) tripVia.push(v); tmap && tmap.closePopup(); tripReplan(); }
function clearTrip() { tripName = null; tripDest = null; tripVia = []; tripRound = false; tripPreview = null; tripErr = null; tripLeg = null; keys.trip = null; renderTrip(); }

function mapSoFar(st) {
  if (!st) return "";
  const nc = st.next_country;
  return `<div class="msofar"><span class="label">Your map so far</span>
    <div class="msg"><div><b>${st.airfields}</b><small>airfields</small></div><div><b>${st.countries}</b><small>${st.countries === 1 ? "country" : "countries"}</small></div><div><b>${(st.nm || 0).toLocaleString()}</b><small>nm flown</small></div><div><b>${st.msfs_seen}</b><small>MSFS POIs seen</small></div></div>
    ${nc ? `<p>Nearest country you haven't landed in: <b>${esc(nc.name)}</b>, about ${nc.nm} nm away${nc.town ? ` (${esc(nc.town)})` : ""}.</p>` : ""}</div>`;
}
function ideasHtml(h) {
  if (!h) return `<p class="hint">Finding trip ideas…</p>`;
  const ideas = h.ideas || [];
  return `<span class="label" style="margin-top:6px">Trip ideas from ${esc(S.here.town || S.here.name || "here")} · in the ${esc(S.aircraft.name)}</span>
    ${ideas.length ? `<div class="ideas">${ideas.map((i) => `<button class="idea" data-idea="${i.id}"><span class="t">${esc(i.title)}</span><span class="d">${i.trip.legs.length} leg${i.trip.legs.length > 1 ? "s" : ""} · ${i.trip.days} day${i.trip.days > 1 ? "s" : ""} · ${i.trip.total_nm} nm${i.round_trip ? " · round trip" : ""}</span><span class="s">${esc(i.why)}</span></button>`).join("")}</div>` : `<p class="hint">No ideas from here yet. Search for a place, or click one on the map.</p>`}
    ${(h.past || []).length ? `<span class="label" style="margin-top:14px">Past trips</span>${h.past.slice(0, 4).map((p, k) => `<div class="ptrip"><span class="t">${esc(p.name)}</span><small>${esc((p.started || "").slice(0, 10))} · ${p.flown}/${p.legs} legs · ${p.nm} nm${p.done ? "" : " · ended early"}</small>${p.dest ? `<button class="linkbtn" data-again="${k}">Plan it again</button>` : ""}</div>`).join("")}` : ""}`;
}
function renderTrip() {
  const tr = S.trip && !S.trip.done ? S.trip : null;
  const key = [tr && tr.idx, tr && tr.legs.length, tripDest && tripDest.ident, tripVia.map(viaKey).join(), tripRound, tripPreview && tripPreview.total_nm, tripPreview && tripPreview.max_leg, tripErr, tripBusy, S.here && S.here.ident, !!S.job, !!tripHome, tripHome && (tripHome.ideas || []).length];
  if (!changed("trip", key)) return;
  ideaHover = null; drawIdeaHover();  // the card under the mouse is about to be replaced, so its preview goes too
  const here = S.here || {};
  if (!tr) loadTripHome();
  let h = "";
  if (tr) {
    const leg = tr.legs[tr.idx];
    const atStart = leg && leg.from === here.ident;
    h += `<span class="label">Trip in progress</span><h2>${esc(tr.name)}, <em>leg ${Math.min(tr.idx + 1, tr.legs.length)} of ${tr.legs.length}.</em></h2>${tripStats(tr)}
      <div class="tlegs">${tripLegsHtml(tr.legs, tr.idx)}</div>
      ${leg && !atStart ? `<div class="banner" style="margin-top:14px">You're in ${esc(here.town || here.name || "")}, not at the start of the next leg (${esc(leg.from_name)}). Replan the trip from here, or end it.</div>` : ""}
      <div class="row" style="margin-top:16px">
        ${atStart ? `<button class="btn lime" id="tgo">${S.job ? "Back to the current leg" : "Fly the next leg"}</button>` : `<button class="btn lime" id="treplan">Replan from here</button>`}
        ${tr.idx + 1 < tr.legs.length && atStart ? `<button class="btn ghost" id="tskip">Skip the next stop</button>` : ""}
        <button class="btn ghost" id="tend">End the trip</button></div>
      <p class="hint" style="margin-top:12px">Each leg shows up first in Fly when you're at its start, with the live weather of the day.</p>`;
  } else {
    const planning = tripDest || tripVia.length;
    h += `<span class="label">Plan a trip</span><h2>Where do you <em>want to go?</em></h2>
      <div class="form"><label for="apq">${planning ? "Search for another place" : "Any airfield, town or code"}</label><input type="text" id="apq" placeholder="Monterey, KMRY, Sedona…" autocomplete="off" style="width:100%"><span class="hint">Or click a place on the map.</span></div>
      <div class="apres" id="apres"></div>`;
    if (planning) {
      h += `<div class="tplan">
        <div class="trow"><span class="label">To</span>${tripDest ? `<span class="tdest"><b>${esc(tripDest.code || tripDest.ident)}</b> ${esc(tripDest.town || tripDest.name)}</span>` : `<span class="hint">Pick a destination, or tick “come back home”.</span>`}</div>
        <div class="trow"><span class="label">Via</span><div class="chips">${tripVia.map((v, k) => `<span class="chip vchip">${esc(v.name)}<button aria-label="Remove ${esc(v.name)}" data-unvia="${k}">×</button></span>`).join("")}<span class="hint">${tripVia.length ? "" : "Click a place or a sight on the map to add a stop."}</span></div></div>
        <label class="toggle"><input type="checkbox" id="tround" ${tripRound ? "checked" : ""}> Come back home afterwards</label>
      </div>`;
      if (tripErr) h += `<div class="banner" style="margin-top:12px">${esc(tripErr)}</div>`;
      if (tripPreview) {
        const t = tripPreview, last = t.legs[t.legs.length - 1];
        h += `<span class="label" style="margin-top:18px">Trip · ${esc(here.town || here.name || "")} to ${esc(t.round_trip ? "and back" : last.to_name)}</span>
          <h2>${word(t.legs.length)} leg${t.legs.length > 1 ? "s" : ""}, <em>${t.days > 1 ? `over ${word(t.days).toLowerCase()} days.` : "in a day."}</em></h2>${tripStats(t)}
          <div class="tlegs">${tripLegsHtml(t.legs, null)}</div>
          <p class="flavor">Each leg turns into your offer when you get there, with the live weather of the day. Legs are kept to about ${t.max_leg} nm.</p>
          <div class="row"><button class="btn lime" id="tstart" ${tripBusy ? "disabled" : ""}>Start the trip</button>${t.legs.length === 1 && !tripVia.length && tripDest ? `<button class="btn" id="tnow" ${tripBusy ? "disabled" : ""}>Fly there now</button>` : ""}<button class="btn ghost" id="tshort">Shorter legs</button><button class="btn ghost" id="tlong">Longer legs</button></div>`;
      } else if (tripBusy) h += `<p class="hint" style="margin-top:14px">Planning…</p>`;
      h += `<p style="margin-top:14px"><button class="linkbtn" id="tclear">Start over</button></p>`;
    } else {
      h += mapSoFar(tripHome && tripHome.stats) + ideasHtml(tripHome);
    }
  }
  $("#tripside").innerHTML = h;
  drawTripMap(tr ? tr.legs : tripPreview ? tripPreview.legs : [], tr ? tr.idx : null);
  const apq = $("#apq");
  if (apq) {
    let tm = null;
    apq.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => {
      const q = apq.value.trim(); if (q.length < 2) { $("#apres").innerHTML = ""; return; }
      const r = await api(`/api/apsearch?q=${encodeURIComponent(q)}`); apResults = r.results || [];
      $("#apres").innerHTML = apResults.map((x, k) => `<div class="apr"><button data-ap="${k}"><span><b>${esc(x.code || x.ident)}</b> ${esc(x.name)}</span><small>${esc(x.town)}${x.town ? ", " : ""}${esc(x.country)} · ${x.dist} nm</small></button>${tripDest ? `<button class="apvia" data-apvia="${k}" aria-label="Add ${esc(x.name)} as a via stop">+ via</button>` : ""}</div>`).join("") || `<p class="hint">Nothing found.</p>`;
      document.querySelectorAll("[data-ap]").forEach((b) => b.onclick = () => { const x = apResults[+b.dataset.ap]; apq.value = ""; $("#apres").innerHTML = ""; setDest(x); });
      document.querySelectorAll("[data-apvia]").forEach((b) => b.onclick = () => { const x = apResults[+b.dataset.apvia]; apq.value = ""; $("#apres").innerHTML = ""; addVia({ ident: x.ident, name: x.town || x.name, lat: x.lat, lon: x.lon }); });
    }, 250); };
  }
  const b = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
  const go = (fn) => async () => { tripBusy = true; keys.trip = null; renderTrip(); try { await fn(); } finally { tripBusy = false; keys.trip = null; renderTrip(); } };
  document.querySelectorAll("[data-unvia]").forEach((x) => x.onclick = () => { tripVia.splice(+x.dataset.unvia, 1); tripReplan(); });
  const tround = $("#tround"); if (tround) tround.onchange = () => { tripRound = tround.checked; tripReplan(); };
  b("#tclear", clearTrip);
  b("#tnow", go(async () => { const r = await api("/api/anywhere", { ident: tripDest.ident }); if (r.error) return toast(r.error); clearTrip(); await poll(); openOffer = r.id; document.querySelector('.tabs button[data-tab="fly"]').click(); }));
  b("#tshort", () => { tripLeg = Math.max(30, Math.round((tripLeg || 100) * 0.75)); tripReplan(true); });
  b("#tlong", () => { tripLeg = Math.round((tripLeg || 100) * 1.3); tripReplan(true); });
  b("#tstart", go(async () => { const r = await api("/api/trip", Object.assign({ action: "start" }, tripAsk())); if (r.error) return toast(r.error); clearTrip(); await poll(); tripHome = null; }));
  b("#tgo", () => { const o = (S.offers || []).find((x) => x.trip_leg); if (o && !S.job) { openOffer = o.id; filtersOpen = false; } document.querySelector('.tabs button[data-tab="fly"]').click(); setTimeout(() => { const el = document.querySelector(`[data-open="${openOffer}"]`); if (el) el.scrollIntoView({ block: "start" }); }, 200); });
  b("#tskip", go(async () => { const r = await api("/api/trip", { action: "skip" }); if (r.error) toast(r.error); await poll(); }));
  b("#tend", go(async () => { if (!confirm("End this trip? The legs you've flown stay in the journal.")) return; await api("/api/trip", { action: "end" }); await poll(); tripHome = null; loadTripHome(true); }));
  b("#treplan", go(async () => { const t = S.trip; const r = await api("/api/trip", { action: "start", ident: t.dest, via: (t.via || []).filter((v) => !v.ident || v.ident !== S.here.ident), round_trip: false }); if (r.error) toast(r.error); await poll(); }));
  document.querySelectorAll("[data-idea]").forEach((el) => {
    const idea = () => ((tripHome && tripHome.ideas) || []).find((i) => i.id === el.dataset.idea);
    el.onmouseenter = el.onfocus = () => { ideaHover = idea(); drawIdeaHover(); };
    el.onmouseleave = el.onblur = () => { ideaHover = null; drawIdeaHover(); };
    el.onclick = () => { const i = idea(); if (!i) return; ideaHover = null; drawIdeaHover();
      const legByTo = Object.fromEntries(i.trip.legs.map((l) => [l.to, l]));
      const d = i.round_trip ? null : legByTo[i.dest];
      tripDest = d ? { ident: d.to, code: d.to_code, name: d.to_name, town: d.to_name, lat: d.lat, lon: d.lon } : null;
      tripVia = (i.via || []).map((v) => v.ident ? { ident: v.ident, name: (legByTo[v.ident] || {}).to_name || v.ident, lat: (legByTo[v.ident] || {}).lat, lon: (legByTo[v.ident] || {}).lon } : Object.assign({}, v));
      tripRound = !!i.round_trip; tripLeg = i.trip.max_leg; tripPreview = i.trip; tripName = i.title; tripErr = null; keys.trip = null; renderTrip(); };
  });
  document.querySelectorAll("[data-again]").forEach((el) => el.onclick = async () => {
    const p = tripHome.past[+el.dataset.again];
    const pl = await api(`/api/trip/place?ident=${encodeURIComponent(p.dest)}`);
    if (pl.place && !pl.place.here) tripDest = pl.place;
    tripVia = (p.via || []).filter((v) => !v.ident || v.ident !== S.here.ident).map((v) => Object.assign({ name: v.name || v.ident }, v));
    tripRound = !!p.round_trip; tripReplan();
  });
}

/* the trip map: rings, places to go, sights, your past flights, and the trip you're planning */
let twx = null;
function drawTripLegend() {
  const el = $("#tlegend");
  if (!el) return;
  const items = twx ? twx.legendItems() : [];
  el.innerHTML = `<i class="lg-photo"></i>Photogrammetry city <i class="lg-hand"></i>Handcrafted airport <i style="background:#d98a1f"></i>MSFS POI <i style="background:#1b2119"></i>Paved <i style="background:#a0702a"></i>Grass/dirt${items.length ? `<div style="margin-top:4px">${items.join(" · ")}</div>` : ""}`;
}
function tripMapInit() {
  tmap = L.map("tmap", { worldCopyJump: true });
  // base maps and overlays like the Fly map, with their own choices remembered
  const tBase = Object.fromEntries(Object.entries(BASEMAPS).map(([k, f]) => [k, f()]));
  let tb = baseName in BASEMAPS ? baseName : "Terrain";
  try { tb = localStorage.getItem("onward-trip-base") || tb; } catch (e) {}
  (tBase[tb] || tBase.Terrain).addTo(tmap); muteFor(tmap, tBase[tb] ? tb : "Terrain");
  tmap.on("baselayerchange", (e) => { muteFor(tmap, e.name); try { localStorage.setItem("onward-trip-base", e.name); } catch (err) {} });
  twx = makeWx(tmap, { legend: drawTripLegend, bar: "twxtime", alt: () => (S.aircraft && S.aircraft.cruise_alt) || 5000 });
  tmap.createPane("tpast"); tmap.getPane("tpast").style.zIndex = 380;
  tmap.createPane("tmarks"); tmap.getPane("tmarks").style.zIndex = 620;
  T.rings = L.layerGroup().addTo(tmap); T.past = L.layerGroup().addTo(tmap); T.pois = L.layerGroup().addTo(tmap);
  T.marks = L.layerGroup().addTo(tmap); T.hover = L.layerGroup().addTo(tmap);
  tripLayer = L.layerGroup().addTo(tmap);
  const tOver = Object.assign({}, Object.fromEntries(Object.entries(OVERLAYS).map(([k, f]) => [k, f()])));
  const aip = S.settings && S.settings.openaip_key;
  if (aip && aip !== "••••") tOver["Airspace & navaids (openAIP, worldwide)"] = L.tileLayer(`https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=${encodeURIComponent(aip)}`, { maxZoom: 14, attribution: "Airspace &copy; openAIP" });
  tOver["My past flights"] = T.past;
  Object.assign(tOver, twx.layers());
  let on = ["My past flights"];
  try { const saved = localStorage.getItem("onward-trip-overlays"); if (saved) on = JSON.parse(saved); } catch (e) {}
  if (!on.includes("My past flights")) tmap.removeLayer(T.past);
  on.forEach((k) => tOver[k] && k !== "My past flights" && tOver[k].addTo(tmap));
  L.control.layers(tBase, tOver, { position: "topright" }).addTo(tmap);
  tmap.on("overlayadd overlayremove", () => { try { localStorage.setItem("onward-trip-overlays", JSON.stringify(Object.keys(tOver).filter((k) => tmap.hasLayer(tOver[k])))); } catch (e) {} });
  setTimeout(() => { twx.refresh(true); drawTripLegend(); }, 300);
  tmap.on("moveend", () => { clearTimeout(T.timer); T.timer = setTimeout(loadTripMarks, 350); });
  tmap.on("click", async (e) => {
    if (S.trip && !S.trip.done) return;
    const r = await api(`/api/trip/place?lat=${e.latlng.lat}&lon=${e.latlng.lng}`);
    if (r.place) openPlaceCard(r.place); else toast(r.error || "No airfield near there.");
  });
  tmap.on("popupopen", (e) => {
    const el = e.popup.getElement(); const card = e.popup.options.card;
    if (!el || !card) return;
    el.querySelectorAll("[data-act]").forEach((btn) => btn.onclick = async () => {
      const act = btn.dataset.act;
      if (act === "dest") setDest(card);
      else if (act === "via") addVia(card.sight ? card : { ident: card.ident, name: card.town || card.name, lat: card.lat, lon: card.lon });
      else if (act === "near") { const r = await api(`/api/trip/place?lat=${card.lat}&lon=${card.lon}`); if (r.place) setDest(r.place); }
      else if (act === "now") { tmap.closePopup(); const r = await api("/api/anywhere", { ident: card.ident }); if (r.error) return toast(r.error); await poll(); openOffer = r.id; document.querySelector('.tabs button[data-tab="fly"]').click(); }
    });
  });
  api("/api/history").then((r) => (r.legs || []).forEach((l) => L.polyline(l.track, { pane: "tpast", color: "#3d5a2c", weight: 2, opacity: 0.35, interactive: false }).addTo(T.past))).catch(() => {});
}
function openPlaceCard(p) {
  const planning = !!tripDest || tripVia.length;
  const tags = (p.tags || []).map((t) => `<span>${esc(t)}</span>`).join("");
  const html = `<div class="pcard"><span class="label">${esc(p.code)} · ${esc(p.country)}${p.visited ? " · been here" : ""}</span><b>${esc(p.town || p.name)}</b><small>${esc(p.name)}${p.len ? ` · ${p.len.toLocaleString()} ft ${p.paved ? "paved" : "unpaved"}` : ""}</small>
    ${tags ? `<div class="ptags">${tags}</div>` : ""}
    <p>${p.here ? "You're here." : `${p.dist} nm from ${esc(S.here.town || S.here.name)}. ${p.legs === 1 ? "One leg." : `${word(p.legs)} legs, about ${p.days} day${p.days > 1 ? "s" : ""}.`}`}</p>
    ${p.here ? "" : `<div class="row"><button class="btn" data-act="dest">${planning ? "Go here instead" : "Plan a trip here"}</button>${planning ? `<button class="btn ghost" data-act="via">Add as a via stop</button>` : p.one_leg && !S.job ? `<button class="btn ghost" data-act="now">Fly there now</button>` : ""}</div>`}</div>`;
  L.popup({ card: p, maxWidth: 320, className: "tpop" }).setLatLng([p.lat, p.lon]).setContent(html).openOn(tmap);
}
function openSightCard(s) {
  const planning = !!tripDest || tripVia.length;
  const card = { sight: true, name: s.name, lat: s.lat, lon: s.lon, label: s.label, source: s.source };
  const html = `<div class="pcard"><span class="label">${esc(s.label || "Sight")}</span><b>${esc(s.name)}</b>
    <p>${planning ? "Add it as a via stop and the trip's route flies over it." : "Plan a trip to the nearest airfield, then add sights on the way."}</p>
    <div class="row">${planning ? `<button class="btn" data-act="via">Fly over it on the way</button>` : `<button class="btn" data-act="near">Plan a trip near here</button>`}</div></div>`;
  L.popup({ card, maxWidth: 320, className: "tpop" }).setLatLng([s.lat, s.lon]).setContent(html).openOn(tmap);
}
async function loadTripMarks() {
  if (!tmap || tab !== "trip") return;
  const b = tmap.getBounds(), z = tmap.getZoom();
  const q = `s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}`;
  try {
    const [m, p] = await Promise.all([api(`/api/trip/map?${q}&z=${z}`), z >= 5 ? api(`/api/pois?${q}${z < 7 ? "&msfs=1" : ""}`) : Promise.resolve({ pois: [] })]);
    T.marks.clearLayers(); T.pois.clearLayers();
    const viaSet = new Set(tripVia.map(viaKey));
    (m.airfields || []).forEach((a) => {
      L.circleMarker([a.lat, a.lon], { pane: "tmarks", radius: z >= 10 ? 5.5 : 4.5, color: "#f4f2ec", weight: 1.5, fillColor: a.paved ? "#1b2119" : "#a0702a", fillOpacity: a.visited ? 0.45 : 1, opacity: a.visited ? 0.6 : 1 })
        .bindTooltip(`<b>${esc(a.code || a.ident)}</b> ${esc(a.name)}<br><small>${a.len ? a.len.toLocaleString() + " ft " : ""}${a.paved ? "paved" : "grass/dirt"}${a.visited ? " · been here" : ""}</small>`, { direction: "top", className: "ttwx" })
        .on("click", async (e) => { L.DomEvent.stop(e); const r = await api(`/api/trip/place?ident=${a.ident}`); if (r.place) openPlaceCard(r.place); }).addTo(T.marks);
    });
    (m.highlights || []).forEach((h) => {
      const photo = h.kind === "photo";
      const icon = L.divIcon({ className: "", html: `<div class="tmk ${photo ? "photo" : "hand"}${h.visited ? " been" : ""}"></div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
      L.marker([h.lat, h.lon], { icon, pane: "tmarks", zIndexOffset: 400 })
        .bindTooltip(`<b>${esc(h.name)}</b><br><small>${photo ? "Photogrammetry city" : "Handcrafted airport"}${h.visited ? " · been here" : ""}</small>`, { direction: "top", className: "ttwx" })
        .on("click", async (e) => { L.DomEvent.stop(e); const r = await api(photo ? `/api/trip/place?lat=${h.lat}&lon=${h.lon}` : `/api/trip/place?ident=${h.ident}`); if (r.place) openPlaceCard(r.place); }).addTo(T.marks);
    });
    (p.pois || []).forEach((s) => {
      if (s.label === "MSFS photogrammetry city") return;
      const msfs = s.source === "msfs";
      L.circleMarker([s.lat, s.lon], { pane: "tmarks", radius: viaSet.has(viaKey(s)) ? 7 : (msfs ? 5 : 4), color: "#f4f2ec", weight: 1.5, fillColor: msfs ? "#d98a1f" : "#4c6b78", fillOpacity: 1 })
        .bindTooltip(`<b>${esc(s.name)}</b><br><small>${esc(s.label || "")}</small>`, { direction: "top", className: "ttwx" })
        .on("click", (e) => { L.DomEvent.stop(e); openSightCard(s); }).addTo(T.pois);
    });
  } catch (e) {}
}
function drawTripRings() {
  if (!tmap || !S.here) return;
  T.rings.clearLayers();
  const r = tripHome && tripHome.ranges;
  if (!r || (S.trip && !S.trip.done)) return;
  const here = [S.here.lat, S.here.lon];
  [[r.leg_nm, `One leg · ${r.leg_nm} nm`, "8 6", 0.9], [r.day_nm, `One day with a fuel stop · ${r.day_nm} nm`, "3 6", 0.55]].forEach(([nm, label, dash, op]) => {
    L.circle(here, { radius: nm * 1852, color: "#1b2119", weight: 1.4, dashArray: dash, opacity: op, fill: false, interactive: false }).addTo(T.rings);
    const lat = S.here.lat - nm / 60;
    L.marker([lat, S.here.lon], { interactive: false, icon: L.divIcon({ className: "", html: `<span class="ringlbl">${esc(label)}</span>`, iconSize: [0, 0] }) }).addTo(T.rings);
  });
}
function drawIdeaHover() {
  if (!tmap) return;
  T.hover.clearLayers();
  if (!ideaHover) return;
  const legs = ideaHover.trip.legs;
  const pts = [[legs[0].from_lat, legs[0].from_lon]];
  legs.forEach((l) => { (l.sights || []).forEach((p) => pts.push([p.lat, p.lon])); pts.push([l.lat, l.lon]); });
  L.polyline(pts, { color: "#f4f2ec", weight: 8, opacity: 0.9, interactive: false }).addTo(T.hover);
  L.polyline(pts, { color: "#3d5a2c", weight: 3, dashArray: "8 5", interactive: false }).addTo(T.hover);
  legs.forEach((l) => {
    (l.sights || []).forEach((p) => L.circleMarker([p.lat, p.lon], { radius: 6, color: "#f4f2ec", weight: 2, fillColor: "#d98a1f", fillOpacity: 1, interactive: false }).bindTooltip(esc(p.name), { permanent: true, direction: "right", offset: [8, 0], className: "ttrip" }).addTo(T.hover));
    L.circleMarker([l.lat, l.lon], { radius: l.stop === "end" ? 8 : 6, color: "#1b2119", weight: 2.5, fillColor: l.stop === "end" ? "#1b2119" : "#f4f2ec", fillOpacity: 1, interactive: false })
      .bindTooltip(`${esc(l.to_name)}${l.stop === "overnight" ? " · night" : l.stop === "end" ? " · end" : ""}`, { permanent: true, direction: "right", offset: [8, 0], className: "ttrip" }).addTo(T.hover);
  });
  const bb = L.latLngBounds(pts);
  if (!tmap.getBounds().contains(bb)) tmap.fitBounds(bb.extend(L.latLng(S.here.lat, S.here.lon)), { padding: [60, 60], maxZoom: 8 });
}
let tripFitKey = null;
function drawTripMap(legs, idx) {
  if (!tmap) tripMapInit();
  setTimeout(() => tmap.invalidateSize(), 30);
  tripLayer.clearLayers();
  const here = S.here;
  const pts = [];
  legs.forEach((l, i) => {
    const line = [[l.from_lat, l.from_lon], ...(l.sights || []).map((p) => [p.lat, p.lon]), [l.lat, l.lon]];
    const done = idx != null && i < idx;
    L.polyline(line, { color: "#f4f2ec", weight: 8, opacity: 0.9, interactive: false }).addTo(tripLayer);
    L.polyline(line, { color: done ? "#3d5a2c" : "#1b2119", weight: done ? 2.5 : 3, dashArray: idx != null && i > idx ? "7 6" : null, interactive: false }).addTo(tripLayer);
    (l.sights || []).forEach((p) => L.circleMarker([p.lat, p.lon], { radius: 7, color: "#1b2119", weight: 2, fillColor: "#d98a1f", fillOpacity: 1 }).bindTooltip(`Via ${esc(p.name)}`, { permanent: legs.length <= 8, direction: "left", offset: [-8, 0], className: "ttrip" }).addTo(tripLayer));
    L.circleMarker([l.lat, l.lon], { radius: l.stop === "end" ? 8 : 6, color: l.via ? "#1b2119" : "#f4f2ec", weight: l.via ? 3 : 2, fillColor: l.stop === "overnight" || l.stop === "end" ? "#1b2119" : "#5d6458", fillOpacity: 1 })
      .bindTooltip(`<b>${esc(l.to_code)}</b> ${esc(l.to_name)}<br><small>${l.via ? "Via stop · " : ""}${l.stop === "overnight" ? "Overnight" : l.stop === "fuel" ? "Fuel stop" : "The end"} · day ${l.day}</small>`, { permanent: legs.length <= 8, direction: "right", offset: [8, 0], className: "ttrip" }).addTo(tripLayer);
    pts.push(...line);
  });
  if (here) { L.marker([here.lat, here.lon], { icon: dot("here"), zIndexOffset: 800 }).bindTooltip("You are here").addTo(tripLayer); pts.push([here.lat, here.lon]); }
  if (tripDest && !legs.length) { L.circleMarker([tripDest.lat, tripDest.lon], { radius: 8, color: "#1b2119", weight: 2, fillColor: "#cfe98a", fillOpacity: 1 }).addTo(tripLayer); pts.push([tripDest.lat, tripDest.lon]); }
  drawTripRings();
  // move the view only when what's being shown changes, not on every refresh
  const fk = JSON.stringify([legs.map((l) => l.to), idx != null, here && here.ident, !!(tripHome && tripHome.ranges)]);
  if (fk !== tripFitKey) {
    tripFitKey = fk;
    if (pts.length > 1) tmap.fitBounds(pts, { padding: [60, 60], maxZoom: 9 });
    else if (here && tripHome && tripHome.ranges) tmap.fitBounds(L.latLng(here.lat, here.lon).toBounds(tripHome.ranges.leg_nm * 1852 * 2.6), { padding: [20, 20] });
    else if (here) tmap.setView([here.lat, here.lon], 7);
  }
  setTimeout(loadTripMarks, 60);
}

/* ---------------- debrief ---------------- */
function scoreHtml(c, gate, dark) {
  if (!c) return "";
  const fact = (l, v) => `<div><small>${l}</small><b>${v}</b></div>`;
  const ft = (v) => (v == null ? "–" : v.toLocaleString() + " ft");
  return `<div class="score ${dark ? "dark" : ""}"><div class="grade g${c.grade}">${c.grade}<small>${c.score}</small></div>
    <div class="sfacts">${fact("Sink", c.fpm + " fpm")}${fact("Speed", `${c.ias} kt <em>(${c.ias - c.vref >= 0 ? "+" : ""}${c.ias - c.vref})</em>`)}
      ${c.runway ? fact("Past threshold", ft(c.past_threshold_ft)) + fact("Centerline", c.centerline_ft == null ? "off the runway" : `${c.centerline_ft} ft ${c.side}`) : ""}
      ${fact("Bank", c.bank + "°")}${gate ? fact("At 500 ft", gate.stable ? "stable" : "not stable") : ""}</div>
    <div class="snotes">${c.runway ? `Runway ${esc(c.runway)} at ${esc(c.airport)}. ` : ""}${c.best ? "<b>New personal best here.</b> " : c.prev_best != null ? `Best here: ${c.prev_best}. ` : ""}${(c.notes || []).map(esc).join(". ")}${gate && !gate.stable && gate.issues.length ? ` At 500 ft: ${gate.issues.map(esc).join(", ")}.` : ""}</div></div>`;
}
function condBars(cond, light) {
  return Object.entries(SYS).map(([k, l]) => { const v = cond[k]; const cls = v < 60 ? "low" : v < 80 ? "mid" : ""; return `<div class="cond"><div class="lbl"><span>${l}</span><span>${Math.round(v)}%</span></div><div class="bar"><i class="${cls}" style="width:${v}%"></i></div></div>`; }).join("");
}
function renderModal() {
  const r = S.last_result;
  const m = $("#modal");
  if (!r) { m.classList.remove("on"); return; }
  if (!changed("modal", r.at || r.text)) return;
  const head = { arrived: "Arrived", diverted: "Diverted", offfield: "Off-field landing", crash: "Crashed" }[r.outcome];
  $("#sheet").innerHTML = `<div class="kicker">${head} · ${esc(r.from)} → ${esc(r.to)}</div>
    <h2>${r.outcome === "crash" ? "That didn't go well." : `${esc(r.to_name)}`}</h2>
    <p style="font-size:16px;line-height:1.6;color:var(--ink2)">${esc(r.text)}</p>
    <div class="facts"><span class="chip">${fmtMin(r.minutes)} flown</span><span class="chip">${r.distance_nm} nm</span>${r.landing_fpm != null ? `<span class="chip">Landing ${r.landing_fpm} fpm</span>` : ""}<span class="chip">${r.sights.length} sight${r.sights.length === 1 ? "" : "s"}</span></div>
    ${r.photos && r.photos.length ? `<h3>Photos</h3>${photoStrip(r.photos, `${r.from} → ${r.to}`)}` : ""}
    ${r.landing ? `<h3>Landing</h3>${scoreHtml(r.landing, r.gate)}` : ""}
    ${r.damage.length ? `<h3>What the plane felt</h3>${r.damage.map((e) => r.accusim ? `<div>${SYS[e.system]}: ${esc(e.reason)}</div>` : `<div>${SYS[e.system]} −${e.amount}%: ${esc(e.reason)}</div>`).join("")}` : ""}
    <h3>Aircraft condition${r.accusim ? " <span class=\"hint\">from A2A AccuSim</span>" : ""}</h3>${condBars(r.condition)}
    ${r.accusim && (r.accusim_failed || []).length ? `<p><b>Failed:</b> ${r.accusim_failed.map(esc).join(", ")}</p>` : ""}
    ${r.accusim && (r.accusim_worst || []).length ? `<p class="hint">Needs attention: ${r.accusim_worst.map((x) => `${esc(x.label)} ${Math.round(x.pct)}%`).join(", ")}</p>` : ""}
    ${r.met ? `<div class="banner good" style="color:var(--ink);margin-top:14px">${esc(r.met.story)}</div>` : ""}
    ${r.status === "in_repair" && r.outcome === "crash" ? `<p><b>The airframe is written off.</b> A replacement is on its way to ${esc(r.to_name)}. Check the Hangar for the timing.</p>` : ""}
    <div class="row" style="margin-top:22px"><button class="btn lime" id="mclose">Onward</button>${r.shop_here && r.status !== "ok" && r.outcome !== "crash" && !r.accusim ? `<button class="btn ghost" id="mshop">Book the shop here</button>` : ""}</div>`;
  m.classList.add("on");
  $("#mclose").onclick = () => { trailPts = []; if (trail) { trail.remove(); trail = null; } post("/api/dismiss"); };
  const sh = $("#mshop"); if (sh) sh.onclick = async () => { await post("/api/repair", { kind: "shop" }); post("/api/dismiss"); };
}

/* ---------------- hangar ---------------- */
let accPartsOpen = false;
function accusimCard(p) {
  const a = p.accusim;
  const part = (x) => { const cls = x.pct < 60 ? "low" : x.pct < 80 ? "mid" : ""; return `<div class="cond"><div class="lbl"><span>${esc(x.label)}</span><span>${Math.round(x.pct)}%</span></div><div class="bar"><i class="${cls}" style="width:${x.pct}%"></i></div></div>`; };
  const groups = Object.assign({}, SYS, { plugs: "Spark plugs" });
  const bySys = Object.keys(groups).map((k) => { const ps = a.parts.filter((x) => x.system === k).sort((x, y) => x.pct - y.pct); return ps.length ? `<h4>${groups[k]}</h4>${ps.map(part).join("")}` : ""; }).join("");
  const pl = a.plugs;
  const plugs = pl && pl.fouled ? `<p><b>Spark plugs:</b> ${pl.fouled} of ${pl.count} fouled (lowest ${Math.round(pl.lowest)}%). <span class="hint">Rich idling on the ground fouls them. A run-up with the mixture leaned usually clears them, or clean them in the A2A tablet. They don't count toward the engine's condition.</span></p>` : "";
  const facts = [a.total_time != null ? `${a.total_time.toFixed(1)} h airframe` : "", a.engine_time != null ? `${a.engine_time.toFixed(1)} h engine` : "", a.oil_pct != null ? `oil ${Math.round(a.oil_pct)}%` : ""].filter(Boolean);
  return `<p><b>Condition from A2A AccuSim.</b> <span class="hint">${facts.join(" · ")}${a.age_min >= 2 ? ` · last read ${a.age_min} min ago` : ""}</span></p>
    <p class="hint">Onward reads the airplane's real condition and doesn't add wear or failures of its own. Do the maintenance in the A2A tablet (Maintenance page): Onward picks up the result automatically.</p>
    ${a.failed.length ? `<p><b>Failed:</b> ${a.failed.map(esc).join(", ")}</p>` : ""}${plugs}
    ${a.worst.length ? `<h4>Needs attention</h4>${a.worst.map(part).join("")}` : `<p class="hint">Nothing below 90%.</p>`}
    <details id="accparts" ${accPartsOpen ? "open" : ""}><summary class="hint">All ${a.parts.length} parts</summary>${bySys}</details>`;
}

function renderHangar() {
  if (!changed("hangar", [S.aircraft.id, S.plane, S.shop_here, S.aircraft_detected, S.job && S.job.id])) return;
  const p = S.plane, a = S.aircraft;
  const rep = p.repair;
  $("#hangar").innerHTML = `<span class="label">Hangar · ${esc(a.name)}</span><h1 class="display">One airplane, <em>a long memory.</em></h1><p class="lede">Every one remembers how it was treated.</p>
  <div class="grid2">
    <div class="card"><h3 style="margin-top:0">Aircraft</h3>
      <select id="acsel" ${S.job ? "disabled" : ""}>${S.profiles.map((x) => `<option value="${x.id}" ${x.id === a.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <p class="hint">${S.sim_title ? `In the sim: ${esc(S.sim_title)}${S.aircraft_detected ? " (matched automatically)" : " (no match, pick one above)"}` : "Connect the sim and the aircraft is picked automatically."}</p>
      <p><span class="status ${p.status}">${STATUS[p.status]}</span> <span class="hint">${p.hours.toFixed(1)} h ${p.accusim ? "flown with Onward" : "on this airframe"}${p.write_offs ? ` · ${p.write_offs} written off` : ""}</span></p>
      ${condBars(p.cond)}
    </div>
    <div class="card"><h3 style="margin-top:0">Maintenance</h3>
      ${p.accusim && !rep ? accusimCard(p) : rep ? `<p>${rep.kind === "replacement" ? "Replacement aircraft arriving" : rep.kind === "field" ? "Field repair under way" : "In the shop"}: ready in <b>${fmtSec(rep.left_s)}</b>.</p>` : `
      <p class="hint">Repairs take real time. A proper shop (larger airfields) fixes everything. A field repair, possible anywhere, patches the plane up to 70% so you can reach a shop.</p>
      <div class="row" style="margin-top:10px">
        <button class="btn" id="rshop" ${S.shop_here && !S.job ? "" : "disabled"}>Shop repair (${fmtMin(p.quotes.shop)})</button>
        <button class="btn ghost" id="rfield" ${p.quotes.field > 0 && !S.job ? "" : "disabled"}>Field repair (${fmtMin(p.quotes.field)})</button>
      </div>
      <p class="hint">${S.shop_here ? "There's a maintenance shop at this airfield." : "No shop at this airfield."}</p>`}
      <h3>${p.accusim ? "Recent flying" : "Recent wear"}</h3>${(p.damage_log || []).slice().reverse().map((e) => `<div class="hint">${SYS[e.system]}${e.noted ? "" : ` −${e.amount}%`}: ${esc(e.reason)}</div>`).join("") || `<p class="hint">Nothing yet. Keep it that way.</p>`}
    </div>
    <div class="card"><h3 style="margin-top:0">Limits used for this aircraft</h3>
      <table class="spec">
        <tr><td>Cruise</td><td>${a.cruise_kt} kt</td></tr><tr><td>Comfortable runway</td><td>${a.min_runway_ft} ft</td></tr>
        <tr><td>Range used for planning</td><td>${a.range_nm} nm</td></tr><tr><td>Grass and dirt</td><td>${a.unpaved ? "yes" : "no"}</td></tr>
        <tr><td>Vne</td><td>${a.vne ?? "–"} kt</td></tr><tr><td>Vfe (first / full)</td><td>${a.vfe_first ?? "–"} / ${a.vfe_full ?? "–"} kt</td></tr>
        <tr><td>Gear extended (Vle)</td><td>${a.vle ?? "fixed"}</td></tr><tr><td>RPM redline</td><td>${a.redline_rpm}</td></tr>
        <tr><td>IFR equipped</td><td>${a.ifr ? "yes" : "no"}</td></tr><tr><td>Fuel burn / usable</td><td>${a.gph} gph / ${a.fuel_gal} gal</td></tr>
        <tr><td>CHT limit</td><td>${a.cht_max_f ? a.cht_max_f + " °F" : "–"}</td></tr><tr><td>G limit</td><td>${a.g_limit}</td></tr>
      </table><p class="hint">Edit aircraft.json in the Onward folder to tune these or add new aircraft.</p>
    </div>
  </div>`;
  $("#acsel").onchange = (e) => post("/api/aircraft", { id: e.target.value });
  const rs = $("#rshop"); if (rs) rs.onclick = () => post("/api/repair", { kind: "shop" });
  const rf = $("#rfield"); if (rf) rf.onclick = () => post("/api/repair", { kind: "field" });
  const ap = $("#accparts"); if (ap) ap.ontoggle = () => { accPartsOpen = ap.open; };
}

/* ---------------- settings ---------------- */
function renderSettings() {
  if (!changed("settings", [S.settings, S.update && [S.update.latest, S.update.checked, S.update.error, (S.update.backups || []).length]])) return;
  const s = S.settings;
  $("#settings").innerHTML = `<span class="label">Settings</span><h1 class="display">How Onward <em>works for you.</em></h1><div class="grid2"><div class="card form">
    <label>Pilot name</label><input type="text" id="sname" value="${esc(s.pilot_name)}">
    <label class="toggle"><input type="checkbox" id="sfail" ${s.failures ? "checked" : ""}> Worn parts can fail in the sim</label>
    <label class="toggle"><input type="checkbox" id="seng" ${s.engine_failures ? "checked" : ""}> Badly neglected engines can quit completely</label>
    <label class="toggle"><input type="checkbox" id="sifr" ${s.ifr_auto ? "checked" : ""}> Offer IFR legs when the weather is bad (IFR aircraft only)</label>
    <label class="toggle"><input type="checkbox" id="sauto" ${s.auto_load_plan ? "checked" : ""}> Send the flight plan to the sim automatically when I take a leg</label>
    <label class="toggle"><input type="checkbox" id="sgtn" ${s.gtn_auto ? "checked" : ""}> Also save it for the GTN (TDS GTNXi) whenever it goes to the sim</label>
    <label for="sgtnf">GTN flight plan folder</label><input type="text" id="sgtnf" value="${esc(s.gtn_folder || "")}" placeholder="C:\ProgramData\TDS\GTNXi\FPL" style="width:100%">
    <p class="hint">Onward saves a Garmin .gfp file there. Airports go in by their code, every other waypoint by its coordinates, so nothing imports as a locked waypoint. On the GTN: Flight Plan, Menu, Catalog, Menu, Import.</p>
    <label class="toggle"><input type="checkbox" id="svoice" ${s.voice ? "checked" : ""}> Spoken callouts (sights, waypoints, stable approach, warnings)</label>
    <label class="toggle"><input type="checkbox" id="sphotos" ${s.photos !== false ? "checked" : ""}> Save screenshots I take during a leg to the journal</label>
    <label>Extra screenshot folders (one per line, optional)</label><textarea id="sphotodirs" rows="2" placeholder="E:\\My captures">${esc(s.photo_folders || "")}</textarea>
    <p class="hint" id="sphotofound">Looking for screenshot folders…</p>
    <label>Shop repair time (minutes per % of damage)</label><input type="number" step="0.1" id="sshop" value="${s.shop_min_per_pt}">
    <label>Field repair time (minutes per % of damage)</label><input type="number" step="0.1" id="sfield" value="${s.field_min_per_pt}">
    <label>Wait for a replacement after a crash (hours)</label><input type="number" step="0.5" id="scrash" value="${s.crash_hours}">
    <p class="hint">Set the times to 0 if you'd rather not wait in real time.</p>
    <label>openAIP API key (optional, free): adds a worldwide airspace and navaid overlay to the map</label><input type="text" id="saip" value="${esc(s.openaip_key || "")}" placeholder="paste your openAIP client ID token">
    <p class="hint">Create a free account at openaip.net, then add an API client under your account settings and paste its token here.</p>
    <div style="margin-top:14px"><button class="btn" id="ssave">Save</button></div>
  </div>
  ${updatesCard()}
  <div class="card" id="aboutcard"><h3 style="margin-top:0">About and credits</h3><p class="hint">Loading…</p></div>
  <div class="card"><h3 style="margin-top:0">MSFS points of interest</h3>
    <p class="hint">Drop Little Navmap userpoint CSV files (for example the community "MSFS POIs" and "handcrafted airports" packs from Flightsim.to) into the <code>data/msfs_pois</code> folder, then press reload. MSFS POIs show as amber dots and are favored when picking routes. Handcrafted airports get a bonus too.</p>
    <button class="btn ghost" id="sreload">Reload POI files</button>
    <h3>Data</h3><p class="hint">Airfields: OurAirports (public domain). Weather: aviationweather.gov. Landmarks: © OpenStreetMap contributors via Overpass. Real-world data can differ from what's in the sim, especially for small strips and runway lighting.</p>
  </div></div>`;
  $("#ssave").onclick = () => post("/api/settings", { settings: { pilot_name: $("#sname").value, failures: $("#sfail").checked, engine_failures: $("#seng").checked, ifr_auto: $("#sifr").checked, update_check: $("#supdchk").checked, update_repo: $("#srepo").value.trim(), update_token: $("#stoken").value.trim(), auto_load_plan: $("#sauto").checked, gtn_auto: $("#sgtn").checked, gtn_folder: $("#sgtnf").value.trim(), voice: $("#svoice").checked, photos: $("#sphotos").checked, photo_folders: $("#sphotodirs").value, openaip_key: $("#saip").value.trim(), shop_min_per_pt: +$("#sshop").value, field_min_per_pt: +$("#sfield").value, crash_hours: +$("#scrash").value } }).then(() => toast("Saved.", true));
  api("/api/photo_folders").then((r) => { const el = $("#sphotofound"); if (el) el.innerHTML = r.folders.length ? "Watching: " + r.folders.map((f) => `<code>${esc(f)}</code>`).join(", ") : "No screenshot folders found yet. Add yours above."; }).catch(() => {});
  const su = $("#supd"); if (su) su.onclick = () => runUpdate();
  const sr = $("#sroll"); if (sr) sr.onclick = () => runUpdate(true);
  $("#scheck").onclick = async () => { $("#scheck").textContent = "Checking…"; await api("/api/update/check", {}); keys.settings = null; await poll(); };
  $("#sreload").onclick = () => post("/api/reload_pois").then(() => toast("POI files reloaded.", true));
  loadAbout();
}

/* ---------------- photos ---------------- */
let lbList = [], lbIdx = 0;
const photoCap = (p, leg) => `${esc(p.where || "")}${p.alt ? ` · ${Number(p.alt).toLocaleString()} ft` : ""}${leg ? ` · ${esc(leg)}` : ""}${p.t ? ` · ${esc(p.t.slice(0, 16).replace("T", " "))} UTC` : ""}`;
function openPhoto(list, i) {
  lbList = list; lbIdx = i;
  const p = list[i];
  $("#lbimg").src = "/photos/" + p.file;
  $("#lbcap").innerHTML = photoCap(p, p.leg) + (list.length > 1 ? ` <span class="hint">(${i + 1} of ${list.length})</span>` : "");
  $("#lightbox").classList.add("on");
  $("#lbprev").style.visibility = i > 0 ? "visible" : "hidden";
  $("#lbnext").style.visibility = i < list.length - 1 ? "visible" : "hidden";
}
const closePhoto = () => { $("#lightbox").classList.remove("on"); $("#lbimg").src = ""; };
$("#lbx").onclick = closePhoto;
$("#lightbox").onclick = (e) => { if (e.target.id === "lightbox") closePhoto(); };
$("#lbprev").onclick = () => lbIdx > 0 && openPhoto(lbList, lbIdx - 1);
$("#lbnext").onclick = () => lbIdx < lbList.length - 1 && openPhoto(lbList, lbIdx + 1);
document.addEventListener("keydown", (e) => {
  if (!$("#lightbox").classList.contains("on")) return;
  if (e.key === "Escape") closePhoto();
  if (e.key === "ArrowLeft") $("#lbprev").click();
  if (e.key === "ArrowRight") $("#lbnext").click();
});
let stripSeq = 0; const strips = {};
function photoStrip(list, leg) {
  if (!list || !list.length) return "";
  const id = "s" + (++stripSeq);
  strips[id] = list.map((p) => ({ ...p, leg }));
  return `<div class="strip">${list.map((p, i) => `<img loading="lazy" src="/photos/${esc(p.thumb || p.file)}" data-strip="${id}" data-i="${i}" title="${esc(p.where || "")}" alt="">`).join("")}</div>`;
}
document.addEventListener("click", (e) => { const t = e.target.closest("[data-strip]"); if (t) openPhoto(strips[t.dataset.strip], +t.dataset.i); });

/* ---------------- world ---------------- */
let wmap = null;
const camIcon = (p) => L.divIcon({ className: "", html: `<div class="cam" style="background-image:url('/photos/${esc(p.thumb || p.file)}')"></div>`, iconSize: [40, 40], iconAnchor: [20, 20] });
function drawJourney(m, legs, opts = {}) {
  const fields = {}, all = [], pts = [];
  legs.forEach((l) => {
    L.polyline(l.track, { color: "#f4f2ec", weight: opts.small ? 5 : 7, opacity: 0.85, interactive: false }).addTo(m);
    L.polyline(l.track, { color: "#3d5a2c", weight: opts.small ? 2 : 2.8, opacity: 0.95 })
      .bindTooltip(`<b>${esc(l.label)}</b> · ${esc(l.date)}${l.nm ? ` · ${l.nm} nm` : ""}${l.grade ? ` · landing ${l.grade}` : ""}${l.photos.length ? ` · ${l.photos.length} photo${l.photos.length > 1 ? "s" : ""}` : ""}`, { sticky: true }).addTo(m);
    const a = l.track[0], b = l.track[l.track.length - 1];
    fields[a.join()] = { p: a }; fields[b.join()] = { p: b, name: l.to_name };
    l.track.forEach((x) => pts.push(x));
    l.photos.forEach((p) => all.push({ ...p, leg: l.label }));
  });
  Object.values(fields).forEach((f) => L.circleMarker(f.p, { radius: opts.small ? 3 : 4.5, color: "#f4f2ec", weight: 1.5, fillColor: "#1b2119", fillOpacity: 1 }).bindTooltip(esc(f.name || "")).addTo(m));
  all.forEach((p, i) => L.marker([p.lat, p.lon], { icon: camIcon(p), zIndexOffset: 500 }).bindTooltip(photoCap(p, p.leg)).on("click", () => openPhoto(all, i)).addTo(m));
  all.forEach((p) => pts.push([p.lat, p.lon]));
  if (pts.length > 1) m.fitBounds(pts, { padding: [40, 40], maxZoom: 10 });
  else if (S && S.here) m.setView([S.here.lat, S.here.lon], 8);
  return all;
}
async function renderWorld() {
  const [h, j] = await Promise.all([api("/api/history"), api("/api/journal")]);
  if (wmap) { wmap.remove(); wmap = null; }
  wmap = L.map("wmap", { worldCopyJump: true, zoomControl: false });
  L.control.zoom({ position: "bottomright" }).addTo(wmap);
  const bases = { "Terrain": BASEMAPS.Terrain(), "Satellite": BASEMAPS.Satellite(), "Streets": BASEMAPS.Streets() };
  const wb = baseName in bases ? baseName : "Terrain";
  bases[wb].addTo(wmap); muteFor(wmap, wb);
  wmap.on("baselayerchange", (e) => muteFor(wmap, e.name));
  L.control.layers(bases, {}, { position: "topright" }).addTo(wmap);
  const all = drawJourney(wmap, h.legs);
  if (S && S.here) L.marker([S.here.lat, S.here.lon], { icon: dot("here") }).bindTooltip(`You are here: ${esc(S.here.name)}`).addTo(wmap);
  const st = j.stats;
  const recent = all.slice(-8).reverse();
  const first = h.legs.length ? h.legs[0].label.split(" → ")[0] : null;
  $("#wpanel").innerHTML = `<span class="label">The map so far</span>
    <h2>${st.legs ? `${word(st.legs)} leg${st.legs > 1 ? "s" : ""} <em>${first ? "from " + esc(first) : "so far"}.</em>` : "Nothing <em>yet.</em>"}</h2>
    <div class="wstats"><div><b>${st.nm.toLocaleString()}</b><small>nm flown</small></div><div><b>${st.airfields}</b><small>airfields</small></div><div><b>${st.countries}</b><small>countries</small></div><div><b>${all.length}</b><small>photos</small></div></div>
    ${recent.length ? `<h3>Latest photos</h3>${photoStrip(recent)}` : `<p class="hint">Screenshots you take during a leg show up here and on the map, right where you took them.</p>`}`;
  setTimeout(() => wmap.invalidateSize(), 50);
}

/* ---------------- journal ---------------- */
let jmap = null;
async function renderJournal() {
  const j = await api("/api/journal");
  const st = j.stats;
  const stat = (v, l) => `<div class="stat"><b>${v}</b><small>${l}</small></div>`;
  const legs = j.journal.filter((e) => e.type !== "note");
  const all = j.journal;
  const lead = legs[0];
  const home = legs.length ? (legs[legs.length - 1].from_name || legs[legs.length - 1].from).replace(/\s*\b(International|Municipal|Regional)?\s*(Airport|Aerodrome|Airfield|Air Base|Airstrip)\b/gi, "").trim() || legs[legs.length - 1].from : (S && S.here ? (S.here.town || S.here.name) : "home");
  const chips = (e) => `<div class="facts"><span class="chip">${fmtMin(e.minutes)}</span><span class="chip">${e.distance_nm} nm</span>${e.landing ? `<span class="chip grade-chip g${e.landing.grade}" title="${esc((e.landing.notes || []).join(". "))}">Landing ${e.landing.grade} · ${e.landing.score}</span>` : e.landing_fpm != null ? `<span class="chip">${e.landing_fpm} fpm</span>` : ""}${e.night ? `<span class="chip">night</span>` : ""}${e.rules === "IFR" ? `<span class="chip">IFR</span>` : ""}${e.go_arounds ? `<span class="chip">${e.go_arounds} go-around${e.go_arounds > 1 ? "s" : ""}</span>` : ""}</div>`;
  const when = (e) => esc(e.at.slice(0, 10));
  const leadHtml = (e) => {
    const ph = (e.photos || [])[0];
    const id = ph ? "lead" + (++stripSeq) : null;
    if (ph) strips[id] = e.photos.map((p) => ({ ...p, leg: `${e.from} → ${e.to}` }));
    return `<article class="entry ${ph ? "lead" : ""}">
      ${ph ? `<div class="cover" data-strip="${id}" data-i="0" style="background-image:url('/photos/${esc(ph.file)}')"><span>${esc(ph.where || "")}</span></div>` : ""}
      <div class="body"><header><span>Leg ${legs.length} · ${esc(e.from)} → ${esc(e.to)} · ${when(e)}</span><span>${esc(e.aircraft)}</span></header>
      <h4>${esc(e.to_name)}</h4>
      <p>${esc(e.text)}</p>${chips(e)}${e.photos && e.photos.length > 1 ? photoStrip(e.photos.slice(1), `${e.from} → ${e.to}`) : ""}</div></article>`;
  };
  const entryHtml = (e, n) => e.type === "note" ? `<div class="entry note">${esc(e.text)}</div>` : `<article class="entry">
      <header><span>Leg ${n} · ${esc(e.from)} → ${esc(e.to)} · ${when(e)}</span><span>${esc(e.aircraft)}</span></header>
      <h4>${esc(e.to_name)}</h4>
      <p>${esc(e.text)}</p>
      ${photoStrip(e.photos, `${e.from} → ${e.to}`)}${chips(e)}
    </article>`;
  const rest = all.slice(all.indexOf(lead) + 1).map((e) => entryHtml(e, legs.length - legs.indexOf(e)));
  $("#journal").innerHTML = `<div class="jhead">
      <div><span class="label">${esc((S && S.settings.pilot_name) || "Your")}${S && S.settings.pilot_name ? "'s" : ""} journal</span>
      <h1 class="display">${legs.length ? `${word(legs.length)} leg${legs.length > 1 ? "s" : ""} from ${esc(home)}.<br><em>Still going.</em>` : `Empty pages <em>for now.</em>`}</h1></div>
      <div class="stats">${stat(st.nm.toLocaleString(), "nautical miles")}${stat(st.airfields, "airfields")}${stat(st.countries, "countries")}${stat(st.sights, "sights seen")}</div>
    </div>
    <div class="jgrid">
      <div>${lead ? leadHtml(lead) + rest.join("") : `<p class="lede">The first leg starts at ${esc(S && S.here ? S.here.name : "your home airfield")}. Pick one in Fly.</p>`}</div>
      <aside>
        <div class="journal-map" id="jmap"></div>
        <span class="label">The route so far</span>
        <ol class="timeline">${legs.slice(0, 12).map((e, i) => `<li><b>${String(legs.length - i).padStart(2, "0")} · ${esc(e.title || e.to_name)}</b><small>${when(e)} · ${e.distance_nm} nm${e.photos && e.photos.length ? ` · ${e.photos.length} photo${e.photos.length > 1 ? "s" : ""}` : ""}</small></li>`).join("")}</ol>
        <div class="stats" style="grid-template-columns:repeat(2,minmax(0,1fr));margin-top:10px">${stat(st.hours, "hours")}${stat(st.best_landing ?? "–", "best landing fpm")}</div>
        <div class="nextcard"><span class="label">Next leg</span><b>${S && S.here ? `Flights are waiting in ${esc(S.here.town || S.here.name)}` : "Flights are waiting"}</b><button class="btn lime" id="gofly">Find adventures</button></div>
      </aside>
    </div>`;
  const gf = $("#gofly"); if (gf) gf.onclick = () => document.querySelector('.tabs button[data-tab="fly"]').click();
  if (jmap) { jmap.remove(); jmap = null; }
  jmap = L.map("jmap", { zoomControl: false, attributionControl: false });
  const jb = baseName in BASEMAPS ? baseName : "Terrain";
  BASEMAPS[jb]().addTo(jmap); muteFor(jmap, jb);
  const h = await api("/api/history");
  drawJourney(jmap, h.legs, { small: true });
}

/* ---------------- first run ---------------- */
let setupPick = null, setupList = [];
function renderSetup() {
  const box = $("#setup");
  if (!S.needs_setup) { box.classList.remove("on"); return; }
  if (box.classList.contains("on")) return;
  box.classList.add("on");
  box.innerHTML = `<div class="sheet"><div class="kicker">Welcome to Onward</div><h2>Where does <em>the journey start?</em></h2>
    <p style="color:var(--ink2)">Every flight starts where the last one ended. Pick your home airfield, and you're off.</p>
    <div class="form"><label for="sname0">Your name, for the journal</label><input type="text" id="sname0" placeholder="Pilot" style="width:100%">
    <label for="shome">Home airfield</label><input type="text" id="shome" placeholder="Search by name, town or code" autocomplete="off" style="width:100%"></div>
    <div class="apres" id="shomeres"></div>
    <p class="hint" id="shomepick">Pick an airfield from the list.</p>
    <div class="row" style="margin-top:18px"><button class="btn lime" id="sgo" disabled>Start the journey</button></div></div>`;
  let tm = null;
  $("#shome").oninput = () => { clearTimeout(tm); tm = setTimeout(async () => {
    const q = $("#shome").value.trim(); if (q.length < 2) { $("#shomeres").innerHTML = ""; return; }
    const r = await api(`/api/apsearch?q=${encodeURIComponent(q)}`); setupList = r.results || [];
    $("#shomeres").innerHTML = setupList.map((x, k) => `<button data-home="${k}"><span><b>${esc(x.code || x.ident)}</b> ${esc(x.name)}</span><small>${esc(x.town)}${x.town ? ", " : ""}${esc(x.country)}</small></button>`).join("") || `<p class="hint" style="padding:8px 12px">Nothing found.</p>`;
    document.querySelectorAll("[data-home]").forEach((b) => b.onclick = () => { setupPick = setupList[+b.dataset.home]; $("#shomepick").innerHTML = `Home: <b>${esc(setupPick.name)}</b>, ${esc(setupPick.town || setupPick.country)}`; $("#shomeres").innerHTML = ""; $("#shome").value = setupPick.name; $("#sgo").disabled = false; });
  }, 250); };
  $("#sgo").onclick = async () => { if (!setupPick) return; $("#sgo").disabled = true; const r = await api("/api/setup", { pilot_name: $("#sname0").value, home: setupPick.ident }); if (r.error) { toast(r.error); $("#sgo").disabled = false; return; } box.classList.remove("on"); keys = {}; prefs = null; await poll(); };
}

/* ---------------- updates ---------------- */
let updDismissed = null, updRunning = false;
function renderUpdateBanner() {
  const u = S.update || {}, el = $("#updbar");
  const show = u.available && updDismissed !== u.latest && !updRunning;
  if (!changed("updbar", [show, u.latest, updRunning])) return;
  if (updRunning) { el.className = "updbar on"; el.innerHTML = `<span>Updating Onward… it restarts on its own, this page reloads when it's back.</span>`; return; }
  el.className = show ? "updbar on" : "updbar";
  el.innerHTML = show ? `<span><b>Onward ${esc(u.latest)} is ready.</b> ${esc((u.notes || [])[0] || "")}${(u.notes || []).length > 1 ? ` <a href="#" id="updmore">and more</a>` : ""}</span>
    <span class="row">${u.frozen ? `<a class="btn lime" href="${esc(u.url || "https://flightsim.to")}" target="_blank" rel="noopener">Get it on flightsim.to</a>` : `<button class="btn lime" id="updnow">Update now</button>`}<button class="linkbtn" id="updlater">Later</button></span>` : "";
  const n = $("#updnow"); if (n) n.onclick = runUpdate;
  const l = $("#updlater"); if (l) l.onclick = () => { updDismissed = u.latest; keys.updbar = null; renderUpdateBanner(); };
  const m = $("#updmore"); if (m) m.onclick = (e) => { e.preventDefault(); document.querySelector('.tabs button[data-tab="settings"]').click(); };
}
async function runUpdate(rollback) {
  const before = S.version;
  if (!confirm(rollback === true ? "Go back to the previous version of Onward? Your data stays as it is." : "Update Onward now? It restarts by itself, and your career, journal and photos stay as they are.")) return;
  updRunning = true; keys.updbar = null; renderUpdateBanner();
  const r = await api(rollback === true ? "/api/update/rollback" : "/api/update/apply", {}).catch(() => ({}));
  if (r && r.error) { updRunning = false; keys.updbar = null; toast(r.error); return; }
  const t0 = Date.now();
  const wait = setInterval(async () => {
    try { const s = await api("/api/state"); if (s.ready && (s.version !== before || Date.now() - t0 > 20000)) { clearInterval(wait); location.reload(); } } catch (e) {}
    if (Date.now() - t0 > 120000) { clearInterval(wait); updRunning = false; toast("Onward didn't come back. Check the black window on the sim PC."); }
  }, 1500);
}
function mdLite(t) {  // just enough Markdown for CREDITS.md: bold, links, bullets, paragraphs
  const inl = (x) => esc(x).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  let out = "", list = false;
  t.split(/\r?\n/).forEach((l) => {
    if (/^#\s/.test(l)) return;
    if (/^- /.test(l)) { if (!list) { out += "<ul class=\"credits\">"; list = true; } out += `<li>${inl(l.slice(2))}</li>`; return; }
    if (list) { out += "</ul>"; list = false; }
    if (l.trim()) out += `<p>${inl(l)}</p>`;
  });
  return out + (list ? "</ul>" : "");
}
async function loadAbout() {
  const el = $("#aboutcard");
  if (!el || el.dataset.done) return;
  el.dataset.done = "1";
  try {
    const r = await api("/api/credits");
    const lic = (r.license || "").split(/\r?\n/).find((l) => l.trim()) || "";
    el.innerHTML = `<h3 style="margin-top:0">About and credits</h3><p>Onward ${esc(S.version)}. ${esc(lic)} Source code: <a href="https://github.com/onward-sim/Onward" target="_blank" rel="noopener">github.com/onward-sim/Onward</a>.</p>${mdLite(r.credits || "")}`;
  } catch (e) {}
}
function updatesCard() {
  const u = S.update || {}, s = S.settings;
  return `<div class="card form"><h3 style="margin-top:0">Updates</h3>
    <p style="margin:0">You have <b>Onward ${esc(S.version)}</b>.${u.available ? ` <b>${esc(u.latest)}</b> is ready.` : u.latest ? " That's the latest." : ""}</p>
    ${u.error ? `<p class="hint" style="color:var(--bad)">${esc(u.error)}</p>` : u.checked ? `<p class="hint">Last checked ${esc(u.checked)}.</p>` : ""}
    ${u.available && (u.notes || []).length ? `<span class="label" style="margin-top:12px">What's new</span><ul class="why">${u.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <div class="row" style="margin-top:12px">${u.available ? (u.frozen ? `<a class="btn lime" href="${esc(u.url || "https://flightsim.to")}" target="_blank" rel="noopener">Get it on flightsim.to</a>` : `<button class="btn lime" id="supd">Update now</button>`) : ""}<button class="btn ghost" id="scheck">Check now</button>${(u.backups || []).length ? `<button class="btn ghost" id="sroll">Go back to ${esc(u.backups[0].split("-")[0])}</button>` : ""}</div>
    ${u.frozen ? `<p class="hint">This is the packaged app: new versions are on flightsim.to. Download the new zip and replace the program folder. Your career and settings stay as they are.</p>` : ""}
    <label class="toggle"><input type="checkbox" id="supdchk" ${s.update_check !== false ? "checked" : ""}> Check for updates when Onward starts</label>
    <label for="srepo">Update source (GitHub owner/repository)</label><input type="text" id="srepo" value="${esc(s.update_repo || "")}" placeholder="onward-sim/Onward">
    <label for="stoken">GitHub token (only for a private repository)</label><input type="text" id="stoken" value="${esc(s.update_token || "")}" placeholder="leave empty for a public repository" autocomplete="off">
    <p class="hint">Updates replace only the program files. Your data stays in <code>${esc(S.data_dir || "data")}</code>, and the previous version is kept so you can go back.</p></div>`;
}

/* ---------------- main loop ---------------- */
function render() {
  if (!S) return;
  $("#boot").classList.toggle("gone", !!S.ready);
  if (!S.ready) { $("#bootlog").textContent = (S.error ? "Error: " + S.error + "\n\n" : "") + (S.log || []).join("\n"); return; }
  const h = S.here;
  $("#where").innerHTML = h ? `<b>${esc(h.code || h.ident)}</b>${esc(h.town || h.name)}, ${esc(h.country_name)} · ${esc(S.aircraft.name)} <span class="status ${S.plane.status}" style="margin-left:6px">${STATUS[S.plane.status]}</span>` : "";
  const sim = $("#sim"), live = S.live || {};
  sim.classList.toggle("ok", !!live.connected);
  sim.querySelector("span").textContent = live.connected ? (S.demo ? "Demo mode" : S.sim_title || "Connected") : live.error || "Waiting for the sim";
  sim.title = live.error || "";
  renderSetup();
  renderUpdateBanner();
  Voice.set(S.settings && S.settings.voice && !S.cockpit_active); // the cockpit screen does the talking when it's open
  $("#voicebtn").textContent = Voice.on ? "Voice on" : "Voice off";
  Voice.feed(S.live && S.live.flight ? S.live.flight.msgs : null);
  renderModal();
  buildLayerControl((S.settings && S.settings.openaip_key) || null);
  if (S.stats && S.stats.legs !== historyLegs) { historyLegs = S.stats.legs; loadHistory(); }
  if (tab === "fly") { renderSide(); drawMap(); drawEditor(); updateProfileLive(); } else if (keys.edit) { keys.edit = null; editLayer.clearLayers(); drawProfile(null); }
  if (tab === "trip") renderTrip();
  if (tab === "hangar") renderHangar();
  if (tab === "settings") renderSettings();
}
async function poll() {
  try { S = await api("/api/state"); render(); } catch (e) { $("#sim").classList.remove("ok"); $("#sim span").textContent = "Server not running"; }
}
poll();
$("#voicebtn").onclick = async () => {
  const v = !(S && S.settings && S.settings.voice);
  await post("/api/settings", { settings: { voice: v } });
  Voice.set(v); if (v) Voice.say("Callouts on.", true);
};
setInterval(poll, 1000);
