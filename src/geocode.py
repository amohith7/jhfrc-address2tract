"""
Geocoder

Converts addresses to latitude/longitude coordinates using the U.S. Census
Geocoding Services API (Geography endpoint).

The geography endpoint returns both coordinates AND Census tract information
in a single call, which is used as a backup GEOID source only when the
local GeoPackage spatial join cannot assign a tract.

Primary tract source : GeoPackage spatial join (local, no API needed)
Geocoding service    : Census Geography Batch API (address → coordinates)
Fallback geocoding   : Census Geography One-Line API (one address at a time)

Both Census API methods are free and do not require an API key.
"""

from __future__ import annotations

import io
import re
import time
import logging

import requests
import pandas as pd

from normalize import normalize_street

logger = logging.getLogger(__name__)

# Geography endpoints return both coordinates AND census tract info
BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"

BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"

# Ordered (benchmark, vintage) pairs the single-address fallback sweeps through.
# The current address-range file (Public_AR_Current) misses some real addresses
# whose house-number ranges only exist in the 2020 file; retrying Census2020
# recovers a meaningful share of them (verified against the live API).
FALLBACK_BENCHMARKS = [
    ("Public_AR_Current", "Current_Current"),
    ("Public_AR_Census2020", "Census2020_Census2020"),
]

DEFAULT_BATCH_SIZE = 1000  # Census API supports up to 10,000; 1,000 is safer
DEFAULT_BATCH_TIMEOUT = 120  # seconds
DEFAULT_FALLBACK_DELAY = 0.5  # seconds between single-address requests


def _aligned_series(series: pd.Series | None, length: int) -> pd.Series:
    """Return a length-aligned Series, substituting empty strings when absent."""
    if series is None:
        return pd.Series([""] * length)
    return series.reset_index(drop=True)


def _sanitize_field(value) -> str:
    """
    Clean a single address component for the Census batch CSV.

    Commas and double quotes are stripped because they are the field delimiter
    and quote character; missing values become empty strings.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in {"nan", "none"}:
        return ""
    return s.replace('"', "").replace(",", " ")


def normalize_zip(value) -> str:
    """
    Normalize a ZIP code to a clean 5-digit (or ZIP+4) string.

    Guards against numeric parsing artifacts: a float-read ZIP becomes
    "37403.0" (trailing ".0") and drops leading zeros ("07030" -> 7030 ->
    "7030"). We strip a trailing ".0", extract the first 5-digit (optionally
    +4) run, and left-pad to 5 digits.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in {"nan", "none"}:
        return ""
    s = re.sub(r"\.0+$", "", s)  # 37403.0 -> 37403
    m = re.search(r"(\d{5})(?:[-\s]?(\d{4}))?", s)
    if not m:
        # Short/garbled numeric ZIP (e.g. "7030"): keep digits, pad to 5.
        digits = re.sub(r"\D", "", s)
        return digits.zfill(5) if digits else ""
    return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)


DEFAULT_BATCH_RETRIES = 3  # attempts on transient (5xx / network) batch failures


