"""Conflict-state patterns: log + tag only. NEVER auto-convene /solve-room.

Default: append to conflicts/<YYYY-MM>.md in the project. Weekly synthesis
batches them; only items that survive batch review escalate to /solve-room.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pattern.model import Pattern


def append_conflict(conflicts_dir: Path, p: Pattern, today: date | None = None) -> Path:
    if p.routing is None or p.routing.tier != "conflict":
        raise ValueError(f"pattern {p.pattern_id} is not conflict-tier")
    today = today or date.today()
    conflicts_dir.mkdir(parents=True, exist_ok=True)
    out_path = conflicts_dir / f"{today.year:04d}-{today.month:02d}.md"
    block = (
        f"\n## `{p.pattern_id}` ({p.routing.subsystem_slug})\n"
        f"- type: {p.type}\n"
        f"- captured: {p.captured_at.isoformat()}\n"
        f"- source: {p.source_url}\n"
        f"- idea: {p.idea}\n"
        f"- evidence:\n"
    )
    block += "".join(f"  - {e}\n" for e in p.evidence)
    with out_path.open("a", encoding="utf-8") as fh:
        if out_path.stat().st_size == 0:
            fh.write(f"# Conflicts {today.year:04d}-{today.month:02d}\n")
            fh.write("Log + tag only. Weekly synthesis triages; only survivors go to /solve-room.\n")
        fh.write(block)
    return out_path
