from __future__ import annotations

import re
from dataclasses import dataclass

from inventory.corpus_map import CorpusMap
from inventory.model import Snapshot, Subsystem
from pattern.model import Pattern, Routing
from route.confidence import score as score_confidence


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2}


def _subsystem_keywords(s: Subsystem) -> set[str]:
    parts = [s.slug.replace(".", " ").replace("-", " "), s.display_name]
    if s.notes:
        parts.append(s.notes)
    return _tokens(" ".join(parts))


def _pattern_keywords(p: Pattern) -> set[str]:
    return _tokens(" ".join((p.idea, *p.evidence)))


def _best_match(p: Pattern, snapshot: Snapshot) -> tuple[Subsystem, int]:
    p_kw = _pattern_keywords(p)
    best: tuple[Subsystem | None, int] = (None, 0)
    for s in snapshot.subsystems:
        overlap = len(p_kw & _subsystem_keywords(s))
        if overlap > best[1]:
            best = (s, overlap)
    if best[0] is None:
        return snapshot.subsystems[0], 0
    return best[0], best[1]


def _match_state_for(overlap: int) -> str:
    return "green" if overlap >= 1 else "yellow"


def _tier_for(confidence: float, match_state: str) -> str:
    if match_state == "red":
        return "conflict"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.40:
        return "medium"
    return "low"


def _write_target_for(subsystem: Subsystem, corpus: CorpusMap | None) -> str | None:
    if corpus is None:
        return None
    for entry in corpus.subsystems:
        if entry.slug == subsystem.slug:
            return entry.write_target_when_novel
    return None


def match(p: Pattern, snapshot: Snapshot, *, corpus: CorpusMap | None = None) -> Pattern:
    subsystem, overlap = _best_match(p, snapshot)
    match_state = _match_state_for(overlap)
    confidence = score_confidence(p, snapshot.schema_version)
    tier = _tier_for(confidence, match_state)
    write_target = _write_target_for(subsystem, corpus)
    routing = Routing(
        subsystem_slug=subsystem.slug,
        match_state=match_state,
        tier=tier,
        snapshot_version=snapshot.schema_version,
        write_target=write_target,
    )
    return p.with_routing(routing)


def match_all(patterns: list[Pattern], snapshot: Snapshot, *, corpus: CorpusMap | None = None) -> list[Pattern]:
    return [match(p, snapshot, corpus=corpus) for p in patterns]
