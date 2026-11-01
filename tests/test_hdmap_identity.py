"""With HD maps off (`--no-hd`; on by default since B10) the builder writes exactly the
.xodr and sidecar it wrote before stage B (tests/hdmap_identity.py, recorded in B0)."""
from __future__ import annotations

import json

import pytest

from tests.hdmap_identity import CASES, EXPECTED, build


@pytest.mark.parametrize("name, preset, seed, maps", CASES, ids=[c[0] for c in CASES])
def test_build_unchanged(name, preset, seed, maps, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    assert build(preset, seed, maps) == json.loads(EXPECTED.read_text())[name]
