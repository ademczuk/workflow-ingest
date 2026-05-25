"""Dry-run matcher against corpus_map_v1.yaml — does NOT silently coerce schemas.

corpus_map_v1.yaml uses a different schema than inventory/snapshot.yaml.
This script builds an in-memory routing pass directly off corpus_map's
keyword/concept/canonical fields. Output flags hits below the novelty
threshold so the operator can read each candidate's canonical page before
declaring novelty.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import yaml


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from extract.digest_to_patterns import parse_summary_md  # noqa: E402


_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2}


def _multitoken(items: list) -> set[str]:
    out: set[str] = set()
    for s in items:
        out.update(_tokens(str(s)))
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--corpus", required=True)
    args = ap.parse_args()

    corpus = yaml.safe_load(Path(args.corpus).read_text(encoding="utf-8"))
    rules = corpus.get("routing_rules", {})
    match_thr = rules.get("match_threshold_jaccard", 0.15)
    novelty_thr = rules.get("novelty_threshold_jaccard", 0.10)
    priority = rules.get("priority_order", [])

    subsystems = []
    for s in corpus["subsystems"]:
        kws = _multitoken(s["keywords"]) | _multitoken(s.get("concepts", []))
        kws.update(_tokens(s.get("name", "")))
        kws.update(_tokens(s.get("role", "")))
        subsystems.append({
            "slug": s["slug"],
            "name": s["name"],
            "keywords": kws,
            "canonical": s.get("obsidian_canonical"),
            "operational": s.get("obsidian_operational", []),
        })

    known_gaps = corpus.get("known_gaps_2026_05_06", [])
    gap_keywords = {g["concept"].lower(): g for g in known_gaps}

    patterns = parse_summary_md(Path(args.summary))

    print(f"== dry-run: {Path(args.summary).name}")
    print(f"   patterns: {len(patterns)}")
    print(f"   subsystems: {len(subsystems)}")
    print(f"   thresholds: match>={match_thr}, novelty<{novelty_thr}")
    print()

    routed = []
    novel_candidates = []
    gap_hits = []

    for p in patterns:
        p_kw = _tokens(p.idea + " " + " ".join(p.evidence))

        scored = sorted(
            ((s, jaccard(p_kw, s["keywords"])) for s in subsystems),
            key=lambda x: (-x[1], priority.index(x[0]["slug"]) if x[0]["slug"] in priority else 999),
        )
        best, best_score = scored[0]

        gap_match = None
        idea_low = (p.idea + " " + " ".join(p.evidence)).lower()
        for gap_concept, gap_meta in gap_keywords.items():
            if gap_concept.lower() in idea_low:
                gap_match = gap_meta
                break

        routed.append((p, best, best_score, scored[1] if len(scored) > 1 else None))
        if gap_match:
            gap_hits.append((p, gap_match))
        if best_score < match_thr:
            novel_candidates.append((p, best, best_score))

    print("== routing distribution")
    counts = Counter(r[1]["slug"] for r in routed)
    for slug, n in counts.most_common():
        print(f"   {slug:20s} {n:3d}")
    print()

    print("== known-gaps_2026_05_06 hits")
    if gap_hits:
        for p, gap in gap_hits:
            print(f"   {gap['concept']:35s} pattern={p.pattern_id} ({p.type})")
            print(f"     idea: {p.idea[:100]}")
            print(f"     landing: {gap['landing_target']}")
            print(f"     why: {gap['why']}")
    else:
        print("   (none)")
    print()

    print("== novel candidates (below match_threshold; READ canonical before declaring novel)")
    if novel_candidates:
        for p, s, score in novel_candidates:
            print(f"   pattern={p.pattern_id} score={score:.3f} -> would-route {s['slug']}")
            print(f"     canonical-to-read: {s['canonical']}")
            print(f"     idea: {p.idea[:120]}")
    else:
        print("   (none below threshold)")
    print()

    print("== top hits per pattern (for cross-check)")
    for p, best, score, runner in routed:
        print(f"   {p.pattern_id} {p.type:25s} -> {best['slug']:20s} (jaccard={score:.3f})")
        print(f"     idea: {p.idea[:120]}")
        if runner is not None:
            r_sub, r_score = runner
            print(f"     runner-up: {r_sub['slug']:20s} (jaccard={r_score:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
