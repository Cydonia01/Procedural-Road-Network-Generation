"""Stage C features are off by default: the existing lsystem presets grow exactly the
networks they grew before (tests/lsystem_identity.py, recorded before stage C)."""

from __future__ import annotations

import json

import pytest

from tests.lsystem_identity import CASES, EXPECTED, build, fingerprint


@pytest.mark.parametrize("name, preset, seed, maps", CASES, ids=[c[0] for c in CASES])
def test_network_unchanged(name, preset, seed, maps, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert fingerprint(build(preset, seed, maps)) == json.loads(EXPECTED.read_text())[name]
