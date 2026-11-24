/*
 * Public site settings.
 *
 * basemapKey: ArcGIS API key for the optional "Show street map" layer (Esri
 * World Navigation basemap, as on the JHFRC Tract Finder). A browser basemap
 * key is always visible in the page, so this key must:
 *   - have the Basemaps privilege ONLY (no geocoding or other paid services);
 *   - be restricted to the referrer https://amohith7.github.io/*.
 * Manage it in the UTC ArcGIS Online organization (Content > Developer
 * credentials). Leave blank to fall back to OpenStreetMap, then CARTO.
 */
window.A2T_CONFIG = {
  basemapKey: "AAPTa6P9d6KL_JyB4IfaWQlq5-g..gbNf8KueMjIQn0zA7XxLpmGwoxtvyaawXDBMRdTiOVO9ipsFH5bCd2V-2wD11l8CDrpxyRW7hqF-_7GolY460B_XL_AG6e3GUTV6sUrW3y2hdhkzKqv-chCnHAcXztimsm9U5g_9CNJTkRD-2cUir8lYdqn_AFI5O_J5KGlaMB7zM3wWOU1MeQbck3RVvh4XCiJ1TP95djZhIlf9p7HdR6BNOoEbs0UszzpNDOE42BOfBjxgW7F8qgnyVg..AT1_aZKWNAU9",
};
