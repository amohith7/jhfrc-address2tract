"""
Tract Join

Downloads and manages the U.S. Census tract reference dataset.
Performs spatial joins to assign a Census tract GEOID to each geocoded address.
"""

from __future__ import annotations

import io
import shutil
import zipfile
import logging
from pathlib import Path

import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

logger = logging.getLogger(__name__)

# U.S. Census Bureau cartographic boundary file (national, ~500k generalized).
# Generalized geometry is small and fast but slightly simplified at boundaries.
CENSUS_TRACT_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_tract_500k.zip"
)
# Full-resolution TIGER/Line tract files are published per state (no national
# file). Format: tl_2024_<stateFIPS>_tract.zip. Larger, but exact boundaries.
TIGER_TRACT_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_{fips}_tract.zip"
)
TRACT_GPKG_NAME = "census_tracts.gpkg"
TIGER_GPKG_NAME = "census_tracts_tiger.gpkg"
TRACT_LAYER = "census_tracts"
DEFAULT_REFERENCE_DIR = Path("data/reference")

# Census ZCTA (ZIP Code Tabulation Area) Gazetteer file: one population-weighted
# centroid per ZCTA. Used to assign an APPROXIMATE tract to addresses no geocoder
# could place, via the centroid of the address's ZIP.
ZCTA_GAZ_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2024_Gazetteer/2024_Gaz_zcta_national.zip"
)
ZCTA_CENTROID_CACHE = "zcta_centroids.csv"


def get_zcta_centroids(reference_dir: str = "data/reference") -> dict:
    """
    Return a mapping {zcta5: (lat, lon)} of ZIP/ZCTA centroids.

    Downloads the Census ZCTA Gazetteer file once and caches a trimmed CSV in
    reference_dir, so later runs need no network. Used only as a last-resort
    approximation (a ZIP spans many tracts); dedicated PO-box ZIPs that have no
    ZCTA are simply absent from the mapping.
    """
    ref = Path(reference_dir)
    ref.mkdir(parents=True, exist_ok=True)
    cache = ref / ZCTA_CENTROID_CACHE

    if cache.exists():
        df = pd.read_csv(cache, dtype=str)
    else:
        logger.info("Downloading Census ZCTA centroid file (one-time)...")
        resp = requests.get(ZCTA_GAZ_URL, timeout=300)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
            with zf.open(name) as fh:
                raw = pd.read_csv(fh, sep="\t", dtype=str)
        raw.columns = [c.strip() for c in raw.columns]
        df = raw[["GEOID", "INTPTLAT", "INTPTLONG"]].copy()
        df["INTPTLAT"] = df["INTPTLAT"].str.strip()
        df["INTPTLONG"] = df["INTPTLONG"].str.strip()
        df.to_csv(cache, index=False)
        logger.info(f"Cached {len(df):,} ZCTA centroids to {cache}.")

    centroids: dict = {}
    for geoid, lat, lon in zip(df["GEOID"], df["INTPTLAT"], df["INTPTLONG"]):
        try:
            centroids[str(geoid).zfill(5)] = (float(lat), float(lon))
        except (TypeError, ValueError):
            continue
    return centroids


