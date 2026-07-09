"""
Two demonstrations on the 1,000 real-address set:

(A) Parsed-fields vs old blob-in-street, geocoding all 1,000 both ways against
    the live Census batch API. Quantifies the parsed-field contribution.

(B) The catastrophic batch-parse failure at scale: inject ONE realistic
    malformed row into a 1,000-row Census response and parse it with the old
    C-engine parser vs the hardened parser.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))
from geocode import geocode_batch, _parse_batch_response  # noqa: E402

df = pd.read_csv("data/input/real_addresses_1000.csv", dtype=str).fillna("")


def n_matched(res):
    return int((res["match_status"] == "Matched").sum())


# ---- (A) parsed vs blob on all 1,000 real addresses ----------------------
print("=" * 60)
print("  (A) PARSED FIELDS vs OLD BLOB-IN-STREET  (1,000 real addrs)")
print("=" * 60)

blob = (df["street"] + ", " + df["city"] + ", " + df["state"] + ", " + df["zip"]).map(
    lambda s: s.replace(",", " ")
)
old = geocode_batch(unique_ids=df["client_id"], street=blob)
new = geocode_batch(
    unique_ids=df["client_id"],
    street=df["street"],
    city=df["city"],
    state=df["state"],
    zip_code=df["zip"],
)
print(f"  OLD (blob in street) : {n_matched(old)}/1000 geocoded")
print(f"  NEW (parsed fields)  : {n_matched(new)}/1000 geocoded")

# ---- (B) one malformed row in a 1,000-row batch --------------------------
print()
print("=" * 60)
print("  (B) ONE MALFORMED ROW IN A 1,000-ROW BATCH")
print("=" * 60)

good = (
    '"{i}","123 MAIN ST","Match","Exact","123 MAIN ST, NASHVILLE, TN, 37203",'
    '"-86.80,36.15","1","L","47","037","016500","1001"\n'
)
lines = [good.format(i=i) for i in range(1, 1000)]
# realistic bad row: unquoted matched-address with extra commas -> ragged
lines.insert(
    500,
    "1000,BAD,Match,Exact,500 2ND AVE, SUITE, B, NASHVILLE, TN, 37201,-86.77,36.16,9,L,47,037,019000,2000\n",
)
resp = "".join(lines)
chunk_ids = pd.Series([str(i) for i in range(1, 1001)])

NAMES = [
    "id",
    "input_addr",
    "match",
    "match_type",
    "matched_addr",
    "coords",
    "tiger_line",
    "side",
    "state_fips",
    "county_fips",
    "census_tract",
    "census_block",
]

# OLD parser behavior: C engine, no on_bad_lines. In the real pipeline the
# resulting ParserError is caught and the ENTIRE chunk becomes No_Match.
try:
    pd.read_csv(io.StringIO(resp), header=None, names=NAMES, dtype=str)
    old_matched = "parsed (no error)"
except Exception as e:
    old_matched = f"0/1000  (raised {type(e).__name__} -> whole batch No_Match)"

# NEW parser: hardened _parse_batch_response
res = pd.DataFrame(_parse_batch_response(resp, chunk_ids))
new_matched = int((res["match_status"] == "Matched").sum())

print("  Batch size                     : 1000 records (1 malformed)")
print(f"  OLD parser (C engine, no skip) : {old_matched}")
print(
    f"  NEW parser (python, skip bad)  : {new_matched}/1000  (only the 1 bad row lost)"
)
print("=" * 60)
