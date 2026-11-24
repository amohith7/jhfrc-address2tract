/*
 * address2tract web: results map. Shades each census tract by how many of
 * the user's records fall in it.
 *
 * Private by default: tract boundaries are loaded from this site
 * (assets/tracts/<state FIPS>.json, built by scripts/build_tract_maps.py), so
 * no tract ID or address is ever sent to a map service. County lines come
 * from the same tract borders.
 *
 * Street map (off until the user switches it on): the Esri World Navigation
 * vector basemap, as on the JHFRC Tract Finder, drawn with MapLibre GL (loaded
 * only when needed). If Esri cannot load, OpenStreetMap, then CARTO. Tile
 * services see only the map area shown.
 */
(function (root) {
  "use strict";

  const TRACTS_DIR = "assets/tracts/";
  // One-hue light->dark ramp (validated: monotone lightness, light end clears
  // the white map surface).
  const RAMP = ["#8fb6e8", "#5f97dd", "#3677cc", "#1f5aa6", "#0f3d7a"];
  const MAX_FIT_ZOOM = 13;
  const MAPLIBRE_DIR = "assets/vendor/maplibre/";
  const MAPLIBRE_VERSION = "4.7.1";
  const ESRI_STYLE = "https://basemapstyles-api.arcgis.com/arcgis/rest/services/styles/v2/styles/arcgis/navigation?token=";
  const BASEMAP_KEY = ((root.A2T_CONFIG || {}).basemapKey || "").trim();

  const states = new Map(); // state FIPS -> Promise<{tracts: Map(geoid -> feature), counties: geometry}>
  let map = null;
  let tractLayer = null;
  let contextLayer = null;
  let labelLayer = null;
  let lastBounds = null;
  let lastKey = "";
  let seq = 0;
  let backdropLayers = [];
  let baseLayer = null;
  let baseOn = false;
  let vectorLoading = null;
  let layerByGeoid = new Map();
  let currentBreaks = [];
  let selected = "";
  let selectHandler = null;
  const reduceMotion = !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => n.toLocaleString();

  function ensureMap() {
    if (map) return;
    map = L.map("tract-map", {
      scrollWheelZoom: false,
      zoomSnap: 0.25,
      attributionControl: true,
      preferCanvas: false,
    });
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Tract boundaries: U.S. Census Bureau");
    map.setView([35.0, -85.3], 9);
    // The map fills its panel, whose height follows the side list.
    if ("ResizeObserver" in root) new ResizeObserver(() => map.invalidateSize()).observe($("tract-map"));
    map.on("zoomend", () => {
      if (tractLayer) tractLayer.eachLayer((l) => tractLayer.resetStyle(l));
      if (selected && layerByGeoid.get(selected)) layerByGeoid.get(selected).bringToFront();
    });
  }

  /** Load (once) and index one state's boundary file. */
  function loadState(fips) {
    if (!states.has(fips)) {
      const p = fetch(TRACTS_DIR + fips + ".json", { credentials: "omit" })
        .then((res) => {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then((topo) => {
          const obj = topo.objects.tracts;
          const fc = topojson.feature(topo, obj);
          const tracts = new Map(fc.features.map((f) => [f.properties.g, f]));
          // Shared borders where the county changes, plus the state's edge.
          const counties = topojson.mesh(topo, obj, (a, b) => a === b || a.properties.c !== b.properties.c);
          return { tracts, counties };
        });
      p.catch(() => states.delete(fips)); // allow a later retry
      states.set(fips, p);
    }
    return states.get(fips);
  }

  function setStatus(text) {
    $("map-status").textContent = text;
  }

  function tooltipFor(f, count) {
    const box = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = f.properties.n;
    const meta = document.createElement("div");
    meta.textContent = f.properties.c + " · " + f.properties.g;
    const n = document.createElement("div");
    n.className = "map-tip-count";
    n.textContent = fmt(count) + (count === 1 ? " record" : " records");
    box.append(name, meta, n);
    return box;
  }

  function renderLegend(breaks) {
    const labels = A2T.breakLabels(breaks);
    const items = breaks.map((_, i) => {
      const li = document.createElement("li");
      const sw = document.createElement("i");
      sw.style.background = RAMP[rampIndex(i, breaks.length)];
      li.append(sw, document.createTextNode(labels[i]));
      return li;
    });
    $("map-legend").replaceChildren(...items);
  }

  // Spread fewer classes across the full ramp so 2 classes are light and dark.
  function rampIndex(i, n) {
    return n <= 1 ? RAMP.length - 1 : Math.round((i * (RAMP.length - 1)) / (n - 1));
  }

  // With a street map underneath, the grey tract grid turns to outlines only so
  // streets show through.
  function backdropStyle() {
    return baseOn
      ? { color: "#8a94a3", weight: 0.6, fill: false }
      : { color: "#cfd6df", weight: 0.6, fillColor: "#eef1f5", fillOpacity: 1, fill: true };
  }

  // Tract shading fades as the user zooms in, so streets and labels underneath
  // show through at neighborhood scale: about 0.85 at county scale, down to 0.3.
  function fillFor() {
    const z = map ? map.getZoom() : 9;
    const k = Math.min(1, Math.max(0, (z - 10) / 4));
    const top = baseOn ? 0.75 : 0.85;
    return +(top - k * (top - (baseOn ? 0.28 : 0.4))).toFixed(2);
  }

  function tractStyle(f) {
    const isSel = f.properties.g === selected;
    return {
      color: isSel ? "#ffc425" : baseOn ? "#0f3d7a" : "#ffffff",
      weight: isSel ? 3.5 : 1,
      fillColor: RAMP[rampIndex(A2T.breakIndex(f.properties.count, currentBreaks), currentBreaks.length)],
      fillOpacity: fillFor(),
    };
  }

  const shortName = (n) => String(n).replace(/^Census Tract /, "Tract ");

  /** Count a number up from 0 when the map draws (skipped for reduced motion). */
  function countUp(node, value) {
    if (reduceMotion || value < 4) {
      node.textContent = fmt(value);
      return;
    }
    const start = performance.now();
    const dur = 700;
    const step = (now) => {
      const k = Math.min(1, (now - start) / dur);
      node.textContent = fmt(Math.round(value * (1 - Math.pow(1 - k, 3))));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function hover(g, on) {
    const layer = layerByGeoid.get(g);
    if (!layer) return;
    if (on) {
      layer.setStyle({ color: "#002147", weight: 2.5 });
      layer.bringToFront();
    } else {
      tractLayer.resetStyle(layer);
      if (g !== selected && selected && layerByGeoid.get(selected)) layerByGeoid.get(selected).bringToFront();
    }
    document.querySelectorAll('#top-tracts [data-g="' + g + '"]').forEach((b) => b.classList.toggle("hot", on));
  }

  /** Highlight one tract (gold outline), optionally zooming to it. */
  function select(g, zoom) {
    selected = layerByGeoid.has(g) ? g : "";
    if (tractLayer) tractLayer.eachLayer((l) => tractLayer.resetStyle(l));
    const layer = layerByGeoid.get(selected);
    if (layer) {
      layer.bringToFront();
      if (zoom) map.fitBounds(layer.getBounds(), { padding: [60, 60], maxZoom: 14 });
    }
    document.querySelectorAll("#top-tracts button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === selected)));
  }

  function choose(g) {
    select(g, true);
    const layer = layerByGeoid.get(g);
    if (selectHandler) selectHandler(g, layer ? layer.feature.properties.n : g);
  }

  function renderTop(features) {
    const top = features
      .slice()
      .sort((a, b) => b.count - a.count || a.f.properties.g.localeCompare(b.f.properties.g))
      .slice(0, 6);
    const max = top.length ? top[0].count : 1;
    const items = top.map((x) => {
      const p = x.f.properties;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tt";
      btn.dataset.g = p.g;
      btn.setAttribute("aria-pressed", String(p.g === selected));
      btn.title = "Zoom to " + p.n + " and show its rows";
      const name = document.createElement("span");
      name.className = "tt-name";
      name.textContent = shortName(p.n);
      const county = document.createElement("span");
      county.className = "tt-county";
      county.textContent = p.c;
      const bar = document.createElement("span");
      bar.className = "tt-bar";
      const fill = document.createElement("i");
      fill.style.background = RAMP[rampIndex(A2T.breakIndex(x.count, currentBreaks), currentBreaks.length)];
      fill.style.setProperty("--w", Math.max(6, (x.count / max) * 100) + "%");
      bar.append(fill);
      const n = document.createElement("b");
      n.textContent = fmt(x.count);
      btn.append(name, n, county, bar);
      btn.addEventListener("click", () => choose(p.g));
      btn.addEventListener("mouseenter", () => hover(p.g, true));
      btn.addEventListener("mouseleave", () => hover(p.g, false));
      btn.addEventListener("focus", () => hover(p.g, true));
      btn.addEventListener("blur", () => hover(p.g, false));
      const li = document.createElement("li");
      li.append(btn);
      return li;
    });
    $("top-tracts").replaceChildren(...items);
  }

  function restyle() {
    backdropLayers.forEach((l) => l.setStyle(backdropStyle()));
    if (tractLayer) tractLayer.eachLayer((l) => tractLayer.resetStyle(l));
  }

  // ------------------------------------------------------------ street map

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  /** MapLibre GL and its Leaflet bridge, fetched from this site on first use. */
  function loadVector() {
    if (!vectorLoading) {
      const v = "?v=" + MAPLIBRE_VERSION;
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = MAPLIBRE_DIR + "maplibre-gl.css" + v;
      document.head.appendChild(css);
      vectorLoading = loadScript(MAPLIBRE_DIR + "maplibre-gl.js" + v).then(() =>
        loadScript(MAPLIBRE_DIR + "leaflet-maplibre-gl.js" + v)
      );
      vectorLoading.catch(() => (vectorLoading = null));
    }
    return vectorLoading;
  }

  function webglAvailable() {
    try {
      const c = document.createElement("canvas");
      return !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch (_) {
      return false;
    }
  }

  function setBase(layer, provider) {
    if (baseLayer) baseLayer.remove();
    baseLayer = layer;
    if (layer) layer.addTo(map);
    const note = $("map-base-note");
    note.hidden = !provider;
    note.textContent = provider
      ? "Street map by " + provider + ". " + provider + " receives the map area shown, never your addresses or tract IDs."
      : "";
    restyle();
  }

  function useCarto() {
    if (!baseOn) return;
    setBase(
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "\u00a9 OpenStreetMap contributors \u00a9 CARTO",
        subdomains: "abcd",
        maxZoom: 19,
      }),
      "CARTO"
    );
  }

  function useOsm() {
    if (!baseOn) return;
    const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "\u00a9 OpenStreetMap contributors",
      maxZoom: 19,
    });
    osm.once("tileerror", () => {
      if (baseLayer === osm) useCarto();
    });
    setBase(osm, "OpenStreetMap");
  }

  async function useEsri() {
    if (!BASEMAP_KEY || !webglAvailable()) return useOsm();
    try {
      await loadVector();
    } catch (_) {
      return useOsm();
    }
    if (!baseOn) return;
    let esri;
    try {
      esri = L.maplibreGL({ style: ESRI_STYLE + encodeURIComponent(BASEMAP_KEY), attribution: "Powered by Esri" });
      setBase(esri, "Esri");
    } catch (_) {
      return useOsm();
    }
    const gl = esri.getMaplibreMap && esri.getMaplibreMap();
    if (gl && gl.on) {
      // Some Esri styles name sprite icons missing from the sheet.
      gl.on("styleimagemissing", (ev) => {
        try {
          if (ev && ev.id && !gl.hasImage(ev.id)) gl.addImage(ev.id, { width: 1, height: 1, data: new Uint8Array(4) });
        } catch (_) {}
      });
      gl.on("error", () => {
        if (baseLayer === esri) useOsm();
      });
    }
  }

  /** Switch the street map on or off. */
  function setStreetMap(on) {
    baseOn = !!on;
    if (!map) return;
    if (baseOn) useEsri();
    else setBase(null, "");
  }

  /** Re-zoom to the tracts on the map. */
  function refit() {
    if (map && lastBounds) map.fitBounds(lastBounds, { padding: [24, 24], maxZoom: MAX_FIT_ZOOM });
  }

  /** Draw (or redraw) the map for the current records. */
  async function update(records) {
    const my = ++seq;
    const counts = A2T.tractCounts(records);
    const wrap = $("map-card");
    if (!counts.size) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const firstDraw = !map;
    ensureMap();
    if (firstDraw && baseOn) setStreetMap(true);
    map.invalidateSize();
    // Same tracts and counts as last time (e.g. only a table filter changed):
    // keep the user's current zoom.
    const key = [...counts].sort().join(";");
    if (key === lastKey && tractLayer) return;
    lastKey = key;
    setStatus("Drawing tracts…");

    const fipsList = [...new Set([...counts.keys()].map((g) => g.slice(0, 2)))];
    let loaded;
    try {
      loaded = await Promise.all(fipsList.map(loadState));
    } catch (err) {
      if (my !== seq) return;
      lastKey = "";
      setStatus("The tract map could not be loaded (" + err.message + "). Your results are not affected.");
      return;
    }
    if (my !== seq) return;

    const features = [];
    let missing = 0;
    for (const [g, count] of counts) {
      const st = loaded[fipsList.indexOf(g.slice(0, 2))];
      const f = st.tracts.get(g);
      if (f) features.push({ f, count });
      else missing += count;
    }
    const breaks = A2T.countBreaks(features.map((x) => x.count));

    currentBreaks = breaks;
    layerByGeoid = new Map();
    if (!features.some((x) => x.f.properties.g === selected)) selected = "";
    if (tractLayer) tractLayer.remove();
    if (contextLayer) contextLayer.remove();
    if (labelLayer) labelLayer.remove();
    // Context, all from the same local files: every tract in the counties that
    // have records (light grey), county lines, and county name labels.
    const servedCounties = new Set(features.map((x) => x.f.properties.g.slice(0, 5)));
    const byCounty = new Map();
    for (const st of loaded) {
      for (const [g, f] of st.tracts) {
        const cc = g.slice(0, 5);
        if (!servedCounties.has(cc)) continue;
        if (!byCounty.has(cc)) byCounty.set(cc, []);
        byCounty.get(cc).push(f);
      }
    }
    const backdrop = [...byCounty.values()].map((fs) =>
      L.geoJSON({ type: "FeatureCollection", features: fs }, { interactive: false, style: backdropStyle })
    );
    backdropLayers = backdrop;
    const labels = backdrop.map((layer, i) => {
      const name = [...byCounty.values()][i][0].properties.c;
      return L.tooltip({ permanent: true, direction: "center", className: "map-county-label", interactive: false })
        .setLatLng(layer.getBounds().getCenter())
        .setContent(name);
    });
    contextLayer = L.layerGroup([
      ...backdrop,
      ...loaded.map((st) => L.geoJSON(st.counties, { interactive: false, style: { color: "#7d8796", weight: 1.2, opacity: 0.9, fill: false } })),
    ]).addTo(map);
    labelLayer = L.layerGroup(labels);

    tractLayer = L.geoJSON(
      { type: "FeatureCollection", features: features.map((x) => ({ ...x.f, properties: { ...x.f.properties, count: x.count } })) },
      {
        style: tractStyle,
        onEachFeature: (f, layer) => {
          layerByGeoid.set(f.properties.g, layer);
          layer.bindTooltip(tooltipFor(f, f.properties.count), { sticky: true, className: "map-tip" });
          layer.on({
            mouseover: () => hover(f.properties.g, true),
            mouseout: () => hover(f.properties.g, false),
            click: () => choose(f.properties.g),
          });
        },
      }
    ).addTo(map);

    // Tracts fade in from the lightest class to the darkest.
    if (!reduceMotion) {
      tractLayer.eachLayer((layer) => {
        const el = layer.getElement && layer.getElement();
        if (!el) return;
        const cls = A2T.breakIndex(layer.feature.properties.count, breaks);
        el.classList.add("tract-in");
        el.style.animationDelay = Math.round(cls * 160 + Math.random() * 140) + "ms";
      });
    }

    labelLayer.addTo(map);
    renderLegend(breaks);
    renderTop(features);
    map.invalidateSize();
    lastBounds = tractLayer.getBounds();
    refit();
    if (selected) select(selected, false);

    const total = features.reduce((s, x) => s + x.count, 0);
    const counties = new Set(features.map((x) => x.f.properties.g.slice(0, 5))).size;
    countUp($("map-tracts"), features.length);
    $("map-tracts-label").textContent = features.length === 1 ? "tract" : "tracts";
    let text = fmt(total) + (total === 1 ? " record" : " records") + " mapped across " + fmt(counties) +
      (counties === 1 ? " county." : " counties.");
    if (missing) text += " " + fmt(missing) + " matched " + (missing === 1 ? "record has a tract" : "records have tracts") + " not on this map.";
    setStatus(text);
  }

  root.A2TMap = {
    update,
    refit,
    setStreetMap,
    /** Clear the highlighted tract (the table's tract filter was removed). */
    clearSelection: () => select("", false),
    /** fn(geoid, tractName) runs when the user picks a tract on the map or in the list. */
    onSelect: (fn) => (selectHandler = fn),
  };
})(typeof self !== "undefined" ? self : this);
