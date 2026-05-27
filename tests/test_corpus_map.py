from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from inventory.corpus_map import (
    CORPUS_SCHEMA_VERSION,
    CorpusSnapshotMismatch,
    load_corpus_map,
    validate_against_snapshot,
)
from inventory.model import Snapshot


PROJECT = Path(__file__).resolve().parent.parent
CORPUS_PATH = PROJECT / "inventory" / "corpus_map_v1.yaml"


@pytest.fixture(scope="session")
def corpus():
    return load_corpus_map(CORPUS_PATH)


def test_corpus_loads(corpus):
    assert corpus.schema_version == CORPUS_SCHEMA_VERSION
    assert isinstance(corpus.schema_version, str)
    assert len(corpus.subsystems) >= 1


def test_schema_version_is_string_not_float(tmp_path: Path):
    bad = tmp_path / "bad_corpus.yaml"
    bad.write_text(
        "schema_version: 1.0\n"
        "generated_at: '2026-05-06'\n"
        "source: test\n"
        "subsystems: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version must be a YAML string"):
        load_corpus_map(bad)


def test_duplicate_slug_rejected(tmp_path: Path):
    bad = tmp_path / "dup.yaml"
    bad.write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "generated_at": "2026-05-06",
        "source": "test",
        "subsystems": [
            {"slug": "x", "name": "X", "role": "", "keywords": ["a"]},
            {"slug": "x", "name": "X dup", "role": "", "keywords": ["b"]},
        ],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate slug"):
        load_corpus_map(bad)


def test_corpus_subsystems_match_corpus_map_v1(corpus):
    # Phase 1 fan-out (2026-05-27): six new routable subsystems added so
    # patterns that previously hit nothing (e.g. job-orchestrator,
    # obsidian, nimbalyst tracker) now have a real target.
    expected_slugs = {
        "anismin", "meridian", "kimiclaw", "clawfish",
        "solve-room", "visual-llm", "brutal-harness", "trident",
        "pantheon", "pipeline.job-orchestrator",
        "knowledge.obsidian", "knowledge.memory",
        "tracker.nimbalyst", "meta.cross-cutting",
    }
    assert set(corpus.slug_set()) == expected_slugs


def test_corpus_known_gaps_capture_lightrag(corpus):
    concepts = {g.concept for g in corpus.known_gaps}
    assert "LightRAG" in concepts


def test_corpus_routing_rules_have_thresholds(corpus):
    rr = corpus.routing_rules
    assert 0.0 <= rr.match_threshold_jaccard <= 1.0
    assert 0.0 <= rr.novelty_threshold_jaccard <= 1.0


def test_validate_against_snapshot_passes(corpus, snapshot: Snapshot):
    validate_against_snapshot(corpus, snapshot)


def test_validate_against_snapshot_fails_on_dead_slug(corpus, snapshot: Snapshot, tmp_path: Path):
    bad = tmp_path / "stale.yaml"
    bad.write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "generated_at": "2026-05-06",
        "source": "test",
        "subsystems": [
            {"slug": "anismin", "name": "Anismin", "role": "", "keywords": ["a"]},
            {"slug": "deleted-subsystem", "name": "Dead", "role": "", "keywords": ["d"]},
        ],
    }), encoding="utf-8")
    stale = load_corpus_map(bad)
    with pytest.raises(CorpusSnapshotMismatch, match="deleted-subsystem"):
        validate_against_snapshot(stale, snapshot)
