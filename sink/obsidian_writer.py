from __future__ import annotations

from pathlib import Path

import yaml

from pattern.model import Pattern


def render_digest(patterns: list[Pattern], video_id: str, source_url: str) -> str:
    if not patterns:
        return _empty_digest(video_id, source_url)

    by_subsystem: dict[str, list[Pattern]] = {}
    skipped: list[Pattern] = []
    conflicts: list[Pattern] = []
    for p in patterns:
        if p.routing is None:
            skipped.append(p)
            continue
        if p.routing.tier == "conflict":
            conflicts.append(p)
            continue
        by_subsystem.setdefault(p.routing.subsystem_slug, []).append(p)

    fm = {
        "source_url": source_url,
        "source_type": "youtube",
        "video_id": video_id,
        "captured_at": patterns[0].captured_at.isoformat(),
        "schema_version": "1.0",
        "tags": ["youtube-digest", "workflow-ingest"],
    }
    fm_block = yaml.safe_dump(fm, sort_keys=False).strip()

    lines = ["---", fm_block, "---", "", f"# Routed digest: {video_id}", "", f"Source: {source_url}", ""]

    if conflicts:
        lines.append("## Conflicts (log + tag only, no auto-convene)")
        lines.append("")
        for p in conflicts:
            lines.extend(_render_pattern(p))
        lines.append("")

    for slug in sorted(by_subsystem):
        lines.append(f"## Routed to `{slug}`")
        lines.append("")
        for p in sorted(by_subsystem[slug], key=lambda x: x.pattern_id):
            lines.extend(_render_pattern(p))
        lines.append("")

    if skipped:
        lines.append("## Skipped (no routing)")
        lines.append("")
        for p in skipped:
            lines.extend(_render_pattern(p))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_pattern(p: Pattern) -> list[str]:
    routing = p.routing
    tier = routing.tier if routing else "unrouted"
    state = routing.match_state if routing else "n/a"
    out = [
        f"- **{p.type}** (`{tier}` / {state}, conf={p.confidence:.2f}) `{p.pattern_id}`",
        f"  - {p.idea}",
    ]
    for ev in p.evidence:
        out.append(f"  - evidence: {ev[:240]}")
    return out


def _empty_digest(video_id: str, source_url: str) -> str:
    return (
        "---\n"
        f"source_url: {source_url}\n"
        "source_type: youtube\n"
        f"video_id: {video_id}\n"
        "schema_version: '1.0'\n"
        "tags: [youtube-digest, workflow-ingest, no-patterns]\n"
        "---\n\n"
        f"# Routed digest: {video_id}\n\n"
        "No patterns extracted from this video.\n"
    )


def write_digest(out_dir: Path, video_id: str, source_url: str, patterns: list[Pattern]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{video_id}.md"
    out_path.write_text(render_digest(patterns, video_id, source_url), encoding="utf-8")
    return out_path
