"""Lightweight smoke test for repo-root package imports."""
from __future__ import annotations


def test_repo_root_import():
    from signal_processing.sdr_positioning import DEFAULT_CATALOGUE, PositioningSystem

    assert DEFAULT_CATALOGUE.name == "stations.json"
    assert PositioningSystem is not None
