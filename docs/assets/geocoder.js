/*
 * address2tract web: geocoding pipeline.
 *
 * Same order as the Python tool's single-address path (src/geocode.py and
 * src/geocode_external.py):
 *   1. Census geocoder, current address file, then the 2020 address file.
 *   2. One retry with a conservatively normalized street.
 *   3. Extra passes over records whose lookups failed on a network error.
 *   4. Only when the user supplies an ArcGIS token: the residual goes to the
 *      ArcGIS World Geocoder (the CLI's --external-provider arcgis). The public
 *      OpenStreetMap (Nominatim) service is not used: its usage policy does not
 *      allow bulk geocoding.
 * Addresses are matched against the Census's current address file, but every
 * tract is a 2020 Census tract (geography vintage Census2020_Current), the
 * same boundaries as the tract maps on this site and the CLI's tract file.
 *
 * Network access goes through an injected `transport`, so tests can run the
 * pipeline offline:
 *   transport.jsonp(url, params) -> Promise<object>     (Census, JSONP)
 *   transport.postForm(url, params) -> Promise<any>     (ArcGIS, CORS POST,
 *                                                         so the token travels
 *                                                         in the body, not the URL)
 *   transport.sleep(ms) -> Promise<void>
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./core.js"));
  } else {
    root.A2TGeocoder = factory(root.A2T);
  }
})(typeof self !== "undefined" ? self : this, function (A2T) {
  "use strict";

  const CENSUS = "https://geocoding.geo.census.gov/geocoder/geographies/";
  const ONELINE_URL = CENSUS + "onelineaddress";
  const COORDS_URL = CENSUS + "coordinates";
  // Esri documents geocode-api for API keys; geocode.arcgis.com is the older
  // host that ArcGIS Online tokens (and the CLI) use. A token refused by one
  // is tried on the other before the step gives up.
  const ARCGIS_PATH = "/arcgis/rest/services/World/GeocodeServer/findAddressCandidates";
  const ARCGIS_URLS = [
    "https://geocode-api.arcgis.com" + ARCGIS_PATH,
    "https://geocode.arcgis.com" + ARCGIS_PATH,
  ];
  const ARCGIS_TEST_ADDRESS = "1600 Pennsylvania Ave NW, Washington, DC 20500";

  // Tract geography is fixed at the 2020 Census so results line up with
  // other 2020-tract data (CDC PLACES, ACS) and do not shift when the Census
  // rolls its "Current" geography forward. Address files stay current.
  const GEOGRAPHY_VINTAGE = "Census2020_Current";
  const TRACT_GEOGRAPHY = "2020 Census tracts";

  // (benchmark, vintage) pairs swept in order (geocode.FALLBACK_BENCHMARKS).
  // Both return 2020 tracts directly.
  const BENCHMARKS = [
    ["Public_AR_Current", GEOGRAPHY_VINTAGE],
    ["Public_AR_Census2020", "Census2020_Census2020"],
  ];

  const DEFAULTS = {
    concurrency: 6, // config.yaml geocoder.concurrency
    retries: 3, // attempts per request on a network error
    retryPasses: 2, // config.yaml geocoder.retry_passes
    externalMaxResidual: 2000, // config.yaml external_max_residual
    arcgisToken: "", // set => the Census residual is looked up with ArcGIS
    arcgisConcurrency: 4,
  };

  const STATUS_NO_MATCH = "No_Match";
  const REASON_NO_GEOCODE = "Address could not be geocoded";
  const REASON_NO_TRACT =
    "Coordinates found but did not fall within a Census tract boundary";

  class Cancelled extends Error {}

  /**
   * The external provider refused the request itself (bad or expired token,
   * quota used up). Stops that stage instead of marking every remaining row
   * as a false No_Match.
   */
  class ProviderError extends Error {}

  function checkCancel(signal) {
    if (signal && signal.aborted) throw new Cancelled("Cancelled");
  }

  /** Call fn with retries and exponential backoff (1s, 2s, ...) on errors. */
  async function withRetry(fn, transport, retries, signal) {
    for (let attempt = 1; ; attempt++) {
      checkCancel(signal);
      try {
        return await fn();
      } catch (err) {
        if (err instanceof Cancelled || attempt >= retries) throw err;
        await transport.sleep(1000 * 2 ** (attempt - 1));
      }
    }
  }

  function tractFrom(geographies) {
    const tracts = (geographies && geographies["Census Tracts"]) || [];
    return tracts.length ? A2T.cleanGeoid(tracts[0].GEOID) : "";
  }

  /** 2020 tract GEOID for a point, or "" if it is in no tract. */
  async function coordsToTract(lat, lon, ctx) {
    const data = await withRetry(
      () =>
        ctx.transport.jsonp(COORDS_URL, {
          x: lon,
          y: lat,
          benchmark: "Public_AR_Current",
          vintage: GEOGRAPHY_VINTAGE,
          layers: "Census Tracts",
          format: "jsonp",
        }),
      ctx.transport,
      ctx.opts.retries,
      ctx.signal
    );
    return tractFrom(data && data.result && data.result.geographies);
  }

  /**
   * Sweep the Census benchmarks for one address. Returns
   * {match, benchmarkIndex, error}; error is true when a lookup failed on the
   * network (so a later pass may still recover it), not on a genuine miss.
   */
  async function censusSingle(address, ctx) {
    let error = false;
    for (let i = 0; i < BENCHMARKS.length; i++) {
      const [benchmark, vintage] = BENCHMARKS[i];
      let data;
      try {
        data = await withRetry(
          () =>
            ctx.transport.jsonp(ONELINE_URL, {
              address,
              benchmark,
              vintage,
              format: "jsonp",
            }),
          ctx.transport,
          ctx.opts.retries,
          ctx.signal
        );
      } catch (err) {
        if (err instanceof Cancelled) throw err;
        error = true;
        continue;
      }
      const matches = (data && data.result && data.result.addressMatches) || [];
      const match = matches[0];
      if (match && match.coordinates && match.coordinates.x != null) {
        return { match, benchmarkIndex: i, error: false };
      }
    }
    return { match: null, benchmarkIndex: -1, error };
  }

  /** Census lookup for one record, with the normalized-street retry. */
  async function geocodeCensus(rec, ctx) {
    let res = await censusSingle(rec.address, ctx);
    let normalized = false;
    if (!res.match) {
      const alt = A2T.normalizeAddressStreet(rec.address);
      if (alt && alt !== rec.address) {
        const retry = await censusSingle(alt, ctx);
        if (retry.match) {
          res = retry;
          normalized = true;
        } else {
          res.error = res.error || retry.error;
        }
      }
    }
    if (!res.match) {
      rec.transientError = res.error;
      return;
    }

    const m = res.match;
    // Both benchmarks return 2020 tracts. Only if the response carried none is
    // the point looked up on its own.
    let tract = tractFrom(m.geographies);
    if (!tract) {
      try {
        tract = await coordsToTract(m.coordinates.y, m.coordinates.x, ctx);
      } catch (err) {
        if (err instanceof Cancelled) throw err;
        rec.transientError = true; // leave unmatched so a retry pass can redo it
        return;
      }
    }
    rec.transientError = false;
    rec.cleaned_address = m.matchedAddress || "";
    rec.census_tract_geoid = tract;
    rec.latitude = Number(m.coordinates.y);
    rec.longitude = Number(m.coordinates.x);
    rec.geocoder = "U.S. Census Bureau";
    rec.address_reference = BENCHMARKS[res.benchmarkIndex][0];
    rec.match_precision = normalized ? "Street address (tidied street name)" : "Street address";
    rec.match_status =
      res.benchmarkIndex === 0 && !normalized ? "Matched" : "Matched_Fallback";
    rec.error_reason = tract ? "" : REASON_NO_TRACT;
  }

  /** Run fn over items with a fixed number of parallel workers. */
  async function pool(items, concurrency, fn, signal) {
    let next = 0;
    const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
      while (next < items.length) {
        checkCancel(signal);
        const item = items[next++];
        await fn(item);
      }
    });
    await Promise.all(workers);
  }

  /**
   * Plain-language text for an ArcGIS {error: {...}} payload, ending with
   * ArcGIS's own code and message so a token problem can be diagnosed.
   */
  function arcgisErrorText(error) {
    const code = Number(error && error.code);
    const details = Array.isArray(error && error.details) ? error.details.join(" ") : "";
    const msg = String((error && error.message) || "").trim();
    const raw = [msg, details].filter(Boolean).join(" ").trim();
    const said = " (ArcGIS said: " + (code ? code + " " : "") + (raw || "no details") + ")";
    if (code === 498 || code === 499 || /token/i.test(raw)) {
      return "ArcGIS did not accept the token" + said;
    }
    if (code === 403) return "ArcGIS refused the request; the token may lack geocoding rights or credits" + said;
    return "ArcGIS returned an error" + said;
  }

  // Plain names for the ArcGIS address types pickArcgis accepts.
  const ARCGIS_PRECISION = {
    PointAddress: "Rooftop or parcel",
    Subaddress: "Rooftop or parcel (unit)",
    StreetAddress: "Street address",
    StreetAddressExt: "Street address",
  };

  /** Tokens never contain whitespace; drop any picked up when copying. */
  function cleanToken(token) {
    return String(token || "").replace(/\s+/g, "");
  }

  /**
   * One ArcGIS request. Until a host has accepted the token, a refusal is
   * retried on the other host; the host that works is used from then on.
   * Throws ProviderError if every host refuses, or a network error.
   */
  async function arcgisRequest(address, ctx) {
    const params = {
      SingleLine: address,
      f: "json",
      maxLocations: 5,
      outFields: "Addr_type,Score",
      countryCode: "USA",
      token: cleanToken(ctx.opts.arcgisToken),
    };
    const order = ctx.arcgisHost != null ? [ctx.arcgisHost] : ARCGIS_URLS.map((_, i) => i);
    let firstError = null;
    for (const i of order) {
      const payload = await withRetry(
        () => ctx.transport.postForm(ARCGIS_URLS[i], params),
        ctx.transport,
        2,
        ctx.signal
      );
      // ArcGIS answers token and quota problems with HTTP 200 and an error body.
      if (payload && payload.error) {
        firstError = firstError || payload.error;
        continue;
      }
      ctx.arcgisHost = i;
      return payload;
    }
    throw new ProviderError(arcgisErrorText(firstError));
  }

  /**
   * Check a token with one public test address before a run.
   * Resolves {ok: true} or {ok: false, message}.
   */
  async function checkArcgisToken(transport, token) {
    const ctx = { transport, signal: null, opts: { ...DEFAULTS, arcgisToken: token } };
    if (!cleanToken(token)) return { ok: false, message: "Paste a token first" };
    try {
      await arcgisRequest(ARCGIS_TEST_ADDRESS, ctx);
      return { ok: true };
    } catch (err) {
      if (err instanceof ProviderError) return { ok: false, message: err.message };
      return { ok: false, message: "Could not reach ArcGIS (" + err.message + ")" };
    }
  }

  async function geocodeArcgis(rec, ctx) {
    let payload;
    try {
      payload = await arcgisRequest(rec.address, ctx);
    } catch (err) {
      if (err instanceof Cancelled || err instanceof ProviderError) throw err;
      rec.arcgisTried = true;
      return; // network trouble for this row only; it stays No_Match
    }
    rec.arcgisTried = true;
    const point = A2T.pickArcgis(payload && payload.candidates);
    if (!point) return;
    let tract;
    try {
      tract = await coordsToTract(point.lat, point.lon, ctx);
    } catch (err) {
      if (err instanceof Cancelled) throw err;
      return;
    }
    rec.cleaned_address = "";
    rec.census_tract_geoid = tract;
    rec.latitude = point.lat;
    rec.longitude = point.lon;
    rec.geocoder = "ArcGIS World Geocoder";
    rec.address_reference = "ArcGIS World Geocoder";
    rec.match_precision = ARCGIS_PRECISION[point.type] || point.type || "";
    rec.match_status = "Matched_External";
    rec.error_reason = tract ? "" : REASON_NO_TRACT;
  }

  /**
   * Build one record per input row: rejected rows are final, the rest are
   * pending geocoding. `cols` holds the chosen column names: id plus either
   * address, or street/city/state/zip.
   */
  function buildRecords(rows, cols) {
    return rows.map((row) => {
      const id = A2T.cleanCell(row[cols.id]);
      const address = cols.address
        ? A2T.cleanCell(row[cols.address])
        : A2T.combineAddress(row, cols);
      const rejected = A2T.isBlank(id) || A2T.isBlank(address);
      return {
        row,
        id,
        address,
        cleaned_address: "",
        census_tract_geoid: "",
        latitude: "",
        longitude: "",
        geocoder: "",
        address_reference: "",
        match_precision: "",
        match_status: rejected ? "Rejected" : STATUS_NO_MATCH,
        error_reason: rejected ? "Missing ID or address" : REASON_NO_GEOCODE,
        transientError: false,
      };
    });
  }

  /**
   * Geocode every pending record in place. onProgress({stage, done, total}) is
   * called as work completes; stage is "census", "retry" or "external" (the
   * last only with an ArcGIS token; without one nothing leaves for any
   * service but the Census). onProviderError(message) fires if ArcGIS refuses the
   * token; that stage then stops and the remaining rows stay No_Match.
   */
  async function run(records, { transport, onProgress, onRecord, onProviderError, signal, options } = {}) {
    const ctx = { transport, signal, opts: { ...DEFAULTS, ...(options || {}) } };
    const report = onProgress || (() => {});
    // onRecord(rec) fires each time a record's lookup finishes (live feed).
    const emit = onRecord || (() => {});
    const pending = records.filter((r) => r.match_status === STATUS_NO_MATCH);

    let done = 0;
    await pool(
      pending,
      ctx.opts.concurrency,
      async (rec) => {
        await geocodeCensus(rec, ctx);
        emit(rec);
        report({ stage: "census", done: ++done, total: pending.length });
      },
      signal
    );

    // Extra passes only over network failures, stopping once a pass recovers
    // nothing (a genuine miss is deterministic, so re-asking cannot help).
    for (let pass = 1; pass <= ctx.opts.retryPasses; pass++) {
      const failed = records.filter(
        (r) => r.match_status === STATUS_NO_MATCH && r.transientError
      );
      if (!failed.length) break;
      let n = 0;
      await pool(
        failed,
        ctx.opts.concurrency,
        async (rec) => {
          await geocodeCensus(rec, ctx);
          emit(rec);
          report({ stage: "retry", done: ++n, total: failed.length, pass });
        },
        signal
      );
      if (failed.every((r) => r.match_status === STATUS_NO_MATCH)) break;
    }

    const residual = records
      .filter((r) => r.match_status === STATUS_NO_MATCH)
      .filter((r) => A2T.isGeocodable(r.address) && !A2T.isPoBox(r.address))
      .slice(0, ctx.opts.externalMaxResidual);
    if (residual.length && ctx.opts.arcgisToken) {
      // ArcGIS: parallel workers; the first refusal stops every worker.
      let n = 0;
      let refused = null;
      await pool(
        residual,
        ctx.opts.arcgisConcurrency,
        async (rec) => {
          if (refused) return;
          try {
            await geocodeArcgis(rec, ctx);
          } catch (err) {
            if (!(err instanceof ProviderError)) throw err;
            if (!refused) refused = err.message;
            return;
          }
          emit(rec);
          report({ stage: "external", done: ++n, total: residual.length });
        },
        signal
      );
      if (refused) {
        const reason = "Address could not be geocoded by the Census; " + refused;
        for (const r of residual) {
          if (r.match_status === STATUS_NO_MATCH && !r.arcgisTried) r.error_reason = reason;
        }
        (onProviderError || (() => {}))(refused);
      }
    }

    for (const r of records) {
      delete r.transientError;
      delete r.arcgisTried;
    }
    return records;
  }

  /**
   * Full output rows: ID, the input address column(s), then the results.
   * `processedOn` is the run date (YYYY-MM-DD), repeated on every row so the
   * file still says when and how it was made after it is copied elsewhere.
   */
  function resultRows(records, cols, processedOn = "") {
    const corrected = records.some((r) => r.corrected_address);
    const inputCols = [cols.id].concat(
      cols.address ? [cols.address] : [cols.street, cols.city, cols.state, cols.zip]
    ).filter(Boolean);
    return records.map((r) => {
      const out = {};
      for (const c of inputCols) out[c] = c === cols.id ? r.id : A2T.cleanCell(r.row[c]);
      // Only present when the user fixed an address on the results page.
      if (corrected) out.corrected_address = r.corrected_address || "";
      out.matched_address = r.cleaned_address;
      out.latitude = r.latitude === "" || r.latitude == null ? "" : r.latitude;
      out.longitude = r.longitude === "" || r.longitude == null ? "" : r.longitude;
      out.census_tract_geoid = r.census_tract_geoid;
      out.match_status = r.match_status;
      out.match_precision = r.match_precision || "";
      out.geocoder = r.geocoder || "";
      out.address_reference = r.address_reference || "";
      out.geography_vintage = r.census_tract_geoid ? GEOGRAPHY_VINTAGE : "";
      out.processed_on = processedOn;
      out.error_reason = r.error_reason;
      return out;
    });
  }

  /**
   * Processing summary for JHFRC's quality check: counts only, no IDs, no
   * tracts. meta = {processedOn, arcgis (bool), appVersion}.
   */
  function processingSummary(records, meta = {}) {
    const t = A2T.tally(records);
    const rate = (n, d) => (d ? Math.round((n / d) * 1000) / 10 + "%" : "0%");
    const usable = t.total - t.rejected;
    return [
      { item: "Records in file", value: t.total },
      { item: "Records with an ID and an address", value: usable },
      { item: "Matched to a tract", value: t.matched },
      { item: "Not matched", value: t.unmatched },
      { item: "Rejected (missing ID or address)", value: t.rejected },
      { item: "Match rate, all records", value: rate(t.matched, t.total) },
      { item: "Match rate, records with an ID and an address", value: rate(t.matched, usable) },
      { item: "Matched by the Census, current address file", value: t.census },
      { item: "Matched by the Census, 2020 address file or tidied street", value: t.censusAlt },
      { item: "Matched by ArcGIS", value: t.external },
      { item: "ArcGIS lookup used", value: meta.arcgis ? "Yes" : "No" },
      { item: "Address reference", value: "Census " + BENCHMARKS.map((b) => b[0]).join(", then ") },
      { item: "Tract geography", value: TRACT_GEOGRAPHY + " (" + GEOGRAPHY_VINTAGE + ")" },
      { item: "Processed on", value: meta.processedOn || "" },
      { item: "address2tract web version", value: meta.appVersion || "" },
    ];
  }

  /**
   * Records per tract, busiest first. A tract with fewer than minCount records
   * (0 = show every count) has its count replaced with "<minCount", following
   * whatever small-cell rule the user's data agreement sets.
   */
  function tractSummary(records, minCount = 0) {
    return [...A2T.tractCounts(records)]
      .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
      .map(([g, n]) => ({
        census_tract_geoid: g,
        records: minCount > 0 && n < minCount ? "<" + minCount : n,
        geography_vintage: GEOGRAPHY_VINTAGE,
      }));
  }

  return {
    buildRecords, run, resultRows, processingSummary, tractSummary, checkArcgisToken,
    Cancelled, ProviderError, DEFAULTS, GEOGRAPHY_VINTAGE, TRACT_GEOGRAPHY,
  };
});
