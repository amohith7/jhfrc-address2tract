"""
Tract Join

Downloads and manages the U.S. Census tract reference dataset.
Performs spatial joins to assign a Census tract GEOID to each geocoded address.
"""

import shutil
import zipfile
import logging
from pathlib import Path

import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

logger = logging.getLogger(__name__)

# U.S. Census Bureau cartographic boundary file (national, ~500k generalized)
# This URL follows the Census Bureau's standard naming convention.
CENSUS_TRACT_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_tract_500k.zip"
)
TRACT_GPKG_NAME = "census_tracts.gpkg"
TRACT_LAYER = "census_tracts"
DEFAULT_REFERENCE_DIR = Path("data/reference")


def get_tract_dataset(reference_dir: Path = DEFAULT_REFERENCE_DIR) -> gpd.GeoDataFrame:
    """
    Return the Census tract GeoDataFrame, downloading and converting it if necessary.

    The dataset is stored locally after the first download so subsequent runs
    do not require an internet connection.
    """
    reference_dir = Path(reference_dir)
    reference_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = reference_dir / TRACT_GPKG_NAME

    if not gpkg_path.exists():
        _download_and_convert(reference_dir, gpkg_path)
    else:
        logger.info(f"Census tract dataset found at: {gpkg_path}")

    logger.info("Loading Census tract dataset...")
    tracts = gpd.read_file(gpkg_path, layer=TRACT_LAYER)
    logger.info(f"Loaded {len(tracts):,} Census tracts.")
    return tracts


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
    geo_df: pd.DataFrame,
    tracts: gpd.GeoDataFrame,
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
    # Build GEOID column if not present
    if "GEOID" not in tracts.columns:
        if all(c in tracts.columns for c in ["STATEFP", "COUNTYFP", "TRACTCE"]):
            tracts = tracts.copy()
            tracts["GEOID"] = (
                tracts["STATEFP"] + tracts["COUNTYFP"] + tracts["TRACTCE"]
            )
        else:
            raise ValueError(
                "Cannot find a GEOID column in the Census tract dataset. "
                "Expected 'GEOID' or 'STATEFP'+'COUNTYFP'+'TRACTCE'."
            )

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

    # Spatial join
    if len(valid_points) > 0:
        joined = gpd.sjoin(valid_points, tracts_slim, how="left", predicate="within")
        joined = joined.rename(columns={"GEOID": "census_tract_geoid"})
        joined = joined.drop(columns=["geometry", "index_right"], errors="ignore")
    else:
        joined = valid_points.drop(columns=["geometry"], errors="ignore")
        joined["census_tract_geoid"] = None

    invalid_points = invalid_points.drop(columns=["geometry"], errors="ignore")
    invalid_points["census_tract_geoid"] = None

    combined = pd.concat(
        [pd.DataFrame(joined), pd.DataFrame(invalid_points)],
        ignore_index=True,
    )
    return combined
