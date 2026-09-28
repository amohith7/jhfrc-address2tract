"""Regression tests: degenerate queries must never reach an external provider.

Covers the bug where a blank / NaN address became the string "nan", slipped
past the missing-data guard, and was fuzzy-matched by Nominatim to a single
arbitrary location (a confidently wrong match for every empty row).
"""

from src.geocode_external import _is_geocodable, _geocode_one_external


def test_is_geocodable_rejects_blank_and_sentinels() -> None:
    for bad in ["", "   ", "nan", "NaN", "None", "null", ", , ", "-- , --"]:
        assert _is_geocodable(bad) is False, bad


def test_is_geocodable_accepts_real_addresses() -> None:
    for good in ["3618 Dug Gap Rd, Dalton, 30720", "30720", "PO Box 5"]:
        assert _is_geocodable(good) is True, good


def test_degenerate_query_is_not_sent_to_provider(monkeypatch) -> None:
    """A blank address returns No_Match without any network call."""
    import src.geocode_external as ext

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("provider was called for a degenerate query")

    monkeypatch.setattr(ext, "_geocode_nominatim", _boom)
    monkeypatch.setattr(ext, "_geocode_arcgis", _boom)
    monkeypatch.setattr(ext, "_geocode_geoapify", _boom)

    row = {"client_id": "1", "_address": "nan"}
    result, had_error = _geocode_one_external(
        row,
        address_col="_address",
        id_col="client_id",
        provider="nominatim",
        user_agent="test",
        arcgis_token=None,
        geoapify_key=None,
    )

    assert result["match_status"] == "No_Match"
    assert result["latitude"] is None and result["longitude"] is None
    assert had_error is False


def test_real_query_still_reaches_provider(monkeypatch) -> None:
    """A valid address is passed through to the provider and matched."""
    import src.geocode_external as ext

    monkeypatch.setattr(ext, "_geocode_nominatim", lambda *a, **k: (35.1, -85.1))

    row = {"client_id": "2", "_address": "3618 Dug Gap Rd, Dalton, 30720"}
    result, had_error = _geocode_one_external(
        row,
        address_col="_address",
        id_col="client_id",
        provider="nominatim",
        user_agent="test",
        arcgis_token=None,
        geoapify_key=None,
    )

    assert result["match_status"] == "Matched_External"
    assert result["latitude"] == 35.1 and result["longitude"] == -85.1
