"""Tests for automations/inventory_refresh.py."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROJECT / "automations" / "inventory_refresh.py"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _make_snapshot(path: Path, subsystem_slug: str = "visual-llm") -> None:
    from inventory.model import Snapshot, Subsystem, SubsystemSource

    snap = Snapshot(
        schema_version="1.0",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generator="test",
        subsystems=(
            Subsystem(
                slug=subsystem_slug,
                display_name="Test Subsystem",
                sources=(
                    SubsystemSource(kind="operator_bullet", ref="test", last_seen=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                ),
            ),
        ),
    )
    snap.write_yaml(path)


def test_dry_run_does_not_modify(tmp_path: Path) -> None:
    snap_path = tmp_path / "snapshot.yaml"
    _make_snapshot(snap_path)
    original = snap_path.read_text(encoding="utf-8")

    result = _run(["--snapshot", str(snap_path), "--repos-root", str(tmp_path), "--dry-run"])
    assert result.returncode == 0
    assert snap_path.read_text(encoding="utf-8") == original


def test_refresh_updates_last_seen(tmp_path: Path) -> None:
    from inventory.model import load_snapshot

    snap_path = tmp_path / "snapshot.yaml"
    _make_snapshot(snap_path, subsystem_slug="visual-llm")

    # Create a real git repo named "visual-llm" with a recent commit
    repo = tmp_path / "visual-llm"
    repo.mkdir()
    import shutil
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run([git, "config", "user.email", "test@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run([git, "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run([git, "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    result = _run(["--snapshot", str(snap_path), "--repos-root", str(tmp_path)])
    assert result.returncode == 0
    assert "1 subsystems updated" in result.stdout

    refreshed = load_snapshot(snap_path)
    sub = refreshed.subsystems[0]
    # A new commit source should have been added with updated last_seen
    commit_sources = [s for s in sub.sources if s.kind == "commit"]
    assert len(commit_sources) == 1
    assert commit_sources[0].last_seen > datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_no_match_leaves_snapshot_unchanged(tmp_path: Path) -> None:
    from inventory.model import load_snapshot

    snap_path = tmp_path / "snapshot.yaml"
    _make_snapshot(snap_path, subsystem_slug="zzz-unknown")

    # No matching repo
    result = _run(["--snapshot", str(snap_path), "--repos-root", str(tmp_path)])
    assert result.returncode == 0
    assert "0 subsystems updated" in result.stdout

    refreshed = load_snapshot(snap_path)
    assert refreshed.generated_at > datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Subsystem unchanged
    assert refreshed.subsystems[0].slug == "zzz-unknown"
