/*
 * address2tract web: results map. Shades each census tract by how many of
 * the user's records fall in it.
 *
 * Private by design: tract boundaries are loaded from this site
 * (assets/tracts/<state FIPS>.json, built by scripts/build_tract_maps.py) and
 * there is no street basemap, so no tract ID, address or map view is sent to
 * any other service. County lines come from the same tract borders.
 */
(function (root) {
  "use strict";

  const TRACTS_DIR = "assets/tracts/";
  // One-hue light->dark ramp (validated: monotone lightness, light end clears
  // the white map surface).
  const RAMP = ["#8fb6e8", "#5f97dd", "#3677cc", "#1f5aa6", "#0f3d7a"];
  const MAX_FIT_ZOOM = 13;

  const states = new Map(); // state FIPS -> Promise<{tracts: Map(geoid -> feature), counties: geometry}>
  let map = null;
  let tractLayer = null;
  let contextLayer = null;
  let labelLayer = null;
  let lastBounds = null;
  let lastKey = "";
  let seq = 0;

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
    ensureMap();
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
      L.geoJSON({ type: "FeatureCollection", features: fs }, {
        interactive: false,
        style: { color: "#cfd6df", weight: 0.6, fillColor: "#eef1f5", fillOpacity: 1 },
      })
    );
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
        style: (f) => ({
          color: "#ffffff",
          weight: 1,
          fillColor: RAMP[rampIndex(A2T.breakIndex(f.properties.count, breaks), breaks.length)],
          fillOpacity: 0.85,
        }),
        onEachFeature: (f, layer) => {
          layer.bindTooltip(tooltipFor(f, f.properties.count), { sticky: true, className: "map-tip" });
          layer.on({
            mouseover: () => {
              layer.setStyle({ color: "#002147", weight: 2.5 });
              layer.bringToFront();
            },
            mouseout: () => tractLayer.resetStyle(layer),
          });
        },
      }
    ).addTo(map);

    labelLayer.addTo(map);
    lastBounds = tractLayer.getBounds();
    refit();
    renderLegend(breaks);

    const total = features.reduce((s, x) => s + x.count, 0);
    const top = features.reduce((a, b) => (b.count > a.count ? b : a), features[0]);
    let text = fmt(total) + (total === 1 ? " record" : " records") + " in " + fmt(features.length) +
      (features.length === 1 ? " tract" : " tracts") + ".";
    if (top) text += " Most: " + top.f.properties.n + ", " + top.f.properties.c + " (" + fmt(top.count) + ").";
    if (missing) text += " " + fmt(missing) + " matched " + (missing === 1 ? "record has a tract" : "records have tracts") + " not on this map.";
    setStatus(text);
  }

  root.A2TMap = { update, refit };
})(typeof self !== "undefined" ? self : this);
