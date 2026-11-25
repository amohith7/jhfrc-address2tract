// Pipeline tests for docs/assets/geocoder.js with a fake, offline transport.
const test = require("node:test");
const assert = require("node:assert/strict");
const G = require("../../docs/assets/geocoder.js");

const CURRENT = "Public_AR_Current";

function censusMatch(x, y, geoid, matchedAddress) {
  return {
    result: {
      addressMatches: [
        {
          matchedAddress,
          coordinates: { x, y },
          geographies: geoid ? { "Census Tracts": [{ GEOID: geoid }] } : {},
        },
      ],
    },
  };
}
const NO_MATCH = { result: { addressMatches: [] } };

/**
 * Fake transport. `census(address, benchmark)` returns a JSONP payload or
 * throws; `coords(x, y)` returns a GEOID. Any other request (fetchJson, as
 * the old OpenStreetMap step used) is recorded so tests can prove it never
 * happens.
 */
function fakeTransport({ census, coords }) {
  const calls = { census: [], coords: [], vintages: new Set(), other: [], sleeps: [] };
  return {
    calls,
    jsonp: async (url, p) => {
      calls.vintages.add(p.vintage);
      if (url.endsWith("/onelineaddress")) {
        calls.census.push([p.address, p.benchmark]);
        return census(p.address, p.benchmark);
      }
      calls.coords.push([p.x, p.y]);
      const geoid = coords ? coords(p.x, p.y) : "";
      return { result: { geographies: { "Census Tracts": geoid ? [{ GEOID: geoid }] : [] } } };
    },
    fetchJson: async (url) => {
      calls.other.push(url);
      return [];
    },
    sleep: async (ms) => {
      calls.sleeps.push(ms);
    },
  };
}

const COLS = { id: "id", address: "addr" };
const records = (rows) => G.buildRecords(rows, COLS);

test("current-file match takes the tract straight from the Census response", async () => {
  const t = fakeTransport({
    census: () => censusMatch(-85.29, 35.04, "47065012400", "615 MCCALLIE AVE, CHATTANOOGA, TN, 37403"),
  });
  const recs = await G.run(records([{ id: "1", addr: "615 McCallie Ave, Chattanooga, TN 37403" }]), { transport: t });
  assert.equal(recs[0].match_status, "Matched");
  assert.equal(recs[0].census_tract_geoid, "47065012400");
  assert.equal(recs[0].cleaned_address, "615 MCCALLIE AVE, CHATTANOOGA, TN, 37403");
  assert.equal(recs[0].error_reason, "");
  assert.equal(recs[0].latitude, 35.04);
  assert.equal(recs[0].longitude, -85.29);
  assert.equal(recs[0].geocoder, "U.S. Census Bureau");
  assert.equal(recs[0].address_reference, CURRENT);
  assert.equal(t.calls.coords.length, 0);
});

test("every Census request asks for 2020 tract geography", async () => {
  const t = fakeTransport({
    census: (a, b) => (b === CURRENT ? NO_MATCH : censusMatch(-85.3, 35.0, "", "X")),
    coords: () => "47065012400",
  });
  await G.run(records([{ id: "1", addr: "1 Elm St, Chattanooga, TN" }]), { transport: t });
  assert.deepEqual([...t.calls.vintages].sort(), ["Census2020_Census2020", "Census2020_Current"]);
  assert.equal(G.GEOGRAPHY_VINTAGE, "Census2020_Current");
});

test("a 2020-address-file match takes its 2020 tract from the response", async () => {
  const t = fakeTransport({
    census: (a, b) => (b === CURRENT ? NO_MATCH : censusMatch(-72.6, 41.7, "09003000100", "X")),
    coords: () => {
      throw new Error("no point lookup needed");
    },
  });
  const recs = await G.run(records([{ id: "1", addr: "1 Elm St, Hartford, CT" }]), { transport: t });
  assert.equal(recs[0].match_status, "Matched_Fallback");
  assert.equal(recs[0].census_tract_geoid, "09003000100");
  assert.equal(recs[0].address_reference, "Public_AR_Census2020");
  assert.equal(t.calls.coords.length, 0);
});

test("a match whose response carries no tract is looked up from its point", async () => {
  const t = fakeTransport({
    census: () => censusMatch(-85.2, 35.1, "", "X"),
    coords: () => "47065011100",
  });
  const recs = await G.run(records([{ id: "1", addr: "1 Elm St, Chattanooga, TN" }]), { transport: t });
  assert.equal(recs[0].census_tract_geoid, "47065011100");
  assert.deepEqual(t.calls.coords, [[-85.2, 35.1]]);
});

