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
from pattern.model import Pattern, Routing, RoutingCandidate


HIGH_TIER_BM25_CUTOFF = 6.0
MEDIUM_TIER_BM25_CUTOFF = 3.5

# Phase 1 fan-out: default cap for pattern.candidates. Three is enough to
# surface a primary + two genuine alternatives without overwhelming the
# Discord rendering pill (which itself caps at three).
DEFAULT_TOP_N_CANDIDATES = 3


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


def _collect_candidates(
    sub_scores: list[float],
    subsystems: tuple[SubsystemEntry, ...],
    top_scores: list[float],
    topical_pages: tuple[TopicalPage, ...],
    winner_slug: str,
    threshold: float,
    top_n: int,
) -> tuple[RoutingCandidate, ...]:
    """Phase 1 fan-out: collect top-N candidates above threshold.

    Returns the winner FIRST, then up to (top_n - 1) additional candidates
    above the threshold. Topical pages use a fixed "wiki" target_kind
    since they ARE wiki entries; subsystems carry their declared
    target_kind from the corpus map. Ties at the threshold are resolved
    by score-then-slug-alpha ordering so the candidate list is
    deterministic across runs. Returns empty tuple when nothing (not even
    the winner) clears the threshold; callers should fall back to
    pattern.routing for the singleton case.
    """
    if top_n <= 0:
        return ()

    # Aggregate every slug+score into a single list. Subsystem and topical
    # entries share the same threshold; their kinds (brain/wiki/etc) just
    # label which write surface the slug owns.
    rows: list[tuple[str, str, float]] = []
    for i, s in enumerate(subsystems):
        sc = sub_scores[i] if i < len(sub_scores) else 0.0
        rows.append((s.slug, s.target_kind, sc))
    for i, t in enumerate(topical_pages):
        sc = top_scores[i] if i < len(top_scores) else 0.0
        rows.append((t.slug, "wiki", sc))

    # Pull the winner out so we can pin it at position 0, then collect
    # other candidates that clear the threshold. The winner can be below
    # threshold (degenerate score=0 fallback case); we still surface it
    # in routing.subsystem_slug but not in candidates, which is
    # documented behavior (empty candidates => "no fan-out signal").
    winner_row = next((r for r in rows if r[0] == winner_slug), None)
    above = [r for r in rows if r[2] >= threshold and r[0] != winner_slug]
    # Deterministic ordering: score desc, then slug asc (alpha tie-break)
    above.sort(key=lambda r: (-r[2], r[0]))

    out: list[RoutingCandidate] = []
    if winner_row is not None and winner_row[2] >= threshold:
        out.append(RoutingCandidate(
            subsystem_slug=winner_row[0],
            target_kind=winner_row[1],
            score=float(winner_row[2]),
            tier=_tier_for(winner_row[2], False),
        ))
    for slug, kind, sc in above[: max(0, top_n - len(out))]:
        out.append(RoutingCandidate(
            subsystem_slug=slug,
            target_kind=kind,
            score=float(sc),
            tier=_tier_for(sc, False),
        ))
    return tuple(out)


def match_bm25(
    p: Pattern,
    corpus: CorpusMap,
    snapshot: Snapshot,
    *,
    index: _Index | None = None,
    top_n_candidates: int = DEFAULT_TOP_N_CANDIDATES,
    candidate_threshold: float = MEDIUM_TIER_BM25_CUTOFF,
) -> Pattern:
    """Score a Pattern against every routing target and pick a winner.

    Phase 1 fan-out: in addition to setting pattern.routing (the winner),
    populates pattern.candidates with up to top_n_candidates targets
    whose BM25 score is >= candidate_threshold. The winner is always
    first in the list when above threshold. Threshold defaults to
    MEDIUM_TIER_BM25_CUTOFF so candidates align with the existing
    medium/high tier boundary.
    """
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
    # Phase 1 fan-out: gather secondary candidates. The winner gets
    # priority position 0 in the candidate list. If even the winner is
    # below threshold (degenerate zero-score case), candidates is an
    # empty tuple and downstream code falls back to pattern.routing.
    candidates = _collect_candidates(
        sub_scores=sub_scores,
        subsystems=idx.subsystems,
        top_scores=top_scores,
        topical_pages=idx.topical_pages,
        winner_slug=slug,
        threshold=candidate_threshold,
        top_n=top_n_candidates,
    )
    return p.with_candidates(candidates).with_routing(routing)


def match_all_bm25(
    patterns: list[Pattern],
    corpus: CorpusMap,
    snapshot: Snapshot,
    *,
    top_n_candidates: int = DEFAULT_TOP_N_CANDIDATES,
    candidate_threshold: float = MEDIUM_TIER_BM25_CUTOFF,
) -> list[Pattern]:
    # Phase 1 fan-out: forwards the candidate kwargs so callers can tune
    # the threshold or top-N across a whole batch without touching the
    # per-pattern call site.
    index = build_index(corpus)
    return [
        match_bm25(
            p, corpus, snapshot,
            index=index,
            top_n_candidates=top_n_candidates,
            candidate_threshold=candidate_threshold,
        )
        for p in patterns
    ]


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
