"""Unit tests for the connectivity preflight (no real network calls)."""

import requests

from src import preflight
from src.preflight import check_connectivity


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def test_census_reachable_reports_ok(monkeypatch) -> None:
    """A normal HTTP response from the Census host counts as reachable."""
    monkeypatch.setattr(preflight.requests, "head", lambda *a, **k: _FakeResponse(200))

    census_ok, results = check_connectivity(check_external=False)

    assert census_ok is True
    assert len(results) == 1
    assert results[0][1] is True


def test_http_error_status_still_counts_as_reachable(monkeypatch) -> None:
    """A 4xx/5xx means the host answered, so it is reachable for our purposes."""
    monkeypatch.setattr(preflight.requests, "head", lambda *a, **k: _FakeResponse(405))

    census_ok, _ = check_connectivity(check_external=False)

    assert census_ok is True


def test_connection_failure_reports_unreachable(monkeypatch) -> None:
    """When both HEAD and the GET fallback fail at the connection level."""

    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(preflight.requests, "head", _boom)
    monkeypatch.setattr(preflight.requests, "get", _boom)

    census_ok, results = check_connectivity(check_external=False)

    assert census_ok is False
    assert results[0][1] is False


def test_get_fallback_used_when_head_refused(monkeypatch) -> None:
    """A host that refuses HEAD but answers GET is still reachable."""

    def _head_fails(*a, **k):
        raise requests.exceptions.RequestException("HEAD not allowed")

    monkeypatch.setattr(preflight.requests, "head", _head_fails)
    monkeypatch.setattr(preflight.requests, "get", lambda *a, **k: _FakeResponse(200))

    census_ok, _ = check_connectivity(check_external=False)

    assert census_ok is True


def test_external_provider_probed_only_when_enabled(monkeypatch) -> None:
    """The optional provider host is probed only when check_external is True."""
    monkeypatch.setattr(preflight.requests, "head", lambda *a, **k: _FakeResponse(200))

    _, off = check_connectivity(check_external=False, external_provider="arcgis")
    assert len(off) == 1

    _, on = check_connectivity(check_external=True, external_provider="arcgis")
    assert len(on) == 2
    assert "arcgis" in on[1][0]
