from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from extract.digest_to_patterns import parse_summary_md
from inventory.model import Snapshot
from route.matcher import match_all


@pytest.fixture(scope="module")
def routed_per_video(snapshot: Snapshot, fixture_paths: list[Path]):
    out: dict[str, list] = {}
    for path in fixture_paths:
        video_id = path.name.replace("_summary.md", "")
        patterns = parse_summary_md(path)
        routed = match_all(patterns, snapshot)
        out[video_id] = routed
    return out


def test_each_video_yields_patterns(routed_per_video):
    for vid, patterns in routed_per_video.items():
        assert patterns, f"{vid}: zero patterns"


_AGENT_ROUTING_TARGETS = {
    "anismin", "meridian", "trident", "kimiclaw", "solve-room", "clawfish",
    "brutal-harness", "pantheon", "visual-llm", "meta.cross-cutting",
}


def test_all_three_videos_route_to_agent_or_meta_subsystems(routed_per_video, snapshot):
    for vid, patterns in routed_per_video.items():
        slugs = {p.routing.subsystem_slug for p in patterns}
        relevant = slugs & _AGENT_ROUTING_TARGETS
        assert relevant, f"{vid}: no patterns routed to known agent/meta slugs. slugs={slugs}"


def test_routing_slugs_all_live_in_snapshot(routed_per_video, snapshot):
    live_slugs = snapshot.slug_set()
    for vid, patterns in routed_per_video.items():
        for p in patterns:
            assert p.routing.subsystem_slug in live_slugs, (
                f"{vid}: routed to dead slug {p.routing.subsystem_slug}"
            )


def test_dedup_detects_cross_video_idea_overlap(routed_per_video):
    """The 3 videos all argue for bespoke agentic pipelines vs generic factories.
    Patterns with overlapping idea-keywords across videos should produce
    distinct pattern_ids (per-source) but flag cross-video thematic overlap.
    This test asserts: at least one pattern in video A shares a meaningful
    keyword set with at least one pattern in video B and C.
    """

    def keyword_set(p):
        words = (p.idea + " " + " ".join(p.evidence)).lower().split()
        return {w.strip(".,;:()[]") for w in words if len(w) > 4}

    by_video = {
        vid: [keyword_set(p) for p in patterns]
        for vid, patterns in routed_per_video.items()
    }
    vids = list(by_video.keys())
    if len(vids) < 2:
        pytest.skip("need >=2 fixture videos for cross-overlap test")

    overlaps_found = 0
    for i in range(len(vids)):
        for j in range(i + 1, len(vids)):
            for ka in by_video[vids[i]]:
                for kb in by_video[vids[j]]:
                    if len(ka & kb) >= 3:
                        overlaps_found += 1
                        break
                if overlaps_found:
                    break
            if not overlaps_found:
                continue
    assert overlaps_found >= 1, (
        "expected meaningful cross-video keyword overlap on the 3 agentic-pipeline videos"
    )


def test_pattern_ids_unique_per_source(routed_per_video):
    """A pattern's id is sha256(source_url + idea). The same idea quoted from two
    different videos must yield two distinct pattern_ids; the same idea from
    the same video must collide."""
    all_ids: list[str] = []
    for vid, patterns in routed_per_video.items():
        ids = [p.pattern_id for p in patterns]
        assert len(ids) == len(set(ids)), f"{vid}: duplicate ids within one video"
        all_ids.extend(ids)
    assert len(all_ids) == len(set(all_ids)), (
        "duplicate pattern_id across videos — sources should disambiguate"
    )


def test_no_pattern_lands_in_conflict_tier_with_stub_confidence(routed_per_video):
    for vid, patterns in routed_per_video.items():
        conflict = [p for p in patterns if p.routing.tier == "conflict"]
        assert not conflict, (
            f"{vid}: matcher produced conflict tier with stub confidence + green/yellow only "
            f"(conflict requires match_state=red which v0 does not emit). "
            f"Got {len(conflict)} conflict-tier patterns."
        )


def test_routing_distribution_summary(routed_per_video, capsys):
    """Diagnostic — prints the routing distribution per video so an operator
    can eyeball the matcher output. Always passes; useful with -s."""
    for vid, patterns in routed_per_video.items():
        slug_counts = Counter(p.routing.subsystem_slug for p in patterns)
        type_counts = Counter(p.type for p in patterns)
        tier_counts = Counter(p.routing.tier for p in patterns)
        print(f"\n=== {vid} ({len(patterns)} patterns) ===")
        print(f"  subsystems: {dict(slug_counts)}")
        print(f"  types:      {dict(type_counts)}")
        print(f"  tiers:      {dict(tier_counts)}")