test("normalized-street retry recovers an abbreviation miss", async () => {
  const t = fakeTransport({
    census: (a, b) =>
      a.startsWith("100 Old Hickory Pike") && b === CURRENT ? censusMatch(-86.7, 36.1, "47037018000", "Y") : NO_MATCH,
  });
  const recs = await G.run(records([{ id: "1", addr: "100 Old Hickory Pk, Nashville, TN" }]), { transport: t });
  assert.equal(recs[0].match_status, "Matched_Fallback");
  assert.equal(recs[0].census_tract_geoid, "47037018000");
});

test("a network failure is retried in a later pass, a genuine miss is not", async () => {
  let failures = 0;
  const t = fakeTransport({
    census: (a) => {
      if (a.startsWith("1 ")) {
        if (failures++ < 6) throw new Error("network");
        return censusMatch(-85, 35, "47065000100", "A");
      }
      return NO_MATCH;
    },
  });
  const recs = await G.run(
    records([
      { id: "1", addr: "1 Flaky Rd" },
      { id: "2", addr: "2 Nowhere Rd" },
    ]),
    { transport: t, options: { externalMaxResidual: 0 } }
  );
  assert.equal(recs[0].match_status, "Matched");
  assert.equal(recs[1].match_status, "No_Match");
  assert.equal(recs[1].error_reason, "Address could not be geocoded");
  // The genuine miss was asked once per benchmark (2), never re-asked by a retry pass.
  assert.equal(t.calls.census.filter(([a]) => a.startsWith("2 ")).length, 2);
  assert.ok(!("transientError" in recs[0]));
});

test("without an ArcGIS token, nothing is sent anywhere but the Census", async () => {
  const t = fakeTransport({ census: () => NO_MATCH });
  t.postForm = async () => {
    throw new Error("ArcGIS must not be called");
  };
  const recs = await G.run(
    records([
      { id: "a", addr: "10 Rural Rd, Ooltewah, TN" },
      { id: "b", addr: "99 Unknown Way, Chattanooga, TN" },
    ]),
    { transport: t }
  );
  assert.deepEqual(recs.map((r) => r.match_status), ["No_Match", "No_Match"]);
  assert.equal(t.calls.other.length, 0);
  assert.equal(t.calls.sleeps.length, 0);
});

test("rows missing an ID or address are rejected and never geocoded", async () => {
  const t = fakeTransport({ census: () => NO_MATCH });
  const recs = await G.run(
    records([
      { id: "", addr: "1 Main St" },
      { id: "2", addr: "  " },
      { id: "3", addr: "nan" },
    ]),
    { transport: t }
  );
  assert.deepEqual(recs.map((r) => r.match_status), ["Rejected", "Rejected", "Rejected"]);
  assert.equal(recs[0].error_reason, "Missing ID or address");
  assert.equal(t.calls.census.length, 0);
  assert.equal(t.calls.other.length, 0);
});

test("output rows keep the input columns and add provider, precision and vintage", async () => {
  const cols = { id: "id", street: "s", city: "c", state: "st", zip: "z" };
  const t = fakeTransport({ census: () => censusMatch(-85, 35, "47065000100", "M") });
  const recs = await G.run(
    G.buildRecords([{ id: "007", s: "1 Main St", c: "Chattanooga", st: "TN", z: "37403.0", extra: "x" }], cols),
    { transport: t }
  );
  assert.deepEqual(G.resultRows(recs, cols, "2026-11-25"), [
    {
      id: "007",
      s: "1 Main St",
      c: "Chattanooga",
      st: "TN",
      z: "37403",
      matched_address: "M",
      latitude: 35,
      longitude: -85,
      census_tract_geoid: "47065000100",
      match_status: "Matched",
      match_precision: "Street address",
      geocoder: "U.S. Census Bureau",
      address_reference: "Public_AR_Current",
      geography_vintage: "Census2020_Current",
      processed_on: "2026-11-25",
      error_reason: "",
    },
  ]);
  assert.equal(t.calls.census[0][0], "1 Main St, Chattanooga, TN, 37403");
});

