"""
External (non-Census) Geocoder Fallback

Free fallback for the small residual of records the Census geocoder cannot
resolve because the address is genuinely absent from the Census TIGER
address-range file (pedestrian plazas, brand-new developments, some urban
segments). It returns coordinates ONLY; the tract is still assigned by the
local GeoPackage spatial join (tract_join.join_points_to_tracts), so the
authoritative tract geometry is unchanged.

Providers (config-selectable, all free):
  - nominatim : OpenStreetMap. No API key. Public server policy is ≤1 req/s and
                prohibits bulk use, so this is appropriate only for a SMALL
                residual set. Requires a descriptive User-Agent.
  - arcgis    : Esri ArcGIS World Geocoder. Needs a free developer token but
                offers higher throughput and better real-address coverage; use
                it when the residual set is large.
  - geoapify  : Geoapify Geocoding API. Needs a free API key (3,000 requests/day
                free, no credit card). Good real-address coverage; a solid
                free choice for a large residual. Precision is judged by
                result_type + rank.confidence + rank.match_type.

PRECISION FILTER: only street/house-level results are accepted. City- or
place-centroid matches are rejected so we never assign a census tract from an
imprecise centroid.

IMPORTANT: external-geocoder yield measured on OpenStreetMap-sourced test data
is optimistic. On arbitrary client address lists the real recovery rate will be
lower. A tract-native commercial geocoder (Geocodio, Smarty) would be simpler
and higher-yield but requires a paid key and was intentionally not used here.
"""

from __future__ import annotations

import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd

logger = logging.getLogger(__name__)

_ZIP5_RE = re.compile(r"\b(\d{5})\b")


def _zip5(text) -> str:
    """Return the first 5-digit ZIP found in text, or '' if none."""
    m = _ZIP5_RE.search(str(text or ""))
    return m.group(1) if m else ""


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ARCGIS_URL = (
    "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/"
    "findAddressCandidates"
)
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"

# Nominatim precision is judged by place_rank: house-number = 30, street ~26-27,
# neighbourhood ~22, suburb ~20, locality/city ~16-20, state ~8. Require
# street-or-finer (>= 26) so a POI AT an address (place_rank 30, but addresstype
# "amenity"/"historic") is kept while any locality/suburb/city centroid is
# rejected. The centroid denylist is a backstop for the rank threshold.
_NOMINATIM_MIN_PLACE_RANK = 26
_NOMINATIM_CENTROID_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "county",
    "state",
    "administrative",
    "suburb",
    "neighbourhood",
    "postcode",
    "country",
    "locality",
    "municipality",
    "region",
    "province",
    "district",
    "isolated_dwelling",
    "island",
    "quarter",
    "city_district",
}
# Nominatim candidates to request so a real street match ranked below a
# higher-importance centroid can still be found by the precision filter.
_NOMINATIM_LIMIT = 5

# ArcGIS address-type values that indicate street/house-level precision.
_ARCGIS_OK_ADDR_TYPES = {"PointAddress", "StreetAddress", "StreetInt", "Subaddress"}
_ARCGIS_MIN_SCORE = 90

# Geoapify precision gate. result_type must be a precise (non-centroid) location,
# the match must not be a coarse city/postcode/state fallback, and confidence
# must be high — so a census tract is never assigned from a city/ZIP centroid.
_GEOAPIFY_OK_RESULT_TYPES = {"building", "street", "amenity"}
_GEOAPIFY_BAD_MATCH_TYPES = {
    "match_by_city_or_disrict",  # spelling per Geoapify API
    "match_by_postcode",
    "match_by_country_or_state",
}
_GEOAPIFY_MIN_CONFIDENCE = 0.9
_GEOAPIFY_RETRIES = 3  # internal retry on transient 429/5xx so a rate-limit
#                        is not silently turned into a false No_Match.

DEFAULT_DELAY = 1.1  # seconds between Nominatim requests (policy: ≤1 req/s)
DEFAULT_USER_AGENT = "jhfrc-address2tract/1.0 (research use)"


def _nominatim_precise(candidate: dict) -> bool:
    """True if a Nominatim candidate is at street/house precision."""
    addr_type = str(candidate.get("addresstype", candidate.get("type", ""))).lower()
    if addr_type in _NOMINATIM_CENTROID_TYPES:
        return False
    try:
        place_rank = int(candidate.get("place_rank", 0))
    except (TypeError, ValueError):
        place_rank = 0
    return place_rank >= _NOMINATIM_MIN_PLACE_RANK


