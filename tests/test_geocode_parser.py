"""Regression tests for Census batch-response parsing."""

import pandas as pd

from src.geocode import _parse_batch_response


def test_malformed_batch_row_does_not_discard_valid_rows() -> None:
    """One malformed API row should not discard the other batch results."""
    response = (
        '"1","A","Match","Exact","A, CITY, TN","-85.1,35.1",'
        '"1","L","47","065","001600","1001"\n'
        "2,BAD,Match,Exact,TOO,MANY,UNQUOTED,FIELDS,-85,35,1,L,47,065,001700,1001\n"
        '"3","C","No_Match","","","","","","","","",""\n'
    )

    results = _parse_batch_response(response, pd.Series(["1", "2", "3"]))
    by_id = {row["unique_id"]: row for row in results}

    assert by_id["1"]["match_status"] == "Matched"
    assert by_id["2"]["match_status"] == "No_Match"
    assert by_id["3"]["match_status"] == "No_Match"
