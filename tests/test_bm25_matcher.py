from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from inventory.corpus_map import CorpusMap, load_corpus_map
from inventory.model import Snapshot
from pattern.model import Pattern
from route.bm25_matcher import build_index, match_all_bm25, match_bm25, score_pattern


PROJECT = Path(__file__).resolve().parent.parent
CORPUS_PATH = PROJECT / "inventory" / "corpus_map_v1.yaml"


@pytest.fixture(scope="session")
def corpus() -> CorpusMap:
    return load_corpus_map(CORPUS_PATH)


def _evidence(idea: str, evidence: str | None = None, source_url: str = "https://www.youtube.com/watch?v=test") -> Pattern:
    return Pattern.build(
        source_url=source_url,
        source_type="youtube",
        source_meta={"video_id": "test"},
        captured_at=date(2026, 5, 6),
        idea=idea,
        evidence=[evidence or idea],
        type="architecture-pattern",
    )


def test_trident_query_routes_to_trident(corpus, snapshot: Snapshot):
    p = _evidence("use trident with codex gemini grok consensus across multiple models")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.subsystem_slug == "trident"
    assert routed.routing.canonical_path  # corpus has obsidian_canonical for trident


def test_visual_llm_query_routes_to_visual_llm(corpus, snapshot: Snapshot):
    p = _evidence("qwen3.6 mmproj parakeet kokoro keyframes scene detect llama-server visual-llm")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.subsystem_slug == "visual-llm"


def test_solve_room_query_routes_to_solve_room(corpus, snapshot: Snapshot):
    p = _evidence("solve-room boardroom four brain manager gate dissent shadow rollout EMPTY_VETO_NEUTRAL")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.subsystem_slug == "solve-room"


def test_known_gap_lightrag_attaches_landing_target(corpus, snapshot: Snapshot):
    p = _evidence("LightRAG knowledge graph relationships and entity extraction")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.tier == "high", "known_gap hits should be high tier"
    assert routed.routing.write_target is not None
    assert "LightRAG-Evaluation" in routed.routing.write_target


def test_routing_attaches_canonical_path_from_corpus(corpus, snapshot: Snapshot):
    p = _evidence("trident codex gemini grok")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.canonical_path
    assert routed.routing.canonical_path.endswith(".md")


def test_low_score_yields_low_tier(corpus, snapshot: Snapshot):
    p = _evidence("chocolate cake recipe with vanilla frosting and fresh strawberries on top")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.tier in ("low", "medium")


def test_match_all_preserves_count(corpus, snapshot: Snapshot):
    patterns = [_evidence(f"trident codex gemini iteration {i}") for i in range(4)]
    routed = match_all_bm25(patterns, corpus, snapshot)
    assert len(routed) == 4
    assert all(p.routing is not None for p in routed)


def test_score_pattern_returns_per_slug_scores(corpus, snapshot: Snapshot):
    p = _evidence("trident codex gemini")
    scores = score_pattern(p, corpus)
    expected_keys = set(corpus.slug_set()) | set(corpus.topical_slug_set())
    assert set(scores.keys()) == expected_keys
    assert scores["trident"] >= max(scores[s] for s in scores if s != "trident" and not s.startswith("page."))


def test_topical_pages_present_in_corpus(corpus):
    expected_topical = {
        "page.vector-databases",
        "page.matryoshka-embeddings",
        "page.karpathy-kb",
        "page.karpathy-auto-research",
        "page.context-rot",
        "page.youtube-summarizer-mcp",
        "page.codex-brain",
    }
    assert set(corpus.topical_slug_set()) == expected_topical


def test_vector_embedding_pattern_routes_to_topical_page(corpus, snapshot: Snapshot):
    """Pattern about vector embeddings should route to Vector-Databases topical
    page, not to a subsystem (none of the subsystems own vector embedding)."""
    p = _evidence(
        "vector embedding 300 dimension semantic clusters bananas apples pears boats ships",
        evidence="[28:24] 3D scatter plot showing vector embeddings with semantic clusters",
    )
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.target_kind == "topical_page"
    assert routed.routing.subsystem_slug.startswith("page.")
    assert "Vector-Databases" in routed.routing.canonical_path or "Matryoshka" in routed.routing.canonical_path


def test_karpathy_vault_pattern_routes_to_topical_page(corpus, snapshot: Snapshot):
    p = _evidence(
        "Karpathy vault structure with raw and wiki folders for knowledge base architecture",
        evidence="[04:30] Karpathy's auto research project layout described",
    )
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.target_kind == "topical_page"
    assert "Karpathy" in routed.routing.canonical_path


def test_subsystem_query_still_routes_to_subsystem(corpus, snapshot: Snapshot):
    """Topical pages must NOT steal routing from queries that are clearly
    about a subsystem. Trident-specific keywords still go to trident."""
    p = _evidence("trident codex gemini grok consensus scoring router fanout free-tier subscription")
    routed = match_bm25(p, corpus, snapshot)
    assert routed.routing.target_kind == "subsystem"
    assert routed.routing.subsystem_slug == "trident"


def test_routing_default_target_kind_is_subsystem():
    from pattern.model import Pattern, Routing
    from datetime import date
    p = Pattern.build(
        source_url="https://example.com",
        source_type="youtube",
        source_meta={},
        captured_at=date(2026, 5, 6),
        idea="x",
        evidence=["x"],
        type="feature-add",
    )
    r = Routing(
        subsystem_slug="anismin",
        match_state="green",
        tier="medium",
        snapshot_version="1.0",
    )
    assert r.target_kind == "subsystem"


def test_bm25_routing_passes_audit(corpus, snapshot: Snapshot):
    from route.audit import audit
    p = _evidence("trident codex gemini grok consensus")
    routed = match_bm25(p, corpus, snapshot)
    result = audit(routed, snapshot, frozenset())
    assert result.ok, result.failures


def test_index_reuse_does_not_change_results(corpus, snapshot: Snapshot):
    index = build_index(corpus)
    p = _evidence("trident codex gemini")
    routed_a = match_bm25(p, corpus, snapshot)
    routed_b = match_bm25(p, corpus, snapshot, index=index)
    assert routed_a.routing.subsystem_slug == routed_b.routing.subsystem_slug
    assert routed_a.routing.tier == routed_b.routing.tier
