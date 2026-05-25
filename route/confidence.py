from __future__ import annotations

from pattern.model import Pattern


STUB_CONFIDENCE = 0.6


def score(pattern: Pattern, snapshot_version: str) -> float:
    return STUB_CONFIDENCE
