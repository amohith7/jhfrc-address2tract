/*
 * Built-in sample file for "Try it with sample data". Public landmark
 * addresses only (no client data). The first 11 were matched exactly by the
 * Census geocoder when the test set was built; S12 is a PO box (never
 * matched) and S13 is a made-up address, so the fix-and-retry step has
 * something to show: correct it to "615 McCallie Ave" and retry.
 */
(function (root) {
  "use strict";
  const columns = ["client_id", "street", "city", "state", "zip"];
  const data = [
    ["S01", "1400 Market St", "Chattanooga", "TN", "37402"],
    ["S02", "10 Bluff View Ave", "Chattanooga", "TN", "37403"],
    ["S03", "116 5th Ave N", "Nashville", "TN", "37219"],
    ["S04", "3764 Elvis Presley Blvd", "Memphis", "TN", "38116"],
    ["S05", "225 Baker St NW", "Atlanta", "GA", "30313"],
    ["S06", "1400 Patten Rd", "Lookout Mountain", "GA", "30750"],
    ["S07", "600 Dexter Ave", "Montgomery", "AL", "36104"],
    ["S08", "1701 Valley View Dr", "Birmingham", "AL", "35209"],
    ["S09", "1 Lodge St", "Asheville", "NC", "28803"],
    ["S10", "401 Chapel Dr", "Durham", "NC", "27708"],
    ["S11", "1600 Pennsylvania Ave NW", "Washington", "DC", "20500"],
    ["S12", "PO Box 100", "Chattanooga", "TN", "37401"],
    ["S13", "99 Nowhere Lane", "Chattanooga", "TN", "37403"],
  ];
  root.A2TSample = {
    fileName: "sample_addresses.xlsx",
    columns,
    rows: data.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]]))),
  };
})(typeof self !== "undefined" ? self : this);
