"""
JHFRC Address to Census Tract Converter
----------------------------------------
Converts client or patient addresses to U.S. Census tract identifiers (GEOID).

Developed by the Journey Health Foundation Research Center (JHFRC).
For questions or support, contact: mohith-addepalli@utc.edu

Usage examples:
  python main.py --input data/input/clients.xlsx --output data/output/results.xlsx \
      --id-column client_id --address-column full_address

  python main.py --input data/input/clients.xlsx --output data/output/results.xlsx \
      --id-column client_id --street-column street --city-column city \
      --state-column state --zip-column zip
"""

import os
import sys
import json
import hashlib
import logging
from pathlib import Path

# Add src/ to the module search path so imports work cleanly
sys.path.insert(0, str(Path(__file__).parent / "src"))

import argparse

import pandas as pd
import yaml

from collections import defaultdict

from phi_validator import validate_no_phi
from geocode import (
    geocode_batch,
    geocode_fallback,
    normalize_zip,
    DEFAULT_CONCURRENCY,
)
from geocode_external import geocode_external
from tract_join import get_tract_dataset, join_points_to_tracts, get_zcta_centroids
from utils.io import (
    read_input,
    write_output,
    scan_input,
    iter_input_chunks,
    concat_csv_parts,
)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy internal messages from geopandas/pyogrio
    logging.getLogger("pyogrio").setLevel(logging.WARNING)
    logging.getLogger("fiona").setLevel(logging.WARNING)


