// Unit tests for docs/assets/core.js. Run with: node --test tests/web
const test = require("node:test");
const assert = require("node:assert/strict");
const A2T = require("../../docs/assets/core.js");

test("cleanCell trims and drops Excel's trailing .0", () => {
  assert.equal(A2T.cleanCell("  001  "), "001");
  assert.equal(A2T.cleanCell("30720.0"), "30720");
  assert.equal(A2T.cleanCell("12.5"), "12.5");
  assert.equal(A2T.cleanCell(null), "");
  assert.equal(A2T.cleanCell(undefined), "");
});

test("normalizeZip restores leading zeros and keeps ZIP+4", () => {
  assert.equal(A2T.normalizeZip("37403.0"), "37403");
  assert.equal(A2T.normalizeZip("7030"), "07030");
  assert.equal(A2T.normalizeZip("37403-1234"), "37403-1234");
  assert.equal(A2T.normalizeZip("374031234"), "37403-1234");
  assert.equal(A2T.normalizeZip("nan"), "");
  assert.equal(A2T.normalizeZip(""), "");
});

test("combineAddress joins present parts and cleans the ZIP", () => {
  const row = { s: "615 McCallie Ave", c: "Chattanooga", st: "TN", z: "37403.0" };
  const cols = { street: "s", city: "c", state: "st", zip: "z" };
  assert.equal(A2T.combineAddress(row, cols), "615 McCallie Ave, Chattanooga, TN, 37403");
  assert.equal(A2T.combineAddress({ s: "1 Main St", c: " ", st: "nan", z: "" }, cols), "1 Main St");
  assert.equal(A2T.combineAddress({ s: "", c: "", st: "", z: "" }, cols), "");
});

test("normalizeStreet expands only a trailing suffix and fuses initials", () => {
  assert.equal(A2T.normalizeStreet("100 Old Hickory Pk"), "100 Old Hickory Pike");
  assert.equal(A2T.normalizeStreet("12 St Louis Ave."), "12 St Louis Avenue");
  assert.equal(A2T.normalizeStreet("5 B.B. King  Blvd"), "5 BB King Boulevard");
  assert.equal(A2T.normalizeStreet("9 N. Main"), "9 N. Main");
  assert.equal(A2T.normalizeStreet("   "), "");
});

test("normalizeAddressStreet only touches the part before the first comma", () => {
  assert.equal(
    A2T.normalizeAddressStreet("742 Evergreen Ter, Hartford, CT 06105"),
    "742 Evergreen Terrace, Hartford, CT 06105"
  );
  // No comma: the state code must not be read as a street suffix.
  assert.equal(A2T.normalizeAddressStreet("742 Evergreen Ter Hartford CT"), "742 Evergreen Ter Hartford CT");
});

test("checkForPhi matches whole tokens like the Python validator", () => {
  assert.deepEqual(
    A2T.checkForPhi(["client_id", "Patient Name", "filename", "DOB", "street", "home_phone", "zip"]),
    ["Patient Name", "DOB", "home_phone"]
  );
  assert.deepEqual(A2T.checkForPhi(["id", "full_address", "city", "state"]), []);
});

test("findDuplicateIds ignores rows that would be rejected", () => {
  const rows = [
    { id: "1", a: "1 Main St" },
    { id: "1", a: "2 Main St" },
    { id: "2", a: "3 Main St" },
    { id: "2", a: "" }, // blank address: rejected, so not a duplicate
    { id: "", a: "4 Main St" },
    { id: "", a: "5 Main St" }, // blank IDs never count
  ];
  assert.deepEqual(A2T.findDuplicateIds(rows, "id", ["a"]), ["1"]);
});

test("isGeocodable and isPoBox guard what goes to the external geocoder", () => {
  assert.equal(A2T.isGeocodable("1 Main St"), true);
  assert.equal(A2T.isGeocodable("  "), false);
  assert.equal(A2T.isGeocodable("nan"), false);
  assert.equal(A2T.isGeocodable(",,,"), false);
  assert.equal(A2T.isPoBox("P.O. Box 12, Chattanooga, TN"), true);
  assert.equal(A2T.isPoBox("PO Box 9"), true);
  assert.equal(A2T.isPoBox("Post Office Box 4"), true);
  assert.equal(A2T.isPoBox("12 Boxwood Ln"), false);
});

test("pickNominatim skips centroid and low-precision candidates", () => {
  const city = { addresstype: "city", place_rank: 16, lat: "1", lon: "1" };
  const road = { addresstype: "road", place_rank: 26, lat: "35.04", lon: "-85.29" };
  const poi = { addresstype: "amenity", place_rank: 30, lat: "2", lon: "2" };
  assert.deepEqual(A2T.pickNominatim([city, road]), { lat: 35.04, lon: -85.29 });
  assert.deepEqual(A2T.pickNominatim([poi]), { lat: 2, lon: 2 });
  assert.equal(A2T.pickNominatim([city]), null);
  assert.equal(A2T.pickNominatim([]), null);
});

test("cleanGeoid pads to 11 digits and rejects junk", () => {
  assert.equal(A2T.cleanGeoid("47065012400"), "47065012400");
  assert.equal(A2T.cleanGeoid("1001020100"), "01001020100");
  assert.equal(A2T.cleanGeoid(""), "");
  assert.equal(A2T.cleanGeoid("123456789012"), "");
});

test("guessColumns recognizes common header names", () => {
  const g = A2T.guessColumns(["Client ID", "Street Address", "City", "State", "ZIP Code"]);
  assert.deepEqual(g, {
    id: "Client ID",
    address: "",
    street: "Street Address",
    city: "City",
    state: "State",
    zip: "ZIP Code",
  });
});
