from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from inventory.corpus_map import (
    CorpusMap,
    KnownGap,
    SubsystemEntry,
    TopicalPage,
    validate_against_snapshot,
)
from inventory.model import Snapshot
from pattern.model import Pattern, Routing


HIGH_TIER_BM25_CUTOFF = 6.0
MEDIUM_TIER_BM25_CUTOFF = 3.5


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2]


def _subsystem_doc(s: SubsystemEntry) -> list[str]:
    parts = [s.name, s.slug.replace("-", " ").replace(".", " "), s.role]
    parts.extend(s.keywords)
    parts.extend(s.concepts)
    return _tokenize(" ".join(parts))


def _topical_doc(p: TopicalPage) -> list[str]:
    parts = [p.name, p.slug.replace("page.", "").replace("-", " ").replace(".", " ")]
    parts.extend(p.keywords)
    parts.extend(p.concepts)
    return _tokenize(" ".join(parts))


def _pattern_query(p: Pattern) -> list[str]:
    return _tokenize(p.idea + " " + " ".join(p.evidence))


def _find_known_gap(p: Pattern, corpus: CorpusMap) -> KnownGap | None:
    haystack = (p.idea + " " + " ".join(p.evidence)).lower()
    for gap in corpus.known_gaps:
        if gap.concept.lower() in haystack:
            return gap
    return None


def _tier_for(score: float, gap_hit: bool) -> str:
    if gap_hit:
        return "high"
    if score >= HIGH_TIER_BM25_CUTOFF:
        return "high"
    if score >= MEDIUM_TIER_BM25_CUTOFF:
        return "medium"
    return "low"


def _pick_best_subsystem_index(
    scores: list[float],
    subsystems: tuple[SubsystemEntry, ...],
    priority_order: tuple[str, ...],
) -> int:
    best_score = max(scores) if scores else 0.0
    if best_score == 0.0:
        for idx, s in enumerate(subsystems):
            if s.slug in priority_order:
                return idx
        return 0
    candidates = [i for i, sc in enumerate(scores) if sc == best_score]
    if len(candidates) == 1:
        return candidates[0]
    pri_index = {slug: i for i, slug in enumerate(priority_order)}
    candidates.sort(key=lambda i: pri_index.get(subsystems[i].slug, 10_000))
    return candidates[0]


@dataclass(frozen=True)
class _Index:
    subsystem_bm25: BM25Okapi
    subsystems: tuple[SubsystemEntry, ...]
    topical_bm25: BM25Okapi | None
    topical_pages: tuple[TopicalPage, ...]


def build_index(corpus: CorpusMap) -> _Index:
    sub_docs = [_subsystem_doc(s) for s in corpus.subsystems]
    sub_bm25 = BM25Okapi(sub_docs) if sub_docs else BM25Okapi([[""]])
    if corpus.topical_pages:
        top_docs = [_topical_doc(p) for p in corpus.topical_pages]
        top_bm25 = BM25Okapi(top_docs)
    else:
        top_bm25 = None
    return _Index(
        subsystem_bm25=sub_bm25,
        subsystems=corpus.subsystems,
        topical_bm25=top_bm25,
        topical_pages=corpus.topical_pages,
    )


def match_bm25(
    p: Pattern,
    corpus: CorpusMap,
    snapshot: Snapshot,
    *,
    index: _Index | None = None,
) -> Pattern:
    validate_against_snapshot(corpus, snapshot)

    idx = index if index is not None else build_index(corpus)
    query = _pattern_query(p)

    sub_scores = idx.subsystem_bm25.get_scores(query).tolist() if idx.subsystems else []
    top_scores = idx.topical_bm25.get_scores(query).tolist() if idx.topical_bm25 is not None else []

    sub_best_idx = _pick_best_subsystem_index(
        sub_scores, idx.subsystems, corpus.routing_rules.priority_order
    ) if idx.subsystems else None
    top_best_idx = top_scores.index(max(top_scores)) if top_scores else None

    sub_best_score = sub_scores[sub_best_idx] if sub_best_idx is not None else 0.0
    top_best_score = top_scores[top_best_idx] if top_best_idx is not None else 0.0

    gap = _find_known_gap(p, corpus)

    if top_best_score > sub_best_score and top_best_idx is not None:
        topical = idx.topical_pages[top_best_idx]
        target_kind = "topical_page"
        slug = topical.slug
        canonical_path = topical.canonical_path
        score = top_best_score
        # Topical pages are append-only canonicals; gap landing overrides
        # only when a gap fires (novel concept stub goes to gap target).
        write_target = gap.landing_target if gap is not None else None
    else:
        best_subsystem = idx.subsystems[sub_best_idx] if sub_best_idx is not None else None
        target_kind = "subsystem"
        slug = best_subsystem.slug if best_subsystem else "meta.cross-cutting"
        canonical_path = best_subsystem.obsidian_canonical if best_subsystem else None
        score = sub_best_score
        if gap is not None:
            write_target = gap.landing_target
        else:
            write_target = best_subsystem.write_target_when_novel if best_subsystem else None

    tier = _tier_for(score, gap is not None)

    routing = Routing(
        subsystem_slug=slug,
        match_state="green",
        tier=tier,
        snapshot_version=snapshot.schema_version,
        canonical_path=canonical_path,
        write_target=write_target,
        target_kind=target_kind,
    )
    return p.with_routing(routing)


def match_all_bm25(
    patterns: list[Pattern],
    corpus: CorpusMap,
    snapshot: Snapshot,
) -> list[Pattern]:
    index = build_index(corpus)
    return [match_bm25(p, corpus, snapshot, index=index) for p in patterns]


def score_pattern(p: Pattern, corpus: CorpusMap, *, index: _Index | None = None) -> dict[str, float]:
    """Diagnostic: returns slug -> bm25 score for every subsystem AND topical page.

    Useful for tier-threshold recalibration and routing-target investigation.
    Subsystem slugs and topical-page slugs (page.* prefix) coexist in the result.
    """
    idx = index if index is not None else build_index(corpus)
    query = _pattern_query(p)
    out: dict[str, float] = {}
    if idx.subsystems:
        sub_scores = idx.subsystem_bm25.get_scores(query).tolist()
        for i, s in enumerate(idx.subsystems):
            out[s.slug] = sub_scores[i]
    if idx.topical_bm25 is not None and idx.topical_pages:
        top_scores = idx.topical_bm25.get_scores(query).tolist()
        for i, t in enumerate(idx.topical_pages):
            out[t.slug] = top_scores[i]
    return out
