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
from geocode import geocode_batch, geocode_fallback
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
        "--input", required=True,
        help="Path to the input file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--output", required=True,
        help="Path for the output file (.xlsx or .csv).",
    )
    parser.add_argument(
        "--id-column", required=True,
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
    parser.add_argument("--city-column",  help="Name of the city column.")
    parser.add_argument("--state-column", help="Name of the state column.")
    parser.add_argument("--zip-column",   help="Name of the ZIP code column.")

    # Optional settings
    parser.add_argument(
        "--tract-dataset",
        help=(
            "Path to an existing Census tract GeoPackage "
            "(default: data/reference/census_tracts.gpkg)."
        ),
    )
    parser.add_argument(
        "--use-fallback", action="store_true", default=None,
        help="Enable Census API fallback geocoding for unmatched records.",
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="Disable Census API fallback geocoding.",
    )
    parser.add_argument(
        "--sheet-name",
        help="Excel sheet name to read (if the workbook has multiple sheets).",
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to the configuration file (default: config/config.yaml).",
    )

    return parser


def _combine_address_fields(row: pd.Series, street: str, city: str, state: str, zip_: str) -> str:
    """Combine separate address columns into a single address string."""
    parts = []
    for col in [street, city, state, zip_]:
        if col and col in row and pd.notna(row[col]):
            value = str(row[col]).strip()
            if value:
                parts.append(value)
    return ", ".join(parts)


def _print_summary(total: int, matched: int, fallback: int, unmatched: int, rejected: int) -> None:
    print()
    print("=" * 52)
    print("  PROCESSING SUMMARY")
    print("=" * 52)
    print(f"  Total rows processed      : {total:,}")
    print(f"  Matched (primary)         : {matched:,}")
    print(f"  Matched (fallback)        : {fallback:,}")
    print(f"  Unmatched                 : {unmatched:,}")
    print(f"  Rejected (missing data)   : {rejected:,}")
    print("=" * 52)
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
        for col in [args.street_column, args.city_column, args.state_column, args.zip_column]:
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
                row, args.street_column, args.city_column,
                args.state_column, args.zip_column,
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
    # 6. Primary geocoding (Census Batch API)
    # ------------------------------------------------------------------
    logger.info(f"Starting primary geocoding for {len(valid_df):,} records...")

    # Ensure ID column is string for consistent merging
    valid_df[args.id_column] = valid_df[args.id_column].astype(str)

    geo_results = geocode_batch(
        addresses=valid_df[ADDR_COL],
        unique_ids=valid_df[args.id_column],
        batch_size=config.get("geocoder", {}).get("batch_size", 1000),
        timeout=config.get("geocoder", {}).get("batch_timeout", 120),
    )
    geo_results["unique_id"] = geo_results["unique_id"].astype(str)

    valid_df = valid_df.merge(
        geo_results[["unique_id", "latitude", "longitude", "match_status", "matched_address"]],
        left_on=args.id_column,
        right_on="unique_id",
        how="left",
    ).drop(columns=["unique_id"])

    # ------------------------------------------------------------------
    # 7. Fallback geocoding for unmatched records (Census single API)
    # ------------------------------------------------------------------
    fallback_count = 0
    if use_fallback:
        unmatched_mask = valid_df["match_status"] != "Matched"
        unmatched_for_fallback = valid_df[unmatched_mask].copy()

        if len(unmatched_for_fallback) > 0:
            logger.info(
                f"Running Census API fallback for {len(unmatched_for_fallback)} "
                "unmatched record(s)..."
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
                    fallback_count += 1

    # ------------------------------------------------------------------
    # 8. Load Census tract dataset and perform spatial join
    # ------------------------------------------------------------------
    try:
        tracts = get_tract_dataset(reference_dir)
    except Exception as e:
        print(f"\nError loading Census tract dataset:\n  {e}")
        sys.exit(1)

    logger.info("Performing spatial join to Census tracts...")
    valid_df = join_points_to_tracts(valid_df, tracts)

    # ------------------------------------------------------------------
    # 9. Set error reasons
    # ------------------------------------------------------------------
    valid_df["error_reason"] = None

    no_coords = valid_df["match_status"] == "No_Match"
    valid_df.loc[no_coords, "error_reason"] = "Address could not be geocoded"

    matched_but_no_tract = (
        valid_df["match_status"].isin(["Matched", "Matched_Fallback"])
        & valid_df["census_tract_geoid"].isna()
    )
    valid_df.loc[matched_but_no_tract, "error_reason"] = (
        "Coordinates found but did not fall within a Census tract boundary"
    )

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
    output_df = output_df.drop(columns=["latitude", "longitude", ADDR_COL], errors="ignore")

    # Reorder output columns
    id_cols = [args.id_column]
    if args.address_column:
        addr_input_cols = [args.address_column]
    else:
        addr_input_cols = [
            c for c in [args.street_column, args.city_column, args.state_column, args.zip_column]
            if c
        ]
    tail_cols = ["cleaned_address", "census_tract_geoid", "match_status", "error_reason"]
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
    matched_primary = int((valid_df["match_status"] == "Matched").sum())
    matched_fb = int((valid_df["match_status"] == "Matched_Fallback").sum())
    unmatched_final = int((valid_df["match_status"] == "No_Match").sum())

    _print_summary(
        total=len(df),
        matched=matched_primary,
        fallback=matched_fb,
        unmatched=unmatched_final,
        rejected=rejected_count,
    )


if __name__ == "__main__":
    main()
