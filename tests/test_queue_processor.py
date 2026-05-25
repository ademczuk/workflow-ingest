"""Tests for automations/queue_processor.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT / "tests" / "fixtures"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(PROJECT / "automations" / "queue_processor.py"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def test_dry_run_produces_no_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "digests"
    conflicts_dir = tmp_path / "conflicts"
    result = _run(
        [
            "--summary", str(FIXTURES / "kQu5pWKS8GA_summary.md"),
            "--out-dir", str(out_dir),
            "--conflicts-dir", str(conflicts_dir),
            "--dry-run",
        ]
    )
    assert result.returncode == 0
    assert "[dry-run] Would write digest" in result.stdout
    assert not list(out_dir.glob("*"))
    assert not list(conflicts_dir.glob("*"))


def test_real_run_writes_digest(tmp_path: Path) -> None:
    out_dir = tmp_path / "digests"
    result = _run(
        [
            "--summary", str(FIXTURES / "kQu5pWKS8GA_summary.md"),
            "--out-dir", str(out_dir),
            "--conflicts-dir", str(tmp_path / "conflicts"),
        ]
    )
    assert result.returncode == 0
    assert "Digest written:" in result.stdout
    digest = list(out_dir.glob("*.md"))
    assert len(digest) == 1
    text = digest[0].read_text(encoding="utf-8")
    assert "youtube-digest" in text


def test_summary_line_includes_decisions() -> None:
    result = _run(
        [
            "--summary", str(FIXTURES / "kQu5pWKS8GA_summary.md"),
            "--dry-run",
        ]
    )
    assert result.returncode == 0
    # Look for the summary line pattern
    assert "Summary:" in result.stdout
    # Should list at least one decision category
    lines = result.stdout.strip().splitlines()
    summary_line = [ln for ln in lines if ln.startswith("Summary:")][0]
    assert "patterns" in summary_line


def test_missing_summary_fails() -> None:
    result = _run(["--summary", str(FIXTURES / "nonexistent_summary.md")])
    assert result.returncode != 0


def test_tracker_without_api_key_warns(tmp_path: Path) -> None:
    env = dict(subprocess.os.environ)
    env.pop("LINEAR_API_KEY", None)
    cmd = [
        sys.executable,
        str(PROJECT / "automations" / "queue_processor.py"),
        "--summary", str(FIXTURES / "kQu5pWKS8GA_summary.md"),
        "--out-dir", str(tmp_path / "digests"),
        "--conflicts-dir", str(tmp_path / "conflicts"),
        "--create-linear-issues",
        "--dry-run",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0
    assert "LINEAR_API_KEY not set" in result.stderr


def test_create_linear_issue_verifies_after_write(monkeypatch) -> None:
    from automations import queue_processor

    calls: list[str] = []

    class FakeResponse:
        def __init__(self, data: dict) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._data

    class FakeRequests:
        @staticmethod
        def post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
            calls.append(json["query"])
            if "IssueCreate" in json["query"]:
                return FakeResponse({
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {"id": "issue-1", "identifier": "CLA-999"},
                        }
                    }
                })
            return FakeResponse({
                "data": {
                    "issue": {
                        "id": "issue-1",
                        "identifier": "CLA-999",
                        "title": "Route pattern",
                        "description": "Pattern abc123 routed from YouTube.",
                    }
                }
            })

    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")
    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    payload = {
        "title": "Route pattern",
        "description": "Pattern abc123 routed from YouTube.",
        "tags": ["workflow-ingest", "pattern-abc123"],
    }

    assert queue_processor._create_linear_issue(payload) == "CLA-999"
    assert len(calls) == 2
    assert "IssueCreate" in calls[0]
    assert "IssueVerify" in calls[1]


def test_create_linear_issue_fails_if_verify_loses_pattern(monkeypatch) -> None:
    from automations import queue_processor

    class FakeResponse:
        def __init__(self, data: dict) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._data

    class FakeRequests:
        @staticmethod
        def post(url: str, headers: dict, json: dict, timeout: int) -> FakeResponse:
            if "IssueCreate" in json["query"]:
                return FakeResponse({
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {"id": "issue-1", "identifier": "CLA-999"},
                        }
                    }
                })
            return FakeResponse({
                "data": {
                    "issue": {
                        "id": "issue-1",
                        "identifier": "CLA-999",
                        "title": "Route pattern",
                        "description": "Pattern missing after write.",
                    }
                }
            })

    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")
    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    payload = {
        "title": "Route pattern",
        "description": "Pattern abc123 routed from YouTube.",
        "tags": ["workflow-ingest", "pattern-abc123"],
    }

    with pytest.raises(RuntimeError, match="verify-after-write failed"):
        queue_processor._create_linear_issue(payload)