def _geocode_nominatim(address: str, user_agent: str) -> tuple | None:
    """
    Return (lat, lon) at street/house precision, or None.

    Requests several candidates and returns the first one that passes the
    precision filter, so a real street match ranked just below a
    higher-importance centroid is still recovered. Raises on transport errors
    so the caller can distinguish a geocoder failure from a true no-result.
    """
    resp = requests.get(
        NOMINATIM_URL,
        params={
            "q": address,
            "format": "jsonv2",
            "limit": _NOMINATIM_LIMIT,
            "countrycodes": "us",
            "addressdetails": 0,
        },
        headers={"User-Agent": user_agent},
        timeout=30,
    )
    resp.raise_for_status()
    for cand in resp.json():
        if _nominatim_precise(cand):
            return float(cand["lat"]), float(cand["lon"])
    return None


def _geocode_arcgis(address: str, token: str) -> tuple | None:
    """
    Return (lat, lon) at street/house precision, or None.

    Detects the ArcGIS error payload (returned with HTTP 200 for token/quota
    failures) and raises so it is not silently treated as a no-result. Raises on
    transport errors too.
    """
    resp = requests.get(
        ARCGIS_URL,
        params={
            "SingleLine": address,
            "f": "json",
            "maxLocations": 5,
            "outFields": "Addr_type,Score",
            "countryCode": "USA",
            "token": token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    # ArcGIS returns HTTP 200 with an {"error": {...}} body for invalid/expired
    # tokens and quota problems; surface it instead of reporting No_Match.
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(f"ArcGIS API error: {payload['error']}")
    for cand in payload.get("candidates", []):
        attrs = cand.get("attributes", {})
        addr_type = attrs.get("Addr_type", "")
        score = float(cand.get("score", attrs.get("Score", 0)) or 0)
        if addr_type in _ARCGIS_OK_ADDR_TYPES and score >= _ARCGIS_MIN_SCORE:
            loc = cand.get("location", {})
            return float(loc["y"]), float(loc["x"])
    return None


def _geocode_geoapify(address: str, api_key: str) -> tuple | None:
    """
    Return (lat, lon) at street/house precision from Geoapify, or None.

    Uses the Forward Geocoding API. A candidate is accepted only if it is a
    precise (building/street/amenity, non-centroid) match AND the ZIP it returns
    equals the ZIP in the input address. ZIP-consistency is a far better
    precision signal than the raw confidence score: correct rural matches often
    carry a low confidence but the right ZIP, while wrong matches land in a
    different ZIP (or a city/ZIP centroid). When the input has no ZIP, fall back
    to the confidence threshold. Retries transient 429/5xx with backoff (a
    rate-limit must not become a false No_Match). Raises on persistent transport
    errors or an API error payload.
    """
    expected_zip = _zip5(address)
    for attempt in range(1, _GEOAPIFY_RETRIES + 1):
        try:
            resp = requests.get(
                GEOAPIFY_URL,
                params={
                    "text": address,
                    "apiKey": api_key,
                    "filter": "countrycode:us",
                    "format": "json",
                    "limit": 5,
                },
                timeout=30,
            )
            status = resp.status_code
            if status >= 500 or status == 429:
                raise requests.HTTPError(f"{status} transient server error")
            resp.raise_for_status()
            payload = resp.json()
            break
        except requests.RequestException as e:
            if attempt < _GEOAPIFY_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s
                continue
            raise
    # Geoapify signals key/quota problems with an error body; surface it rather
    # than reporting No_Match.
    if (
        isinstance(payload, dict)
        and payload.get("statusCode")
        and not payload.get("results")
    ):
        raise RuntimeError(f"Geoapify API error: {payload}")
    for cand in payload.get("results", []):
        rtype = str(cand.get("result_type", "")).lower()
        rank = cand.get("rank", {}) or {}
        mtype = str(rank.get("match_type", "")).lower()
        try:
            conf = float(rank.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        # Must be a precise, non-centroid match with usable coordinates.
        if (
            rtype not in _GEOAPIFY_OK_RESULT_TYPES
            or mtype in _GEOAPIFY_BAD_MATCH_TYPES
            or cand.get("lat") is None
            or cand.get("lon") is None
        ):
            continue
        returned_zip = _zip5(cand.get("postcode") or cand.get("formatted"))
        if expected_zip:
            # Primary gate: the returned ZIP must match the input ZIP.
            if returned_zip == expected_zip:
                return float(cand["lat"]), float(cand["lon"])
        elif conf >= _GEOAPIFY_MIN_CONFIDENCE:
            # No input ZIP to check against: fall back to the confidence score.
            return float(cand["lat"]), float(cand["lon"])
    return None


def _geocode_one_external(
    row, address_col, id_col, provider, user_agent, arcgis_token, geoapify_key
) -> tuple:
    """Geocode ONE record with the selected provider. Returns (result_dict,
    had_error). Pure/self-contained (no shared mutable state), so it is safe to
    run in a thread. had_error distinguishes a geocoder failure (HTTP 429,
    timeout, bad token) from an address that legitimately did not resolve."""
    uid = str(row[id_col])
    addr = str(row[address_col])
    coords = None
    had_error = False
    try:
        if provider == "arcgis":
            coords = _geocode_arcgis(addr, arcgis_token)
        elif provider == "geoapify":
            coords = _geocode_geoapify(addr, geoapify_key)
        else:
            coords = _geocode_nominatim(addr, user_agent)
    except Exception as e:
        had_error = True
        logger.warning(f"  External geocoder error for ID {uid} ('{addr}'): {e}")

    if coords is not None:
        result = {
            "unique_id": uid,
            "latitude": coords[0],
            "longitude": coords[1],
            "match_status": "Matched_External",
        }
    else:
        result = {
            "unique_id": uid,
            "latitude": None,
            "longitude": None,
            "match_status": "No_Match",
        }
    return result, had_error


def geocode_external(
    unmatched_df: pd.DataFrame,
    address_col: str,
    id_col: str,
    provider: str = "nominatim",
    user_agent: str = DEFAULT_USER_AGENT,
    arcgis_token: str | None = None,
    geoapify_key: str | None = None,
    delay: float = DEFAULT_DELAY,
    concurrency: int = 1,
) -> pd.DataFrame:
    """
    Geocode each residual record with a non-Census provider.

    Returns a DataFrame with columns:
        unique_id, latitude, longitude, match_status
    match_status is "Matched_External" when a street/house-precision result was
    found, otherwise "No_Match". Coordinates feed the local tract spatial join.

    concurrency applies only to ArcGIS/Geoapify (both handle their own
    throughput; Geoapify retries 429 internally). Nominatim always runs
    sequentially with the delay, because its public-server policy caps it at
    ≤1 req/s. Results are returned in the original input order.
    """
    provider = (provider or "nominatim").lower()
    if provider == "arcgis" and not arcgis_token:
        logger.warning(
            "  External provider 'arcgis' selected but no arcgis_token provided; "
            "skipping external fallback."
        )
        return pd.DataFrame(
            columns=["unique_id", "latitude", "longitude", "match_status"]
        )
    if provider == "geoapify" and not geoapify_key:
        logger.warning(
            "  External provider 'geoapify' selected but no geoapify_key provided; "
            "skipping external fallback."
        )
        return pd.DataFrame(
            columns=["unique_id", "latitude", "longitude", "match_status"]
        )

    total = len(unmatched_df)
    rows = [row for _, row in unmatched_df.iterrows()]
    args = (address_col, id_col, provider, user_agent, arcgis_token, geoapify_key)
    error_count = 0

    # Nominatim must stay sequential (≤1 req/s). Also run sequentially when
    # concurrency <= 1 or there is nothing to parallelize.
    if provider == "nominatim" or concurrency <= 1 or total <= 1:
        results = []
        for i, row in enumerate(rows):
            logger.info(
                f"  External ({provider}) geocoding record {i + 1} of {total} "
                f"(ID: {row[id_col]})..."
            )
            res, err = _geocode_one_external(row, *args)
            results.append(res)
            error_count += err
            if provider == "nominatim" and i < total - 1:
                time.sleep(delay)
    else:
        logger.info(
            f"  External ({provider}): geocoding {total} record(s) with "
            f"{concurrency}-way concurrency..."
        )
        results = [None] * total
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_idx = {
                executor.submit(_geocode_one_external, row, *args): i
                for i, row in enumerate(rows)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                res, err = future.result()
                results[idx] = res
                error_count += err
                done += 1
                if done % 100 == 0 or done == total:
                    logger.info(f"  External progress: {done} of {total} done")

    if error_count:
        pct = 100.0 * error_count / total if total else 0.0
        logger.warning(
            f"  External fallback: {error_count}/{total} record(s) "
            f"({pct:.0f}%) failed due to geocoder errors, not unresolvable "
            "addresses. Results for those records are No_Match but may be "
            "recoverable on retry."
        )

    return pd.DataFrame(results)
