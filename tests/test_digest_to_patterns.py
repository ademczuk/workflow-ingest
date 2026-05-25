from __future__ import annotations

from pathlib import Path

import pytest

from extract.digest_to_patterns import parse_summary_md


def test_parse_each_fixture(fixture_paths: list[Path]):
    for path in fixture_paths:
        patterns = parse_summary_md(path)
        assert patterns, f"{path.name}: zero patterns extracted"


def test_extracted_patterns_have_evidence(fixture_paths: list[Path]):
    for path in fixture_paths:
        for p in parse_summary_md(path):
            assert p.evidence
            assert p.idea


def test_source_type_is_youtube_for_youtube_fixtures(fixture_paths: list[Path]):
    for path in fixture_paths:
        for p in parse_summary_md(path):
            assert p.source_type == "youtube"
            assert "video_id" in p.source_meta


def test_no_youtube_fields_at_top_level(fixture_paths: list[Path]):
    for path in fixture_paths:
        for p in parse_summary_md(path):
            d = p.to_dict()
            forbidden = {"video_id", "frame_ts", "uploader", "duration_s"}
            assert not (forbidden & d.keys()), (
                f"{path.name}: pattern leaked youtube field at top level: {forbidden & d.keys()}"
            )


def test_pattern_ids_unique_within_fixture(fixture_paths: list[Path]):
    for path in fixture_paths:
        ids = [p.pattern_id for p in parse_summary_md(path)]
        assert len(ids) == len(set(ids)), f"{path.name}: duplicate pattern_ids within one digest"
