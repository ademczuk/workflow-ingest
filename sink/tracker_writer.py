"""Write routed patterns as tracker ideas with verify-after-write.

The actual MCP create + re-query happens in the automation runtime where the
nimbalyst-mcp tool surface is available. This module defines the payload
shape and the verify contract so tests can exercise it offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pattern.model import Pattern


@dataclass(frozen=True)
class TrackerWriteResult:
    ok: bool
    tracker_id: str | None
    pattern_id: str
    verify_passed: bool
    error: str | None = None


def build_tracker_payload(p: Pattern) -> dict:
    if p.routing is None:
        raise ValueError(f"pattern {p.pattern_id} has no routing; cannot write to tracker")
    return {
        "type": "idea",
        "title": p.idea[:80],
        "description": _build_description(p),
        "tags": [
            "workflow-ingest",
            f"source-{p.source_type}",
            f"subsystem-{p.routing.subsystem_slug}",
            f"tier-{p.routing.tier}",
            f"match-{p.routing.match_state}",
            f"pattern-{p.pattern_id}",
        ],
    }


def _build_description(p: Pattern) -> str:
    routing = p.routing
    parts = [
        f"**Pattern**: `{p.pattern_id}` ({p.type})",
        f"**Source**: {p.source_url}",
        f"**Routed to**: `{routing.subsystem_slug}` (tier={routing.tier}, state={routing.match_state})",
        f"**Confidence**: {p.confidence:.2f}",
        "",
        "## Idea",
        p.idea,
        "",
        "## Evidence",
    ]
    parts.extend(f"- {e}" for e in p.evidence)
    return "\n".join(parts)


class TrackerClient(Protocol):
    def create(self, payload: dict) -> str: ...
    def get(self, tracker_id: str) -> dict | None: ...


def write_with_verify(client: TrackerClient, p: Pattern) -> TrackerWriteResult:
    payload = build_tracker_payload(p)
    try:
        tracker_id = client.create(payload)
    except Exception as e:
        return TrackerWriteResult(
            ok=False, tracker_id=None, pattern_id=p.pattern_id, verify_passed=False, error=str(e)
        )

    verify = client.get(tracker_id)
    if verify is None:
        return TrackerWriteResult(
            ok=False,
            tracker_id=tracker_id,
            pattern_id=p.pattern_id,
            verify_passed=False,
            error="created but verify-after-write returned None",
        )

    expected_pattern_tag = f"pattern-{p.pattern_id}"
    actual_tags = set(verify.get("tags", []))
    if expected_pattern_tag not in actual_tags:
        return TrackerWriteResult(
            ok=False,
            tracker_id=tracker_id,
            pattern_id=p.pattern_id,
            verify_passed=False,
            error=f"verify failed: tag {expected_pattern_tag} missing from {actual_tags}",
        )

    return TrackerWriteResult(
        ok=True, tracker_id=tracker_id, pattern_id=p.pattern_id, verify_passed=True
    )