def get_tract_dataset(
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    source: str = "cb500k",
    states: list | None = None,
) -> gpd.GeoDataFrame:
    """
    Return the Census tract GeoDataFrame, downloading and converting if needed.

    The dataset is stored locally after the first download so subsequent runs
    do not require an internet connection.

    Parameters
    ----------
    reference_dir : Directory for the cached reference dataset.
    source        : "cb500k" (default, national generalized cartographic file)
                    or "tiger" (full-resolution TIGER/Line, per-state).
    states        : List of 2-digit state FIPS codes (required for "tiger";
                    e.g. ["47","13","01","37"] for TN, GA, AL, NC).
    """
    reference_dir = Path(reference_dir)
    reference_dir.mkdir(parents=True, exist_ok=True)

    if source == "tiger":
        if not states:
            logger.warning(
                "tract_source='tiger' requires tract_states; "
                "falling back to the national cb500k file."
            )
        else:
            gpkg_path = reference_dir / TIGER_GPKG_NAME
            if not gpkg_path.exists():
                _download_and_convert_tiger(reference_dir, gpkg_path, states)
            else:
                logger.info(f"TIGER/Line tract dataset found at: {gpkg_path}")
            logger.info("Loading TIGER/Line tract dataset...")
            tracts = gpd.read_file(gpkg_path, layer=TRACT_LAYER)
            logger.info(f"Loaded {len(tracts):,} Census tracts (TIGER/Line).")
            return tracts

    gpkg_path = reference_dir / TRACT_GPKG_NAME
    if not gpkg_path.exists():
        _download_and_convert(reference_dir, gpkg_path)
    else:
        logger.info(f"Census tract dataset found at: {gpkg_path}")

    logger.info("Loading Census tract dataset...")
    tracts = gpd.read_file(gpkg_path, layer=TRACT_LAYER)
    logger.info(f"Loaded {len(tracts):,} Census tracts.")
    return tracts


def _download_and_convert_tiger(
    reference_dir: Path, gpkg_path: Path, states: list
) -> None:
    """Download full-resolution TIGER/Line tract files for the given state FIPS
    codes and merge them into a single GeoPackage."""
    import pandas as pd

    print("\nFull-resolution TIGER/Line tract dataset not found locally.")
    print(f"Downloading tract files for states: {', '.join(states)}")

    frames = []
    for fips in states:
        fips = str(fips).zfill(2)
        url = TIGER_TRACT_URL.format(fips=fips)
        zip_path = reference_dir / f"tl_2024_{fips}_tract.zip"
        extract_dir = reference_dir / f"tl_2024_{fips}_tract"
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(extract_dir)
            shp = next(iter(extract_dir.glob("*.shp")), None)
            if shp is None:
                raise RuntimeError(f"No shapefile for state {fips}")
            frames.append(gpd.read_file(shp))
            print(f"  State {fips}: loaded.")
        except Exception as e:
            raise RuntimeError(
                f"Failed to download/parse TIGER tract file for state {fips}.\n"
                f"Details: {e}"
            ) from e
        finally:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)

    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    merged.to_file(gpkg_path, layer=TRACT_LAYER, driver="GPKG")
    print(f"  TIGER/Line tract dataset saved to: {gpkg_path}\n")


def _download_and_convert(reference_dir: Path, gpkg_path: Path) -> None:
    """Download the Census tract shapefile and convert it to a GeoPackage."""
    zip_path = reference_dir / "cb_2024_us_tract_500k.zip"
    extract_dir = reference_dir / "cb_2024_us_tract_500k"

    print("\nCensus tract dataset not found locally.")
    print("Downloading from the U.S. Census Bureau. This only happens once.")
    print(f"Source: {CENSUS_TRACT_URL}\n")

    # --- Download ---
    try:
        with requests.get(CENSUS_TRACT_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            total_bytes = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes:
                        pct = downloaded / total_bytes * 100
                        print(f"\r  Downloading... {pct:.1f}%", end="", flush=True)
        mb = downloaded / 1024 / 1024
        print(f"\r  Download complete ({mb:.1f} MB).          ")
    except requests.RequestException as e:
        if zip_path.exists():
            zip_path.unlink()
        raise RuntimeError(
            f"Failed to download the Census tract dataset.\n"
            f"Please check your internet connection and try again.\n"
            f"Details: {e}"
        ) from e

    # --- Extract ---
    print("  Extracting shapefile...")
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)

    shp_files = list(extract_dir.glob("*.shp"))
    if not shp_files:
        raise RuntimeError("No shapefile found in the downloaded archive.")
    shp_path = shp_files[0]

    # --- Convert to GeoPackage ---
    print("  Converting to GeoPackage format (this may take a moment)...")
    gdf = gpd.read_file(shp_path)
    gdf.to_file(gpkg_path, layer=TRACT_LAYER, driver="GPKG")

    # --- Clean up temporary files ---
    zip_path.unlink(missing_ok=True)
    shutil.rmtree(extract_dir, ignore_errors=True)

    print(f"  Census tract dataset saved to: {gpkg_path}\n")


