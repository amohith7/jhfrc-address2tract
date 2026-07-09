"""
External (non-Census) Geocoder Fallback

Free fallback for the small residual of records the Census geocoder cannot
resolve because the address is genuinely absent from the Census TIGER
address-range file (pedestrian plazas, brand-new developments, some urban
segments). It returns coordinates ONLY; the tract is still assigned by the
local GeoPackage spatial join (tract_join.join_points_to_tracts), so the
authoritative tract geometry is unchanged.

Providers (config-selectable, both free):
  - nominatim : OpenStreetMap. No API key. Public server policy is ≤1 req/s and
                prohibits bulk use, so this is appropriate only for a SMALL
                residual set. Requires a descriptive User-Agent.
  - arcgis    : Esri ArcGIS World Geocoder. Needs a free developer token but
                offers higher throughput and better real-address coverage; use
                it when the residual set is large.

PRECISION FILTER: only street/house-level results are accepted. City- or
place-centroid matches are rejected so we never assign a census tract from an
imprecise centroid.

IMPORTANT: external-geocoder yield measured on OpenStreetMap-sourced test data
is optimistic. On arbitrary client address lists the real recovery rate will be
lower. A tract-native commercial geocoder (Geocodio, Smarty) would be simpler
and higher-yield but requires a paid key and was intentionally not used here.
"""

from __future__ import annotations

import time
import logging

import requests
import pandas as pd

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ARCGIS_URL = (
    "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/"
    "findAddressCandidates"
)

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


def geocode_external(
    unmatched_df: pd.DataFrame,
    address_col: str,
    id_col: str,
    provider: str = "nominatim",
    user_agent: str = DEFAULT_USER_AGENT,
    arcgis_token: str | None = None,
    delay: float = DEFAULT_DELAY,
) -> pd.DataFrame:
    """
    Geocode each residual record with a free non-Census provider.

    Returns a DataFrame with columns:
        unique_id, latitude, longitude, match_status
    match_status is "Matched_External" when a street/house-precision result was
    found, otherwise "No_Match". Coordinates feed the local tract spatial join.
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

    results = []
    total = len(unmatched_df)
    error_count = 0  # transport/API failures (distinct from true no-result)
    for i, (_, row) in enumerate(unmatched_df.iterrows()):
        uid = str(row[id_col])
        addr = str(row[address_col])
        logger.info(
            f"  External ({provider}) geocoding record {i + 1} of {total} "
            f"(ID: {uid})..."
        )

        coords = None
        try:
            if provider == "arcgis":
                coords = _geocode_arcgis(addr, arcgis_token)
            else:
                coords = _geocode_nominatim(addr, user_agent)
        except Exception as e:
            # A geocoder failure (HTTP 429, timeout, invalid ArcGIS token/quota)
            # is NOT the same as an address that legitimately did not resolve.
            # Log it at WARNING so a systemic outage is visible, then record the
            # record as No_Match so processing continues.
            error_count += 1
            logger.warning(f"  External geocoder error for ID {uid} ('{addr}'): {e}")

        if coords is not None:
            results.append(
                {
                    "unique_id": uid,
                    "latitude": coords[0],
                    "longitude": coords[1],
                    "match_status": "Matched_External",
                }
            )
        else:
            results.append(
                {
                    "unique_id": uid,
                    "latitude": None,
                    "longitude": None,
                    "match_status": "No_Match",
                }
            )

        # Nominatim public server requires rate limiting; ArcGIS does not.
        if provider != "arcgis" and i < total - 1:
            time.sleep(delay)

    if error_count:
        pct = 100.0 * error_count / total if total else 0.0
        logger.warning(
            f"  External fallback: {error_count}/{total} record(s) "
            f"({pct:.0f}%) failed due to geocoder errors, not unresolvable "
            "addresses. Results for those records are No_Match but may be "
            "recoverable on retry."
        )

    return pd.DataFrame(results)
