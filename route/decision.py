"""5-way decision classifier for routed patterns.

Maps a Pattern (with routing + audit result) to an actionable decision:
  integrate / already-covered / wiki-only / conflict / discard
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from route.audit import AuditResult
from pattern.model import Pattern


Decision = Literal["integrate", "already-covered", "wiki-only", "conflict", "discard"]


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    reason: str
    write_target: str | None


def decide(
    pattern: Pattern,
    audit: AuditResult,
    existing_pattern_ids: frozenset[str],
) -> DecisionResult:
    """Classify a routed pattern into one of five actionable decisions.

    Rules (v1):
      - discard:    audit failed OR pattern has zero evidence
      - conflict:   match_state=red OR tier=conflict
      - already-covered: pattern_id seen before
      - wiki-only:  topical_page target OR low tier with no known gap
      - integrate:  audit passes, high/medium tier, has write_target
    """
    routing = pattern.routing

    # 1. discard — fundamental quality gate
    if not audit.ok:
        return DecisionResult(
            decision="discard",
            reason=f"audit failed: {'; '.join(audit.failures)}",
            write_target=None,
        )
    if not pattern.evidence:
        return DecisionResult(
            decision="discard",
            reason="zero evidence entries",
            write_target=None,
        )

    # 2. conflict — never integrate conflicting signals
    if routing is not None:
        if routing.match_state == "red" or routing.tier == "conflict":
            return DecisionResult(
                decision="conflict",
                reason=f"match_state={routing.match_state}, tier={routing.tier}",
                write_target=None,
            )

    # 3. already-covered — dedupe gate
    if pattern.pattern_id in existing_pattern_ids:
        return DecisionResult(
            decision="already-covered",
            reason=f"duplicate pattern_id {pattern.pattern_id}",
            write_target=None,
        )

    # Routing must exist for everything below
    if routing is None:
        return DecisionResult(
            decision="discard",
            reason="pattern has no routing",
            write_target=None,
        )

    # 4. wiki-only — knowledge-only, no tracker action
    if routing.target_kind == "topical_page":
        return DecisionResult(
            decision="wiki-only",
            reason=f"topical_page target ({routing.subsystem_slug})",
            write_target=routing.write_target,
        )
    if routing.tier == "low" and routing.write_target is None:
        return DecisionResult(
            decision="wiki-only",
            reason=f"low tier with no write_target ({routing.subsystem_slug})",
            write_target=None,
        )

    # 5. integrate — actionable, tracker-worthy
    if routing.tier in ("high", "medium"):
        if routing.write_target is not None:
            return DecisionResult(
                decision="integrate",
                reason=f"{routing.tier} tier, audit clean, write_target={routing.write_target}",
                write_target=routing.write_target,
            )
        return DecisionResult(
            decision="wiki-only",
            reason=f"{routing.tier} tier but no write_target defined for {routing.subsystem_slug}",
            write_target=None,
        )

    # Fallback (should not reach here if rules above are exhaustive)
    return DecisionResult(
        decision="discard",
        reason=f"unhandled routing state: tier={routing.tier}, target_kind={routing.target_kind}",
        write_target=None,
    )


def decide_all(
    patterns: list[Pattern],
    audits: list[AuditResult],
    existing_pattern_ids: frozenset[str],
) -> list[DecisionResult]:
    """Run decide() over a batch, preserving order."""
    if len(patterns) != len(audits):
        raise ValueError(f"patterns ({len(patterns)}) and audits ({len(audits)}) length mismatch")
    return [decide(p, a, existing_pattern_ids) for p, a in zip(patterns, audits)]