test("the processing summary has counts only, and the tract summary can hide small counts", async () => {
  const t = fakeTransport({
    census: (a) => (a.startsWith("9") ? NO_MATCH : censusMatch(-85, 35, a.startsWith("1") ? "47065000100" : "47065000200", "M")),
  });
  const rows = [
    { id: "MRN-1", addr: "1 A St" },
    { id: "MRN-2", addr: "1 B St" },
    { id: "MRN-3", addr: "1 C St" },
    { id: "MRN-4", addr: "2 D St" },
    { id: "MRN-5", addr: "9 Nowhere" },
    { id: "", addr: "1 E St" },
  ];
  const recs = await G.run(records(rows), { transport: t });
  const summary = G.processingSummary(recs, { processedOn: "2026-11-25", arcgis: false, appVersion: "v" });
  const text = JSON.stringify(summary);
  assert.ok(!/MRN|470650/.test(text), "no IDs or tracts in the processing summary");
  const get = (label) => summary.find((r) => r.item === label).value;
  assert.equal(get("Records in file"), 6);
  assert.equal(get("Matched to a tract"), 4);
  assert.equal(get("Match rate, all records"), "66.7%");
  assert.equal(get("Match rate, records with an ID and an address"), "80%");
  assert.equal(get("ArcGIS lookup used"), "No");
  assert.match(get("Tract geography"), /Census2020_Current/);

  assert.deepEqual(G.tractSummary(recs), [
    { census_tract_geoid: "47065000100", records: 3, geography_vintage: "Census2020_Current" },
    { census_tract_geoid: "47065000200", records: 1, geography_vintage: "Census2020_Current" },
  ]);
  assert.deepEqual(G.tractSummary(recs, 5).map((r) => r.records), ["<5", "<5"]);
  assert.deepEqual(G.tractSummary(recs, 2).map((r) => r.records), [3, "<2"]);
});

test("cancelling stops the run", async () => {
  const controller = new AbortController();
  const t = fakeTransport({
    census: () => {
      controller.abort();
      return NO_MATCH;
    },
  });
  const rows = Array.from({ length: 20 }, (_, i) => ({ id: String(i), addr: i + " Main St" }));
  await assert.rejects(G.run(records(rows), { transport: t, signal: controller.signal }), G.Cancelled);
  assert.ok(t.calls.census.length < 40);
});

test("onRecord fires once per finished lookup, and a corrected address is reported", async () => {
  const t = fakeTransport({
    census: (a) => (a.startsWith("615 ") ? censusMatch(-85.3, 35.04, "47065012400", "615 MCCALLIE AVE") : NO_MATCH),
  });
  const recs = records([
    { id: "1", addr: "615 McCallie Ave, Chattanooga, TN" },
    { id: "2", addr: "61 McCalie Av, Chattanooga, TN" },
  ]);
  const seen = [];
  await G.run(recs, { transport: t, onRecord: (r) => seen.push(r.id), options: { externalMaxResidual: 0 } });
  assert.deepEqual(seen.sort(), ["1", "2"]);

  // The user fixes row 2 on the results page and retries only that row.
  recs[1].address = recs[1].corrected_address = "615 McCallie Ave, Chattanooga, TN";
  await G.run([recs[1]], { transport: t, options: { externalMaxResidual: 0 } });
  assert.equal(recs[1].match_status, "Matched");
  const out = G.resultRows(recs, COLS);
  assert.equal(out[0].corrected_address, "");
  assert.equal(out[1].corrected_address, "615 McCallie Ave, Chattanooga, TN");
  assert.equal(out[1].addr, "61 McCalie Av, Chattanooga, TN"); // original input kept
});

// ------------------------------------------------------------------ ArcGIS

function arcgisCandidate(x, y, addrType = "PointAddress", score = 98) {
  return { location: { x, y }, score, attributes: { Addr_type: addrType, Score: score } };
}

/** Census misses everything; ArcGIS answers through postForm. */
function arcgisTransport(arcgis) {
  const t = fakeTransport({ census: () => NO_MATCH, coords: () => "47065012400" });
  t.calls.arcgis = [];
  t.postForm = async (url, p) => {
    t.calls.arcgis.push({ url, p });
    return arcgis(p.SingleLine);
  };
  return t;
}

test("with a token, ArcGIS looks up the Census misses and gets the token in the POST body", async () => {
  const t = arcgisTransport(() => ({ candidates: [arcgisCandidate(-85.29, 35.04)] }));
  const recs = await G.run(records([{ id: "1", addr: "615 McCallie Ave, Chattanooga, TN 37403" }]), {
    transport: t,
    options: { arcgisToken: "TEST-TOKEN" },
  });
  assert.equal(recs[0].match_status, "Matched_External");
  assert.equal(recs[0].census_tract_geoid, "47065012400");
  assert.equal(recs[0].geocoder, "ArcGIS World Geocoder");
  assert.equal(recs[0].match_precision, "Rooftop or parcel");
  assert.deepEqual([recs[0].latitude, recs[0].longitude], [35.04, -85.29]);
  assert.ok(t.calls.vintages.has("Census2020_Current"), "ArcGIS points get 2020 tracts too");
  assert.equal(t.calls.other.length, 0);
  assert.equal(t.calls.arcgis.length, 1);
  const { url, p } = t.calls.arcgis[0];
  assert.equal(p.token, "TEST-TOKEN");
  assert.ok(!url.includes("TEST-TOKEN") && !url.includes("?"), "token must not be in the URL");
  assert.equal(p.countryCode, "USA");
  assert.equal(t.calls.sleeps.length, 0, "no 1-second spacing for ArcGIS");
});

