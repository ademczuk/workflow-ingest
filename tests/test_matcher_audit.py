from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extract.digest_to_patterns import parse_summary_md
from inventory.model import Snapshot
from pattern.model import Pattern, Routing
from route.audit import AuditFailure, audit
from route.matcher import match, match_all


def _evidence_pattern(idea: str, source_url: str = "https://www.youtube.com/watch?v=test") -> Pattern:
    return Pattern.build(
        source_url=source_url,
        source_type="youtube",
        source_meta={"video_id": "test"},
        captured_at=date(2026, 5, 4),
        idea=idea,
        evidence=["[01:02] " + idea],
        type="architecture-pattern",
    )


def test_match_attaches_routing(snapshot: Snapshot):
    p = _evidence_pattern("trident multi-model orchestration with codex gemini grok")
    routed = match(p, snapshot)
    assert routed.routing is not None
    assert routed.routing.subsystem_slug in snapshot.slug_set()


def test_trident_keywords_route_to_trident(snapshot: Snapshot):
    p = _evidence_pattern("use trident codex gemini grok consensus for multi-model routing")
    routed = match(p, snapshot)
    assert routed.routing.subsystem_slug == "trident"


def test_visual_llm_keywords_route_to_visual_llm(snapshot: Snapshot):
    p = _evidence_pattern("qwen3.6 parakeet kokoro visual video keyframe transcript pipeline")
    routed = match(p, snapshot)
    assert routed.routing.subsystem_slug == "visual-llm"


def test_audit_passes_for_well_formed_routed_pattern(snapshot: Snapshot):
    p = _evidence_pattern("trident codex gemini")
    routed = match(p, snapshot)
    result = audit(routed, snapshot, frozenset())
    assert result.ok, result.failures


def test_audit_rejects_unrouted_pattern(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    result = audit(p, snapshot, frozenset())
    assert not result.ok
    assert any("no routing" in f for f in result.failures)


def test_audit_rejects_dead_subsystem_slug(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    bad_routing = Routing(
        subsystem_slug="nonexistent.slug",
        match_state="green",
        tier="high",
        snapshot_version=snapshot.schema_version,
    )
    routed = p.with_routing(bad_routing)
    result = audit(routed, snapshot, frozenset())
    assert not result.ok
    assert any("not live in snapshot" in f for f in result.failures)


def test_audit_rejects_duplicate_pattern_id(snapshot: Snapshot):
    p = _evidence_pattern("trident")
    routed = match(p, snapshot)
    result = audit(routed, snapshot, frozenset({routed.pattern_id}))
    assert not result.ok
    assert any("duplicate pattern_id" in f for f in result.failures)


def test_audit_rejects_conflict_signal_mismatch(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    bad_routing = Routing(
        subsystem_slug=next(iter(snapshot.slug_set())),
        match_state="red",
        tier="high",
        snapshot_version=snapshot.schema_version,
    )
    routed = p.with_routing(bad_routing)
    result = audit(routed, snapshot, frozenset())
    assert not result.ok
    assert any("conflict signal" in f for f in result.failures)


def test_match_all_preserves_count(snapshot: Snapshot):
    patterns = [_evidence_pattern(f"idea {i}") for i in range(5)]
    routed = match_all(patterns, snapshot)
    assert len(routed) == 5
    assert all(p.routing is not None for p in routed)
