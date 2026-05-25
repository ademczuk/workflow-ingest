from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from inventory.model import (
    SCHEMA_VERSION,
    Snapshot,
    Subsystem,
    SubsystemSource,
    load_snapshot,
)


def test_snapshot_loads(snapshot: Snapshot):
    assert snapshot.schema_version == SCHEMA_VERSION
    assert len(snapshot.subsystems) >= 8


def test_all_slugs_unique(snapshot: Snapshot):
    slugs = [s.slug for s in snapshot.subsystems]
    assert len(slugs) == len(set(slugs))


def test_every_subsystem_has_source(snapshot: Snapshot):
    for s in snapshot.subsystems:
        assert s.sources, f"{s.slug} has no sources"


def test_memory_files_are_first_class(snapshot: Snapshot):
    kinds = {src.kind for s in snapshot.subsystems for src in s.sources}
    assert "memory_file" in kinds, "memory_file source kind missing — v1 contract"


def test_known_subsystems_present(snapshot: Snapshot):
    expected = {
        "anismin",
        "meridian",
        "trident",
        "visual-llm",
        "solve-room",
        "kimiclaw",
        "brutal-harness",
        "clawfish",
        "knowledge.obsidian",
        "tracker.nimbalyst",
    }
    actual = snapshot.slug_set()
    missing = expected - actual
    assert not missing, f"missing slugs: {missing}"


def test_schema_version_mismatch_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({
        "schema_version": "0.9",
        "generated_at": "2026-05-04T20:00:00",
        "generator": "test",
        "subsystems": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_snapshot(bad)


def test_duplicate_slug_rejected(tmp_path: Path):
    bad = tmp_path / "dup.yaml"
    bad.write_text(yaml.safe_dump({
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-05-04T20:00:00",
        "generator": "test",
        "subsystems": [
            {
                "slug": "x",
                "display_name": "X",
                "sources": [{"kind": "operator_bullet", "ref": "x", "last_seen": "2026-05-04T20:00:00"}],
            },
            {
                "slug": "x",
                "display_name": "X duplicate",
                "sources": [{"kind": "operator_bullet", "ref": "y", "last_seen": "2026-05-04T20:00:00"}],
            },
        ],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate slug"):
        load_snapshot(bad)
