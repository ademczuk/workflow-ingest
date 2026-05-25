"""BM25 matcher dry-run with full distribution + tier dump.

Usage:
    python tools/dry_run_bm25.py --summary <path/to/summary.md>

Reads corpus_map_v1.yaml and snapshot.yaml, validates slug intersection,
runs BM25 matcher, prints per-pattern routing + score distribution.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median, stdev


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from extract.digest_to_patterns import parse_summary_md  # noqa: E402
from inventory.corpus_map import load_corpus_map  # noqa: E402
from inventory.model import load_snapshot  # noqa: E402
from route.bm25_matcher import _find_known_gap, build_index, match_bm25, score_pattern  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--corpus", default=str(PROJECT / "inventory" / "corpus_map_v1.yaml"))
    ap.add_argument("--snapshot", default=str(PROJECT / "inventory" / "snapshot.yaml"))
    args = ap.parse_args()

    corpus = load_corpus_map(Path(args.corpus))
    snapshot = load_snapshot(Path(args.snapshot))
    index = build_index(corpus)

    patterns = parse_summary_md(Path(args.summary))

    print(f"== BM25 dry-run: {Path(args.summary).name}")
    print(f"   patterns: {len(patterns)}  (after discardable filter)")
    print(f"   subsystems: {len(corpus.subsystems)}")
    print(f"   bm25_match_threshold: {corpus.routing_rules.bm25_match_threshold}")
    print()

    routed = []
    all_scores: list[float] = []
    for p in patterns:
        r = match_bm25(p, corpus, snapshot, index=index)
        scores = score_pattern(p, corpus, index=index)
        best_score = scores.get(r.routing.subsystem_slug, 0.0)
        all_scores.append(best_score)
        routed.append((p, r, best_score, scores))

    print("== routing distribution (subsystem vs topical_page)")
    counts = Counter(r[1].routing.subsystem_slug for r in routed)
    for slug, n in counts.most_common():
        kind = "topical" if slug.startswith("page.") else "system"
        print(f"   [{kind}] {slug:30s} {n:3d}")
    target_kind_counts = Counter(r[1].routing.target_kind for r in routed)
    print(f"   target_kind: {dict(target_kind_counts)}")
    print()

    print("== tier distribution")
    tier_counts = Counter(r[1].routing.tier for r in routed)
    for tier, n in tier_counts.most_common():
        print(f"   {tier:20s} {n:3d}")
    print()

    print("== best-score statistics (across all patterns)")
    if all_scores:
        print(f"   min={min(all_scores):.3f}  max={max(all_scores):.3f}")
        print(f"   mean={mean(all_scores):.3f}  median={median(all_scores):.3f}")
        if len(all_scores) > 1:
            print(f"   stdev={stdev(all_scores):.3f}")
    print()

    gap_hits = [(p, r, _find_known_gap(p, corpus)) for p, r, _, _ in routed]
    actual_gaps = [(p, r, g) for p, r, g in gap_hits if g is not None]
    print(f"== actual known-gap matches ({len(actual_gaps)} / {len(routed)} patterns)")
    for p, r, gap in actual_gaps:
        print(f"   pattern={p.pattern_id} concept={gap.concept} -> {gap.landing_target}")
        print(f"     idea: {p.idea[:120]}")
    print()

    print("== per-pattern detail")
    for p, r, best_score, scores in routed:
        target_score = scores.get(r.routing.subsystem_slug, best_score)
        print(
            f"   {p.pattern_id} {p.type:25s} -> [{r.routing.target_kind}] {r.routing.subsystem_slug:30s} "
            f"score={target_score:5.2f}  tier={r.routing.tier}"
        )
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
        if len(sorted_scores) >= 2 and target_score - sorted_scores[1][1] < 0.5 and target_score > 0:
            top2 = sorted_scores[:2]
            print(f"     close call: {top2[0][0]}={top2[0][1]:.2f} vs {top2[1][0]}={top2[1][1]:.2f}")
        print(f"     idea: {p.idea[:130]}")
        if r.routing.canonical_path:
            print(f"     canonical: {r.routing.canonical_path}")
        if r.routing.write_target:
            print(f"     write_target: {r.routing.write_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