def _load_config(config_path: str) -> dict:
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="address2tract",
        description=(
            "Convert client or patient addresses to U.S. Census tract identifiers.\n"
            "Developed by the Journey Health Foundation Research Center (JHFRC)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Required arguments
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the output file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--id-column",
        required=True,
        help="Name of the column containing unique identifiers.",
    )

    # Address input: full address OR separate fields
    addr_group = parser.add_mutually_exclusive_group(required=True)
    addr_group.add_argument(
        "--address-column",
        help="Name of the column containing full addresses.",
    )
    addr_group.add_argument(
        "--street-column",
        help="Name of the street address column (use with --city-column, etc.).",
    )

    # Separate address field options
    parser.add_argument("--city-column", help="Name of the city column.")
    parser.add_argument("--state-column", help="Name of the state column.")
    parser.add_argument("--zip-column", help="Name of the ZIP code column.")

    # Optional settings
    parser.add_argument(
        "--tract-dataset",
        help=(
            "Path to an existing Census tract GeoPackage "
            "(default: data/reference/census_tracts.gpkg)."
        ),
    )
    parser.add_argument(
        "--use-fallback",
        action="store_true",
        default=None,
        help="Enable Census API fallback geocoding for unmatched records.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Disable Census API fallback geocoding.",
    )
    parser.add_argument(
        "--use-external-fallback",
        action="store_true",
        default=None,
        help=(
            "Also try a free external geocoder (OpenStreetMap) for addresses "
            "the Census cannot find. Slower; recovers hard-to-find addresses."
        ),
    )
    parser.add_argument(
        "--no-external-fallback",
        action="store_true",
        help="Disable the external geocoder fallback.",
    )
    parser.add_argument(
        "--approve-egress",
        action="store_true",
        help=(
            "REQUIRED to allow any outbound network call. Without it the tool "
            "refuses to contact any geocoder and exits after printing exactly "
            "what it WOULD send and where. No data is ever sent to Anthropic."
        ),
    )
    parser.add_argument(
        "--external-provider",
        choices=["nominatim", "arcgis", "geoapify"],
        default=None,
        help=(
            "External geocoder for the residual (overrides config). 'nominatim' "
            "(free, small residuals only), 'arcgis' (token), or 'geoapify' "
            "(free API key)."
        ),
    )
    parser.add_argument(
        "--arcgis-token",
        default=None,
        help=(
            "ArcGIS World Geocoder token, for --external-provider arcgis. "
            "ArcGIS tokens are typically temporary, so pass it per run here (or "
            "via the ARCGIS_TOKEN environment variable) rather than storing it "
            "in the config file. Precedence: this flag, then ARCGIS_TOKEN, then "
            "config."
        ),
    )
    parser.add_argument(
        "--geoapify-key",
        default=None,
        help=(
            "Geoapify API key, for --external-provider geoapify. Precedence: "
            "this flag, then the GEOAPIFY_KEY environment variable, then config."
        ),
    )
    parser.add_argument(
        "--zip-approx",
        action="store_true",
        help=(
            "Last-resort fallback: after all geocoding, assign an APPROXIMATE "
            "tract to any still-unmatched row from its ZIP/ZCTA centroid "
            "(labeled Matched_ZIP_Approx). Coarse (a ZIP spans many tracts); off "
            "by default. Downloads a small Census ZCTA file once (needs "
            "--approve-egress the first time)."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help=(
            "Rows to process per chunk for large files. The tool decides "
            "automatically based on file size; use this only to override. "
            "0 forces single-pass (no chunking)."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=(
            "Number of Census batch requests to send in parallel. Defaults to 6. "
            "Only affects files large enough to span more than one batch; use 1 "
            "to force fully sequential geocoding."
        ),
    )
    parser.add_argument(
        "--retry-passes",
        type=int,
        default=None,
        help=(
            "Extra times to re-attempt still-unmatched addresses within a run, "
            "after the normal fallback (default 2). Stops early once a pass "
            "recovers nothing. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Retry mode: treat --input as a PREVIOUS results file, re-process "
            "only its No_Match/Tie rows, and write an updated file to --output "
            "with any newly matched tracts merged in."
        ),
    )
    parser.add_argument(
        "--sheet-name",
        help="Excel sheet name to read (if the workbook has multiple sheets).",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the configuration file (default: config/config.yaml).",
    )

    return parser


def _combine_address_fields(
    row: pd.Series, street: str, city: str, state: str, zip_: str
) -> str:
    """Combine separate address columns into a single address string.

    The ZIP component is normalized (strip a stray ".0", restore leading zeros)
    so the combined string used by the single-address and external geocoders
    carries a clean ZIP, matching the batch path.
    """
    parts = []
    for col in [street, city, state, zip_]:
        if col and col in row and pd.notna(row[col]):
            value = str(row[col]).strip()
            if col == zip_:
                value = normalize_zip(value)
            if value:
                parts.append(value)
    return ", ".join(parts)


def _print_summary(
    total: int,
    matched_gpkg: int,
    matched_api_backup: int,
    matched_fallback: int,
    matched_external: int,
    unmatched: int,
    rejected: int,
    matched_zip_approx: int = 0,
) -> None:
    total_matched = (
        matched_gpkg
        + matched_api_backup
        + matched_fallback
        + matched_external
        + matched_zip_approx
    )
    match_pct = (100.0 * total_matched / total) if total else 0.0
    unmatched_pct = (100.0 * unmatched / total) if total else 0.0

    print()
    print("=" * 56)
    print("  PROCESSING SUMMARY")
    print("=" * 56)
    print(f"  Total rows processed          : {total:,}")
    print(f"  Matched — GeoPackage (primary): {matched_gpkg:,}")
    print(f"  Matched — Census API (backup) : {matched_api_backup:,}")
    print(f"  Matched — Census fallback     : {matched_fallback:,}")
    print(f"  Matched — External geocoder   : {matched_external:,}")
    if matched_zip_approx:
        print(f"  Matched — ZIP approx (coarse) : {matched_zip_approx:,}")
    print(f"  Unmatched                     : {unmatched:,}")
    print(f"  Rejected (missing data)       : {rejected:,}")
    print("-" * 56)
    print(f"  Matched total                 : {total_matched:,}  ({match_pct:.1f}%)")
    print(f"  Unmatched                     : {unmatched:,}  ({unmatched_pct:.1f}%)")
    print("=" * 56)
    if unmatched_pct > 10.0:
        print()
        print(
            f"  NOTE: {unmatched_pct:.1f}% of addresses were not matched. If this\n"
            "  seems high, please report it to JHFRC (mohith-addepalli@utc.edu)\n"
            "  along with a few example addresses so we can help."
        )
    print()


# Default row count above which chunked, resumable processing turns on
# automatically. Files at or below this size are processed in a single pass.
DEFAULT_CHUNK_THRESHOLD = 50000


def _file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    """Return a stable, memory-bounded SHA-256 fingerprint for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_resumed_part(
    part_path: Path, expected_rows: int, required_columns: set[str]
) -> None:
    """Reject a stale, incomplete, or structurally invalid resumable part."""
    try:
        part = pd.read_csv(part_path, dtype=str)
    except Exception as exc:
        raise ValueError(f"could not read the part file: {exc}") from exc

    missing = sorted(required_columns.difference(part.columns))
    if missing:
        raise ValueError(f"required columns are missing: {missing}")
    if len(part) != expected_rows:
        raise ValueError(f"expected {expected_rows:,} rows but found {len(part):,}")


# Default number of extra retry passes over still-unmatched rows within a run.
DEFAULT_RETRY_PASSES = 2


def _tally(status: pd.Series) -> dict:
    """Count match_status values for the run summary."""
    s = status.fillna("")
    return {
        "total": int(len(s)),
        "rejected": int((s == "Rejected").sum()),
        "matched_gpkg": int((s == "Matched").sum()),
        "matched_api_backup": int((s == "Matched_CensusAPI_Backup").sum()),
        "matched_fallback": int((s == "Matched_Fallback").sum()),
        "matched_external": int((s == "Matched_External").sum()),
        "matched_zip_approx": int((s == "Matched_ZIP_Approx").sum()),
        "unmatched": int(s.isin(["No_Match", "Tie"]).sum()),
    }


def _census_fallback_pass(
    valid_df: pd.DataFrame,
    tracts,
    args,
    config: dict,
    addr_col: str,
    logger: logging.Logger,
    label: str,
    concurrency: int = 1,
) -> tuple:
    """Run ONE Census single-address fallback pass over the current No_Match/Tie
    rows of valid_df, re-join the newly geocoded rows to tracts, and return
    (valid_df, number_newly_matched). Called once normally, or repeatedly by the
    in-run retry-passes loop.
    """
    unmatched_mask = valid_df["match_status"].isin(["No_Match", "Tie"])
    unmatched = valid_df[unmatched_mask].copy()
    if len(unmatched) == 0:
        return valid_df, 0

    logger.info(
        f"{label}: Census single-address API for {len(unmatched)} "
        "unmatched/tie record(s)..."
    )
    fallback_results = geocode_fallback(
        unmatched_df=unmatched,
        address_col=addr_col,
        id_col=args.id_column,
        delay=config.get("geocoder", {}).get("fallback_delay", 0.5),
        concurrency=concurrency,
    )
    fallback_results["unique_id"] = fallback_results["unique_id"].astype(str)

    newly_matched = []
    for _, fb_row in fallback_results.iterrows():
        if fb_row["match_status"] == "Matched_Fallback":
            uid = str(fb_row["unique_id"])
            mask = valid_df[args.id_column] == uid
            valid_df.loc[mask, "latitude"] = fb_row["latitude"]
            valid_df.loc[mask, "longitude"] = fb_row["longitude"]
            valid_df.loc[mask, "match_status"] = "Matched_Fallback"
            valid_df.loc[mask, "matched_address"] = fb_row["matched_address"]
            valid_df.loc[mask, "census_api_geoid"] = fb_row.get("census_api_geoid")
            newly_matched.append(uid)

    # Re-run the spatial join for ONLY the records newly geocoded in this pass.
    if newly_matched:
        logger.info(
            f"  Re-running GeoPackage spatial join for {len(newly_matched)} "
            "newly geocoded record(s)..."
        )
        new_mask = valid_df[args.id_column].isin(newly_matched)
        new_rows = valid_df[new_mask].copy()
        new_rows = join_points_to_tracts(new_rows, tracts)

        # Census API GEOID backup for newly geocoded rows still null after join.
        nb_null = new_rows["census_tract_geoid"].isna()
        nb_api = new_rows.get("census_api_geoid", pd.Series(dtype=str)).notna()
        nb_use = nb_null & nb_api
        if nb_use.any():
            new_rows.loc[nb_use, "census_tract_geoid"] = new_rows.loc[
                nb_use, "census_api_geoid"
            ]
            new_rows.loc[nb_use, "match_status"] = "Matched_CensusAPI_Backup"

        new_rows = new_rows.drop(columns=["census_api_geoid"], errors="ignore")
        valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")
        valid_df.loc[new_mask, "census_tract_geoid"] = new_rows[
            "census_tract_geoid"
        ].values
        valid_df.loc[new_mask, "match_status"] = new_rows["match_status"].values

    valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")
    return valid_df, len(newly_matched)


def _zip_approx_pass(
    valid_df: pd.DataFrame,
    tracts,
    args,
    config: dict,
    logger: logging.Logger,
) -> tuple:
    """Last-resort fallback (runs AFTER all geocoding): assign an APPROXIMATE
    tract to rows no geocoder could place, using the centroid of the address's
    ZIP (its Census ZCTA). Labeled 'Matched_ZIP_Approx'. This is coarse — a ZIP
    spans many tracts — so it is a flagged approximation, not a precise location.
    Rows whose ZIP has no ZCTA (e.g. some dedicated PO-box ZIPs) stay No_Match.
    Returns (valid_df, number_assigned).
    """
    if not args.zip_column:
        logger.info("ZIP-approx skipped: no --zip-column configured.")
        return valid_df, 0
    mask = valid_df["match_status"].isin(["No_Match", "Tie"])
    if not mask.any():
        return valid_df, 0
    try:
        centroids = get_zcta_centroids(config.get("reference_dir", "data/reference"))
    except Exception as e:
        logger.warning(f"ZIP-approx skipped: could not load ZCTA centroids ({e}).")
        return valid_df, 0

    todo_idx = list(valid_df.index[mask])
    z5 = (
        valid_df.loc[todo_idx, args.zip_column]
        .fillna("")
        .astype(str)
        .str.extract(r"(\d{5})")[0]
    )
    sub_idx, lats, lons = [], [], []
    for idx in todo_idx:
        z = z5.get(idx)
        coord = centroids.get(z) if z else None
        if coord:
            sub_idx.append(idx)
            lats.append(coord[0])
            lons.append(coord[1])
    if not sub_idx:
        logger.info("ZIP-approx: no still-unmatched rows have a known ZCTA centroid.")
        return valid_df, 0

    logger.info(
        f"ZIP-approx: assigning an approximate tract to {len(sub_idx)} "
        "still-unmatched row(s) via ZIP/ZCTA centroid (Matched_ZIP_Approx)..."
    )
    pts = pd.DataFrame({"latitude": lats, "longitude": lons})
    joined = join_points_to_tracts(pts, tracts)  # preserves row order
    geoids = list(joined["census_tract_geoid"])
    n = 0
    for idx, geoid in zip(sub_idx, geoids):
        if pd.notna(geoid) and str(geoid) not in ("", "nan"):
            valid_df.at[idx, "census_tract_geoid"] = geoid
            valid_df.at[idx, "match_status"] = "Matched_ZIP_Approx"
            n += 1
    logger.info(f"ZIP-approx: assigned {n} approximate tract(s).")
    return valid_df, n


def _process_frame(
    df: pd.DataFrame,
    tracts,
    args,
    config: dict,
    use_fallback: bool,
    use_external_fallback: bool,
    use_zip_approx: bool,
    external_budget: list,
    concurrency: int,
    retry_passes: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    """
    Run the full geocoding + tract-assignment pipeline on one DataFrame and
    return the assembled output DataFrame.

    This is called once for a single-pass run, or once per chunk for a large,
    chunked run. Column validation, PHI screening, and the global unique-ID
    check are performed by the caller (main) before this runs, so this function
    assumes IDs are globally unique and required columns are present.

    external_budget is a single-element mutable list holding the number of
    addresses still permitted to be sent to the free external geocoder across
    the WHOLE run. It is shared across every chunk so the total number of
    external requests can never exceed the configured limit, no matter how many
    chunks the file is split into. This function decrements it as it consumes.
    """
    # ------------------------------------------------------------------
    # 4. Build a single address column (combine fields if needed)
    # ------------------------------------------------------------------
    ADDR_COL = "_address"
    if args.address_column:
        df[ADDR_COL] = df[args.address_column].astype(str).str.strip()
    else:
        df[ADDR_COL] = df.apply(
            lambda row: _combine_address_fields(
                row,
                args.street_column,
                args.city_column,
                args.state_column,
                args.zip_column,
            ),
            axis=1,
        )

    # ------------------------------------------------------------------
    # 5. Reject rows with missing ID or address
    # ------------------------------------------------------------------
    rejected_mask = (
        df[args.id_column].isna()
        | df[ADDR_COL].isna()
        | (df[ADDR_COL].str.strip() == "")
    )
    rejected_df = df[rejected_mask].copy()
    valid_df = df[~rejected_mask].copy()
    rejected_count = len(rejected_df)

    if rejected_count > 0:
        logger.info(f"{rejected_count} row(s) rejected due to missing ID or address.")

    # ------------------------------------------------------------------
    # 6-9b. Geocoding + tract assignment (only when there are valid rows)
    # ------------------------------------------------------------------
    if len(valid_df) > 0:
        # 6. Geocoding — Census Geography Batch API (address → coordinates).
        #    The geography endpoint also returns a tract GEOID from the Census
        #    API, stored as census_api_geoid and used only as backup below.
        logger.info(
            f"Geocoding {len(valid_df):,} records via Census Geography Batch API..."
        )

        valid_df[args.id_column] = valid_df[args.id_column].astype(str)

        # Route parsed address components to their own Census batch fields. When
        # separate street/city/state/zip columns are provided, sending each to
        # its own field is more reliable than one concatenated blob.
        if args.address_column:
            street_series = valid_df[ADDR_COL]
            city_series = state_series = zip_series = None
        else:
            street_series = (
                valid_df[args.street_column]
                if args.street_column
                else valid_df[ADDR_COL]
            )
            city_series = valid_df[args.city_column] if args.city_column else None
            state_series = valid_df[args.state_column] if args.state_column else None
            # Normalize ZIPs so a float-parsed value (37403.0) or a leading-zero
            # loss (07030 -> 7030) cannot degrade the batch match.
            zip_series = (
                valid_df[args.zip_column].map(normalize_zip)
                if args.zip_column
                else None
            )

        geo_results = geocode_batch(
            unique_ids=valid_df[args.id_column],
            street=street_series,
            city=city_series,
            state=state_series,
            zip_code=zip_series,
            batch_size=config.get("geocoder", {}).get("batch_size", 1000),
            timeout=config.get("geocoder", {}).get("batch_timeout", 120),
            concurrency=concurrency,
        )
        geo_results["unique_id"] = geo_results["unique_id"].astype(str)

        valid_df = valid_df.merge(
            geo_results[
                [
                    "unique_id",
                    "latitude",
                    "longitude",
                    "match_status",
                    "matched_address",
                    "census_api_geoid",
                ]
            ],
            left_on=args.id_column,
            right_on="unique_id",
            how="left",
        ).drop(columns=["unique_id"])

        # 7. PRIMARY tract assignment — GeoPackage spatial join (local).
        #    This is the authoritative source for census_tract_geoid.
        logger.info("Primary tract assignment: GeoPackage spatial join...")
        valid_df = join_points_to_tracts(valid_df, tracts)

        # 8. BACKUP tract assignment — Census API GEOID for records where the
        #    GeoPackage spatial join returned no tract (rare; e.g. coordinates
        #    on a tract boundary).
        gpkg_null = valid_df["census_tract_geoid"].isna()
        api_geoid_available = valid_df.get(
            "census_api_geoid", pd.Series(dtype=str)
        ).notna()
        has_coords = valid_df["match_status"].isin(["Matched", "Matched_Fallback"])

        use_api_backup = gpkg_null & api_geoid_available & has_coords
        backup_count = int(use_api_backup.sum())

        if backup_count > 0:
            logger.info(
                f"Census API GEOID backup applied to {backup_count} record(s) "
                "where GeoPackage spatial join returned no tract."
            )
            valid_df.loc[use_api_backup, "census_tract_geoid"] = valid_df.loc[
                use_api_backup, "census_api_geoid"
            ]
            valid_df.loc[use_api_backup, "match_status"] = "Matched_CensusAPI_Backup"

        valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")

        # 9. FALLBACK geocoding — Census single-address API, only for records
        #    the batch geocoder could not geocode at all. Runs an initial pass,
        #    then up to retry_passes more passes over whatever is still
        #    unmatched, stopping early as soon as a pass recovers nothing new
        #    (a genuine No_Match is deterministic, so extra passes only help when
        #    a transient API failure caused an earlier miss).
        if use_fallback:
            max_attempts = 1 + max(0, retry_passes)
            for attempt in range(max_attempts):
                label = (
                    "Fallback"
                    if attempt == 0
                    else f"Retry pass {attempt}/{retry_passes}"
                )
                valid_df, n_new = _census_fallback_pass(
                    valid_df,
                    tracts,
                    args,
                    config,
                    ADDR_COL,
                    logger,
                    label,
                    concurrency=concurrency,
                )
                # Stop once a pass produces no new matches (nothing left to
                # recover, or the residual is genuinely unmatchable).
                if n_new == 0:
                    break

        valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")

        # 9b. EXTERNAL geocoder fallback — free non-Census provider for the
        #     residual the Census geocoder cannot resolve (addresses not in the
        #     Census TIGER address-range file). Returns coordinates only; the
        #     tract is still assigned by the local GeoPackage spatial join, so
        #     the tract geometry stays authoritative.
        if use_external_fallback:
            ext_mask = valid_df["match_status"].isin(["No_Match", "Tie"])
            ext_unmatched = valid_df[ext_mask].copy()

            # Skip PO Box addresses: a PO box has no physical location, so no
            # geocoder can place it. Sending it to an external (often token- or
            # quota-metered) provider such as ArcGIS only wastes budget, so drop
            # PO boxes from the external pass. They remain No_Match.
            pobox_mask = (
                ext_unmatched[ADDR_COL]
                .fillna("")
                .str.contains(
                    r"\bP\.?\s*O\.?\s*BOX\b|\bPOST\s+OFFICE\s+BOX\b",
                    case=False,
                    regex=True,
                )
            )
            if pobox_mask.any():
                logger.info(
                    f"External fallback: skipping {int(pobox_mask.sum())} PO Box "
                    "address(es) (no physical location; left as No_Match, no "
                    "geocoder quota used)."
                )
                ext_unmatched = ext_unmatched[~pobox_mask].copy()

            # Enforce the run-wide external budget. external_budget[0] is the
            # number of addresses still permitted to reach the free geocoder
            # across ALL chunks; it is shared, so many small chunks cannot add
            # up to more than the configured total.
            remaining = external_budget[0]
            if remaining <= 0 and len(ext_unmatched) > 0:
                logger.warning(
                    f"External geocoder skipped: the run-wide budget of external "
                    "lookups is exhausted. Remaining unresolved addresses are "
                    "left as No_Match. Use ArcGIS with a token or a paid geocoder "
                    "to resolve a residual this large."
                )
            elif len(ext_unmatched) > remaining:
                # Only send up to the remaining budget; leave the rest as-is so a
                # free public geocoder is never over-used.
                logger.warning(
                    f"External geocoder budget-limited: geocoding only "
                    f"{remaining:,} of {len(ext_unmatched):,} unresolved addresses "
                    "to stay within the run-wide limit for a free public "
                    "geocoder. Use ArcGIS with a token or a paid geocoder to "
                    "resolve the rest."
                )
                ext_unmatched = ext_unmatched.head(remaining)

            if len(ext_unmatched) > 0 and remaining > 0:
                geo_cfg = config.get("geocoder", {})
                provider = args.external_provider or geo_cfg.get(
                    "external_provider", "nominatim"
                )
                logger.info(
                    f"External fallback ({provider}): {len(ext_unmatched)} "
                    "residual record(s)..."
                )
                # Consume the budget up front by the number actually sent.
                external_budget[0] -= len(ext_unmatched)
                ext_results = geocode_external(
                    unmatched_df=ext_unmatched,
                    address_col=ADDR_COL,
                    id_col=args.id_column,
                    provider=provider,
                    user_agent=geo_cfg.get(
                        "external_user_agent",
                        "jhfrc-address2tract/1.0 (research use)",
                    ),
                    arcgis_token=(
                        args.arcgis_token
                        or os.environ.get("ARCGIS_TOKEN")
                        or geo_cfg.get("arcgis_token")
                    ),
                    geoapify_key=(
                        args.geoapify_key
                        or os.environ.get("GEOAPIFY_KEY")
                        or geo_cfg.get("geoapify_key")
                    ),
                    delay=geo_cfg.get("external_delay", 1.1),
                    concurrency=concurrency,
                )
                ext_results["unique_id"] = ext_results["unique_id"].astype(str)

                external_count = 0
                for _, ex_row in ext_results.iterrows():
                    if ex_row["match_status"] == "Matched_External":
                        uid = str(ex_row["unique_id"])
                        mask = valid_df[args.id_column] == uid
                        valid_df.loc[mask, "latitude"] = ex_row["latitude"]
                        valid_df.loc[mask, "longitude"] = ex_row["longitude"]
                        valid_df.loc[mask, "match_status"] = "Matched_External"
                        external_count += 1

                # Assign tracts for the externally geocoded records via join.
                if external_count > 0:
                    logger.info("Spatial join for externally geocoded records...")
                    ext_join_mask = valid_df["match_status"] == "Matched_External"
                    ext_rows = valid_df[ext_join_mask].copy()
                    ext_rows = join_points_to_tracts(ext_rows, tracts)
                    valid_df.loc[ext_join_mask, "census_tract_geoid"] = ext_rows[
                        "census_tract_geoid"
                    ].values

    # Ensure the columns the assembly step needs exist even when the whole
    # frame was rejected (no geocoding ran).
    for col in [
        "latitude",
        "longitude",
        "match_status",
        "matched_address",
        "census_tract_geoid",
    ]:
        if col not in valid_df.columns:
            valid_df[col] = pd.Series([None] * len(valid_df), index=valid_df.index)

    # ------------------------------------------------------------------
    # 9c. ZIP-approximate fallback (LAST resort, after all geocoding incl.
    #     external). Assigns a coarse tract from the ZIP/ZCTA centroid to rows
    #     nothing else could place. Labeled Matched_ZIP_Approx.
    # ------------------------------------------------------------------
    if use_zip_approx:
        valid_df, _ = _zip_approx_pass(valid_df, tracts, args, config, logger)

    # ------------------------------------------------------------------
    # 10. Set error reasons
    # ------------------------------------------------------------------
    valid_df["error_reason"] = None

    # Any record still No_Match or Tie after all fallbacks could not be geocoded.
    no_coords = valid_df["match_status"].isin(["No_Match", "Tie"])
    valid_df.loc[no_coords, "error_reason"] = "Address could not be geocoded"

    # ZIP-approximate rows carry a note so they are never mistaken for a precise
    # geocode.
    zip_approx = valid_df["match_status"] == "Matched_ZIP_Approx"
    valid_df.loc[zip_approx, "error_reason"] = (
        "Approximate: tract assigned from the ZIP/ZCTA centroid, not an exact "
        "geocode"
    )

    matched_but_no_tract = (
        valid_df["match_status"].isin(
            [
                "Matched",
                "Matched_Fallback",
                "Matched_CensusAPI_Backup",
                "Matched_External",
            ]
        )
        & valid_df["census_tract_geoid"].isna()
    )
    valid_df.loc[matched_but_no_tract, "error_reason"] = (
        "Coordinates found but did not fall within a Census tract boundary"
    )

    # ------------------------------------------------------------------
    # 11. Assemble final output
    # ------------------------------------------------------------------
    valid_df = valid_df.rename(columns={"matched_address": "cleaned_address"})

    if rejected_count > 0:
        rejected_df[ADDR_COL] = rejected_df.get(ADDR_COL, "")
        rejected_df["latitude"] = None
        rejected_df["longitude"] = None
        rejected_df["match_status"] = "Rejected"
        rejected_df["cleaned_address"] = None
        rejected_df["census_tract_geoid"] = None
        rejected_df["error_reason"] = "Missing ID or address"
        output_df = pd.concat([valid_df, rejected_df], ignore_index=True)
    else:
        output_df = valid_df.copy()

    # Drop internal columns
    output_df = output_df.drop(
        columns=["latitude", "longitude", ADDR_COL], errors="ignore"
    )

    # Reorder output columns
    id_cols = [args.id_column]
    if args.address_column:
        addr_input_cols = [args.address_column]
    else:
        addr_input_cols = [
            c
            for c in [
                args.street_column,
                args.city_column,
                args.state_column,
                args.zip_column,
            ]
            if c
        ]
    tail_cols = [
        "cleaned_address",
        "census_tract_geoid",
        "match_status",
        "error_reason",
    ]
    ordered = id_cols + addr_input_cols + tail_cols
    output_df = output_df[[c for c in ordered if c in output_df.columns]]

    return output_df


# Tool-generated result columns, updated in place when merging a retry back into
# a previous results file.
_RESULT_COLS = ["cleaned_address", "census_tract_geoid", "match_status", "error_reason"]


def _run_retry_mode(
    tracts,
    args,
    config: dict,
    address_columns: list,
    use_fallback: bool,
    use_external_fallback: bool,
    use_zip_approx: bool,
    external_is_free_public: bool,
    external_max_residual: int,
    concurrency: int,
    retry_passes: int,
    logger: logging.Logger,
) -> dict:
    """Retry-from-file mode: re-process only the No_Match/Tie rows of a previous
    results file and merge any newly matched tracts back into the full file.
    Returns the aggregate tally for the summary.
    """
    # Retry mode loads the whole previous file into memory (it needs every row to
    # write the file back). The normal path auto-chunks above DEFAULT_CHUNK_THRESHOLD;
    # retry mode has no equivalent, so scan the size first (memory-bounded for CSV)
    # and warn before committing to a full-file load that could exhaust RAM.
    try:
        pre_scan = scan_input(
            args.input,
            args.id_column,
            address_columns=address_columns,
            sheet_name=args.sheet_name,
        )
        if pre_scan["n_rows"] > DEFAULT_CHUNK_THRESHOLD:
            logger.warning(
                f"Retry mode loads the entire file ({pre_scan['n_rows']:,} rows) "
                "into memory in one pass and does not chunk. If this exhausts "
                "memory, re-run the full file through normal (chunked) mode "
                "instead of --retry-failed."
            )
    except Exception:
        # A scan failure is non-fatal; the read below reports any real problem.
        pass

    try:
        prev = read_input(args.input, sheet_name=args.sheet_name)
    except Exception as e:
        print(f"\nError reading input file:\n  {e}")
        sys.exit(1)

    # The retry input must be a prior results file (needs the ID, the address
    # column(s), and a match_status column to know which rows failed).
    required = [args.id_column, "match_status"] + address_columns
    missing = [c for c in required if c not in prev.columns]
    if missing:
        print(
            "\nError: --retry-failed expects a PREVIOUS results file, but these "
            "required columns were not found:"
        )
        for c in missing:
            print(f"  - {c}")
        print(f"\nColumns found in the file: {list(prev.columns)}")
        sys.exit(1)

    try:
        validate_no_phi(list(prev.columns))
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    prev[args.id_column] = prev[args.id_column].astype(str)

    # Duplicate IDs would cross-contaminate rows during the merge-back (a single
    # retried result would overwrite every row sharing that ID, and a duplicate
    # inside the retried subset raises on reindex). Fail fast, mirroring the
    # normal path's duplicate-ID guard.
    dup_mask = prev[args.id_column].duplicated(keep=False)
    if dup_mask.any():
        dup_ids = list(prev.loc[dup_mask, args.id_column].unique()[:5])
        print(
            f"\nError: the ID column '{args.id_column}' in the retry file contains "
            f"duplicate values ({int(dup_mask.sum())} row(s))."
        )
        print(
            "Each row must have a unique identifier so retried results can be "
            "matched back correctly."
        )
        print(f"Example duplicate IDs: {dup_ids}")
        sys.exit(1)

    failed_mask = prev["match_status"].isin(["No_Match", "Tie"])
    n_failed = int(failed_mask.sum())

    if n_failed == 0:
        logger.info("Retry mode: no No_Match/Tie rows found; writing file unchanged.")
        try:
            write_output(prev, args.output)
        except Exception as e:
            print(f"\nError writing output file:\n  {e}")
            sys.exit(1)
        return _tally(prev["match_status"])

    logger.info(f"Retry mode: re-processing {n_failed:,} No_Match/Tie row(s)...")

    # Feed only the original input columns back through the pipeline; drop the
    # tool-generated result columns so the fresh geocode does not collide with
    # the previous run's values.
    subset = (
        prev[failed_mask]
        .drop(columns=[c for c in _RESULT_COLS if c in prev.columns], errors="ignore")
        .copy()
    )

    external_budget = (
        [external_max_residual]
        if external_is_free_public
        else [max(n_failed, external_max_residual)]
    )

    retry_out = _process_frame(
        subset,
        tracts,
        args,
        config,
        use_fallback,
        use_external_fallback,
        use_zip_approx,
        external_budget,
        concurrency,
        retry_passes,
        logger,
    )
    retry_out[args.id_column] = retry_out[args.id_column].astype(str)

    # Merge the refreshed result columns back onto the retried rows, keyed on the
    # unique ID. Untouched rows keep their original values.
    updated = prev.set_index(args.id_column)
    ridx = retry_out.set_index(args.id_column)
    for col in _RESULT_COLS:
        if col in ridx.columns:
            # Add any result column the previous file lacked (e.g. an older or
            # hand-built failures file with no census_tract_geoid) so a recovered
            # tract is written rather than silently dropped.
            if col not in updated.columns:
                updated[col] = pd.NA
            updated.loc[ridx.index, col] = ridx[col]
    # Preserve the previous column order, then append any result columns that were
    # newly added above so they survive the output projection.
    out_cols = list(prev.columns) + [c for c in _RESULT_COLS if c not in prev.columns]
    updated = updated.reset_index()[out_cols]

    newly_matched = int(
        ridx["match_status"].astype(str).str.startswith("Matched").sum()
    )
    logger.info(
        f"Retry mode: recovered {newly_matched:,} of {n_failed:,} previously "
        "unmatched record(s)."
    )

    try:
        write_output(updated, args.output)
    except Exception as e:
        print(f"\nError writing output file:\n  {e}")
        sys.exit(1)

    return _tally(updated["match_status"])


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = _load_config(args.config)
    _setup_logging(config.get("log_level", "INFO"))
    logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # EGRESS GATE. This tool geocodes by sending addresses to a geocoding
    # service, which is an outbound network call. To guarantee no data leaves
    # this machine without explicit approval, the tool refuses to run any
    # network step unless --approve-egress is passed. Nothing is ever sent to
    # Anthropic; the model that wrote this code does not run it.
    # ------------------------------------------------------------------
    if not args.approve_egress:
        geo_cfg = config.get("geocoder", {})
        provider = args.external_provider or geo_cfg.get(
            "external_provider", "nominatim"
        )
        ext_on = config.get("use_external_fallback", True) and not (
            args.no_external_fallback
        )
        print()
        print("=" * 64)
        print("  EGRESS NOT APPROVED — nothing was sent. Dry run only.")
        print("=" * 64)
        print("  This run WOULD make outbound requests to:")
        print("    - geocoding.geo.census.gov   (Census geocoder: your")
        print("      addresses -> coordinates)")
        if ext_on:
            dest = {
                "geoapify": "api.geoapify.com",
                "arcgis": "geocode.arcgis.com",
                "nominatim": "nominatim.openstreetmap.org",
            }.get(provider, provider)
            print(f"    - {dest}   (external fallback for the residual)")
        print("    - www2.census.gov   (one-time tract-map download, only if")
        print("      the local tract file is missing; also the ZCTA centroid")
        print("      file when --zip-approx is used)")
        print()
        print("  It sends NOTHING to Anthropic / Claude. Your input file is")
        print("  read and written only on this machine.")
        print()
        print("  To authorize these requests and run for real, re-run with:")
        print("      --approve-egress")
        print("=" * 64)
        sys.exit(0)

    logger.info(
        "Egress approved by --approve-egress. Outbound geocoding enabled "
        "(Census / external provider only; nothing to Anthropic)."
    )

    # Determine fallback setting (CLI flags override config)
    use_fallback = config.get("use_fallback", True)
    if args.no_fallback:
        use_fallback = False
    elif args.use_fallback:
        use_fallback = True

    # Determine external-fallback setting (CLI flags override config)
    use_external_fallback = config.get("use_external_fallback", False)
    if args.no_external_fallback:
        use_external_fallback = False
    elif args.use_external_fallback:
        use_external_fallback = True

    # ZIP-approximate last-resort fallback (off by default; CLI flag or config).
    use_zip_approx = bool(args.zip_approx) or config.get("use_zip_approx", False)

    # Determine reference data directory
    if args.tract_dataset:
        reference_dir = Path(args.tract_dataset).parent
    else:
        reference_dir = Path(config.get("reference_dir", "data/reference"))

    geo_cfg = config.get("geocoder", {})
    external_provider = args.external_provider or geo_cfg.get(
        "external_provider", "nominatim"
    )
    # Whether the configured external provider is the free, rate-limited public
    # service (OpenStreetMap / Nominatim). Bulk-capable providers such as ArcGIS
    # (which require a token) are exempt from the scale caps below.
    external_is_free_public = external_provider == "nominatim"
    # Run-wide budget on how many unresolved addresses may be sent to the free
    # external geocoder, so the total across all chunks cannot spam a public
    # service. Only enforced for the free provider.
    external_max_residual = int(geo_cfg.get("external_max_residual", 2000))
    # File-wide cap: above this many input rows the FREE external geocoder is
    # disabled automatically (a very large residual would be abusive on a free
    # public service). ArcGIS/paid providers are not subject to this.
    external_max_rows = int(geo_cfg.get("external_max_rows", 100000))

    # Census batch concurrency (CLI overrides config; default 6). Clamp to >= 1.
    if args.concurrency is not None:
        concurrency = args.concurrency
    else:
        # A present-but-null YAML key (`concurrency:` with no value) parses to
        # None, which .get() does NOT replace with the default; coalesce it here.
        concurrency = geo_cfg.get("concurrency")
        if concurrency is None:
            concurrency = DEFAULT_CONCURRENCY
    try:
        concurrency = max(1, int(concurrency))
    except (TypeError, ValueError):
        print(
            f"\nError: invalid geocoder.concurrency in config: {concurrency!r} "
            "(expected an integer)."
        )
        sys.exit(1)

    # In-run retry passes over still-unmatched rows (CLI overrides config;
    # default 2). Clamp to >= 0.
    if args.retry_passes is not None:
        retry_passes = args.retry_passes
    else:
        retry_passes = geo_cfg.get("retry_passes")
        if retry_passes is None:
            retry_passes = DEFAULT_RETRY_PASSES
    try:
        retry_passes = max(0, int(retry_passes))
    except (TypeError, ValueError):
        print(
            f"\nError: invalid geocoder.retry_passes in config: {retry_passes!r} "
            "(expected an integer)."
        )
        sys.exit(1)

    print()
    print("=== JHFRC Address to Census Tract Converter ===")
    print()

    # The column(s) that make up the address, used both to validate the input
    # and to decide which rows would be rejected during the duplicate-ID scan.
    if args.address_column:
        address_columns = [args.address_column]
    else:
        address_columns = [
            c
            for c in [
                args.street_column,
                args.city_column,
                args.state_column,
                args.zip_column,
            ]
            if c
        ]

    # ------------------------------------------------------------------
    # Retry-from-file mode: re-process only the failed rows of a previous
    # results file, merge matches back in, and finish. Skips the normal
    # scan / chunk / resume flow entirely.
    # ------------------------------------------------------------------
    if args.retry_failed:
        try:
            tracts = get_tract_dataset(
                reference_dir,
                source=config.get("tract_source", "cb500k"),
                states=config.get("tract_states"),
            )
        except Exception as e:
            print(f"\nError loading Census tract dataset:\n  {e}")
            sys.exit(1)

        agg = _run_retry_mode(
            tracts,
            args,
            config,
            address_columns,
            use_fallback,
            use_external_fallback,
            use_zip_approx,
            external_is_free_public,
            external_max_residual,
            concurrency,
            retry_passes,
            logger,
        )
        _print_summary(
            total=agg["total"],
            matched_gpkg=agg["matched_gpkg"],
            matched_api_backup=agg["matched_api_backup"],
            matched_fallback=agg["matched_fallback"],
            matched_external=agg["matched_external"],
            matched_zip_approx=agg["matched_zip_approx"],
            unmatched=agg["unmatched"],
            rejected=agg["rejected"],
        )
        return

    # ------------------------------------------------------------------
    # 1. Scan the input file: column names, row count, duplicate IDs.
    #    (CSV is scanned without loading the whole file into memory.)
    # ------------------------------------------------------------------
    logger.info(f"Scanning input file: {args.input}")
    try:
        scan = scan_input(
            args.input,
            args.id_column,
            address_columns=address_columns,
            sheet_name=args.sheet_name,
        )
    except Exception as e:
        print(f"\nError reading input file:\n  {e}")
        sys.exit(1)

    columns = scan["columns"]
    n_rows = scan["n_rows"]
    logger.info(f"Input has {n_rows:,} rows.")

    # ------------------------------------------------------------------
    # 2. Validate that required columns exist
    # ------------------------------------------------------------------
    required_cols = [args.id_column] + address_columns

    missing = [c for c in required_cols if c not in columns]
    if missing:
        print("\nError: The following columns were not found in the input file:")
        for c in missing:
            print(f"  - {c}")
        print(f"\nColumns found in the file: {columns}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. PHI / sensitive data check
    # ------------------------------------------------------------------
    logger.info("Checking for sensitive data columns...")
    try:
        validate_no_phi(columns)
    except ValueError as e:
        print(str(e))
        sys.exit(1)
    logger.info("No sensitive columns detected. Proceeding.")

    # ------------------------------------------------------------------
    # 4. Global unique-ID check. The pipeline keys results on the ID column,
    #    so IDs must be unique across the ENTIRE file (not just within a
    #    chunk). Duplicate IDs would cross-contaminate rows. Fail fast.
    # ------------------------------------------------------------------
    if scan["n_duplicate_rows"] > 0:
        print(
            f"\nError: the ID column '{args.id_column}' contains duplicate "
            f"values ({scan['n_duplicate_rows']} row(s)).\n"
            "Each row must have a unique identifier so results can be matched "
            "back correctly.\n"
            f"Example duplicate IDs: {scan['duplicate_ids']}"
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # 5. Decide single-pass vs. chunked processing.
    #    Chunking turns on automatically for large files so the user does not
    #    have to think about memory. --chunk-size overrides; --chunk-size 0
    #    forces a single pass.
    # ------------------------------------------------------------------
    if args.chunk_size is not None and args.chunk_size < 0:
        print(
            "\nError: --chunk-size must be 0 (force a single pass) or a positive "
            f"integer. You provided: {args.chunk_size}"
        )
        sys.exit(1)

    if args.chunk_size == 0:
        chunk_size_val = None
        use_chunking = False
    else:
        chunk_size_val = (
            args.chunk_size
            if args.chunk_size
            else config.get("chunk_size") or DEFAULT_CHUNK_THRESHOLD
        )
        use_chunking = n_rows > chunk_size_val

    # At large scale, disable the FREE external geocoder automatically to avoid
    # spamming a public service with a huge residual. This applies only to the
    # free, rate-limited provider (OpenStreetMap / Nominatim); a bulk-capable
    # provider such as ArcGIS (configured with a token) is built for volume and
    # keeps running on the residual at any scale.
    if use_external_fallback and external_is_free_public and n_rows > external_max_rows:
        logger.warning(
            f"Free external geocoder (OpenStreetMap) auto-disabled: {n_rows:,} "
            f"input rows exceed the safe limit ({external_max_rows:,}) for a free "
            "public service, whose policy prohibits bulk use. The Census geocoder "
            "still runs. To geocode the residual at this scale, switch "
            "external_provider to 'arcgis' with a token, or use a paid geocoder."
        )
        use_external_fallback = False

    # ------------------------------------------------------------------
    # 6. Load the Census tract reference dataset once.
    # ------------------------------------------------------------------
    try:
        tracts = get_tract_dataset(
            reference_dir,
            source=config.get("tract_source", "cb500k"),
            states=config.get("tract_states"),
        )
    except Exception as e:
        print(f"\nError loading Census tract dataset:\n  {e}")
        sys.exit(1)

    # Run-wide budget for the external geocoder, shared across all chunks so the
    # total number of requests can never exceed this limit. The free public
    # provider is held to external_max_residual; a bulk-capable provider such as
    # ArcGIS is allowed to work the entire residual (bounded only by row count).
    if external_is_free_public:
        external_budget = [external_max_residual]
    else:
        external_budget = [max(n_rows, external_max_residual)]

    # ------------------------------------------------------------------
    # 7. Process — single pass, or chunked with resume.
    # ------------------------------------------------------------------
    if not use_chunking:
        try:
            df = read_input(args.input, sheet_name=args.sheet_name)
        except Exception as e:
            print(f"\nError reading input file:\n  {e}")
            sys.exit(1)

        output_df = _process_frame(
            df,
            tracts,
            args,
            config,
            use_fallback,
            use_external_fallback,
            use_zip_approx,
            external_budget,
            concurrency,
            retry_passes,
            logger,
        )

        try:
            write_output(output_df, args.output)
        except Exception as e:
            print(f"\nError writing output file:\n  {e}")
            sys.exit(1)

        agg = _tally(output_df["match_status"])
    else:
        # Chunked mode writes CSV (Excel caps at ~1,048,576 rows and cannot hold
        # a million-plus record output).
        output_path = Path(args.output)
        if output_path.suffix.lower() in (".xlsx", ".xls"):
            output_path = output_path.with_suffix(".csv")
            logger.warning(
                "Chunked mode writes CSV, not Excel (Excel is limited to about "
                f"1,048,576 rows). Output will be: {output_path}"
            )

        parts_dir = output_path.parent / (output_path.stem + "_parts")
        parts_dir.mkdir(parents=True, exist_ok=True)

        # Resume safety: existing part files are only trusted when they came from
        # a run over the SAME input file and the SAME chunk size. Otherwise the
        # chunk boundaries differ and reusing old parts would duplicate or drop
        # rows. A manifest records that identity; on any mismatch we refuse to
        # resume rather than silently produce a corrupt output.
        input_stat = Path(args.input).stat()
        run_signature = {
            "chunk_size": chunk_size_val,
            "input_path": str(Path(args.input).resolve()),
            "input_size": input_stat.st_size,
            "input_mtime": int(input_stat.st_mtime),
            "input_sha256": _file_sha256(Path(args.input)),
            "n_rows": n_rows,
        }
        manifest_path = parts_dir / "manifest.json"
        existing_parts = sorted(parts_dir.glob("part_*.csv"))
        if manifest_path.exists():
            try:
                manifest_state = json.loads(manifest_path.read_text())
                prev_signature = manifest_state.get("run_signature")
            except Exception:
                manifest_state = {}
                prev_signature = None
            if prev_signature != run_signature:
                print(
                    "\nError: the output part folder already contains results from "
                    "a different run\n"
                    f"  {parts_dir}\n"
                    "The input file or the chunk size has changed since those parts "
                    "were created, so they cannot be safely reused (the chunk "
                    "boundaries would not line up).\n"
                    "Either re-run with the original input file and chunk size, or "
                    "delete that folder to start fresh."
                )
                sys.exit(1)
            saved_budget = manifest_state.get("external_budget_remaining")
            if external_is_free_public and isinstance(saved_budget, int):
                external_budget[0] = min(external_budget[0], max(saved_budget, 0))
        elif existing_parts:
            print(
                "\nError: the output part folder contains part files but no "
                "manifest\n"
                f"  {parts_dir}\n"
                "These parts are from an older version or an unknown run and "
                "cannot be safely reused. Delete that folder to start fresh."
            )
            sys.exit(1)
        else:
            manifest_state = {
                "run_signature": run_signature,
                "external_budget_remaining": external_budget[0],
            }
            manifest_path.write_text(json.dumps(manifest_state, indent=2))

        logger.info(
            f"Large file ({n_rows:,} rows): chunked processing at "
            f"{chunk_size_val:,} rows per chunk. Part files: {parts_dir}"
        )

        agg = defaultdict(int)
        part_paths = []
        for idx, chunk in iter_input_chunks(
            args.input, chunk_size_val, sheet_name=args.sheet_name
        ):
            part_path = parts_dir / f"part_{idx:05d}.csv"
            if part_path.exists():
                # Resume: this chunk was already completed in a prior run.
                try:
                    _validate_resumed_part(
                        part_path,
                        expected_rows=len(chunk),
                        required_columns=set(chunk.columns).union(_RESULT_COLS),
                    )
                except ValueError as exc:
                    print(
                        "\nError: an existing resume part failed validation:\n"
                        f"  {part_path}\n  {exc}\n"
                        "Move the invalid part out of the parts folder and run "
                        "the command again."
                    )
                    sys.exit(1)
                logger.info(
                    f"Chunk {idx}: already complete ({part_path.name}), skipping."
                )
            else:
                out_df = _process_frame(
                    chunk,
                    tracts,
                    args,
                    config,
                    use_fallback,
                    use_external_fallback,
                    use_zip_approx,
                    external_budget,
                    concurrency,
                    retry_passes,
                    logger,
                )
                # Write atomically: full write to a temp file, then rename, so an
                # interrupted run never leaves a half-written part behind.
                tmp_path = parts_dir / f"part_{idx:05d}.csv.tmp"
                out_df.to_csv(tmp_path, index=False)
                tmp_path.replace(part_path)
                logger.info(
                    f"Chunk {idx}: {len(out_df):,} rows written to {part_path.name}"
                )
                manifest_state["external_budget_remaining"] = external_budget[0]
                manifest_tmp = manifest_path.with_suffix(".json.tmp")
                manifest_tmp.write_text(json.dumps(manifest_state, indent=2))
                manifest_tmp.replace(manifest_path)

            # Tally from the part file so resumed (skipped) chunks still count.
            status = pd.read_csv(part_path, usecols=["match_status"], dtype=str)[
                "match_status"
            ]
            for k, v in _tally(status).items():
                agg[k] += v
            part_paths.append(str(part_path))

        concat_csv_parts(part_paths, str(output_path))
        logger.info(
            f"Combined {len(part_paths)} part file(s) into {output_path} "
            f"({agg['total']:,} rows). Part files kept in {parts_dir} for "
            "resume safety; you may delete that folder once the output looks good."
        )

    # ------------------------------------------------------------------
    # 8. Print summary
    # ------------------------------------------------------------------
    _print_summary(
        total=agg["total"],
        matched_gpkg=agg["matched_gpkg"],
        matched_api_backup=agg["matched_api_backup"],
        matched_fallback=agg["matched_fallback"],
        matched_external=agg["matched_external"],
        matched_zip_approx=agg["matched_zip_approx"],
        unmatched=agg["unmatched"],
        rejected=agg["rejected"],
    )


if __name__ == "__main__":
    main()
