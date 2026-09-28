"""
Connectivity preflight for the JHFRC Address to Census Tract Converter.

Runs only after egress has been approved (``--approve-egress``). It makes a
small reachability check against the geocoding host(s) the run will actually
use, sending NO address data (a host probe only), so a missing or broken
internet connection fails fast with a clear message instead of every batch
grinding through its full retry budget first.
"""

from __future__ import annotations

import requests

# Host probe URLs mirror the endpoints used by the geocoders. We only contact
# the host root, never posting an address, so nothing sensitive leaves the
# machine during the check.
CENSUS_HOST_URL = "https://geocoding.geo.census.gov/geocoder"
PROVIDER_HOST_URLS = {
    "nominatim": "https://nominatim.openstreetmap.org/",
    "arcgis": "https://geocode.arcgis.com/",
    "geoapify": "https://api.geoapify.com/",
}

DEFAULT_PREFLIGHT_TIMEOUT = 10  # seconds


def _reachable(url: str, timeout: int) -> tuple[bool, str]:
    """
    Return ``(ok, detail)`` for a single host.

    Any HTTP response (even 4xx/5xx) means the host is reachable, which is all
    the preflight cares about. Only a connection-level failure (DNS lookup,
    connection refused, timeout, no route) counts as unreachable. HEAD is tried
    first; if the host refuses it, a lightweight streamed GET is tried once
    before concluding the host is truly unreachable.

    Args:
        url: Host URL to probe.
        timeout: Per-request timeout in seconds.

    Returns:
        A ``(ok, detail)`` tuple. ``detail`` is the HTTP status when reachable,
        otherwise the exception class name.
    """
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return True, f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException:
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            resp.close()
            return True, f"HTTP {resp.status_code}"
        except requests.exceptions.RequestException as exc:
            return False, type(exc).__name__


def check_connectivity(
    *,
    check_external: bool,
    external_provider: str = "nominatim",
    timeout: int = DEFAULT_PREFLIGHT_TIMEOUT,
) -> tuple[bool, list[tuple[str, bool, str]]]:
    """
    Probe the hosts this run will use.

    The Census geocoder is the required host. The external fallback provider is
    optional: it is only probed when ``check_external`` is True, and a failure
    there is a warning rather than a hard stop, since the Census geocoder can
    still run on its own.

    Args:
        check_external: Whether the external fallback provider is enabled for
            this run and should also be probed.
        external_provider: Configured external provider name (nominatim,
            arcgis, or geoapify).
        timeout: Per-request timeout in seconds.

    Returns:
        ``(census_ok, results)`` where ``results`` is a list of
        ``(label, ok, detail)`` tuples, one per host probed.
    """
    results: list[tuple[str, bool, str]] = []

    census_ok, detail = _reachable(CENSUS_HOST_URL, timeout)
    results.append(("geocoding.geo.census.gov (Census geocoder)", census_ok, detail))

    if check_external:
        provider = (external_provider or "nominatim").lower()
        url = PROVIDER_HOST_URLS.get(provider)
        if url is not None:
            ok, detail = _reachable(url, timeout)
            host = url.split("//", 1)[-1].rstrip("/")
            results.append((f"{host} (external fallback: {provider})", ok, detail))

    return census_ok, results
