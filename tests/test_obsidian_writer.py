from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pattern.model import Pattern, Routing
from sink.obsidian_writer import render_digest, write_digest


def _routed(idea: str, slug: str, tier: str = "medium", state: str = "green") -> Pattern:
    p = Pattern.build(
        source_url="https://www.youtube.com/watch?v=abc",
        source_type="youtube",
        source_meta={"video_id": "abc"},
        captured_at=date(2026, 5, 4),
        idea=idea,
        evidence=["[01:00] " + idea],
        type="architecture-pattern",
    )
    return p.with_routing(Routing(subsystem_slug=slug, match_state=state, tier=tier, snapshot_version="1.0"))


def test_render_digest_groups_by_subsystem():
    patterns = [
        _routed("idea A", "agent-orchestration.trident"),
        _routed("idea B", "agent-orchestration.trident"),
        _routed("idea C", "pipeline.visual-llm"),
    ]
    out = render_digest(patterns, "abc", "https://www.youtube.com/watch?v=abc")
    assert "Routed to `agent-orchestration.trident`" in out
    assert "Routed to `pipeline.visual-llm`" in out


def test_render_digest_includes_all_pattern_ids():
    patterns = [
        _routed(f"idea {i}", "agent-orchestration.trident") for i in range(5)
    ]
    out = render_digest(patterns, "abc", "https://www.youtube.com/watch?v=abc")
    for p in patterns:
        assert p.pattern_id in out, f"pattern {p.pattern_id} missing from rendered digest"


def test_verify_after_write_round_trip(tmp_path: Path):
    """Writes a digest, reads it back, asserts every routed pattern is present.
    This is the verify-after-write primitive — never trust the writer's success
    return without re-querying the destination."""
    patterns = [
        _routed("alpha", "agent-orchestration.trident"),
        _routed("beta", "pipeline.visual-llm"),
    ]
    out_path = write_digest(tmp_path, "abc", "https://www.youtube.com/watch?v=abc", patterns)
    assert out_path.exists()
    written = out_path.read_text(encoding="utf-8")
    for p in patterns:
        assert p.pattern_id in written
        assert p.routing.subsystem_slug in written


def test_empty_patterns_writes_no_patterns_marker(tmp_path: Path):
    out_path = write_digest(tmp_path, "abc", "https://www.youtube.com/watch?v=abc", [])
    written = out_path.read_text(encoding="utf-8")
    assert "no-patterns" in written
    assert "No patterns extracted" in written


def test_conflict_section_separated():
    patterns = [
        _routed("conflict idea", "meta.cross-cutting", tier="conflict", state="red"),
        _routed("normal idea", "agent-orchestration.trident"),
    ]
    out = render_digest(patterns, "abc", "https://www.youtube.com/watch?v=abc")
    assert "Conflicts (log + tag only" in out
    assert "no auto-convene" in out
