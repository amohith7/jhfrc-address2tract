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
 * throws; `coords(x, y)` returns a GEOID; `nominatim(q)` returns candidates.
 */
function fakeTransport({ census, coords, nominatim }) {
  const calls = { census: [], coords: [], nominatim: [], sleeps: [] };
  return {
    calls,
    jsonp: async (url, p) => {
      if (url.endsWith("/onelineaddress")) {
        calls.census.push([p.address, p.benchmark]);
        return census(p.address, p.benchmark);
      }
      calls.coords.push([p.x, p.y]);
      const geoid = coords ? coords(p.x, p.y) : "";
      return { result: { geographies: { "Census Tracts": geoid ? [{ GEOID: geoid }] : [] } } };
    },
    fetchJson: async (url, p) => {
      calls.nominatim.push(p.q);
      return nominatim ? nominatim(p.q) : [];
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
  assert.equal(t.calls.coords.length, 0);
});

test("2020-file match is re-located on the current tracts", async () => {
  const t = fakeTransport({
    census: (a, b) => (b === CURRENT ? NO_MATCH : censusMatch(-72.6, 41.7, "09003000100", "X")),
    coords: () => "09110000100",
  });
  const recs = await G.run(records([{ id: "1", addr: "1 Elm St, Hartford, CT" }]), { transport: t });
  assert.equal(recs[0].match_status, "Matched_Fallback");
  assert.equal(recs[0].census_tract_geoid, "09110000100");
  assert.deepEqual(t.calls.coords, [[-72.6, 41.7]]);
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

test("residual goes to Nominatim at 1 request/second, skipping PO boxes", async () => {
  const t = fakeTransport({
    census: () => NO_MATCH,
    nominatim: (q) =>
      q.startsWith("10 ")
        ? [{ addresstype: "road", place_rank: 26, lat: "35.1", lon: "-85.2" }]
        : [{ addresstype: "city", place_rank: 16, lat: "35", lon: "-85" }],
    coords: () => "47065011100",
  });
  const recs = await G.run(
    records([
      { id: "a", addr: "10 Rural Rd, Ooltewah, TN" },
      { id: "b", addr: "PO Box 12, Chattanooga, TN" },
      { id: "c", addr: "99 Unknown Way, Chattanooga, TN" },
    ]),
    { transport: t }
  );
  assert.equal(recs[0].match_status, "Matched_External");
  assert.equal(recs[0].census_tract_geoid, "47065011100");
  assert.equal(recs[1].match_status, "No_Match"); // PO box never sent out
  assert.equal(recs[2].match_status, "No_Match"); // city centroid rejected
  assert.deepEqual(t.calls.nominatim, ["10 Rural Rd, Ooltewah, TN", "99 Unknown Way, Chattanooga, TN"]);
  assert.deepEqual(t.calls.sleeps, [1100]);
});

test("a point outside every tract is kept with an explanation", async () => {
  const t = fakeTransport({
    census: () => NO_MATCH,
    nominatim: () => [{ addresstype: "road", place_rank: 27, lat: "30", lon: "-80" }],
    coords: () => "",
  });
  const recs = await G.run(records([{ id: "1", addr: "1 Offshore Pier" }]), { transport: t });
  assert.equal(recs[0].match_status, "Matched_External");
  assert.equal(recs[0].census_tract_geoid, "");
  assert.equal(recs[0].error_reason, "Coordinates found but did not fall within a Census tract boundary");
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
  assert.equal(t.calls.nominatim.length, 0);
});

test("output rows keep the input columns, and the share file has no address", async () => {
  const cols = { id: "id", street: "s", city: "c", state: "st", zip: "z" };
  const t = fakeTransport({ census: () => censusMatch(-85, 35, "47065000100", "M") });
  const recs = await G.run(
    G.buildRecords([{ id: "007", s: "1 Main St", c: "Chattanooga", st: "TN", z: "37403.0", extra: "x" }], cols),
    { transport: t }
  );
  assert.deepEqual(G.resultRows(recs, cols), [
    {
      id: "007",
      s: "1 Main St",
      c: "Chattanooga",
      st: "TN",
      z: "37403",
      cleaned_address: "M",
      census_tract_geoid: "47065000100",
      match_status: "Matched",
      error_reason: "",
    },
  ]);
  assert.deepEqual(G.shareRows(recs, cols), [
    { id: "007", census_tract_geoid: "47065000100", match_status: "Matched" },
  ]);
  assert.equal(t.calls.census[0][0], "1 Main St, Chattanooga, TN, 37403");
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
  assert.ok(!("corrected_address" in G.shareRows(recs, COLS)[1]));
});
