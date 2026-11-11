/*
 * address2tract web: geocoding pipeline.
 *
 * Same order as the Python tool's single-address path (src/geocode.py and
 * src/geocode_external.py):
 *   1. Census geocoder, current address file, then the 2020 address file.
 *   2. One retry with a conservatively normalized street.
 *   3. Extra passes over records whose lookups failed on a network error.
 *   4. Nominatim (OpenStreetMap) for the residual, at most 1 request/second.
 * The tract always comes from the Census Bureau's current tract geography.
 *
 * Network access goes through an injected `transport`, so tests can run the
 * pipeline offline:
 *   transport.jsonp(url, params) -> Promise<object>   (Census, JSONP)
 *   transport.fetchJson(url, params) -> Promise<any>  (Nominatim, CORS)
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
  const NOMINATIM_URL = "https://nominatim.openstreetmap.org/search";

  // (benchmark, vintage) pairs swept in order (geocode.FALLBACK_BENCHMARKS).
  const BENCHMARKS = [
    ["Public_AR_Current", "Current_Current"],
    ["Public_AR_Census2020", "Census2020_Census2020"],
  ];

  const DEFAULTS = {
    concurrency: 6, // config.yaml geocoder.concurrency
    retries: 3, // attempts per request on a network error
    retryPasses: 2, // config.yaml geocoder.retry_passes
    externalDelayMs: 1100, // Nominatim policy: <= 1 request/second
    externalMaxResidual: 2000, // config.yaml external_max_residual
  };

  const STATUS_NO_MATCH = "No_Match";
  const REASON_NO_GEOCODE = "Address could not be geocoded";
  const REASON_NO_TRACT =
    "Coordinates found but did not fall within a Census tract boundary";

  class Cancelled extends Error {}

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

  /** Current-vintage tract GEOID for a point, or "" if it is in no tract. */
  async function coordsToTract(lat, lon, ctx) {
    const data = await withRetry(
      () =>
        ctx.transport.jsonp(COORDS_URL, {
          x: lon,
          y: lat,
          benchmark: "Public_AR_Current",
          vintage: "Current_Current",
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
    // The current benchmark's vintage carries the current tract directly. A
    // 2020-file match is re-located on the current tracts from its point.
    let tract = res.benchmarkIndex === 0 ? tractFrom(m.geographies) : "";
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

  async function geocodeExternal(rec, ctx) {
    const query = {
      q: rec.address,
      format: "jsonv2",
      limit: 5,
      countrycodes: "us",
      addressdetails: 0,
    };
    let candidates;
    try {
      candidates = await withRetry(
        () => ctx.transport.fetchJson(NOMINATIM_URL, query),
        ctx.transport,
        2,
        ctx.signal
      );
    } catch (err) {
      if (err instanceof Cancelled) throw err;
      return;
    }
    const point = A2T.pickNominatim(candidates);
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
        match_status: rejected ? "Rejected" : STATUS_NO_MATCH,
        error_reason: rejected ? "Missing ID or address" : REASON_NO_GEOCODE,
        transientError: false,
      };
    });
  }

  /**
   * Geocode every pending record in place. onProgress({stage, done, total}) is
   * called as work completes; stage is "census", "retry" or "external".
   */
  async function run(records, { transport, onProgress, signal, options } = {}) {
    const ctx = { transport, signal, opts: { ...DEFAULTS, ...(options || {}) } };
    const report = onProgress || (() => {});
    const pending = records.filter((r) => r.match_status === STATUS_NO_MATCH);

    let done = 0;
    await pool(
      pending,
      ctx.opts.concurrency,
      async (rec) => {
        await geocodeCensus(rec, ctx);
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
    for (let i = 0; i < residual.length; i++) {
      checkCancel(signal);
      if (i > 0) await transport.sleep(ctx.opts.externalDelayMs);
      await geocodeExternal(residual[i], ctx);
      report({ stage: "external", done: i + 1, total: residual.length });
    }

    for (const r of records) delete r.transientError;
    return records;
  }

  /** Full output rows: ID, the input address column(s), then the results. */
  function resultRows(records, cols) {
    const inputCols = [cols.id].concat(
      cols.address ? [cols.address] : [cols.street, cols.city, cols.state, cols.zip]
    ).filter(Boolean);
    return records.map((r) => {
      const out = {};
      for (const c of inputCols) out[c] = c === cols.id ? r.id : A2T.cleanCell(r.row[c]);
      out.cleaned_address = r.cleaned_address;
      out.census_tract_geoid = r.census_tract_geoid;
      out.match_status = r.match_status;
      out.error_reason = r.error_reason;
      return out;
    });
  }

  /** De-identified rows to share with JHFRC: ID, tract and status only. */
  function shareRows(records, cols) {
    return records.map((r) => ({
      [cols.id]: r.id,
      census_tract_geoid: r.census_tract_geoid,
      match_status: r.match_status,
    }));
  }

  return { buildRecords, run, resultRows, shareRows, Cancelled, DEFAULTS };
});
