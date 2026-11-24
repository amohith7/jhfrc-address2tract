# Vendored libraries

| File | Library | Version | License | Source |
|------|---------|---------|---------|--------|
| `xlsx.full.min.js` | SheetJS Community Edition | 0.20.3 | Apache License 2.0 | https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js |
| `leaflet.js`, `leaflet.css` | Leaflet | 1.9.4 | BSD 2-Clause | https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/ (leaflet.js matches the cdnjs SRI hash; leaflet.css matches it apart from line endings, converted to LF) |
| `maplibre/maplibre-gl.js`, `maplibre/maplibre-gl.css`, `maplibre/leaflet-maplibre-gl.js` | MapLibre GL JS and its Leaflet bridge | 4.7.1 | BSD 3-Clause (`maplibre/LICENSE.txt`) | Same files as the JHFRC Tract Finder (`web/static/vendor/maplibre/`) |
| `topojson-client.min.js` | topojson-client | 3.1.0 | ISC | https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js |

SheetJS is served from this repository rather than a CDN so the site keeps working
if the CDN changes, and so the exact code that reads a partner's file is the code
reviewed here. The npm-published `xlsx` package stops at 0.18.5, which has known
security issues (CVE-2023-30533, CVE-2024-22363); 0.20.3 fixes both.

Leaflet and topojson-client draw the results map. The tract boundaries they draw live in
`docs/assets/tracts/` and are built by `scripts/build_tract_maps.py`, so the map makes no
requests to any map or tile service unless the user switches on **Show street map**. That loads
MapLibre (only then) and the Esri World Navigation basemap with the key in `docs/assets/config.js`,
falling back to OpenStreetMap, then CARTO. Tile services receive only the map area shown.

To upgrade, download the new `xlsx.full.min.js` from https://cdn.sheetjs.com/,
replace this file, and update the version above.
