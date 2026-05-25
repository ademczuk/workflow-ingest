"""CLI decision report from a summary.md.

Usage:
    python tools/decide.py --summary <path/to/summary.md>
    python tools/decide.py --summary <path> --json
    python tools/decide.py --summary <path> --matcher bm25
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from extract.digest_to_patterns import parse_summary_md  # noqa: E402
from inventory.model import load_snapshot  # noqa: E402
from route.audit import audit  # noqa: E402
from route.decision import decide_all, Decision  # noqa: E402
from route.matcher import match_all  # noqa: E402
from route.bm25_matcher import match_all_bm25  # noqa: E402
from inventory.corpus_map import load_corpus_map  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Decision report for a YouTube summary.md")
    ap.add_argument("--summary", required=True, help="Path to summary.md")
    ap.add_argument("--snapshot", default=str(PROJECT / "inventory" / "snapshot.yaml"))
    ap.add_argument("--corpus", default=str(PROJECT / "inventory" / "corpus_map_v1.yaml"))
    ap.add_argument("--matcher", default="keyword", choices=["keyword", "bm25"])
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = ap.parse_args()

    summary_path = Path(args.summary)
    snapshot = load_snapshot(Path(args.snapshot))

    patterns = parse_summary_md(summary_path)
    if not patterns:
        print("No patterns extracted from summary.", file=sys.stderr)
        return 1

    if args.matcher == "bm25":
        corpus = load_corpus_map(Path(args.corpus))
        routed = match_all_bm25(patterns, corpus, snapshot)
    else:
        routed = match_all(patterns, snapshot)

    audits = [audit(p, snapshot, frozenset()) for p in routed]
    decisions = decide_all(routed, audits, frozenset())

    if args.json:
        _emit_json(routed, audits, decisions)
    else:
        _emit_text(routed, audits, decisions, summary_path.name)
    return 0


def _emit_text(
    patterns: list,
    audits: list,
    decisions: list,
    summary_name: str,
) -> None:
    print(f"== Decision report: {summary_name}")
    print(f"   patterns: {len(patterns)}")
    print()

    # Decision distribution
    decision_counts = Counter(d.decision for d in decisions)
    print("== decision distribution")
    for dec, n in decision_counts.most_common():
        print(f"   {dec:20s} {n:3d}")
    print()

    # Per-pattern detail
    print("== per-pattern detail")
    for p, a, d in zip(patterns, audits, decisions):
        routing = p.routing
        tier = routing.tier if routing else "n/a"
        state = routing.match_state if routing else "n/a"
        slug = routing.subsystem_slug if routing else "n/a"
        print(
            f"   {p.pattern_id}  {p.type:25s}  [{slug:30s}]  "
            f"tier={tier:8s}  decision={d.decision}"
        )
        print(f"     idea: {p.idea[:130]}")
        print(f"     reason: {d.reason}")
        if d.write_target:
            print(f"     write_target: {d.write_target}")
        if not a.ok:
            print(f"     audit_failures: {'; '.join(a.failures)}")
        print()


def _emit_json(patterns, audits, decisions) -> None:
    out = []
    for p, a, d in zip(patterns, audits, decisions):
        routing = p.routing
        out.append({
            "pattern_id": p.pattern_id,
            "idea": p.idea,
            "type": p.type,
            "subsystem_slug": routing.subsystem_slug if routing else None,
            "tier": routing.tier if routing else None,
            "match_state": routing.match_state if routing else None,
            "decision": d.decision,
            "reason": d.reason,
            "write_target": d.write_target,
            "audit_ok": a.ok,
            "audit_failures": list(a.failures),
            "source_url": p.source_url,
        })
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
