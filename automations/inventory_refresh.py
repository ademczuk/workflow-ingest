"""Refresh inventory/snapshot.yaml from live git repos.

Usage:
    python automations/inventory_refresh.py
    python automations/inventory_refresh.py --snapshot inventory/snapshot.yaml
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from inventory.model import Snapshot, Subsystem, SubsystemSource, load_snapshot  # noqa: E402

DEFAULT_CUTOFF_DAYS = 30


def _git_last_commit_date(repo_path: Path) -> datetime | None:
    """Return the author date of the latest commit, or None if not a git repo."""
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        return None
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    try:
        result = subprocess.run(
            [git_bin, "log", "-1", "--format=%cI"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return datetime.fromisoformat(result.stdout.strip())
    except Exception:
        return None


def _find_repos(root: Path, max_depth: int = 3) -> dict[str, Path]:
    """Map directory name -> Path for every git repo under root (recursive, bounded)."""
    repos: dict[str, Path] = {}
    if not root.exists():
        return repos

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in current.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if child.name in ("node_modules", "venv", ".venv", "__pycache__", "target", "dist", "build"):
                continue
            if (child / ".git").exists():
                repos[child.name] = child
            else:
                walk(child, depth + 1)

    walk(root, 0)
    return repos


def _has_recent_activity(repo_path: Path, cutoff: datetime) -> bool:
    last = _git_last_commit_date(repo_path)
    if last is None:
        return False
    # Normalize to UTC for comparison
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last >= cutoff


# Hardcoded repo-name -> subsystem-slug overrides for non-exact matches.
_REPO_SLUG_OVERRIDES: dict[str, str] = {
    "workflow-ingest": "meta.cross-cutting",
    "nimbalyst-1-Gemini": "tracker.nimbalyst",
    "nimbalyst-1-clone": "tracker.nimbalyst",
    "clawdie-crm": "clawfish",
    "clawdie-crm-fix": "clawfish",
    "clawdie-page-templates": "clawfish",
    "anismin-openclaw": "anismin",
    "anismin-workspace": "anismin",
    "meridian-openclaw": "meridian",
    "job-matching-intelligence": "pipeline.job-orchestrator",
}


def _slug_for_repo(repo_name: str, snapshot: Snapshot) -> str | None:
    """Map a repo directory name to a subsystem slug, or None if no match."""
    # Exact slug match
    for sub in snapshot.subsystems:
        if repo_name.lower() == sub.slug.lower():
            return sub.slug
    # Exact display_name match (case-insensitive, spaces replaced)
    normalized = repo_name.lower().replace(" ", "-").replace("_", "-")
    for sub in snapshot.subsystems:
        display_normalized = sub.display_name.lower().replace(" ", "-").replace("_", "-")
        if normalized == display_normalized:
            return sub.slug
    # Override lookup
    return _REPO_SLUG_OVERRIDES.get(repo_name)


def refresh(
    snapshot: Snapshot,
    repos_root: Path,
    *,
    cutoff_days: int = DEFAULT_CUTOFF_DAYS,
    now: datetime | None = None,
) -> Snapshot:
    """Return a new Snapshot with last_seen refreshed for subsystems linked to active repos."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cutoff_days)
    repos = _find_repos(repos_root)

    # Determine which subsystems have active repos
    active_slugs: set[str] = set()
    for repo_name, repo_path in repos.items():
        if not _has_recent_activity(repo_path, cutoff):
            continue
        slug = _slug_for_repo(repo_name, snapshot)
        if slug:
            active_slugs.add(slug)

    refreshed_subsystems: list[Subsystem] = []
    for sub in snapshot.subsystems:
        if sub.slug in active_slugs:
            # Ensure there is at least one commit source; if not, add one.
            has_commit = any(src.kind == "commit" for src in sub.sources)
            if not has_commit:
                # Find the repo path that matched this subsystem
                repo_path = None
                for repo_name, path in repos.items():
                    if _slug_for_repo(repo_name, snapshot) == sub.slug and _has_recent_activity(path, cutoff):
                        repo_path = path
                        break
                new_sources = tuple(
                    sub.sources
                    + (
                        SubsystemSource(
                            kind="commit",
                            ref=str(repo_path) if repo_path else f"C:/Projects/{sub.slug}",
                            last_seen=now,
                        ),
                    )
                )
            else:
                new_sources = tuple(
                    SubsystemSource(
                        kind=src.kind,
                        ref=src.ref,
                        last_seen=now if src.kind == "commit" else src.last_seen,
                    )
                    for src in sub.sources
                )
            refreshed_subsystems.append(
                Subsystem(
                    slug=sub.slug,
                    display_name=sub.display_name,
                    sources=new_sources,
                    notes=sub.notes,
                )
            )
        else:
            refreshed_subsystems.append(sub)

    return Snapshot(
        schema_version=snapshot.schema_version,
        generated_at=now,
        generator="inventory_refresh.py",
        subsystems=tuple(refreshed_subsystems),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh snapshot.yaml from live git repos")
    ap.add_argument("--snapshot", default=str(PROJECT / "inventory" / "snapshot.yaml"))
    ap.add_argument("--repos-root", default=str(Path("C:/").resolve() / "Projects"))
    ap.add_argument("--cutoff-days", type=int, default=DEFAULT_CUTOFF_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    snapshot_path = Path(args.snapshot)
    snapshot = load_snapshot(snapshot_path)

    refreshed = refresh(
        snapshot,
        Path(args.repos_root),
        cutoff_days=args.cutoff_days,
    )

    changed = refreshed.generated_at != snapshot.generated_at
    active_count = sum(
        1 for old, new in zip(snapshot.subsystems, refreshed.subsystems)
        if old.sources != new.sources
    )

    if args.dry_run:
        print(f"[dry-run] Would refresh {snapshot_path}")
        print(f"[dry-run] Active repos matched: {active_count} subsystems updated")
        return 0

    # Atomic write
    tmp_path = snapshot_path.with_suffix(".yaml.tmp")
    refreshed.write_yaml(tmp_path)
    tmp_path.replace(snapshot_path)

    print(f"Refreshed {snapshot_path}: {active_count} subsystems updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
