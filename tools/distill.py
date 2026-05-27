"""distill.py - one-shot YouTube distill: summarize -> decide -> verdict.

The v0.3 glue the HANDOVER defers. Given a YouTube URL it:
  1. resolves the video_id and reuses an existing summary.md if present (--force to redo)
  2. runs visual-llm youtube_summarize.py (GPU) when no cached summary exists
  3. runs decide.py (BM25) to classify the extracted patterns
  4. emits a compact "worth consuming / integrating?" verdict (text, or --json)

Read-only by default (decide.py writes nothing). Pass --integrate to also run the
queue processor (writes digest/conflicts; --create-linear-issues for tracker rows).

Usage:
  python tools/distill.py <youtube-url>
  python tools/distill.py <url> --json
  python tools/distill.py <url> --force        # re-summarize even if cached
  python tools/distill.py <url> --integrate    # also run queue_processor (writes)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


def _parakeet_alive(timeout_s: float = 2.0) -> bool:
    """Probe the Parakeet STT container on 127.0.0.1:5093. Returns False if
    Docker is down, the container is not running, or the health endpoint hangs.
    Used to choose between --use-parakeet (default) and --prefer-subs (fallback
    to YouTube auto-captions) so a stopped Docker daemon doesn't silently hang
    the summarizer for 30 min."""
    url = os.environ.get("PARAKEET_HEALTH_URL", "http://127.0.0.1:5093/health")
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            return 200 <= r.status < 300
    except Exception:
        return False

PROJECT = Path(__file__).resolve().parent.parent
VISUAL_LLM = Path(os.environ.get("VISUAL_LLM_DIR", r"C:/Projects/visual-llm"))
SUMMARIZE = VISUAL_LLM / "pipeline" / "youtube_summarize.py"
DECIDE = PROJECT / "tools" / "decide.py"
QUEUE = PROJECT / "automations" / "queue_processor.py"
PY = sys.executable

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")


def video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url or "")
    if m:
        return m.group(1)
    s = (url or "").strip()
    return s if re.fullmatch(r"[A-Za-z0-9_-]{11}", s) else None


def _run(cmd, timeout):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _load_title(out_dir: Path, vid: str) -> str:
    try:
        d = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        return (d.get("title") or d.get("video_title") or vid)[:160]
    except Exception:
        return vid


def _fail(as_json: bool, kind: str, msg: str) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": kind, "detail": msg}))
    else:
        print(f"[x] {kind}: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Distill a YouTube video: summarize -> decide -> verdict")
    ap.add_argument("url")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true", help="Re-summarize even if a cached summary exists")
    ap.add_argument("--integrate", action="store_true", help="Also run queue_processor (writes digest/conflicts)")
    ap.add_argument("--create-linear-issues", action="store_true", help="With --integrate, also create Linear issues")
    ap.add_argument("--summarize-timeout", type=int, default=1800)
    args = ap.parse_args()

    vid = video_id_from_url(args.url)
    if not vid:
        return _fail(args.json, "bad_url", f"Could not parse a YouTube video id from: {args.url}")

    out_dir = VISUAL_LLM / "data" / vid
    summary_md = out_dir / "summary.md"

    summarized_now = False
    have_summary = summary_md.exists() and summary_md.stat().st_size > 0
    if args.force or not have_summary:
        if not SUMMARIZE.exists():
            return _fail(args.json, "summarizer_missing", f"youtube_summarize.py not found at {SUMMARIZE}")
        # Default to YouTube auto-captions: faster, more reliable, and Parakeet
        # has hung on real videos even when /health was 200 (one such incident
        # ate a 30-min subprocess budget). Opt in to Parakeet only when subs are
        # known-unavailable or KCS_DISTILL_USE_PARAKEET=1 is set.
        force_parakeet = os.environ.get("KCS_DISTILL_USE_PARAKEET", "").strip() in ("1", "true", "yes")
        stt_flag = "--use-parakeet" if (force_parakeet and _parakeet_alive()) else "--prefer-subs"
        print(f"distill: stt_flag={stt_flag} (force_parakeet={force_parakeet})", file=sys.stderr, flush=True)
        r = _run([PY, "-u", str(SUMMARIZE), args.url, "--max-keyframes", "12",
                  "--scene-detect", "auto", stt_flag], args.summarize_timeout)
        summarized_now = True
        if not (summary_md.exists() and summary_md.stat().st_size > 0):
            tail = (r.stderr or r.stdout or "")[-500:]
            return _fail(args.json, "summarize_failed", f"no summary.md produced. tail: {tail}")

    dr = _run([PY, "-u", str(DECIDE), "--summary", str(summary_md), "--matcher", "bm25", "--json"], 180)
    if dr.returncode != 0:
        return _fail(args.json, "decide_failed", (dr.stderr or dr.stdout or "")[-500:])
    try:
        patterns = json.loads(dr.stdout)
    except Exception as e:
        return _fail(args.json, "decide_parse_failed", f"{e}: {dr.stdout[:300]}")

    counts: dict[str, int] = {}
    for p in patterns:
        counts[p["decision"]] = counts.get(p["decision"], 0) + 1
    n_integrate = counts.get("integrate", 0)
    n_wiki = counts.get("wiki-only", 0)
    n_covered = counts.get("already-covered", 0)

    if n_integrate > 0:
        worth, headline = True, f"Worth integrating: {n_integrate} high-value pattern(s)"
    elif n_wiki > 0:
        worth, headline = True, f"Worth a skim: {n_wiki} wiki-only insight(s), nothing to integrate"
    elif n_covered > 0:
        worth, headline = False, "Already covered: nothing new here"
    else:
        worth, headline = False, "Probably skip: no actionable patterns"

    # Phase 1 fan-out: pass `routes` through so the bridge can render a
    # secondary-targets pill. `subsystem` stays for the legacy single-slug
    # rendering path; both shapes coexist for backwards compatibility.
    top = [
        {"idea": p["idea"], "subsystem": p["subsystem_slug"], "tier": p["tier"], "routes": p.get("routes", [])}
        for p in patterns if p["decision"] == "integrate"
    ][:5]

    integrated = None
    if args.integrate and n_integrate > 0:
        qcmd = [PY, "-u", str(QUEUE), "--summary", str(summary_md)]
        if args.create_linear_issues:
            qcmd.append("--create-linear-issues")
        qr = _run(qcmd, 300)
        integrated = {"rc": qr.returncode, "tail": (qr.stdout or qr.stderr or "")[-400:]}

    result = {
        "ok": True, "url": args.url, "video_id": vid, "title": _load_title(out_dir, vid),
        "summary_md": str(summary_md), "summarized_now": summarized_now,
        "total_patterns": len(patterns), "counts": counts,
        "worth": worth, "headline": headline, "top_integrate": top, "integrated": integrated,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    mark = "[OK]" if worth else "[--]"
    print(f"{result['title']}")
    print(f"  {result['url']}")
    print(f"\n{mark} {headline}")
    print(f"  patterns: {result['total_patterns']}  |  "
          + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if top:
        print("\n  top integrate:")
        for t in top:
            print(f"  - [{t['subsystem']}] ({t['tier']}) {t['idea'][:120]}")
    print(f"\n  summary: {result['summary_md']}")
    if integrated:
        print(f"  integrated: rc={integrated['rc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
