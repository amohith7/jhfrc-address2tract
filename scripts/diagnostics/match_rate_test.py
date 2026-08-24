"""
Compare Census batch geocoder match rates: old blob-in-street method vs.
the new parsed-fields method. Hits the live Census API with a synthetic
sample of real, mixed-difficulty addresses. No PHI, no GeoPackage needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from geocode import geocode_batch  # noqa: E402

# Synthetic sample: real TN addresses reflecting messy real-world intake data.
# Difficulty is concentrated where the old blob method struggles: unit/apt
# numbers in the street, missing ZIPs, and lowercase text.
SAMPLE = [
    # id, street, city, state, zip
    ("1", "615 McCallie Ave", "Chattanooga", "TN", "37403"),
    ("2", "1101 W Cumberland Ave", "Knoxville", "TN", "37916"),
    ("3", "2101 West End Ave", "Nashville", "TN", "37203"),
    ("4", "3641 Central Ave", "Memphis", "TN", "38111"),
    ("5", "201 Ravine Rd", "Clarksville", "TN", ""),  # no ZIP
    ("6", "1 Public Sq", "Nashville", "TN", ""),  # no ZIP
    ("7", "100 cherokee blvd", "chattanooga", "tn", "37405"),  # lowercase
    ("8", "800 S Gay St", "Knoxville", "TN", ""),  # no ZIP
    ("9", "149 N Main St", "Memphis", "TN", "38103"),
    ("10", "350 Pageant Ln", "Clarksville", "TN", "37040"),
    ("11", "1600 Broadway", "Nashville", "TN", "37203"),
    ("12", "728 Market St Apt 400", "Chattanooga", "TN", "37402"),  # unit
    ("13", "900 Volunteer Blvd Unit 5", "Knoxville", "TN", "37996"),  # unit
    ("14", "250 Riverfront Pkwy", "Chattanooga", "TN", ""),  # no ZIP
    ("15", "3900 Hillsboro Pike Ste 200", "Nashville", "TN", "37215"),  # suite
    ("16", "5050 Poplar Ave", "Memphis", "TN", ""),  # no ZIP
    ("17", "1720 w end ave", "nashville", "tn", "37203"),  # lowercase
    ("18", "540 McCallie Ave Apt 2B", "Chattanooga", "TN", "37403"),  # unit
    ("19", "1301 E Main St", "Murfreesboro", "TN", ""),  # no ZIP
    ("20", "615 Lynn Garden Dr", "Kingsport", "TN", "37660"),
    ("21", "100 W Martin Luther King Blvd", "Chattanooga", "TN", "37402"),
    ("22", "2400 N Roan St", "Johnson City", "TN", ""),  # no ZIP
    ("23", "9000 Executive Park Dr Ste 100", "Knoxville", "TN", "37923"),  # suite
    ("24", "1000 Ridgeway Loop Rd", "Memphis", "TN", ""),  # no ZIP
    ("25", "424 Church St", "Nashville", "TN", "37219"),
]

df = pd.DataFrame(SAMPLE, columns=["id", "street", "city", "state", "zip"])


def matched(result: pd.DataFrame) -> int:
    return int((result["match_status"] == "Matched").sum())


def combine_like_old(row) -> str:
    """Replicate the ORIGINAL pipeline exactly: join non-empty fields with
    ', ' then strip commas to spaces (old geocode.py sanitization)."""
    parts = [row[c] for c in ("street", "city", "state", "zip") if str(row[c]).strip()]
    return ", ".join(parts).replace(",", " ")


print(f"\nSynthetic sample: {len(df)} addresses\n" + "=" * 56)

# --- OLD method: everything crammed into the street field, commas removed ---
blob = df.apply(combine_like_old, axis=1)
old = geocode_batch(unique_ids=df["id"], street=blob)  # city/state/zip = None
old_matched = matched(old)

# --- NEW method: parsed fields routed to their own Census columns ---
new = geocode_batch(
    unique_ids=df["id"],
    street=df["street"],
    city=df["city"],
    state=df["state"],
    zip_code=df["zip"],
)
new_matched = matched(new)

print("\n" + "=" * 56)
print("  RESULT")
print("=" * 56)
print(f"  OLD (blob in street field) : {old_matched}/{len(df)} matched")
print(f"  NEW (parsed fields)        : {new_matched}/{len(df)} matched")
print("=" * 56)

# Show which IDs the new method recovered that the old one missed
old_ids = set(old.loc[old["match_status"] == "Matched", "unique_id"])
new_ids = set(new.loc[new["match_status"] == "Matched", "unique_id"])
recovered = sorted(new_ids - old_ids, key=int)
lost = sorted(old_ids - new_ids, key=int)
if recovered:
    print(f"  Recovered by parsed fields : {recovered}")
if lost:
    print(f"  Regressed (new missed)     : {lost}")
print()
