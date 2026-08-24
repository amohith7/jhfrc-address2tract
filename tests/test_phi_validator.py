"""Unit tests for sensitive-column screening."""

import pytest

from src.phi_validator import check_for_phi, validate_no_phi


def test_flags_sensitive_tokens_without_flagging_filename() -> None:
    columns = ["client_id", "patient_name", "date_of_birth", "filename"]

    assert check_for_phi(columns) == ["patient_name", "date_of_birth"]


def test_accepts_minimum_address_schema() -> None:
    validate_no_phi(["client_id", "street", "city", "state", "zip"])


def test_validation_error_names_the_flagged_column() -> None:
    with pytest.raises(ValueError, match="clinical_notes"):
        validate_no_phi(["client_id", "clinical_notes"])
