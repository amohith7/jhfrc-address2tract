"""Regression tests for chunk-resume integrity safeguards."""

from pathlib import Path

import pandas as pd
import pytest

from main import _file_sha256, _validate_resumed_part


def test_file_sha256_changes_for_same_size_content(tmp_path: Path) -> None:
    """Same-size input edits must produce different resume identities."""
    input_path = tmp_path / "input.csv"
    input_path.write_bytes(b"id,address\n1,alpha\n")
    first = _file_sha256(input_path)

    input_path.write_bytes(b"id,address\n1,bravo\n")

    assert _file_sha256(input_path) != first


def test_validate_resumed_part_accepts_complete_part(tmp_path: Path) -> None:
    """A complete part with the expected schema and size is reusable."""
    part_path = tmp_path / "part_00000.csv"
    pd.DataFrame(
        {
            "client_id": ["1", "2"],
            "match_status": ["Matched", "No_Match"],
            "census_tract_geoid": ["47065001600", None],
            "cleaned_address": ["A", "B"],
            "error_reason": [None, "No match"],
        }
    ).to_csv(part_path, index=False)

    _validate_resumed_part(
        part_path,
        expected_rows=2,
        required_columns={"client_id", "match_status", "census_tract_geoid"},
    )


def test_validate_resumed_part_rejects_incomplete_part(tmp_path: Path) -> None:
    """A readable part with fewer rows than its source chunk is rejected."""
    part_path = tmp_path / "part_00000.csv"
    pd.DataFrame({"client_id": ["1"], "match_status": ["Matched"]}).to_csv(
        part_path, index=False
    )

    with pytest.raises(ValueError, match="expected 2 rows but found 1"):
        _validate_resumed_part(
            part_path, expected_rows=2, required_columns={"client_id", "match_status"},
        )


def test_validate_resumed_part_rejects_missing_columns(tmp_path: Path) -> None:
    """A part without required result columns is rejected."""
    part_path = tmp_path / "part_00000.csv"
    pd.DataFrame({"client_id": ["1"]}).to_csv(part_path, index=False)

    with pytest.raises(ValueError, match="required columns are missing"):
        _validate_resumed_part(
            part_path, expected_rows=1, required_columns={"client_id", "match_status"},
        )
