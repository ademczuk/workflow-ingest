from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from pattern.model import Pattern, PatternType


_DISCARDABLE_RE = re.compile(
    r"(?i)("
    r"sponsor|masterclass"
    r"|presenter appears"
    r"|picture-in-picture"
    r"|the speaker (?:clarifies|disclaims)"
    r")"
)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Markdown structure recognizers (line-based, format-tolerant).
# H2 header: starts with exactly two `#` followed by whitespace. The trailing
# `(?!#)` is unnecessary because we match `##\s+`, which `###` (followed by a
# non-space `#`) does not satisfy.
_H2_RE = re.compile(r"^##\s+(.*?)\s*$")
# A bullet line: starts with optional whitespace + `*` or `-` + space.
_BULLET_START_RE = re.compile(r"^\s*[\*\-]\s+(.*)$")
# Continuation of a bullet: indented (non-empty, not a bullet, not a header).
_INDENT_CONT_RE = re.compile(r"^\s+\S")


# Fuzzy keyword buckets for classifying H2 sections.
# Each tuple: (bucket name, sequence of case-insensitive substrings; any hit assigns the bucket).
# Order matters: `notable_spoken` is checked BEFORE `notable_visuals` because
# real-world drift like "Notable spoken content (claims/data not in visuals,
# with [MM:SS])" contains the substring "visual" inside a parenthetical, and
# the spoken signal is the stronger match.
_SECTION_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("key_points", ("key point", "key insight", "main point")),
    ("notable_spoken", ("spoken", "audio-only", "narration")),
    ("notable_visuals", ("visual", "on screen", "slide")),
)


def _classify_header(header_text: str) -> str | None:
    """Return bucket name if the header matches a known bucket, else None.

    Matching is case-insensitive substring against the header text. The first
    matching bucket in declaration order wins (order is documented above the
    bucket table). Headers that don't match any bucket are skipped, not raised.
    """
    lowered = header_text.lower()
    for bucket, needles in _SECTION_BUCKETS:
        if any(needle in lowered for needle in needles):
            return bucket
    return None


_TYPE_RULES: list[tuple[PatternType, tuple[str, ...]]] = [
    ("anti-pattern", ("don't", "do not", "trap", "fail", "anti", "avoid", "insane")),
    ("tooling-swap", ("instead of", "swap", "replace", "use ", " over ")),
    ("observability-gap", ("dashboard", "visibility", "observ", "audit", "trace")),
    ("external-tool", ("framework", "library", "package", "repo", "open source")),
    ("feature-add", ("add", "build", "create", "implement", "introduce")),
]


def _classify_type(text: str) -> PatternType:
    t = text.lower()
    for type_, keywords in _TYPE_RULES:
        if any(k in t for k in keywords):
            return type_
    return "architecture-pattern"


def _strip_md(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).strip("*").strip()


def _extract_section_bullets(section_lines: list[str]) -> list[str]:
    """Extract bullets from a list of raw lines (already scoped to one H2 section).

    Bullets are any line starting with `* ` or `- ` after optional whitespace.
    Subsequent non-bullet, non-blank, indented lines are treated as
    continuation of the prior bullet (so wrapped bullets stay intact).
    Blank lines, header lines, and other content end the current bullet.
    """
    bullets: list[str] = []
    current: list[str] | None = None

    def _flush() -> None:
        nonlocal current
        if current is None:
            return
        cleaned = _strip_md(" ".join(current))
        if cleaned and not _DISCARDABLE_RE.search(cleaned):
            bullets.append(cleaned)
        current = None

    for raw_line in section_lines:
        line = raw_line.rstrip("\n")
        if not line.strip():
            _flush()
            continue
        bullet_match = _BULLET_START_RE.match(line)
        if bullet_match:
            _flush()
            current = [bullet_match.group(1)]
            continue
        if current is not None and _INDENT_CONT_RE.match(line):
            current.append(line.strip())
            continue
        # Any other line ends the current bullet but does not start one.
        _flush()

    _flush()
    return bullets


def _split_into_sections(text: str) -> list[tuple[str, list[str]]]:
    """Walk the markdown line-by-line and yield (h2_header_text, body_lines).

    Sections are bounded by H1 or H2 headers; H3+ lines stay inside their
    enclosing H2 section. Content before the first H2 is dropped.
    """
    sections: list[tuple[str, list[str]]] = []
    current_header: str | None = None
    current_body: list[str] = []

    for line in text.splitlines(keepends=False):
        h2 = _H2_RE.match(line)
        if h2:
            if current_header is not None:
                sections.append((current_header, current_body))
            current_header = h2.group(1).strip()
            current_body = []
            continue
        # An H1 also closes the current H2 section.
        if current_header is not None and line.startswith("# ") and not line.startswith("## "):
            sections.append((current_header, current_body))
            current_header = None
            current_body = []
            continue
        if current_header is not None:
            current_body.append(line)

    if current_header is not None:
        sections.append((current_header, current_body))
    return sections


def _idea_from_bullet(bullet: str) -> str:
    sentence_split = re.split(r"(?<=[.!?])\s+", bullet, maxsplit=2)
    if not sentence_split:
        return bullet[:280]
    head = sentence_split[0]
    if len(head) < 60 and len(sentence_split) > 1:
        head = head + " " + sentence_split[1]
    return head[:280].strip()


def parse_summary_md(path: Path) -> list[Pattern]:
    text = path.read_text(encoding="utf-8")

    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        raise ValueError(f"{path}: missing YAML frontmatter")
    fm = yaml.safe_load(fm_match.group(1))

    source_url = fm.get("source_url")
    video_id = fm.get("video_id")
    captured_at_raw = fm.get("captured_at")
    if not source_url or not video_id or not captured_at_raw:
        raise ValueError(f"{path}: frontmatter missing source_url, video_id, or captured_at")
    captured_at = (
        captured_at_raw if isinstance(captured_at_raw, date)
        else date.fromisoformat(str(captured_at_raw))
    )

    bullets: list[str] = []
    # Track which buckets have already yielded bullets. A bucket only claims
    # its slot after producing at least one bullet, so a brittle false-positive
    # header like "Joint summary (audio + visual)" doesn't shadow the real
    # "Notable visuals" section that follows it.
    filled_buckets: set[str] = set()
    for header_text, body_lines in _split_into_sections(text):
        bucket = _classify_header(header_text)
        if bucket is None:
            continue
        if bucket in filled_buckets:
            continue
        section_bullets = _extract_section_bullets(body_lines)
        if not section_bullets:
            continue
        filled_buckets.add(bucket)
        bullets.extend(section_bullets)

    patterns: list[Pattern] = []
    seen_ideas: set[str] = set()
    for bullet in bullets:
        idea = _idea_from_bullet(bullet)
        if idea in seen_ideas:
            continue
        seen_ideas.add(idea)
        patterns.append(
            Pattern.build(
                source_url=source_url,
                source_type="youtube",
                source_meta={"video_id": video_id},
                captured_at=captured_at,
                idea=idea,
                evidence=[bullet],
                type=_classify_type(idea + " " + bullet),
            )
        )

    return patterns
