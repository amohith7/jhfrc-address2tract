/*
 * address2tract web: pure helper logic (no network, no DOM).
 *
 * Each function mirrors a rule in the Python tool so the website and the
 * command-line tool clean, validate and label records the same way. The Python
 * source a function is ported from is named in its comment. Loaded in the
 * browser as window.A2T, and in Node (tests) via require().
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.A2T = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Whole number Excel stored as a float, e.g. "30720.0" (src/utils/io.py).
  const INT_DOT_ZERO = /^(-?\d+)\.0$/;

  /** Trim and drop Excel's trailing ".0" (io.normalize_text_columns). Blank -> "". */
  function cleanCell(value) {
    if (value === null || value === undefined) return "";
    let s = String(value).trim();
    const m = INT_DOT_ZERO.exec(s);
    if (m) s = m[1];
    return s;
  }

  /** True for blank or NaN-sentinel text that must never be geocoded. */
  function isBlank(value) {
    const s = cleanCell(value).toLowerCase();
    return s === "" || s === "nan" || s === "none" || s === "null";
  }

  /** 5-digit (or ZIP+4) ZIP with leading zeros restored (geocode.normalize_zip). */
  function normalizeZip(value) {
    if (isBlank(value)) return "";
    let s = String(value).trim().replace(/\.0+$/, "");
    const m = /(\d{5})(?:[-\s]?(\d{4}))?/.exec(s);
    if (!m) {
      const digits = s.replace(/\D/g, "");
      return digits ? digits.padStart(5, "0") : "";
    }
    return m[2] ? m[1] + "-" + m[2] : m[1];
  }

  /** Join street/city/state/zip into one line (main._combine_address_fields). */
  function combineAddress(row, cols) {
    const parts = [];
    for (const key of ["street", "city", "state", "zip"]) {
      const col = cols[key];
      if (!col) continue;
      let value = cleanCell(row[col]);
      if (isBlank(value)) continue;
      if (key === "zip") value = normalizeZip(value);
      if (value) parts.push(value);
    }
    return parts.join(", ");
  }

  // Street-type suffixes expanded only as the LAST token (normalize.py).
  const SUFFIX_EXPANSIONS = {
    st: "Street", ave: "Avenue", av: "Avenue", blvd: "Boulevard", dr: "Drive",
    rd: "Road", ln: "Lane", ct: "Court", pl: "Place", pkwy: "Parkway",
    pky: "Parkway", hwy: "Highway", sq: "Square", ter: "Terrace", trl: "Trail",
    cir: "Circle", pk: "Pike",
  };

  /** Conservative street cleanup used only on retries (normalize.normalize_street). */
  function normalizeStreet(street) {
    let s = String(street || "").split(/\s+/).filter(Boolean).join(" ");
    if (!s) return "";
    s = s.replace(/\b([A-Za-z])\.([A-Za-z])\./g, "$1$2 ");
    const parts = s.split(" ").filter(Boolean);
    const last = parts[parts.length - 1].replace(/\.+$/, "").toLowerCase();
    if (Object.prototype.hasOwnProperty.call(SUFFIX_EXPANSIONS, last)) {
      parts[parts.length - 1] = SUFFIX_EXPANSIONS[last];
    }
    return parts.join(" ");
  }

  /**
   * Normalize only the street part of a one-line address
   * (geocode._normalize_address_street). With no comma the street cannot be
   * isolated safely, so the address is returned unchanged.
   */
  function normalizeAddressStreet(address) {
    const addr = String(address);
    const i = addr.indexOf(",");
    if (i === -1) return addr;
    return normalizeStreet(addr.slice(0, i)) + addr.slice(i);
  }

  // Column-name indicators of PHI (src/phi_validator.py).
  const PHI_INDICATORS = [
    "name", "first_name", "last_name", "firstname", "lastname",
    "patient_name", "patientname", "full_name", "fullname",
    "date_of_birth", "dob", "birth_date", "birthdate",
    "ssn", "social_security", "social_security_number",
    "medical_record_number", "mrn",
    "diagnosis", "diagnoses",
    "insurance", "insurance_id",
    "phone", "phone_number", "telephone",
    "email", "email_address",
    "notes", "clinical_notes",
  ];

  function matchesPhi(column, indicator) {
    const colNorm = String(column).toLowerCase().trim().replace(/ /g, "_");
    const ind = indicator.toLowerCase().replace(/ /g, "_").split("_");
    const colParts = colNorm.split("_");
    if (colNorm === ind.join("_")) return true;
    for (let i = 0; i + ind.length <= colParts.length; i++) {
      if (ind.every((p, j) => colParts[i + j] === p)) return true;
    }
    return false;
  }

  /** Column names that look like PHI (phi_validator.check_for_phi). */
  function checkForPhi(columns) {
    return columns.filter((c) => PHI_INDICATORS.some((ind) => matchesPhi(c, ind)));
  }

  // Cell contents that look like personal details. The column-name screen
  // above cannot see these when they sit under an innocent header such as
  // "contact", or are typed into the address itself.
  const CONTENT_PATTERNS = [
    ["an email address", /[^\s@,;]+@[^\s@,;]+\.[A-Za-z]{2,}/],
    // 3-3-4 digits with separators, so a ZIP+4 or a plain numeric ID is not hit.
    ["a phone number", /(?:\(\d{3}\)\s?|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b/],
    ["a Social Security number", /\b\d{3}-\d{2}-\d{4}\b/],
    ["a date (such as a date of birth)", /\b\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4})\b|\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b/],
    // Words, a comma, then a house number: "Jane Testcase, 615 McCallie Ave".
    ["a name in front of the address", /^\s*[A-Za-z][A-Za-z .'-]*,\s*\d/],
  ];

  /**
   * Cells that look like personal details, grouped by column and kind:
   * [{column, kind, rows: [1-based spreadsheet row numbers, header = row 1]}].
   * Values are never returned, so the warning itself does not repeat them.
   */
  function scanCellsForPhi(rows, columns) {
    const found = new Map();
    rows.forEach((row, i) => {
      for (const col of columns) {
        const text = cleanCell(row[col]);
        if (!text) continue;
        for (const [kind, re] of CONTENT_PATTERNS) {
          if (!re.test(text)) continue;
          const key = col + "\u0000" + kind;
          if (!found.has(key)) found.set(key, { column: col, kind, rows: [] });
          found.get(key).rows.push(i + 2);
        }
      }
    });
    return [...found.values()];
  }

  /**
   * Duplicate IDs among rows that will actually be geocoded (io.scan_input):
   * rows with a blank ID or a fully blank address are ignored.
   */
  function findDuplicateIds(rows, idCol, addressCols) {
    const seen = new Map();
    for (const row of rows) {
      const id = cleanCell(row[idCol]);
      if (!id) continue;
      if (addressCols.length && addressCols.every((c) => isBlank(row[c]))) continue;
      seen.set(id, (seen.get(id) || 0) + 1);
    }
    const dups = [...seen].filter(([, n]) => n > 1).map(([id]) => id);
    return dups.sort();
  }

  /** Worth sending to an external geocoder (geocode_external._is_geocodable). */
  function isGeocodable(address) {
    return !isBlank(address) && /[A-Za-z0-9]/.test(String(address));
  }

  const PO_BOX = /\bP\.?\s*O\.?\s*BOX\b|\bPOST\s+OFFICE\s+BOX\b/i;

  /** PO boxes have no physical location, so they are never sent externally. */
  function isPoBox(address) {
    return PO_BOX.test(String(address || ""));
  }

  // Nominatim precision gate (geocode_external._nominatim_precise).
  const NOMINATIM_MIN_PLACE_RANK = 26;
  const NOMINATIM_CENTROID_TYPES = new Set([
    "city", "town", "village", "hamlet", "county", "state", "administrative",
    "suburb", "neighbourhood", "postcode", "country", "locality", "municipality",
    "region", "province", "district", "isolated_dwelling", "island", "quarter",
    "city_district",
  ]);

  function nominatimPrecise(candidate) {
    const type = String(candidate.addresstype || candidate.type || "").toLowerCase();
    if (NOMINATIM_CENTROID_TYPES.has(type)) return false;
    const rank = parseInt(candidate.place_rank, 10);
    return (Number.isNaN(rank) ? 0 : rank) >= NOMINATIM_MIN_PLACE_RANK;
  }

  /** First street-or-finer Nominatim candidate as {lat, lon}, else null. */
  function pickNominatim(candidates) {
    for (const c of candidates || []) {
      if (nominatimPrecise(c)) return { lat: parseFloat(c.lat), lon: parseFloat(c.lon) };
    }
    return null;
  }

  /** 11-digit tract GEOID, or "" when it is not a valid tract code. */
  function cleanGeoid(value) {
    const s = cleanCell(value).replace(/\D/g, "");
    if (!s) return "";
    const g = s.padStart(11, "0");
    return g.length === 11 ? g : "";
  }

  // Guess columns from common header names so most files need no picking.
  const GUESSES = {
    id: ["client_id", "id", "unique_id", "record_id", "case_number", "case_id"],
    address: ["full_address", "address", "fulladdress", "address_full"],
    street: ["street", "street_address", "address1", "address_1", "addr1", "address_line_1"],
    city: ["city", "town"],
    state: ["state", "st", "state_code"],
    zip: ["zip", "zipcode", "zip_code", "postal_code", "postcode", "zip5"],
  };

  function guessColumns(columns) {
    const norm = (c) => String(c).toLowerCase().trim().replace(/[\s-]+/g, "_");
    const out = {};
    for (const [key, names] of Object.entries(GUESSES)) {
      out[key] = columns.find((c) => names.includes(norm(c))) || "";
    }
    return out;
  }

  /** Counts by match status, using the same buckets as the Python summary. */
  function tally(records) {
    const t = {
      total: records.length, matched: 0, census: 0, censusAlt: 0, external: 0,
      unmatched: 0, rejected: 0,
    };
    for (const r of records) {
      if (r.match_status === "Matched") t.census++;
      else if (r.match_status === "Matched_Fallback") t.censusAlt++;
      else if (r.match_status === "Matched_External") t.external++;
      else if (r.match_status === "Rejected") t.rejected++;
      else t.unmatched++;
    }
    t.matched = t.census + t.censusAlt + t.external;
    return t;
  }

  return {
    cleanCell, isBlank, normalizeZip, combineAddress, normalizeStreet,
    normalizeAddressStreet, checkForPhi, scanCellsForPhi, findDuplicateIds, isGeocodable, isPoBox,
    nominatimPrecise, pickNominatim, cleanGeoid, guessColumns, tally,
  };
});
