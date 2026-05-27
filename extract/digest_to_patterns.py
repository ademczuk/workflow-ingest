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
_KEY_POINTS_RE = re.compile(
    r"^##\s+Key points[^\n]*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_NOTABLE_VISUALS_RE = re.compile(
    r"^##\s+Notable visuals[^\n]*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_NOTABLE_SPOKEN_RE = re.compile(
    r"^##\s+Notable spoken content[^\n]*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^[\*\-]\s+(.+?)(?=\n[\*\-]\s|\n\n|\Z)", re.DOTALL | re.MULTILINE)


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


def _extract_section_bullets(section_text: str) -> list[str]:
    bullets = []
    for m in _BULLET_RE.finditer(section_text):
        cleaned = _strip_md(m.group(1))
        if not cleaned:
            continue
        if _DISCARDABLE_RE.search(cleaned):
            continue
        bullets.append(cleaned)
    return bullets


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
    for section_re in (_KEY_POINTS_RE, _NOTABLE_VISUALS_RE, _NOTABLE_SPOKEN_RE):
        m = section_re.search(text)
        if m:
            bullets.extend(_extract_section_bullets(m.group(1)))

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