test("ArcGIS candidates below street precision or score 90 are not accepted", async () => {
  const t = arcgisTransport(() => ({
    candidates: [arcgisCandidate(1, 1, "Locality", 100), arcgisCandidate(2, 2, "PointAddress", 85)],
  }));
  const recs = await G.run(records([{ id: "1", addr: "1 Main St, Somewhere, TN" }]), {
    transport: t,
    options: { arcgisToken: "TEST-TOKEN" },
  });
  assert.equal(recs[0].match_status, "No_Match");
  assert.equal(recs[0].error_reason, "Address could not be geocoded");
});

test("a refused token stops the ArcGIS step and is reported, not hidden as No_Match", async () => {
  const t = arcgisTransport(() => ({ error: { code: 498, message: "Invalid token." } }));
  const rows = Array.from({ length: 10 }, (_, i) => ({ id: String(i), addr: i + " Main St, Town, TN" }));
  const errors = [];
  const recs = await G.run(records(rows), {
    transport: t,
    options: { arcgisToken: "BAD", arcgisConcurrency: 2 },
    onProviderError: (m) => errors.push(m),
  });
  assert.equal(errors.length, 1);
  assert.match(errors[0], /did not accept the token \(ArcGIS said: 498 Invalid token\.\)/);
  assert.ok(t.calls.arcgis.length <= 4, "workers stop after the first refusal (2 workers x 2 hosts)");
  for (const r of recs) {
    assert.equal(r.match_status, "No_Match");
    assert.match(r.error_reason, /did not accept the token/);
    assert.equal(r.arcgisTried, undefined);
  }
});

test("an ArcGIS point outside every tract is kept with an explanation", async () => {
  const t = fakeTransport({ census: () => NO_MATCH, coords: () => "" });
  t.postForm = async () => ({ candidates: [arcgisCandidate(-80, 30, "StreetAddress", 95)] });
  const recs = await G.run(records([{ id: "1", addr: "1 Offshore Pier, Town, FL" }]), {
    transport: t,
    options: { arcgisToken: "T" },
  });
  assert.equal(recs[0].match_status, "Matched_External");
  assert.equal(recs[0].census_tract_geoid, "");
  assert.equal(recs[0].error_reason, "Coordinates found but did not fall within a Census tract boundary");
});

test("a token refused by geocode-api is retried on geocode.arcgis.com, which is then kept", async () => {
  const t = fakeTransport({ census: () => NO_MATCH, coords: () => "47065012400" });
  t.calls.arcgis = [];
  t.postForm = async (url, p) => {
    t.calls.arcgis.push({ url, token: p.token });
    if (url.startsWith("https://geocode-api.")) return { error: { code: 498, message: "Invalid Token" } };
    return { candidates: [arcgisCandidate(-85.29, 35.04)] };
  };
  const rows = [1, 2, 3].map((i) => ({ id: String(i), addr: i + " Main St, Town, TN" }));
  const recs = await G.run(records(rows), {
    transport: t,
    options: { arcgisToken: "  AAPK-line1\n line2  ", arcgisConcurrency: 1 },
  });
  assert.ok(recs.every((r) => r.match_status === "Matched_External"));
  assert.equal(t.calls.arcgis[0].url.startsWith("https://geocode-api."), true);
  assert.equal(t.calls.arcgis.length, 4, "one refused probe, then the working host for all three");
  assert.ok(t.calls.arcgis.slice(1).every((c) => c.url.startsWith("https://geocode.arcgis.com")));
  assert.ok(t.calls.arcgis.every((c) => c.token === "AAPK-line1line2"), "whitespace removed from the token");
});

test("checkArcgisToken reports success, ArcGIS's own error, and network trouble", async () => {
  const ok = { postForm: async () => ({ candidates: [] }), sleep: async () => {} };
  assert.deepEqual(await G.checkArcgisToken(ok, "T"), { ok: true });
  const bad = { postForm: async () => ({ error: { code: 403, message: "User does not have permissions.", details: [] } }), sleep: async () => {} };
  const r = await G.checkArcgisToken(bad, "T");
  assert.equal(r.ok, false);
  assert.match(r.message, /geocoding rights or credits \(ArcGIS said: 403 User does not have permissions\.\)/);
  const down = { postForm: async () => { throw new Error("Failed to fetch"); }, sleep: async () => {} };
  assert.match((await G.checkArcgisToken(down, "T")).message, /Could not reach ArcGIS/);
  assert.equal((await G.checkArcgisToken(ok, "   ")).ok, false);
});
