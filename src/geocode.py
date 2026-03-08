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

import io
import time
import logging

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# Geography endpoints return both coordinates AND census tract info
BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"

BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"

DEFAULT_BATCH_SIZE = 1000   # Census API supports up to 10,000; 1,000 is safer
DEFAULT_BATCH_TIMEOUT = 120  # seconds
DEFAULT_FALLBACK_DELAY = 0.5  # seconds between single-address requests


def geocode_batch(
    addresses: pd.Series,
    unique_ids: pd.Series,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: int = DEFAULT_BATCH_TIMEOUT,
) -> pd.DataFrame:
    """
    Geocode addresses using the Census Geography Batch API.

    Returns coordinates for use in GeoPackage spatial join (primary tract source),
    plus a census_api_geoid field used only as backup when the spatial join fails.

    Parameters
    ----------
    addresses   : Series of address strings to geocode.
    unique_ids  : Series of unique identifiers aligned with `addresses`.
    batch_size  : Number of records to send per API request.
    timeout     : Request timeout in seconds.

    Returns
    -------
    DataFrame with columns:
        unique_id, latitude, longitude, match_status, matched_address,
        census_api_geoid (backup GEOID from Census API, may be None)
    """
    results = []
    total = len(addresses)

    addresses = addresses.reset_index(drop=True)
    unique_ids = unique_ids.reset_index(drop=True)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        chunk_ids = unique_ids.iloc[start:end]
        chunk_addr = addresses.iloc[start:end]

        logger.info(f"  Geocoding records {start + 1}–{end} of {total}...")

        csv_lines = []
        for uid, addr in zip(chunk_ids, chunk_addr):
            safe_addr = str(addr).replace('"', "").replace(",", " ")
            safe_id = str(uid).replace('"', "")
            csv_lines.append(f'"{safe_id}","{safe_addr}",,,""\n')
        csv_content = "".join(csv_lines)

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
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"  Batch geocoding request failed: {e}")
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
                "id", "input_addr", "match", "match_type",
                "matched_addr", "coords", "tiger_line", "side",
                "state_fips", "county_fips", "census_tract", "census_block",
            ],
            dtype=str,
        )
    except Exception as e:
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
            results.append({
                "unique_id": uid,
                "latitude": lat,
                "longitude": lon,
                "match_status": "Matched",
                "matched_address": matched_addr,
                "census_api_geoid": census_api_geoid,
            })
        else:
            results.append(_no_match(uid))

    # Records not returned by the API at all
    for uid in chunk_ids:
        if str(uid) not in found_ids:
            results.append(_no_match(str(uid)))

    return results


def geocode_single(address: str, unique_id: str) -> dict:
    """
    Geocode a single address using the Census Geography one-line API.
    Used as fallback for records the batch geocoder could not match.
    Returns coordinates (for GeoPackage spatial join) and a backup GEOID.
    """
    try:
        response = requests.get(
            ONELINE_URL,
            params={
                "address": address,
                "benchmark": BENCHMARK,
                "vintage": VINTAGE,
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
                tracts = (
                    match.get("geographies", {})
                    .get("Census Tracts", [])
                )
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
        logger.debug(f"  Fallback geocoding failed for ID {unique_id}: {e}")

    return _no_match(unique_id)


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
        results.append(geocode_single(addr, uid))
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
