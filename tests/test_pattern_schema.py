from __future__ import annotations

from datetime import date

import pytest

from pattern.model import SCHEMA_VERSION, Pattern, Routing, pattern_id_for


def test_pattern_schema_version_is_1_1():
    assert SCHEMA_VERSION == "1.1"


def _build(idea: str = "use bespoke pipelines instead of generic factories") -> Pattern:
    return Pattern.build(
        source_url="https://www.youtube.com/watch?v=mREHBZQbhBo",
        source_type="youtube",
        source_meta={"video_id": "mREHBZQbhBo"},
        captured_at=date(2026, 5, 4),
        idea=idea,
        evidence=["[02:02] generic agentic AI is a trap"],
        type="anti-pattern",
    )


def test_pattern_id_is_deterministic():
    p1 = _build()
    p2 = _build()
    assert p1.pattern_id == p2.pattern_id


def test_pattern_id_differs_per_idea():
    p1 = _build("use bespoke pipelines")
    p2 = _build("use generic factories")
    assert p1.pattern_id != p2.pattern_id


def test_pattern_requires_evidence():
    with pytest.raises(ValueError):
        Pattern.build(
            source_url="https://example.com",
            source_type="youtube",
            source_meta={},
            captured_at=date(2026, 5, 4),
            idea="x",
            evidence=[],
            type="feature-add",
        )


def test_with_routing_preserves_immutable_fields():
    p = _build()
    routed = p.with_routing(
        Routing(
            subsystem_slug="trident",
            match_state="green",
            tier="high",
            snapshot_version="1.0",
        )
    )
    assert routed.pattern_id == p.pattern_id
    assert routed.idea == p.idea
    assert routed.routing.subsystem_slug == "trident"
    assert routed.routing.canonical_path is None
    assert routed.routing.write_target is None


def test_routing_with_canonical_and_write_target():
    p = _build()
    routed = p.with_routing(
        Routing(
            subsystem_slug="trident",
            match_state="green",
            tier="high",
            snapshot_version="1.0",
            canonical_path="02-Knowledge/Wiki/AI/Codex-Brain.md",
            write_target="02-Knowledge/Wiki/AI/",
        )
    )
    assert routed.routing.canonical_path == "02-Knowledge/Wiki/AI/Codex-Brain.md"
    assert routed.routing.write_target == "02-Knowledge/Wiki/AI/"


def test_pattern_id_prefix_length():
    assert len(pattern_id_for("a", "b")) == 16
