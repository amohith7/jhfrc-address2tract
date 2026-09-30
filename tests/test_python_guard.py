"""Unit tests for the startup Python-version guard in main.py."""

import pytest

import main


def _fake_version(major, minor):
    """A stand-in for sys.version_info (indexable like the real 5-tuple)."""
    return (major, minor, 0, "final", 0)


@pytest.mark.parametrize("vt", [(3, 10), (3, 11), (3, 12)])
def test_supported_versions_pass(monkeypatch, vt):
    monkeypatch.setattr(main.sys, "version_info", _fake_version(*vt))
    main._require_supported_python()  # must not raise


@pytest.mark.parametrize("vt", [(2, 7), (3, 8), (3, 9), (3, 13), (3, 14)])
def test_unsupported_versions_exit(monkeypatch, vt):
    monkeypatch.setattr(main.sys, "version_info", _fake_version(*vt))
    with pytest.raises(SystemExit):
        main._require_supported_python()
