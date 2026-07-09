"""
Demonstrate the catastrophic batch-parse failure in the ORIGINAL parser and
confirm the hardened parser survives it.

Real Census batch responses occasionally contain a row whose matched-address
field has an unescaped comma/quote, producing more fields than expected. The
original parser (C engine, no on_bad_lines) raises on the whole payload, so an
ENTIRE batch of up to 1,000 records is marked No_Match. The hardened parser
(python engine, on_bad_lines='skip') drops only the single bad row.
"""

import io
import pandas as pd

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

# A 5-record batch response. Record 3 has an unescaped comma OUTSIDE quotes in
# an unquoted matched-address field -> extra fields on that line only.
RESPONSE = (
    '"1","615 MCCALLIE AVE","Match","Exact","615 MCCALLIE AVE, CHATTANOOGA, TN, 37403","-85.29,35.04","1","L","47","065","001600","1001"\n'
    '"2","2101 WEST END AVE","Match","Exact","2101 WEST END AVE, NASHVILLE, TN, 37203","-86.80,36.15","2","L","47","037","013900","2001"\n'
    "3,BAD ROW,Match,Exact,123 MAIN ST, SUITE, A, TOWN, TN, 37000,-85.0,35.0,9,L,47,065,001100,1001\n"
    '"4","149 N MAIN ST","Match","Exact","149 N MAIN ST, MEMPHIS, TN, 38103","-90.05,35.15","4","L","47","157","010600","4001"\n'
    '"5","424 CHURCH ST","Match","Exact","424 CHURCH ST, NASHVILLE, TN, 37219","-86.78,36.16","5","L","47","037","019200","5001"\n'
)


def parse_old(text):
    """Original behavior: C engine, no on_bad_lines. Raises on ragged rows."""
    return pd.read_csv(io.StringIO(text), header=None, names=NAMES, dtype=str)


def parse_new(text):
    """Hardened behavior: python engine, skip bad lines."""
    return pd.read_csv(
        io.StringIO(text),
        header=None,
        names=NAMES,
        dtype=str,
        engine="python",
        on_bad_lines="skip",
    )


print("=" * 56)
print("  BATCH PARSE ROBUSTNESS (1 bad row in a 5-record batch)")
print("=" * 56)

try:
    df_old = parse_old(RESPONSE)
    print(f"  OLD parser: parsed {len(df_old)} rows")
except Exception as e:
    print(f"  OLD parser: RAISED -> whole batch lost as No_Match")
    print(f"              ({type(e).__name__}: {str(e)[:70]}...)")

df_new = parse_new(RESPONSE)
ok = df_new["match"].eq("Match").sum()
print(
    f"  NEW parser: parsed {len(df_new)} rows, {ok} usable matches "
    f"(only the 1 bad row dropped)"
)
print("=" * 56)