def _post_batch_with_retry(
    csv_content: str, timeout: int, retries: int = DEFAULT_BATCH_RETRIES
):
    """
    POST one batch to the Census geocoder, retrying transient failures.

    A single transient server hiccup (HTTP 5xx, timeout, connection reset)
    should not collapse an entire batch of up to 1,000 records onto the slow
    one-at-a-time fallback, so we retry with exponential backoff. Client errors
    (HTTP 4xx) are not retried. Returns the Response, or None if all attempts
    failed.
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                BATCH_URL,
                data={"benchmark": BENCHMARK, "vintage": VINTAGE},
                files={
                    "addressFile": (
                        "addresses.csv",
                        csv_content.encode("utf-8"),
                        "text/csv",
                    )
                },
                timeout=timeout,
            )
            status = response.status_code
            # Retry only on transient server-side statuses.
            if status >= 500 or status == 429:
                raise requests.HTTPError(f"{status} transient server error")
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            if attempt < retries:
                backoff = 2 ** (attempt - 1)  # 1s, 2s, 4s, ...
                logger.warning(
                    f"  Batch request failed (attempt {attempt}/{retries}): {e}. "
                    f"Retrying in {backoff}s..."
                )
                time.sleep(backoff)
            else:
                logger.warning(
                    f"  Batch request failed after {retries} attempts: {e}. "
                    "Falling back to single-address geocoding for this chunk."
                )
    return None


def _batch_csv_row(uid, street, city, state, zip_code) -> str:
    """Build one Census batch geocoder input row: ID, Street, City, State, ZIP."""
    return (
        f'"{_sanitize_field(uid)}",'
        f'"{_sanitize_field(street)}",'
        f'"{_sanitize_field(city)}",'
        f'"{_sanitize_field(state)}",'
        f'"{_sanitize_field(normalize_zip(zip_code))}"\n'
    )


def geocode_batch(
    unique_ids: pd.Series,
    street: pd.Series,
    city: pd.Series | None = None,
    state: pd.Series | None = None,
    zip_code: pd.Series | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: int = DEFAULT_BATCH_TIMEOUT,
) -> pd.DataFrame:
    """
    Geocode addresses using the Census Geography Batch API.

    The Census batch geocoder expects each address split into its own field
    (Street, City, State, ZIP). Supplying those fields separately, rather than
    a single concatenated string stuffed into the Street field, improves the
    match rate and reduces ambiguous "Tie" results.

    Returns coordinates for use in GeoPackage spatial join (primary tract source),
    plus a census_api_geoid field used only as backup when the spatial join fails.

    Parameters
    ----------
    unique_ids  : Series of unique identifiers aligned with the address fields.
    street      : Series of street address strings. When only a single full
                  address is available, pass it here and leave the component
                  fields as None.
    city        : Series of city names (optional).
    state       : Series of state codes/names (optional).
    zip_code    : Series of ZIP codes (optional).
    batch_size  : Number of records to send per API request.
    timeout     : Request timeout in seconds.

    Returns
    -------
    DataFrame with columns:
        unique_id, latitude, longitude, match_status, matched_address,
        census_api_geoid (backup GEOID from Census API, may be None)
    """
    results = []
    total = len(unique_ids)

    unique_ids = unique_ids.reset_index(drop=True)
    street = street.reset_index(drop=True)
    city = _aligned_series(city, total)
    state = _aligned_series(state, total)
    zip_code = _aligned_series(zip_code, total)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        chunk_ids = unique_ids.iloc[start:end]

        logger.info(f"  Geocoding records {start + 1}–{end} of {total}...")

        csv_lines = []
        for i in range(start, end):
            csv_lines.append(
                _batch_csv_row(
                    unique_ids.iloc[i],
                    street.iloc[i],
                    city.iloc[i],
                    state.iloc[i],
                    zip_code.iloc[i],
                )
            )
        csv_content = "".join(csv_lines)

        response = _post_batch_with_retry(csv_content, timeout)
        if response is None:
            # Transient failures persisted through all retries. Mark this chunk
            # No_Match; the single-address fallback will still try each record.
            for uid in chunk_ids:
                results.append(_no_match(str(uid)))
            continue

        chunk_results = _parse_batch_response(response.text, chunk_ids)
        results.extend(chunk_results)

    return pd.DataFrame(results)


def _parse_batch_response(response_text: str, chunk_ids: pd.Series) -> list:
    """
    Parse the CSV response from the Census Geography Batch Geocoder.

    The geography endpoint returns 12 columns:
      ID, Input Address, Match, Match Type, Matched Address, Coordinates,
      Tiger Line ID, Tiger Line Side, State FIPS, County FIPS,
      Census Tract, Census Block
    """
    found_ids = set()

    try:
        df = pd.read_csv(
            io.StringIO(response_text),
            header=None,
            names=[
                "id",
                "input_addr",
                "match",
                "match_type",
                "matched_addr",
                "coords",
                "tiger_line",
                "side",
                "state_fips",
                "county_fips",
                "census_tract",
                "census_block",
            ],
            dtype=str,
            engine="python",
            on_bad_lines="skip",
        )
    except Exception as e:
        # A hard parse failure should not silently discard the whole batch;
        # unmatched records fall through to the single-address fallback step.
        logger.warning(f"  Could not parse batch response: {e}")
        return [_no_match(str(uid)) for uid in chunk_ids]

    results = []
    for _, row in df.iterrows():
        uid = str(row.get("id", "")).strip()
        found_ids.add(uid)

        lat, lon = None, None
        match_indicator = str(row.get("match", "")).strip()

        if match_indicator == "Match":
            coords = str(row.get("coords", ""))
            if "," in coords:
                try:
                    lon_str, lat_str = coords.split(",", 1)
                    lon = float(lon_str.strip())
                    lat = float(lat_str.strip())
                except ValueError:
                    pass

        matched_addr = str(row.get("matched_addr", "")).strip() or None

        # Build GEOID from geography columns returned by the Census API.
        # This is stored as a backup only — GeoPackage spatial join is primary.
        census_api_geoid = _build_geoid(
            row.get("state_fips", ""),
            row.get("county_fips", ""),
            row.get("census_tract", ""),
        )

        if lat is not None and lon is not None:
            results.append(
                {
                    "unique_id": uid,
                    "latitude": lat,
                    "longitude": lon,
                    "match_status": "Matched",
                    "matched_address": matched_addr,
                    "census_api_geoid": census_api_geoid,
                }
            )
        elif match_indicator == "Tie":
            # A "Tie" means the address was ambiguous (multiple candidates) and
            # carries no coordinates. Label it distinctly so main.py can route
            # it to the single-address fallback, which usually disambiguates it,
            # instead of silently discarding it as a true No_Match.
            results.append(_tie(uid))
        else:
            results.append(_no_match(uid))

    # Records not returned by the API at all
    for uid in chunk_ids:
        if str(uid) not in found_ids:
            results.append(_no_match(str(uid)))

    return results


def _geocode_single_benchmark(
    address: str, unique_id: str, benchmark: str, vintage: str
) -> dict | None:
    """
    Geocode one address against a single (benchmark, vintage) pair.
    Returns a match dict on success, or None if this benchmark did not match.
    """
    try:
        response = requests.get(
            ONELINE_URL,
            params={
                "address": address,
                "benchmark": benchmark,
                "vintage": vintage,
                "format": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            match = matches[0]
            coords = match.get("coordinates", {})
            lat = coords.get("y")
            lon = coords.get("x")
            if lat is not None and lon is not None:
                # Try to get backup GEOID from the geography response
                tracts = match.get("geographies", {}).get("Census Tracts", [])
                census_api_geoid = tracts[0].get("GEOID") if tracts else None

                return {
                    "unique_id": unique_id,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "match_status": "Matched_Fallback",
                    "matched_address": match.get("matchedAddress", ""),
                    "census_api_geoid": census_api_geoid,
                }
    except Exception as e:
        logger.debug(
            f"  Fallback geocoding failed for ID {unique_id} "
            f"(benchmark {benchmark}): {e}"
        )
    return None


def geocode_single(address: str, unique_id: str) -> dict:
    """
    Geocode a single address using the Census Geography one-line API.
    Used as fallback for records the batch geocoder could not match.

    Sweeps the benchmarks in FALLBACK_BENCHMARKS in order and returns the first
    match. The Public_AR_Census2020 benchmark recovers real addresses that the
    Current address-range file lacks. Returns coordinates (for the GeoPackage
    spatial join) and a backup GEOID.
    """
    for benchmark, vintage in FALLBACK_BENCHMARKS:
        result = _geocode_single_benchmark(address, unique_id, benchmark, vintage)
        if result is not None:
            return result
    return _no_match(unique_id)


def _normalize_address_street(address: str) -> str:
    """
    Normalize only the street portion of a one-line address.

    Splits on the first comma: everything before it is the street (normalized),
    everything after (city, state, ZIP) is preserved verbatim. If there is NO
    comma we cannot safely isolate the street from a trailing city/state token
    (e.g. "742 Evergreen Ter Hartford CT" would turn the state code "CT" into
    "Court"), so the address is returned unchanged.
    """
    addr = str(address)
    if "," in addr:
        street, rest = addr.split(",", 1)
        return f"{normalize_street(street)},{rest}"
    return addr


def geocode_fallback(
    unmatched_df: pd.DataFrame,
    address_col: str,
    id_col: str,
    delay: float = DEFAULT_FALLBACK_DELAY,
) -> pd.DataFrame:
    """
    Run single-address geocoding for each unmatched record.
    Rate-limited to be respectful of Census API guidelines.

    Parameters
    ----------
    unmatched_df : DataFrame of records not matched in the batch step.
    address_col  : Name of the column containing the address string.
    id_col       : Name of the unique identifier column.
    delay        : Seconds to pause between requests.

    Returns
    -------
    DataFrame with columns:
        unique_id, latitude, longitude, match_status, matched_address,
        census_api_geoid
    """
    results = []
    total = len(unmatched_df)

    for i, (_, row) in enumerate(unmatched_df.iterrows()):
        uid = str(row[id_col])
        addr = str(row[address_col])
        logger.info(f"  Fallback geocoding record {i + 1} of {total} (ID: {uid})...")

        result = geocode_single(addr, uid)

        # If the raw address still did not match, retry once with a
        # conservatively normalized variant. Normalization is STREET-only, so
        # we apply it to just the first comma-segment (the street) and leave the
        # city/state/zip untouched — never feed the whole one-line address to
        # the street normalizer. This only runs on already-failing records, so
        # it cannot regress a good match.
        if result["match_status"] == "No_Match":
            normalized = _normalize_address_street(addr)
            if normalized and normalized != addr:
                retry = geocode_single(normalized, uid)
                if retry["match_status"] != "No_Match":
                    result = retry

        results.append(result)
        if i < total - 1:
            time.sleep(delay)

    return pd.DataFrame(results)


def _build_geoid(state: str, county: str, tract: str) -> str | None:
    """
    Construct an 11-digit Census GEOID from state, county, and tract FIPS codes.
    Returns None if any component is missing or invalid.
    """
    s = str(state).strip()
    c = str(county).strip()
    t = str(tract).strip()
    invalid = {"", "nan", "None"}
    if s in invalid or c in invalid or t in invalid:
        return None
    return s.zfill(2) + c.zfill(3) + t.zfill(6)


def _no_match(unique_id: str) -> dict:
    return {
        "unique_id": unique_id,
        "latitude": None,
        "longitude": None,
        "match_status": "No_Match",
        "matched_address": None,
        "census_api_geoid": None,
    }


def _tie(unique_id: str) -> dict:
    """Ambiguous batch result (multiple candidates, no coordinates)."""
    return {
        "unique_id": unique_id,
        "latitude": None,
        "longitude": None,
        "match_status": "Tie",
        "matched_address": None,
        "census_api_geoid": None,
    }
