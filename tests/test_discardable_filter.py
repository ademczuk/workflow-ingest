from __future__ import annotations

from pathlib import Path

import pytest

from extract.digest_to_patterns import _DISCARDABLE_RE, parse_summary_md


PROJECT = Path(__file__).resolve().parent.parent
KQU = PROJECT / "tests" / "fixtures" / "kQu5pWKS8GA_summary.md"


@pytest.mark.parametrize("text,should_drop", [
    ("A sponsor segment promotes the Claude Code Masterclass", True),
    ("the presenter appears in a picture-in-picture overlay", True),
    ("The speaker clarifies that this video will not cover setup", True),
    ("The speaker disclaims responsibility for the example", True),
    ("Sponsorship details are listed at the end", True),
    ("The speaker explains Level 1: Auto Memory in detail", False),
    ("vector embedding clusters semantic neighbours", False),
    ("LightRAG sidebar shows ID, Labels, Degree, Description", False),
])
def test_discardable_regex(text: str, should_drop: bool):
    matched = bool(_DISCARDABLE_RE.search(text))
    assert matched == should_drop, f"text={text!r} matched={matched} expected={should_drop}"


def test_kqu_fixture_drops_three_known_noise_patterns():
    if not KQU.exists():
        pytest.skip("kQu5pWKS8GA fixture not present")
    patterns = parse_summary_md(KQU)
    ideas = [p.idea.lower() for p in patterns]
    for needle in ("sponsor", "masterclass", "picture-in-picture", "the speaker clarifies"):
        assert not any(needle in i for i in ideas), (
            f"discardable filter let {needle!r} through: {[i for i in ideas if needle in i]}"
        )


def test_kqu_fixture_keeps_real_signal():
    if not KQU.exists():
        pytest.skip("kQu5pWKS8GA fixture not present")
    patterns = parse_summary_md(KQU)
    text_blob = " ".join(p.idea + " " + " ".join(p.evidence) for p in patterns).lower()
    for needle in ("auto memory", "lightrag", "vector"):
        assert needle in text_blob, f"filter dropped real signal: {needle!r}"
