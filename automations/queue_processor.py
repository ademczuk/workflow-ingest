"""End-to-end queue processor: summary.md -> route -> audit -> decide -> sinks.

Usage:
    python automations/queue_processor.py --summary <path/to/summary.md>
    python automations/queue_processor.py --summary <path> --dry-run
    python automations/queue_processor.py --summary <path> --create-linear-issues
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from extract.digest_to_patterns import parse_summary_md  # noqa: E402
from inventory.corpus_map import load_corpus_map  # noqa: E402
from inventory.model import load_snapshot  # noqa: E402
from route.audit import audit  # noqa: E402
from route.decision import decide_all  # noqa: E402
from route.bm25_matcher import match_all_bm25  # noqa: E402
from sink.obsidian_writer import write_digest  # noqa: E402
from sink.conflict_log import append_conflict  # noqa: E402
from sink.tracker_writer import build_tracker_payload  # noqa: E402

LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_LINEAR_TEAM_ID = "4a536ff7-196b-411e-89bb-63230106443d"


def _linear_headers() -> dict[str, str] | None:
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        return None
    return {"Authorization": key, "Content-Type": "application/json"}


def _linear_graphql(query: str, variables: dict) -> dict | None:
    headers = _linear_headers()
    if headers is None:
        return None
    try:
        import requests  # type: ignore[import-untyped]
    except ImportError:
        return None

    resp = requests.post(
        LINEAR_API_URL,
        headers=headers,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Linear API error: {data['errors']}")
    return data


def _pattern_id_from_payload(payload: dict) -> str | None:
    for tag in payload.get("tags", []):
        if isinstance(tag, str) and tag.startswith("pattern-"):
            return tag.removeprefix("pattern-")
    return None


def _verify_linear_issue(issue_id: str, payload: dict) -> None:
    query = """
    query IssueVerify($id: String!) {
      issue(id: $id) {
        id
        identifier
        title
        description
      }
    }
    """
    data = _linear_graphql(query, {"id": issue_id})
    issue = ((data or {}).get("data") or {}).get("issue")
    if not issue:
        raise RuntimeError(f"verify-after-write failed: issue {issue_id} was not found")
    if issue.get("title") != payload["title"]:
        raise RuntimeError(
            f"verify-after-write failed: title mismatch for {issue.get('identifier') or issue_id}"
        )
    pattern_id = _pattern_id_from_payload(payload)
    if pattern_id and pattern_id not in (issue.get("description") or ""):
        raise RuntimeError(
            f"verify-after-write failed: pattern {pattern_id} missing from "
            f"{issue.get('identifier') or issue_id}"
        )


def _create_linear_issue(payload: dict) -> str | None:
    team_id = os.environ.get("LINEAR_TEAM_ID", DEFAULT_LINEAR_TEAM_ID)
    mutation = """
    mutation IssueCreate($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier }
      }
    }
    """
    variables = {
        "input": {
            "title": payload["title"],
            "description": payload["description"],
            "teamId": team_id,
            "labelIds": [],
        }
    }
    data = _linear_graphql(mutation, variables)
    if data is None:
        return None
    result = data["data"]["issueCreate"]
    if not result.get("success"):
        raise RuntimeError("Linear issueCreate returned success=false")
    issue = result["issue"]
    _verify_linear_issue(issue["id"], payload)
    return issue["identifier"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Queue processor: summary.md -> sinks")
    ap.add_argument("--summary", required=True, help="Path to summary.md")
    ap.add_argument("--snapshot", default=str(PROJECT / "inventory" / "snapshot.yaml"))
    ap.add_argument("--corpus", default=str(PROJECT / "inventory" / "corpus_map_v1.yaml"))
    ap.add_argument("--out-dir", default=str(PROJECT / "data" / "digests"))
    ap.add_argument("--conflicts-dir", default=str(PROJECT / "data" / "conflicts"))
    ap.add_argument("--dry-run", action="store_true", help="Skip all writes; print what would happen")
    ap.add_argument("--create-linear-issues", action="store_true", help="Create Linear issues for integrate decisions")
    args = ap.parse_args()

    summary_path = Path(args.summary)
    snapshot = load_snapshot(Path(args.snapshot))
    corpus = load_corpus_map(Path(args.corpus))

    patterns = parse_summary_md(summary_path)
    if not patterns:
        print("No patterns extracted from summary.", file=sys.stderr)
        return 1

    # Pipeline: match -> audit -> decide
    routed = match_all_bm25(patterns, corpus, snapshot)
    existing_ids = frozenset()
    audits = [audit(p, snapshot, existing_ids) for p in routed]
    decisions = decide_all(routed, audits, existing_ids)

    # Group by decision
    integrate: list[tuple[object, object]] = []
    conflicts: list[tuple[object, object]] = []
    wiki_only: list[tuple[object, object]] = []
    discard: list[tuple[object, object]] = []
    already_covered: list[tuple[object, object]] = []

    for p, d in zip(routed, decisions):
        if d.decision == "integrate":
            integrate.append((p, d))
        elif d.decision == "conflict":
            conflicts.append((p, d))
        elif d.decision == "wiki-only":
            wiki_only.append((p, d))
        elif d.decision == "discard":
            discard.append((p, d))
        elif d.decision == "already-covered":
            already_covered.append((p, d))

    video_id = patterns[0].source_meta.get("video_id", "unknown")
    source_url = patterns[0].source_url

    # Write Obsidian digest (integrate + wiki-only + already-covered)
    digest_patterns = [p for p, _d in integrate + wiki_only + already_covered]
    if not args.dry_run:
        out_dir = Path(args.out_dir)
        digest_path = write_digest(out_dir, video_id, source_url, digest_patterns)
        print(f"Digest written: {digest_path}")
    else:
        print(f"[dry-run] Would write digest to {args.out_dir}/{video_id}.md ({len(digest_patterns)} patterns)")

    # Write conflicts
    for p, _d in conflicts:
        if not args.dry_run:
            cpath = append_conflict(Path(args.conflicts_dir), p)
            print(f"Conflict logged: {cpath}")
        else:
            print(f"[dry-run] Would log conflict for {p.pattern_id} -> {p.routing.subsystem_slug}")

    # Create Linear issues for integrate decisions
    tracker_ok = 0
    tracker_fail = 0
    if args.create_linear_issues:
        if _linear_headers() is None:
            print("Warning: LINEAR_API_KEY not set; skipping tracker writes.", file=sys.stderr)
        for p, _d in integrate:
            payload = build_tracker_payload(p)
            if not args.dry_run:
                try:
                    issue_id = _create_linear_issue(payload)
                    if issue_id:
                        print(f"Tracker created: {issue_id} ({p.pattern_id})")
                        tracker_ok += 1
                    else:
                        print(f"Tracker skipped (no API key): {p.pattern_id}")
                        tracker_fail += 1
                except Exception as e:
                    print(f"Tracker failed: {p.pattern_id} — {e}")
                    tracker_fail += 1
            else:
                print(f"[dry-run] Would create Linear issue for {p.pattern_id}: {payload['title'][:70]}...")

    # Summary line
    total = len(patterns)
    counts = Counter(d.decision for d in decisions)
    parts = [f"{total} patterns"]
    for dec in ("integrate", "wiki-only", "conflict", "discard", "already-covered"):
        n = counts.get(dec, 0)
        if n:
            parts.append(f"{n} {dec}")
    if args.create_linear_issues and not args.dry_run:
        parts.append(f"{tracker_ok} tracker OK")
        if tracker_fail:
            parts.append(f"{tracker_fail} tracker fail")
    print("\nSummary: " + ", ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
