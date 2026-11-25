"""Build the per-state tract boundary files the website's results map draws.

The map runs entirely in the browser: it loads these files from the site
itself, so no tract ID, address or map view is sent to any other service.

Source: the same Census cartographic boundary file the command-line tool joins
against (src/tract_join.py, cb_2020_us_tract_500k). Each state becomes
docs/assets/tracts/<state FIPS>.json, a TopoJSON topology with one object,
"tracts", whose features carry:

    g  11-digit tract GEOID
    n  tract name, e.g. "Census Tract 31"
    c  county name, e.g. "Hamilton County"

TopoJSON stores each shared border once, so the files are about a quarter of
the size of GeoJSON, and the browser draws county lines from the tract borders
where the county changes. Borders are simplified (about 30 m) for display
only; tract assignment happens elsewhere and is not affected.

Usage:
    python scripts/build_tract_maps.py --zip path/to/cb_2020_us_tract_500k.zip
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import topojson as tp

SOURCE_URL = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_tract_500k.zip"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "tracts"
SIMPLIFY_DEGREES = 0.0003
QUANTIZE = 1e5


def build(zip_path: Path, out_dir: Path) -> None:
    """Write one TopoJSON file per state, plus index.json naming the states."""
    gdf = gpd.read_file(f"zip://{zip_path}").to_crs(4326)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    out_dir.mkdir(parents=True, exist_ok=True)
    states = {}
    total = 0
    for fips, state in sorted(gdf.groupby("STATEFP")):
        layer = state[["GEOID", "NAMELSAD", "NAMELSADCO", "geometry"]].rename(
            columns={"GEOID": "g", "NAMELSAD": "n", "NAMELSADCO": "c"}
        )
        topo = tp.Topology(
            layer,
            prequantize=QUANTIZE,
            toposimplify=SIMPLIFY_DEGREES,
            object_name="tracts",
        )
        path = out_dir / f"{fips}.json"
        path.write_bytes(topo.to_json().encode("utf-8"))
        states[fips] = str(state["STATE_NAME"].iloc[0])
        total += path.stat().st_size
        print(
            f"{fips} {states[fips]:<30} {len(state):>5} tracts {path.stat().st_size / 1e6:5.2f} MB"
        )
    meta = {
        "source": SOURCE_URL,
        "simplify_degrees": SIMPLIFY_DEGREES,
        "quantize": QUANTIZE,
        "states": states,
    }
    (out_dir / "index.json").write_bytes(
        (json.dumps(meta, indent=1) + "\n").encode("utf-8")
    )
    print(f"total {total / 1e6:.1f} MB")


def main() -> None:
    """Parse arguments and build the files."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--zip", type=Path, required=True, help=f"Downloaded {SOURCE_URL}"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output folder")
    args = parser.parse_args()
    build(args.zip, args.out)


if __name__ == "__main__":
    main()
