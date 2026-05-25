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
        return Pattern(**{**asdict(self), "evidence": self.evidence, "routing": routing})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["captured_at"] = self.captured_at.isoformat()
        d["evidence"] = list(self.evidence)
        return d
