"""Format-tolerance tests for the digest_to_patterns markdown walker.

These tests guard against the regression class that broke the prior
regex-based extractor twice on 2026-05-26: (1) parenthetical header
subtitles, and (2) capitalization drift. The walker should yield
patterns for any H2 header whose text fuzzy-matches one of the three
buckets, regardless of capitalization or trailing subtitle.

Run with: python -m pytest tests/test_digest_extractor.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from extract.digest_to_patterns import (
    _classify_header,
    _extract_section_bullets,
    _split_into_sections,
    parse_summary_md,
)


# Four known-good summaries that should each yield > 5 patterns.
# Current baselines (2026-05-27): 19, 13, 18, 20.
KNOWN_GOOD_SUMMARIES = [
    Path("C:/Projects/visual-llm/data/F-Ewm7qOr_c/summary.md"),
    Path("C:/Projects/visual-llm/data/PnEusTChQcE/summary.md"),
    Path("C:/Projects/visual-llm/data/c0gVowvMR-g/summary.md"),
    Path("C:/Projects/visual-llm/data/-Bc_VeH3kQ4/summary.md"),
]


@pytest.fixture
def synthetic_summary(tmp_path: Path) -> Path:
    """A minimal but valid summary.md with frontmatter the parser accepts."""
    def _make(body: str) -> Path:
        content = (
            "---\n"
            'title: Synthetic Fixture\n'
            'source_url: "https://www.youtube.com/watch?v=synthetic1"\n'
            "source_type: youtube\n"
            "video_id: synthetic1\n"
            "captured_at: 2026-05-27\n"
            "---\n"
            "\n"
            "# Synthetic Fixture\n"
            "\n"
            + body
        )
        path = tmp_path / "summary.md"
        path.write_text(content, encoding="utf-8")
        return path

    return _make


# --- Known-good fixtures ---------------------------------------------------


@pytest.mark.parametrize("summary_path", KNOWN_GOOD_SUMMARIES, ids=lambda p: p.parent.name)
def test_known_good_summary_yields_at_least_five_patterns(summary_path: Path):
    """Each of the 4 known-good summary files should yield > 5 patterns.

    Current baselines comfortably exceed the floor: 19, 13, 18, 20.
    Floor of 5 is intentionally below the lowest baseline so a small
    classification tweak doesn't fail the test, but a structural break
    (regex stops matching headers entirely) does.
    """
    if not summary_path.exists():
        pytest.skip(f"{summary_path} not present on this host")
    patterns = parse_summary_md(summary_path)
    assert len(patterns) > 5, (
        f"{summary_path.parent.name}: extractor returned only {len(patterns)} "
        "patterns; this looks like a structural extractor break"
    )


# --- Synthetic header drift cases -----------------------------------------


def test_header_with_subtitle_and_capitalization_drift(synthetic_summary):
    """A KEY POINTS header with a parenthetical subtitle should still match.

    This is the exact failure mode that broke the regex extractor on
    2026-05-26: capitalization drift AND parenthetical subtitle together.
    """
    body = (
        "## KEY POINTS (with [MM:SS] markers)\n"
        "\n"
        "- First synthetic point about agent memory.\n"
        "- Second synthetic point about retrieval latency.\n"
        "- Third synthetic point about caching strategies.\n"
    )
    path = synthetic_summary(body)
    patterns = parse_summary_md(path)
    assert len(patterns) == 3, (
        f"Expected 3 patterns from the synthetic KEY POINTS section, "
        f"got {len(patterns)}: {[p.idea for p in patterns]}"
    )


def test_h3_header_is_not_classified_as_section(synthetic_summary):
    """H3 headers should never trigger section extraction (H2 only).

    If the walker accidentally promotes H3 to a section, bullets nested
    under unrelated H3s (e.g. inside ## Transcript) would leak.
    """
    body = (
        "## Overview\n"
        "\n"
        "Some prose, no bullets here.\n"
        "\n"
        "### Key points\n"
        "\n"
        "- This bullet is under an H3 and must be ignored.\n"
        "- Another H3 bullet that must not be captured.\n"
    )
    path = synthetic_summary(body)
    patterns = parse_summary_md(path)
    assert patterns == [], (
        f"H3 'Key points' was incorrectly treated as a section; "
        f"got patterns: {[p.idea for p in patterns]}"
    )


def test_empty_matching_section_yields_no_patterns_and_no_error(synthetic_summary):
    """A header that matches a bucket but has no bullets should be a no-op.

    No exception, no error, just an empty result list.
    """
    body = (
        "## Key Points\n"
        "\n"
        "(intentionally empty: model produced the header but no bullets)\n"
        "\n"
        "## Notable visuals\n"
        "\n"
        "## Notable spoken content\n"
    )
    path = synthetic_summary(body)
    patterns = parse_summary_md(path)
    assert patterns == [], (
        f"Empty matching sections should yield no patterns; got "
        f"{[p.idea for p in patterns]}"
    )


# --- Lower-level classifier checks (defense-in-depth) ----------------------


@pytest.mark.parametrize("header,expected", [
    ("Key points", "key_points"),
    ("Key Points", "key_points"),
    ("KEY POINTS (with [MM:SS] markers)", "key_points"),
    ("Key insights", "key_points"),
    ("Main points and takeaways", "key_points"),
    ("Notable visuals", "notable_visuals"),
    ("Notable Visuals (slides/code/diagrams not in transcript)", "notable_visuals"),
    ("On screen text callouts", "notable_visuals"),
    ("Slide-by-slide commentary", "notable_visuals"),
    ("Notable spoken content", "notable_spoken"),
    ("Notable Spoken Content (claims/data not in visuals, with [MM:SS])", "notable_spoken"),
    ("Audio-only callouts", "notable_spoken"),
    ("Narration highlights", "notable_spoken"),
    ("Overview", None),
    ("Transcript", None),
    ("Keyframes", None),
])
def test_classify_header_fuzzy_match(header: str, expected: str | None):
    """The classifier must survive subtitle drift, case drift, and noise."""
    assert _classify_header(header) == expected, (
        f"classify_header({header!r}) returned {_classify_header(header)!r}, "
        f"expected {expected!r}"
    )


def test_section_walker_collects_h2_headers_only():
    """The walker should not split on H1 or H3 headers."""
    text = (
        "# Top-level title\n"
        "\n"
        "## Section A\n"
        "body A\n"
        "### A subsection\n"
        "still in A\n"
        "## Section B\n"
        "body B\n"
    )
    sections = _split_into_sections(text)
    headers = [h for h, _ in sections]
    assert headers == ["Section A", "Section B"], headers


def test_extract_bullets_handles_indented_continuations():
    """Indented continuation lines should be merged into the prior bullet."""
    lines = [
        "- First bullet starts here",
        "  and continues on the next line",
        "  with one more wrap",
        "- Second bullet, no wrap",
    ]
    out = _extract_section_bullets(lines)
    assert len(out) == 2, out
    assert "continues on the next line" in out[0]
    assert "with one more wrap" in out[0]


def test_extract_bullets_supports_asterisk_and_dash():
    lines = [
        "* asterisk bullet one",
        "- dash bullet two",
        "* asterisk bullet three",
    ]
    out = _extract_section_bullets(lines)
    assert len(out) == 3, out
