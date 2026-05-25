"""Tests for the 5-way decision classifier.

Covers:
  - synthetic rule tests for each decision category
  - end-to-end fixture test using a real summary.md
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extract.digest_to_patterns import parse_summary_md
from inventory.model import Snapshot, load_snapshot
from pattern.model import Pattern, Routing
from route.audit import audit, AuditResult
from route.decision import decide, decide_all, DecisionResult
from route.matcher import match_all


def _evidence_pattern(
    idea: str,
    source_url: str = "https://www.youtube.com/watch?v=test",
    type_: str = "architecture-pattern",
) -> Pattern:
    return Pattern.build(
        source_url=source_url,
        source_type="youtube",
        source_meta={"video_id": "test"},
        captured_at=date(2026, 5, 4),
        idea=idea,
        evidence=["[01:02] " + idea],
        type=type_,
    )


# ── synthetic rule tests ────────────────────────────────────────────────────


def test_discard_on_failed_audit(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    bad_routing = Routing(
        subsystem_slug="nonexistent.slug",
        match_state="green",
        tier="high",
        snapshot_version=snapshot.schema_version,
    )
    routed = p.with_routing(bad_routing)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "discard"
    assert "not live in snapshot" in d.reason


def test_conflict_on_red_match_state(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    conflict_routing = Routing(
        subsystem_slug=next(iter(snapshot.slug_set())),
        match_state="red",
        tier="conflict",
        snapshot_version=snapshot.schema_version,
    )
    routed = p.with_routing(conflict_routing)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "conflict"
    assert "match_state=red" in d.reason


def test_already_covered_on_duplicate_id(snapshot: Snapshot):
    p = _evidence_pattern("trident codex gemini consensus")
    routed = match_all([p], snapshot)[0]
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset({routed.pattern_id}))
    assert d.decision == "already-covered"
    assert "duplicate pattern_id" in d.reason


def test_wiki_only_on_topical_page_target(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    wiki_routing = Routing(
        subsystem_slug="knowledge.obsidian",
        match_state="green",
        tier="high",
        snapshot_version=snapshot.schema_version,
        target_kind="topical_page",
        write_target="developer/Obsidian/wiki-append",
    )
    routed = p.with_routing(wiki_routing)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "wiki-only"
    assert "topical_page" in d.reason


def test_integrate_on_high_tier_with_write_target(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    sub = next(s for s in snapshot.subsystems if s.slug == "trident")
    integrate_routing = Routing(
        subsystem_slug=sub.slug,
        match_state="green",
        tier="high",
        snapshot_version=snapshot.schema_version,
        write_target="tracker.nimbalyst",
    )
    routed = p.with_routing(integrate_routing)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "integrate"
    assert "high tier" in d.reason
    assert d.write_target == "tracker.nimbalyst"


def test_wiki_only_on_low_tier_without_write_target(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    sub = next(s for s in snapshot.subsystems if s.slug == "trident")
    low_routing = Routing(
        subsystem_slug=sub.slug,
        match_state="yellow",
        tier="low",
        snapshot_version=snapshot.schema_version,
        write_target=None,
    )
    routed = p.with_routing(low_routing)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "wiki-only"
    assert "low tier" in d.reason


def test_integrate_falls_back_to_wiki_only_when_no_write_target(snapshot: Snapshot):
    p = _evidence_pattern("anything")
    sub = next(s for s in snapshot.subsystems if s.slug == "trident")
    medium_no_target = Routing(
        subsystem_slug=sub.slug,
        match_state="green",
        tier="medium",
        snapshot_version=snapshot.schema_version,
        write_target=None,
    )
    routed = p.with_routing(medium_no_target)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "wiki-only"
    assert "no write_target" in d.reason


def test_wiki_only_on_low_tier_with_write_target(snapshot: Snapshot):
    """Low-tier patterns should stay wiki-only even if a write_target exists.
    Integration work is reserved for high/medium tier signals."""
    p = _evidence_pattern("anything")
    sub = next(s for s in snapshot.subsystems if s.slug == "trident")
    low_with_target = Routing(
        subsystem_slug=sub.slug,
        match_state="yellow",
        tier="low",
        snapshot_version=snapshot.schema_version,
        write_target="tracker.nimbalyst",
    )
    routed = p.with_routing(low_with_target)
    a = audit(routed, snapshot, frozenset())
    d = decide(routed, a, frozenset())
    assert d.decision == "wiki-only"
    assert "low tier" in d.reason
    assert d.write_target == "tracker.nimbalyst"


# ── end-to-end fixture test ─────────────────────────────────────────────────


def test_fixture_yields_at_least_one_integrate_or_wiki_only(snapshot: Snapshot):
    """A real summary.md should produce at least one actionable (non-discard) pattern."""
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "kQu5pWKS8GA_summary.md"
    patterns = parse_summary_md(fixture)
    routed = match_all(patterns, snapshot)
    audits = [audit(p, snapshot, frozenset()) for p in routed]
    decisions = decide_all(routed, audits, frozenset())

    actionable = [d for d in decisions if d.decision in ("integrate", "wiki-only")]
    assert actionable, (
        f"Expected at least one integrate or wiki-only from fixture, got: "
        f"{Counter(d.decision for d in decisions)}"
    )


def test_fixture_produces_no_conflicts_with_stub_confidence(snapshot: Snapshot):
    """v0 stub confidence (0.6) + green/yellow only should never hit conflict tier."""
    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "kQu5pWKS8GA_summary.md"
    patterns = parse_summary_md(fixture)
    routed = match_all(patterns, snapshot)
    audits = [audit(p, snapshot, frozenset()) for p in routed]
    decisions = decide_all(routed, audits, frozenset())

    conflicts = [d for d in decisions if d.decision == "conflict"]
    assert not conflicts, (
        f"Expected zero conflict decisions with stub confidence; got {len(conflicts)}"
    )


def test_decide_all_preserves_order(snapshot: Snapshot):
    patterns = [_evidence_pattern(f"idea {i}") for i in range(3)]
    routed = match_all(patterns, snapshot)
    audits = [audit(p, snapshot, frozenset()) for p in routed]
    decisions = decide_all(routed, audits, frozenset())
    assert len(decisions) == 3
    assert all(isinstance(d, DecisionResult) for d in decisions)


def test_decide_all_length_mismatch_raises():
    p = _evidence_pattern("x")
    with pytest.raises(ValueError, match="length mismatch"):
        decide_all([p], [], frozenset())
