"""Unit tests for conservative street normalization."""

from src.normalize import normalize_street


def test_expands_only_a_trailing_street_suffix() -> None:
    assert normalize_street("123 Main St") == "123 Main Street"
    assert normalize_street("St Louis Ave") == "St Louis Avenue"


def test_fuses_consecutive_initials_without_changing_single_initial() -> None:
    assert normalize_street("100 B.B. King Blvd") == "100 BB King Boulevard"
    assert normalize_street("100 N. Main St") == "100 N. Main Street"


def test_empty_values_are_safe() -> None:
    assert normalize_street(None) == ""
    assert normalize_street("   ") == ""
