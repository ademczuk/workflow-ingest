"""Phase 1 fan-out tests for route/bm25_matcher.py.

Validates that the matcher populates pattern.candidates in addition to
the legacy pattern.routing winner, and that:
  - the 6 new corpus systems are routable with declared target_kind
  - every Pattern still has a working pattern.routing.subsystem_slug
  - patterns matching nothing above threshold get an empty candidates
    tuple (matches the documented "no fan-out signal" contract)

Run with: python -m pytest tests/test_router_fanout.py -v
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from extract.digest_to_patterns import parse_summary_md
from inventory.corpus_map import CorpusMap, load_corpus_map
from inventory.model import Snapshot
from pattern.model import Pattern, RoutingCandidate
from route.bm25_matcher import (
    DEFAULT_TOP_N_CANDIDATES,
    HIGH_TIER_BM25_CUTOFF,
    MEDIUM_TIER_BM25_CUTOFF,
    match_all_bm25,
    match_bm25,
)


PROJECT = Path(__file__).resolve().parent.parent
CORPUS_PATH = PROJECT / "inventory" / "corpus_map_v1.yaml"

KNOWN_GOOD_SUMMARIES = [
    Path("C:/Projects/visual-llm/data/F-Ewm7qOr_c/summary.md"),
    Path("C:/Projects/visual-llm/data/PnEusTChQcE/summary.md"),
    Path("C:/Projects/visual-llm/data/c0gVowvMR-g/summary.md"),
    Path("C:/Projects/visual-llm/data/-Bc_VeH3kQ4/summary.md"),
]


@pytest.fixture(scope="module")
def corpus() -> CorpusMap:
    return load_corpus_map(CORPUS_PATH)


def _pattern(idea: str, evidence: str | None = None) -> Pattern:
    return Pattern.build(
        source_url="https://www.youtube.com/watch?v=fanout-test",
        source_type="youtube",
        source_meta={"video_id": "fanout-test"},
        captured_at=date(2026, 5, 27),
        idea=idea,
        evidence=[evidence or idea],
        type="architecture-pattern",
    )


# --- New corpus systems ----------------------------------------------------


def test_six_new_systems_present_in_corpus(corpus):
    """Phase 1 fan-out added 6 routable subsystems that previously only
    existed in the snapshot as provenance entries."""
    new_slugs = {
        "pantheon",
        "pipeline.job-orchestrator",
        "knowledge.obsidian",
        "knowledge.memory",
        "tracker.nimbalyst",
        "meta.cross-cutting",
    }
    assert new_slugs.issubset(set(corpus.slug_set()))


def test_new_systems_have_non_default_target_kind(corpus):
    """Each new system declares its own target_kind; none should fall
    back to the "brain" default. This catches accidental omissions in
    the YAML where target_kind was missed and silently defaulted."""
    expected = {
        "pantheon": "orchestration",
        "pipeline.job-orchestrator": "pipeline",
        "knowledge.obsidian": "wiki",
        "knowledge.memory": "wiki",
        "tracker.nimbalyst": "tracker",
        "meta.cross-cutting": "none",
    }
    for slug, want in expected.items():
        entry = corpus.by_slug(slug)
        assert entry is not None, f"corpus missing {slug}"
        assert entry.target_kind == want, (
            f"{slug}: target_kind={entry.target_kind!r}, expected {want!r}"
        )


def test_existing_subsystems_have_target_kind(corpus):
    """The 8 pre-existing entries also need target_kind set, not the
    default. Catches the case where someone added target_kind to the
    schema but forgot to populate the existing rows."""
    expected = {
        "anismin": "brain",
        "meridian": "brain",
        "kimiclaw": "brain",
        "clawfish": "brain",
        "solve-room": "orchestration",
        "visual-llm": "pipeline",
        "brutal-harness": "pipeline",
        "trident": "orchestration",
    }
    for slug, want in expected.items():
        entry = corpus.by_slug(slug)
        assert entry is not None
        assert entry.target_kind == want, (
            f"{slug}: target_kind={entry.target_kind!r}, expected {want!r}"
        )


# --- Candidate fan-out -----------------------------------------------------


def test_strong_match_populates_winner_candidate(corpus, snapshot: Snapshot):
    """A pattern that hits a single subsystem hard still produces at
    least one candidate (the winner). Validates the simplest fan-out
    case."""
    p = _pattern(
        "trident codex gemini grok consensus scoring router fanout free-tier"
    )
    routed = match_bm25(p, corpus, snapshot)
    assert len(routed.candidates) >= 1
    assert routed.candidates[0].subsystem_slug == "trident"
    assert routed.candidates[0].target_kind == "orchestration"
    assert routed.candidates[0].score >= MEDIUM_TIER_BM25_CUTOFF


def test_routing_winner_still_set_for_backwards_compat(corpus, snapshot: Snapshot):
    """Phase 1 fan-out must preserve pattern.routing.subsystem_slug for
    every consumer that reads the winner directly."""
    p = _pattern("trident codex gemini consensus")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing is not None
    assert routed.routing.subsystem_slug == "trident"


def test_no_match_yields_empty_candidates(corpus, snapshot: Snapshot):
    """Pattern with no real keyword overlap should produce empty
    candidates. pattern.routing falls back to the priority-order
    default. Uses single-token cuisine words so common-word noise
    ("with", "and") doesn't accidentally cross threshold via BM25 IDF
    weighting (a real edge case discovered while building this test:
    pantheon's keyword "tiered deliberation" includes "and" and BM25
    rewarded the multi-occurrence overlap)."""
    p = _pattern("xyzzy plugh foobar quux fizzbuzz wibble wobble")
    routed = match_bm25(p, corpus, snapshot)
    # routing is always populated (winner has a fallback to priority order)
    assert routed.routing is not None
    assert routed.routing.subsystem_slug != ""
    # candidates should be empty because no slug cleared the threshold
    assert routed.candidates == ()


def test_top_n_cap_respected(corpus, snapshot: Snapshot):
    """Even when many slugs clear threshold, candidates is capped at
    top_n. Avoids the case where a generic pattern fires every entry
    and floods the Discord pill."""
    p = _pattern(
        "trident codex gemini grok claude consensus scoring router meridian "
        "anismin kimiclaw solve-room"
    )
    routed = match_bm25(p, corpus, snapshot, top_n_candidates=2)
    assert len(routed.candidates) <= 2


def test_candidate_threshold_configurable(corpus, snapshot: Snapshot):
    """Lowering the threshold should produce >= as many candidates as
    the default. Validates the kwarg is wired correctly."""
    p = _pattern("trident codex gemini")
    high = match_bm25(p, corpus, snapshot, candidate_threshold=HIGH_TIER_BM25_CUTOFF)
    low = match_bm25(p, corpus, snapshot, candidate_threshold=0.1)
    assert len(low.candidates) >= len(high.candidates)


def test_candidates_are_score_sorted_after_winner(corpus, snapshot: Snapshot):
    """The winner is pinned at index 0; remaining candidates are sorted
    by descending score. Verifies deterministic ordering so downstream
    rendering (e.g. Discord pill) is stable across runs."""
    p = _pattern(
        "trident codex gemini grok claude consensus scoring meridian anismin"
    )
    routed = match_bm25(p, corpus, snapshot, top_n_candidates=5)
    if len(routed.candidates) >= 3:
        # positions 1..N must be sorted descending by score
        scores_after_winner = [c.score for c in routed.candidates[1:]]
        assert scores_after_winner == sorted(scores_after_winner, reverse=True)


# --- Known-good summary regression -----------------------------------------


@pytest.mark.parametrize("summary_path", KNOWN_GOOD_SUMMARIES, ids=lambda p: p.parent.name)
def test_known_good_summary_produces_multi_candidate_pattern(
    summary_path: Path, corpus, snapshot: Snapshot,
):
    """At least one pattern from each known-good summary should fan out
    to >= 2 candidates. If every pattern hits exactly one target, the
    fan-out infrastructure isn't actually producing signal and Phase 2
    has nothing to consume."""
    if not summary_path.exists():
        pytest.skip(f"{summary_path} not present on this host")

    patterns = parse_summary_md(summary_path)
    routed = match_all_bm25(patterns, corpus, snapshot)
    multi = [r for r in routed if len(r.candidates) >= 2]
    assert multi, (
        f"{summary_path.parent.name}: zero patterns fanned out to >= 2 "
        "candidates; fan-out infrastructure produced no signal"
    )


@pytest.mark.parametrize("summary_path", KNOWN_GOOD_SUMMARIES, ids=lambda p: p.parent.name)
def test_every_pattern_has_routing_winner(
    summary_path: Path, corpus, snapshot: Snapshot,
):
    """Backwards-compat guard: every Pattern must still have a non-empty
    pattern.routing.subsystem_slug after Phase 1 fan-out."""
    if not summary_path.exists():
        pytest.skip(f"{summary_path} not present on this host")

    patterns = parse_summary_md(summary_path)
    routed = match_all_bm25(patterns, corpus, snapshot)
    for r in routed:
        assert r.routing is not None
        assert r.routing.subsystem_slug, (
            f"{summary_path.parent.name}: pattern {r.pattern_id} lost "
            "its routing winner"
        )


# --- Edge case: ties at threshold ------------------------------------------


def test_candidates_are_deterministic_across_runs(corpus, snapshot: Snapshot):
    """Running the matcher twice on the same pattern must yield the
    same candidate ordering. Ties resolve by score-then-slug-alpha so
    floating-point equality doesn't shuffle results between runs."""
    p = _pattern("trident codex gemini meridian anismin kimiclaw")
    a = match_bm25(p, corpus, snapshot)
    b = match_bm25(p, corpus, snapshot)
    assert [c.subsystem_slug for c in a.candidates] == [
        c.subsystem_slug for c in b.candidates
    ]


def test_default_top_n_is_three(corpus, snapshot: Snapshot):
    """DEFAULT_TOP_N_CANDIDATES is the documented contract; downstream
    rendering caps at 3 to match. If this constant changes the bridge
    pill rendering needs a corresponding update."""
    assert DEFAULT_TOP_N_CANDIDATES == 3
    # Sanity check that the default actually applies when no kwarg is given.
    p = _pattern(
        "trident codex gemini grok claude consensus scoring router meridian "
        "anismin kimiclaw solve-room boardroom"
    )
    routed = match_bm25(p, corpus, snapshot)
    assert len(routed.candidates) <= DEFAULT_TOP_N_CANDIDATES


def test_routing_candidate_dataclass_shape():
    """RoutingCandidate is frozen and exposes the four documented
    fields. Defensive against accidental refactors that drop a field."""
    c = RoutingCandidate(
        subsystem_slug="anismin",
        target_kind="brain",
        score=4.2,
        tier="medium",
    )
    assert c.subsystem_slug == "anismin"
    assert c.target_kind == "brain"
    assert c.score == 4.2
    assert c.tier == "medium"
    with pytest.raises(Exception):
        c.score = 9.9  # frozen dataclass forbids mutation
