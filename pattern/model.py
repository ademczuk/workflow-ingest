from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Literal


SCHEMA_VERSION = "1.1"

PatternType = Literal[
    "feature-add",
    "architecture-pattern",
    "tooling-swap",
    "observability-gap",
    "anti-pattern",
    "external-tool",
]

SourceType = Literal["youtube"]
MatchState = Literal["green", "yellow", "red"]
Tier = Literal["high", "medium", "low", "conflict"]


def pattern_id_for(source_url: str, idea: str) -> str:
    h = hashlib.sha256(f"{source_url}\x00{idea}".encode("utf-8")).hexdigest()
    return h[:16]


TargetKind = Literal["subsystem", "topical_page"]

# Routable surface kind. Used by Phase 1 fan-out to label each candidate so
# downstream consumers (Phase 2 write router, Discord rendering) can decide
# whether a target is a brain/wiki/tracker/etc. Default is "brain" for
# backwards compatibility with corpus_map entries that predate this field.
SubsystemTargetKind = Literal[
    "brain",
    "wiki",
    "tracker",
    "pipeline",
    "orchestration",
    "discord",
    "none",
]


@dataclass(frozen=True)
class Routing:
    subsystem_slug: str
    match_state: MatchState
    tier: Tier
    snapshot_version: str
    canonical_path: str | None = None
    write_target: str | None = None
    target_kind: TargetKind = "subsystem"


@dataclass(frozen=True)
class RoutingCandidate:
    """One scored routing option for a Pattern in Phase 1 fan-out.

    Multiple candidates can exist per Pattern; the winner is also surfaced
    via pattern.routing for backwards compatibility. `target_kind` here is
    the SubsystemTargetKind from the corpus map (brain/wiki/tracker/...),
    NOT the TargetKind on Routing (which distinguishes subsystem vs
    topical_page). The two attributes coexist because they answer
    different questions: TargetKind = "what shape of routing record",
    SubsystemTargetKind = "what kind of write surface the slug owns".
    """
    subsystem_slug: str
    target_kind: SubsystemTargetKind
    score: float
    tier: Tier


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    source_url: str
    source_type: SourceType
    source_meta: dict
    captured_at: date
    idea: str
    evidence: tuple[str, ...]
    type: PatternType
    confidence: float = 0.6
    routing: Routing | None = None
    # Phase 1 fan-out: secondary candidates, ranked by BM25 score and
    # capped at the matcher's top_n (default 3). May be empty when no
    # secondary target clears MEDIUM_TIER_BM25_CUTOFF. Chose option (a)
    # from the design doc (sibling field, not Routing list) so every
    # caller that reads pattern.routing.subsystem_slug keeps working
    # unchanged.
    candidates: tuple[RoutingCandidate, ...] = ()

    @staticmethod
    def build(
        source_url: str,
        source_type: SourceType,
        source_meta: dict,
        captured_at: date,
        idea: str,
        evidence: list[str],
        type: PatternType,
        confidence: float = 0.6,
    ) -> "Pattern":
        if not evidence:
            raise ValueError("Pattern requires >=1 evidence entry")
        return Pattern(
            pattern_id=pattern_id_for(source_url, idea),
            source_url=source_url,
            source_type=source_type,
            source_meta=dict(source_meta),
            captured_at=captured_at,
            idea=idea.strip(),
            evidence=tuple(evidence),
            type=type,
            confidence=confidence,
        )

    def with_routing(self, routing: Routing) -> "Pattern":
        # Preserve candidates across with_routing so the BM25 matcher can
        # set candidates and routing in either order without losing fan-out
        # state. asdict() loses tuple-ness on dict fields, so we restore
        # evidence and candidates explicitly.
        return Pattern(**{
            **asdict(self),
            "evidence": self.evidence,
            "routing": routing,
            "candidates": self.candidates,
        })

    def with_candidates(self, candidates: tuple["RoutingCandidate", ...]) -> "Pattern":
        return Pattern(**{
            **asdict(self),
            "evidence": self.evidence,
            "candidates": candidates,
        })

    def to_dict(self) -> dict:
        d = asdict(self)
        d["captured_at"] = self.captured_at.isoformat()
        d["evidence"] = list(self.evidence)
        d["candidates"] = [
            {
                "subsystem_slug": c.subsystem_slug,
                "target_kind": c.target_kind,
                "score": c.score,
                "tier": c.tier,
            }
            for c in self.candidates
        ]
        return d
