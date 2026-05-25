"""Drain Obsidian inbox/youtube-queue.md into tracker items.

v1 contract:
- inbox file is one URL per line; '#' starts an inline tag list
- idempotent on URL hash; partial drain leaves un-drained URLs in place
- after successful tracker_create + verify, the corresponding line is removed
- if youtube_status reports red, the drain SKIPS that batch (does NOT delete
  the inbox lines) and creates a tracker bug instead
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


_URL_LINE_RE = re.compile(r"^(\S+)(?:\s+(#.+))?\s*$")


@dataclass(frozen=True)
class InboxEntry:
    url: str
    tags: tuple[str, ...]
    line_hash: str


def parse_inbox(text: str) -> list[InboxEntry]:
    out: list[InboxEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _URL_LINE_RE.match(line)
        if not m:
            continue
        url = m.group(1)
        tag_blob = m.group(2) or ""
        tags = tuple(t.lstrip("#").strip() for t in tag_blob.split() if t.strip())
        out.append(
            InboxEntry(
                url=url,
                tags=tags,
                line_hash=hashlib.sha256(line.encode("utf-8")).hexdigest()[:12],
            )
        )
    return out


def drain(inbox_path: Path) -> list[InboxEntry]:
    if not inbox_path.exists():
        return []
    return parse_inbox(inbox_path.read_text(encoding="utf-8"))


def remove_entries(inbox_path: Path, drained_hashes: set[str]) -> None:
    if not drained_hashes or not inbox_path.exists():
        return
    kept: list[str] = []
    for raw in inbox_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        m = _URL_LINE_RE.match(line)
        if not m:
            kept.append(raw)
            continue
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]
        if h not in drained_hashes:
            kept.append(raw)
    inbox_path.write_text("\n".join(kept).rstrip() + ("\n" if kept else ""), encoding="utf-8")
