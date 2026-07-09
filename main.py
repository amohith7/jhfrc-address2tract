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

import sys
import logging
from pathlib import Path

# Add src/ to the module search path so imports work cleanly
sys.path.insert(0, str(Path(__file__).parent / "src"))

import argparse

import pandas as pd
import yaml

from phi_validator import validate_no_phi
from geocode import geocode_batch, geocode_fallback, normalize_zip
from geocode_external import geocode_external
from tract_join import get_tract_dataset, join_points_to_tracts
from utils.io import read_input, write_output


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
        "--input", required=True, help="Path to the input file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--output", required=True, help="Path for the output file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--id-column",
        required=True,
        help="Name of the column containing unique identifiers.",
    )

    # Address input: full address OR separate fields
    addr_group = parser.add_mutually_exclusive_group(required=True)
    addr_group.add_argument(
        "--address-column", help="Name of the column containing full addresses.",
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
) -> None:
    print()
    print("=" * 56)
    print("  PROCESSING SUMMARY")
    print("=" * 56)
    print(f"  Total rows processed          : {total:,}")
    print(f"  Matched — GeoPackage (primary): {matched_gpkg:,}")
    print(f"  Matched — Census API (backup) : {matched_api_backup:,}")
    print(f"  Matched — Census fallback     : {matched_fallback:,}")
    print(f"  Matched — External geocoder   : {matched_external:,}")
    print(f"  Unmatched                     : {unmatched:,}")
    print(f"  Rejected (missing data)       : {rejected:,}")
    print("=" * 56)
    print()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = _load_config(args.config)
    _setup_logging(config.get("log_level", "INFO"))
    logger = logging.getLogger(__name__)

    # Determine fallback setting (CLI flags override config)
    use_fallback = config.get("use_fallback", True)
    if args.no_fallback:
        use_fallback = False
    elif args.use_fallback:
        use_fallback = True

    # Determine reference data directory
    if args.tract_dataset:
        reference_dir = Path(args.tract_dataset).parent
    else:
        reference_dir = Path(config.get("reference_dir", "data/reference"))

    print()
    print("=== JHFRC Address to Census Tract Converter ===")
    print()

    # ------------------------------------------------------------------
    # 1. Read input file
    # ------------------------------------------------------------------
    logger.info(f"Reading input file: {args.input}")
    try:
        df = read_input(args.input, sheet_name=args.sheet_name)
    except Exception as e:
        print(f"\nError reading input file:\n  {e}")
        sys.exit(1)

    logger.info(f"Loaded {len(df):,} rows.")

    # ------------------------------------------------------------------
    # 2. Validate that required columns exist
    # ------------------------------------------------------------------
    required_cols = [args.id_column]
    if args.address_column:
        required_cols.append(args.address_column)
    else:
        for col in [
            args.street_column,
            args.city_column,
            args.state_column,
            args.zip_column,
        ]:
            if col:
                required_cols.append(col)

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print("\nError: The following columns were not found in the input file:")
        for c in missing:
            print(f"  - {c}")
        print(f"\nColumns found in the file: {list(df.columns)}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. PHI / sensitive data check
    # ------------------------------------------------------------------
    logger.info("Checking for sensitive data columns...")
    try:
        validate_no_phi(list(df.columns))
    except ValueError as e:
        print(str(e))
        sys.exit(1)
    logger.info("No sensitive columns detected. Proceeding.")

    # ------------------------------------------------------------------
    # 4. Build a single address column (combine fields if needed)
    # ------------------------------------------------------------------
    ADDR_COL = "_address"
    if args.address_column:
        df[ADDR_COL] = df[args.address_column].astype(str).str.strip()
    else:
        logger.info("Combining address fields into a single address column...")
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

    # The pipeline merges and writes results keyed on the ID column, so IDs must
    # be unique. Duplicate IDs would explode the merge and cross-contaminate
    # rows (one record's coordinates written onto another). Fail fast and clearly
    # rather than silently corrupt the output.
    dup_ids = valid_df[args.id_column].astype(str)
    dup_mask = dup_ids.duplicated(keep=False)
    if dup_mask.any():
        examples = sorted(dup_ids[dup_mask].unique())[:10]
        print(
            f"\nError: the ID column '{args.id_column}' contains duplicate "
            f"values ({int(dup_mask.sum())} row(s)).\n"
            "Each row must have a unique identifier so results can be matched "
            "back correctly.\n"
            f"Example duplicate IDs: {examples}"
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # 6. Geocoding — Census Geography Batch API (address → coordinates)
    #    The geography endpoint also returns a tract GEOID from the Census
    #    API, stored as census_api_geoid and used only as backup below.
    # ------------------------------------------------------------------
    logger.info(
        f"Geocoding {len(valid_df):,} records via Census Geography Batch API..."
    )

    valid_df[args.id_column] = valid_df[args.id_column].astype(str)

    # Route parsed address components to their own Census batch fields. When
    # separate street/city/state/zip columns are provided, sending each to its
    # own field is more reliable than one concatenated blob in the street field.
    if args.address_column:
        street_series = valid_df[ADDR_COL]
        city_series = state_series = zip_series = None
    else:
        street_series = (
            valid_df[args.street_column] if args.street_column else valid_df[ADDR_COL]
        )
        city_series = valid_df[args.city_column] if args.city_column else None
        state_series = valid_df[args.state_column] if args.state_column else None
        # Normalize ZIPs so a float-parsed value (37403.0) or a leading-zero
        # loss (07030 -> 7030) cannot degrade the batch match.
        zip_series = (
            valid_df[args.zip_column].map(normalize_zip) if args.zip_column else None
        )

    geo_results = geocode_batch(
        unique_ids=valid_df[args.id_column],
        street=street_series,
        city=city_series,
        state=state_series,
        zip_code=zip_series,
        batch_size=config.get("geocoder", {}).get("batch_size", 1000),
        timeout=config.get("geocoder", {}).get("batch_timeout", 120),
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

    # ------------------------------------------------------------------
    # 7. PRIMARY tract assignment — GeoPackage spatial join (local)
    #    All records with coordinates are joined against the local
    #    GeoPackage. This is the authoritative source for census_tract_geoid.
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

    logger.info("Primary tract assignment: GeoPackage spatial join...")
    valid_df = join_points_to_tracts(valid_df, tracts)

    # ------------------------------------------------------------------
    # 8. BACKUP tract assignment — Census API GEOID
    #    For records where the GeoPackage spatial join returned no tract
    #    (rare — e.g. coordinates near a tract boundary), use the GEOID
    #    returned directly by the Census Geography batch API as backup.
    # ------------------------------------------------------------------
    gpkg_null = valid_df["census_tract_geoid"].isna()
    api_geoid_available = valid_df.get("census_api_geoid", pd.Series(dtype=str)).notna()
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

    # ------------------------------------------------------------------
    # 9. FALLBACK geocoding — Census single-address API
    #    Only for records the batch geocoder could not geocode at all.
    #    After getting coordinates, GeoPackage spatial join runs again.
    # ------------------------------------------------------------------
    fallback_count = 0
    if use_fallback:
        # Both true No_Match and ambiguous Tie results are recoverable by the
        # single-address endpoint, so route both into the fallback.
        unmatched_mask = valid_df["match_status"].isin(["No_Match", "Tie"])
        unmatched_for_fallback = valid_df[unmatched_mask].copy()

        if len(unmatched_for_fallback) > 0:
            logger.info(
                f"Fallback: Census single-address API for "
                f"{len(unmatched_for_fallback)} unmatched/tie record(s)..."
            )
            fallback_results = geocode_fallback(
                unmatched_df=unmatched_for_fallback,
                address_col=ADDR_COL,
                id_col=args.id_column,
                delay=config.get("geocoder", {}).get("fallback_delay", 0.5),
            )
            fallback_results["unique_id"] = fallback_results["unique_id"].astype(str)

            for _, fb_row in fallback_results.iterrows():
                uid = str(fb_row["unique_id"])
                if fb_row["match_status"] == "Matched_Fallback":
                    mask = valid_df[args.id_column] == uid
                    valid_df.loc[mask, "latitude"] = fb_row["latitude"]
                    valid_df.loc[mask, "longitude"] = fb_row["longitude"]
                    valid_df.loc[mask, "match_status"] = "Matched_Fallback"
                    valid_df.loc[mask, "matched_address"] = fb_row["matched_address"]
                    valid_df.loc[mask, "census_api_geoid"] = fb_row.get(
                        "census_api_geoid"
                    )
                    fallback_count += 1

            # Re-run GeoPackage spatial join for newly geocoded fallback records
            if fallback_count > 0:
                logger.info(
                    "Re-running GeoPackage spatial join for fallback-geocoded records..."
                )
                fallback_mask = valid_df["match_status"] == "Matched_Fallback"
                fallback_rows = valid_df[fallback_mask].copy()
                fallback_rows = join_points_to_tracts(fallback_rows, tracts)

                # Apply Census API backup for fallback rows where GeoPackage still null
                fb_gpkg_null = fallback_rows["census_tract_geoid"].isna()
                fb_api_available = fallback_rows.get(
                    "census_api_geoid", pd.Series(dtype=str)
                ).notna()
                fb_use_backup = fb_gpkg_null & fb_api_available
                if fb_use_backup.any():
                    fallback_rows.loc[
                        fb_use_backup, "census_tract_geoid"
                    ] = fallback_rows.loc[fb_use_backup, "census_api_geoid"]
                    fallback_rows.loc[
                        fb_use_backup, "match_status"
                    ] = "Matched_CensusAPI_Backup"

                fallback_rows = fallback_rows.drop(
                    columns=["census_api_geoid"], errors="ignore"
                )
                valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")

                # Merge fallback spatial join results back
                valid_df.loc[fallback_mask, "census_tract_geoid"] = fallback_rows[
                    "census_tract_geoid"
                ].values
                valid_df.loc[fallback_mask, "match_status"] = fallback_rows[
                    "match_status"
                ].values

    valid_df = valid_df.drop(columns=["census_api_geoid"], errors="ignore")

    # ------------------------------------------------------------------
    # 9b. EXTERNAL geocoder fallback — free non-Census provider
    #     For the residual the Census geocoder cannot resolve (addresses not in
    #     the Census TIGER address-range file). Returns coordinates only; the
    #     tract is still assigned by the local GeoPackage spatial join, so the
    #     tract geometry stays authoritative. Results are labeled distinctly.
    # ------------------------------------------------------------------
    if config.get("use_external_fallback", False):
        ext_mask = valid_df["match_status"].isin(["No_Match", "Tie"])
        ext_unmatched = valid_df[ext_mask].copy()

        if len(ext_unmatched) > 0:
            geo_cfg = config.get("geocoder", {})
            provider = geo_cfg.get("external_provider", "nominatim")
            logger.info(
                f"External fallback ({provider}): {len(ext_unmatched)} "
                "residual record(s)..."
            )
            ext_results = geocode_external(
                unmatched_df=ext_unmatched,
                address_col=ADDR_COL,
                id_col=args.id_column,
                provider=provider,
                user_agent=geo_cfg.get(
                    "external_user_agent", "jhfrc-address2tract/1.0 (research use)"
                ),
                arcgis_token=geo_cfg.get("arcgis_token"),
                delay=geo_cfg.get("external_delay", 1.1),
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

            # Assign tracts for the externally geocoded records via local join.
            if external_count > 0:
                logger.info("Spatial join for externally geocoded records...")
                ext_join_mask = valid_df["match_status"] == "Matched_External"
                ext_rows = valid_df[ext_join_mask].copy()
                ext_rows = join_points_to_tracts(ext_rows, tracts)
                valid_df.loc[ext_join_mask, "census_tract_geoid"] = ext_rows[
                    "census_tract_geoid"
                ].values

    # ------------------------------------------------------------------
    # 10. Set error reasons
    # ------------------------------------------------------------------
    valid_df["error_reason"] = None

    # Any record still No_Match or Tie after all fallbacks could not be geocoded.
    no_coords = valid_df["match_status"].isin(["No_Match", "Tie"])
    valid_df.loc[no_coords, "error_reason"] = "Address could not be geocoded"

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
    valid_df.loc[
        matched_but_no_tract, "error_reason"
    ] = "Coordinates found but did not fall within a Census tract boundary"

    # ------------------------------------------------------------------
    # 10. Assemble final output
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

    # ------------------------------------------------------------------
    # 11. Write output
    # ------------------------------------------------------------------
    try:
        write_output(output_df, args.output)
    except Exception as e:
        print(f"\nError writing output file:\n  {e}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 12. Print summary
    # ------------------------------------------------------------------
    matched_gpkg = int((valid_df["match_status"] == "Matched").sum())
    matched_api_backup = int(
        (valid_df["match_status"] == "Matched_CensusAPI_Backup").sum()
    )
    matched_fb = int((valid_df["match_status"] == "Matched_Fallback").sum())
    matched_ext = int((valid_df["match_status"] == "Matched_External").sum())
    unmatched_final = int(valid_df["match_status"].isin(["No_Match", "Tie"]).sum())

    _print_summary(
        total=len(df),
        matched_gpkg=matched_gpkg,
        matched_api_backup=matched_api_backup,
        matched_fallback=matched_fb,
        matched_external=matched_ext,
        unmatched=unmatched_final,
        rejected=rejected_count,
    )


if __name__ == "__main__":
    main()