def join_points_to_tracts(
    geo_df: pd.DataFrame, tracts: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Assign a Census tract GEOID to each geocoded record via spatial join.

    Parameters
    ----------
    geo_df  : DataFrame containing 'latitude' and 'longitude' columns.
    tracts  : GeoDataFrame of Census tract polygons.

    Returns
    -------
    Regular DataFrame with 'census_tract_geoid' column added.
    Rows without valid coordinates receive a null GEOID.
    """
    # Drop any pre-existing tract GEOID column to avoid duplicates on re-runs
    geo_df = geo_df.drop(columns=["census_tract_geoid"], errors="ignore")

    # Build GEOID column if not present
    if "GEOID" not in tracts.columns:
        if all(c in tracts.columns for c in ["STATEFP", "COUNTYFP", "TRACTCE"]):
            tracts = tracts.copy()
            tracts["GEOID"] = (
                tracts["STATEFP"].astype(str).str.zfill(2)
                + tracts["COUNTYFP"].astype(str).str.zfill(3)
                + tracts["TRACTCE"].astype(str).str.zfill(6)
            )
        else:
            raise ValueError(
                "Cannot find a GEOID column in the Census tract dataset. "
                "Expected 'GEOID' or 'STATEFP'+'COUNTYFP'+'TRACTCE'."
            )

    # Ensure GEOID is always stored and returned as a string (never float)
    tracts = tracts.copy()
    tracts["GEOID"] = tracts["GEOID"].astype(str).str.strip()

    # Normalize CRS to WGS84
    if tracts.crs is None:
        tracts = tracts.set_crs("EPSG:4326")
    elif tracts.crs.to_epsg() != 4326:
        tracts = tracts.to_crs("EPSG:4326")

    tracts_slim = tracts[["geometry", "GEOID"]].copy()

    # Build point geometries
    def make_point(row):
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            return Point(lon, lat)
        except (TypeError, ValueError):
            return None

    geometry = geo_df.apply(make_point, axis=1)
    points_gdf = gpd.GeoDataFrame(geo_df.copy(), geometry=geometry, crs="EPSG:4326")

    # Separate rows with and without valid geometry
    valid_mask = points_gdf.geometry.notna() & ~points_gdf.geometry.is_empty
    valid_points = points_gdf[valid_mask].copy()
    invalid_points = points_gdf[~valid_mask].copy()

    # Spatial join. Use "intersects" rather than "within" so a point that lands
    # exactly on a (generalized) tract boundary still gets assigned instead of
    # falling through to a null tract.
    if len(valid_points) > 0:
        joined = gpd.sjoin(
            valid_points, tracts_slim, how="left", predicate="intersects"
        )
        # "intersects" can return two tracts for a point on a shared boundary;
        # keep the first match per original point so row counts are preserved.
        joined = joined[~joined.index.duplicated(keep="first")]
        joined = joined.rename(columns={"GEOID": "census_tract_geoid"})
        joined = joined.drop(columns=["geometry", "index_right"], errors="ignore")
    else:
        joined = valid_points.drop(columns=["geometry"], errors="ignore")
        joined["census_tract_geoid"] = None

    invalid_points = invalid_points.drop(columns=["geometry"], errors="ignore")
    invalid_points["census_tract_geoid"] = None

    combined = pd.concat(
        [
            pd.DataFrame(joined).reset_index(drop=True),
            pd.DataFrame(invalid_points).reset_index(drop=True),
        ],
        ignore_index=True,
    )

    # Ensure GEOID is returned as a clean string (not float like 4.706e+10)
    def _clean_geoid(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        s = str(val).strip()
        if s.endswith(".0"):
            s = s[:-2]
        return s if s not in ("nan", "None", "") else None

    combined["census_tract_geoid"] = combined["census_tract_geoid"].apply(_clean_geoid)
    return combined
