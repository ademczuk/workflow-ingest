"""distill_service.py - host HTTP service that runs the YouTube distill glue async.

The clawfish Discord bridge runs in a container and cannot touch the host GPU
pipeline. This tiny host service wraps tools/distill.py so the bridge can kick a
distill over HTTP (via host.docker.internal) and poll for the verdict.

Routes:
  POST /distill   {url}            -> 202 {job_id, status:"running"}   (token-gated)
  GET  /distill/<job_id>           -> {status: running|done|error, verdict?, error?} (token-gated)
  GET  /healthz                    -> {ok, jobs}                       (open)

Async by design: summarize can take minutes, so POST returns immediately and a
daemon thread runs distill.py --json. Poll the job for the verdict.

Bind 127.0.0.1: on Docker Desktop a container reaches host loopback services via
host.docker.internal, so we do NOT expose this on the LAN. Token gate returns 404
on miss (federation-tokens doctrine: don't telegraph the endpoint).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, request

PORT = int(os.environ.get("KCS_DISTILL_PORT", "9789"))
BIND = os.environ.get("KCS_DISTILL_HOST", "127.0.0.1")
WORKFLOW_INGEST = Path(os.environ.get("WORKFLOW_INGEST_DIR", r"C:/Projects/workflow-ingest"))
DISTILL = WORKFLOW_INGEST / "tools" / "distill.py"
DECIDE = WORKFLOW_INGEST / "tools" / "decide.py"
VISUAL_LLM_DATA = Path(os.environ.get("VISUAL_LLM_DIR", r"C:/Projects/visual-llm")) / "data"
PY = sys.executable
JOB_TTL_S = 3600
DISTILL_TIMEOUT_S = int(os.environ.get("KCS_DISTILL_TIMEOUT_S", "2000"))

# Distill-list cache. The dashboard polls /distill/list, and re-scanning the
# data directory (currently ~20 videos, plus per-video decide.py runs to
# resolve patterns counts) is expensive. Cache the whole list response for
# DISTILL_LIST_TTL_S seconds. Per-video verdict is also persisted as a
# verdict.json sidecar so cold caches don't re-run decide for every video.
DISTILL_LIST_TTL_S = int(os.environ.get("KCS_DISTILL_LIST_TTL_S", "60"))
_distill_list_cache: dict = {"ts": 0.0, "videos": []}
_distill_list_lock = threading.Lock()
# Path-safety: YouTube video IDs are 11 chars from this alphabet. Anything
# else is rejected as a path-traversal attempt before we touch the filesystem.
import re as _re
_YT_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{11}$")

# VRAM-mode arbiter integration: if qwen36 is not loaded when a job lands,
# the worker thread will run this script to flip the GPU back from brutal
# (or any other mode) before dispatching distill.py. Without this, the
# service used to 503 the request immediately and the arbiter never saw the
# demand because the job was rejected before /healthz could count it.
WITH_BRUTAL_SCRIPT = os.environ.get(
    "KCS_VRAM_ARBITER",
    r"C:/Projects/Claude_Code/Job_Orchestrator/scripts/with_brutal_llm.py",
)
ENSURE_QWEN36_TIMEOUT_S = int(os.environ.get("KCS_ENSURE_QWEN36_TIMEOUT_S", "180"))

# CLA-206: subprocess-level cadence watchdog. The outer DISTILL_TIMEOUT_S
# (30 min) used to be the only guard - if qwen3.6 zombied mid-run, the
# subprocess hung for the full 30 min before returning. With a stdout-line
# cadence check we can detect a stalled inference within minutes and kill
# the subprocess so the worker can surface the failure and retrigger qwen36.
DISTILL_STDOUT_SILENCE_S = int(os.environ.get("KCS_DISTILL_STDOUT_SILENCE_S", "300"))
RESTART_QWEN36_BAT = os.environ.get(
    "KCS_RESTART_QWEN36_BAT",
    r"C:/Projects/visual-llm/scripts/restart_qwen36.bat",
)


def _run_distill_with_cadence_watchdog(url: str, total_timeout_s: int, silence_s: int):
    """Run distill.py via Popen + reader threads. If the subprocess produces
    no stdout for `silence_s` seconds while still alive, kill it AND fire
    restart_qwen36.bat (the most common cause of stdout silence is a hung
    qwen36 inference, and the next dispatch will fail-loud-fast if qwen36
    isn't restarted). Returns:
        (returncode, captured_stdout, captured_stderr, killed_for_silence, killed_for_total)
    """
    proc = subprocess.Popen(
        [PY, "-u", str(DISTILL), url, "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    last_activity = [time.time()]
    captured_stdout: list[str] = []
    captured_stderr: list[str] = []

    def _reader(stream, sink):
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                last_activity[0] = time.time()
                sink.append(line)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, captured_stdout), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, captured_stderr), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.time() + total_timeout_s
    killed_for_silence = False
    killed_for_total = False
    while True:
        if proc.poll() is not None:
            break
        now = time.time()
        if now > deadline:
            try:
                proc.kill()
            except Exception:
                pass
            killed_for_total = True
            break
        if (now - last_activity[0]) > silence_s:
            print(
                f"distill_service: subprocess silent for {silence_s}s; "
                f"killing PID {proc.pid} and triggering restart_qwen36.bat",
                file=sys.stderr, flush=True,
            )
            try:
                proc.kill()
            except Exception:
                pass
            killed_for_silence = True
            # Fire-and-forget: don't block on the restart, the next distill
            # request will probe /slots itself and the worker's
            # ensure-qwen36 path will await it.
            try:
                subprocess.Popen([RESTART_QWEN36_BAT], shell=True)
            except Exception as exc:
                print(f"distill_service: restart_qwen36.bat trigger failed: {exc}",
                      file=sys.stderr, flush=True)
            break
        time.sleep(2)

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    return (proc.returncode, "".join(captured_stdout), "".join(captured_stderr),
            killed_for_silence, killed_for_total)

# Optional clawfish-opus "take" layered on the mechanical verdict (Discord
# presentation). Calls the starfish-reason shim (-> clawfish-opus). Best-effort:
# the mechanical verdict stands if the take fails or is disabled.
STARFISH = os.environ.get(
    "STARFISH_REASON",
    r"C:/Projects/_Jobs/Collaborations/Markus/Void-Patcher/starfish/bin/starfish-reason",
)
TAKE_ENABLED = os.environ.get("KCS_DISTILL_TAKE", "1").strip().lower() not in ("0", "false", "no", "")
TAKE_TIMEOUT_S = int(os.environ.get("KCS_DISTILL_TAKE_TIMEOUT_S", "90"))


def _bash() -> str:
    for c in (os.environ.get("KCS_BASH"), r"C:/Program Files/Git/bin/bash.exe", "bash"):
        if c and (c == "bash" or Path(c).exists()):
            return c
    return "bash"


def _clawfish_take(verdict: dict) -> str:
    """Ask clawfish-opus for a blunt 1-2 line take. '' on disable/failure."""
    if not TAKE_ENABLED or not verdict.get("ok") or not Path(STARFISH).exists():
        return ""
    tops = "; ".join(
        f"[{t.get('subsystem')}] {(t.get('idea') or '')[:100]}"
        for t in (verdict.get("top_integrate") or [])[:4]
    )
    prompt = (
        "A YouTube video was auto-distilled for our team's Discord channel. "
        f"Title: {verdict.get('title', '')}. "
        f"Mechanical verdict: {verdict.get('headline', '')}. "
        f"Top patterns flagged to integrate: {tops or 'none'}. "
        "In 1-2 blunt sentences for the channel: is this worth the team's time, "
        "and who specifically should watch it? No preamble, do not restate the verdict."
    )
    try:
        r = subprocess.run(
            [_bash(), STARFISH], input=prompt, capture_output=True, text=True,
            timeout=TAKE_TIMEOUT_S, encoding="utf-8", errors="replace",
        )
        return (r.stdout or "").strip()[:600]
    except Exception:
        return ""


def _read_token() -> str:
    for var in ("KCS_DISTILL_TOKEN", "KCS_CASCADE_TOKEN"):
        v = os.environ.get(var)
        if v:
            return v.strip()
    # fall back to the kimiclaw .env (host-only), same source the other proxies use
    env = Path(os.environ.get("KCS_ENV_FILE", r"C:/Projects/_Jobs/Collaborations/Andrew/kimiclaw/repo/.env"))
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("KCS_CASCADE_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return ""


TOKEN = _read_token()
app = Flask(__name__)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _check_token():
    if not TOKEN:
        abort(404)
    presented = request.headers.get("X-Distill-Token", "").strip()
    if not (presented and secrets.compare_digest(presented, TOKEN)):
        abort(404)


def _worker(job_id: str, url: str):
    try:
        # Pre-flight: ensure qwen36 is alive. If the VRAM is currently loaded
        # with brutal-llm (or anything else), trigger an auto-flip back to
        # qwen36 before dispatching. This is the path that lets distill survive
        # the system being in brutal mode — without it, distill_service used to
        # 503 immediately and the arbiter never saw the demand because the job
        # was rejected before /healthz could count it. See 2026-05-27 incident.
        ok, why = _qwen_dispatcher_alive(timeout_s=4.0)
        if not ok:
            print(f"distill_service [{job_id}]: qwen36 unresponsive ({why}); "
                  f"attempting ensure qwen36", file=sys.stderr, flush=True)
            try:
                ensure = subprocess.run(
                    [PY, "-u", WITH_BRUTAL_SCRIPT, "ensure", "qwen36"],
                    capture_output=True, text=True, timeout=ENSURE_QWEN36_TIMEOUT_S,
                    encoding="utf-8", errors="replace",
                )
                if ensure.returncode != 0:
                    with _lock:
                        _jobs[job_id].update(
                            status="done",
                            verdict={"ok": False, "error": "ensure_qwen36_failed",
                                     "detail": (ensure.stderr or ensure.stdout or "")[-400:]},
                        )
                    return
                ok, why = _qwen_dispatcher_alive(timeout_s=4.0)
                if not ok:
                    with _lock:
                        _jobs[job_id].update(
                            status="done",
                            verdict={"ok": False, "error": "qwen36_still_unresponsive_after_ensure",
                                     "detail": why},
                        )
                    return
            except subprocess.TimeoutExpired:
                with _lock:
                    _jobs[job_id].update(
                        status="done",
                        verdict={"ok": False, "error": "ensure_qwen36_timeout",
                                 "detail": f"with_brutal_llm ensure qwen36 exceeded {ENSURE_QWEN36_TIMEOUT_S}s"},
                    )
                return

        # CLA-206: cadence watchdog replaces a bare subprocess.run. If the
        # subprocess emits no stdout for DISTILL_STDOUT_SILENCE_S seconds
        # while still alive, it is killed and restart_qwen36.bat fires.
        rc, out, err, silenced, total_timeout = _run_distill_with_cadence_watchdog(
            url, DISTILL_TIMEOUT_S, DISTILL_STDOUT_SILENCE_S,
        )
        if silenced:
            with _lock:
                _jobs[job_id].update(
                    status="done",
                    verdict={"ok": False, "error": "subprocess_stalled",
                             "detail": f"no stdout for {DISTILL_STDOUT_SILENCE_S}s, "
                                       f"killed PID + triggered restart_qwen36"},
                )
            return
        if total_timeout:
            with _lock:
                _jobs[job_id].update(status="error", error="distill timed out")
            return
        try:
            verdict = json.loads(out)
        except Exception:
            verdict = {"ok": False, "error": "parse_failed",
                       "detail": (out or err or "")[-400:]}
        if isinstance(verdict, dict) and verdict.get("ok"):
            take = _clawfish_take(verdict)
            if take:
                verdict["take"] = take
        with _lock:
            _jobs[job_id].update(status="done", verdict=verdict)
    except Exception as exc:
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc)[:300])


@app.route("/healthz")
def healthz():
    with _lock:
        n = len(_jobs)
        # "active" = jobs whose worker thread is still in flight. The arbiter
        # reads this to detect REAL distill demand; using total `jobs` (which
        # includes completed-but-not-pruned entries up to JOB_TTL_S=3600) would
        # block brutal-llm auto-acquire for an hour after the last successful
        # distill while the meta harness misroutes 400s to qwen36 unchecked
        # (observed 2026-05-27 11:00 - GPU pegged at 70% / 400W on wrong-port
        # text reranks because Rule 1 distill_active stayed True forever).
        active = sum(1 for v in _jobs.values() if v.get("status") == "running")
    return jsonify({"ok": True, "jobs": n, "active": active, "token": bool(TOKEN)})


QWEN_URL = os.environ.get("KCS_QWEN_BASE_URL", "http://127.0.0.1:7870")


def _qwen_dispatcher_alive(timeout_s: float = 5.0) -> tuple[bool, str]:
    """Probe /slots with a tight timeout. /health staying 200 while /slots
    times out is the zombie-dispatcher signature seen 2026-05-25/26 — caller
    should refuse new work loudly rather than dispatching into a 30-min void."""
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(f"{QWEN_URL}/slots", timeout=timeout_s) as r:
            if 200 <= r.status < 300:
                return True, ""
            return False, f"slots http {r.status}"
    except urllib.error.URLError as e:
        return False, f"slots unreachable: {e.reason}"
    except Exception as e:
        return False, f"slots probe failed: {type(e).__name__}: {e}"


@app.route("/distill", methods=["POST"])
def distill():
    _check_token()
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    # NOTE: we no longer probe /slots here and 503 on failure. The /slots check
    # moved into the worker, where it can also trigger an `ensure qwen36`
    # auto-flip if the system is currently in brutal-llm mode. Rejecting at
    # this gate prevented the arbiter from ever seeing distill demand (job was
    # rejected before /healthz could register it). Registering the job and
    # letting the worker handle the switch makes the auto-flip path actually
    # reachable.
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        # prune expired jobs
        for k in [k for k, v in _jobs.items() if now - v.get("started", now) > JOB_TTL_S]:
            _jobs.pop(k, None)
        _jobs[job_id] = {"status": "running", "verdict": None, "error": None,
                         "started": now, "url": url}
    threading.Thread(target=_worker, args=(job_id, url), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "running"}), 202


@app.route("/distill/<job_id>")
def job_status(job_id):
    _check_token()
    with _lock:
        j = _jobs.get(job_id)
    if not j:
        return jsonify({"error": "not_found"}), 404
    return jsonify({
        "job_id": job_id, "status": j["status"], "url": j["url"],
        "verdict": j["verdict"], "error": j["error"],
        "elapsed_s": round(time.time() - j["started"], 1),
    })


# ---------------------------------------------------------------------------
# Distill-list endpoints for the MeridianOS Distill dashboard app.
# /distill/list      — scan VISUAL_LLM/data/* for finished summaries with
#                       per-video verdict counts (cached 60s in memory)
# /distill/<vid>/summary — return raw summary.md as text/markdown
# Both token-gated via _check_token() (404 on miss, federation doctrine).
# ---------------------------------------------------------------------------


def _parse_summary_md_frontmatter(md_path: Path) -> dict:
    """Read the title line out of a summary.md YAML-ish frontmatter block.
    Frontmatter is a leading '---' / '---' fence with `key: value` lines.
    Returns {} on any parse failure (caller falls back to video_id)."""
    out: dict = {}
    try:
        with md_path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline().rstrip()
            if first.strip() != "---":
                return out
            for _ in range(60):  # cap, frontmatter is never huge
                line = f.readline()
                if not line:
                    break
                s = line.rstrip()
                if s.strip() == "---":
                    break
                if ":" in s:
                    k, _, v = s.partition(":")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and v:
                        out[k] = v
    except Exception:
        return {}
    return out


def _compute_video_verdict(out_dir: Path, vid: str, force: bool = False) -> dict:
    """Get verdict counts (total/integrate/wiki + worth/headline) for a video.
    Reads/writes a sidecar verdict.json so we only invoke decide.py once per
    summary. Returns a dict with at least:
        {patterns_total, patterns_integrate, patterns_wiki, worth, headline}
    On failure returns the same shape with zeroes and worth=False."""
    summary_md = out_dir / "summary.md"
    sidecar = out_dir / "verdict.json"
    if not summary_md.exists():
        return {"patterns_total": 0, "patterns_integrate": 0,
                "patterns_wiki": 0, "worth": False,
                "headline": "no summary.md"}
    if sidecar.exists() and not force:
        try:
            sm_mt = summary_md.stat().st_mtime
            sc_mt = sidecar.stat().st_mtime
            if sc_mt >= sm_mt - 1:
                return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        r = subprocess.run(
            [PY, "-u", str(DECIDE), "--summary", str(summary_md),
             "--matcher", "bm25", "--json"],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return {"patterns_total": 0, "patterns_integrate": 0,
                    "patterns_wiki": 0, "worth": False,
                    "headline": "decide.py failed",
                    "error": (r.stderr or r.stdout or "")[-200:]}
        patterns = json.loads(r.stdout)
    except Exception as exc:
        return {"patterns_total": 0, "patterns_integrate": 0,
                "patterns_wiki": 0, "worth": False,
                "headline": f"decide error: {type(exc).__name__}"}

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

    out = {
        "patterns_total": len(patterns),
        "patterns_integrate": n_integrate,
        "patterns_wiki": n_wiki,
        "patterns_covered": n_covered,
        "worth": worth,
        "headline": headline,
    }
    try:
        sidecar.write_text(json.dumps(out), encoding="utf-8")
    except Exception:
        pass
    return out


def _build_video_entry(vid_dir: Path) -> dict | None:
    vid = vid_dir.name
    if not _YT_ID_RE.match(vid):
        return None
    summary_md = vid_dir / "summary.md"
    summary_json = vid_dir / "summary.json"
    if not (summary_md.exists() and summary_json.exists()):
        return None

    title = vid
    url = f"https://www.youtube.com/watch?v={vid}"
    duration_s = None
    try:
        sj = json.loads(summary_json.read_text(encoding="utf-8"))
        title = (sj.get("title") or sj.get("video_title") or "").strip() or title
        url = (sj.get("url") or url)
        duration_s = sj.get("duration_s")
    except Exception:
        pass

    fm = _parse_summary_md_frontmatter(summary_md)
    if fm.get("title"):
        title = fm["title"]
    if not duration_s and fm.get("duration_s"):
        try:
            duration_s = int(fm["duration_s"])
        except Exception:
            duration_s = None

    st = summary_md.stat()
    verdict = _compute_video_verdict(vid_dir, vid)

    from datetime import datetime, timezone
    mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    return {
        "video_id": vid,
        "title": title[:240],
        "url": url,
        "duration_s": duration_s,
        "mtime": mtime_iso,
        "size_bytes": st.st_size,
        "patterns_total": verdict.get("patterns_total", 0),
        "patterns_integrate": verdict.get("patterns_integrate", 0),
        "patterns_wiki": verdict.get("patterns_wiki", 0),
        "worth": bool(verdict.get("worth", False)),
        "headline": verdict.get("headline", ""),
    }


def _scan_videos() -> list[dict]:
    if not VISUAL_LLM_DATA.is_dir():
        return []
    entries: list[dict] = []
    try:
        for child in VISUAL_LLM_DATA.iterdir():
            if not child.is_dir():
                continue
            try:
                e = _build_video_entry(child)
            except Exception:
                e = None
            if e:
                entries.append(e)
    except Exception:
        return entries
    entries.sort(key=lambda x: x["mtime"], reverse=True)
    return entries


@app.route("/distill/list")
def distill_list():
    _check_token()
    now = time.time()
    with _distill_list_lock:
        if (now - _distill_list_cache["ts"]) < DISTILL_LIST_TTL_S and _distill_list_cache["videos"]:
            videos = _distill_list_cache["videos"]
            cached_age = round(now - _distill_list_cache["ts"], 1)
            return jsonify({"videos": videos, "cached_age_s": cached_age,
                             "ttl_s": DISTILL_LIST_TTL_S, "count": len(videos)})
    videos = _scan_videos()
    with _distill_list_lock:
        _distill_list_cache["ts"] = time.time()
        _distill_list_cache["videos"] = videos
    return jsonify({"videos": videos, "cached_age_s": 0.0,
                     "ttl_s": DISTILL_LIST_TTL_S, "count": len(videos)})


@app.route("/distill/<video_id>/summary")
def distill_video_summary(video_id):
    _check_token()
    if not _YT_ID_RE.match(video_id):
        return jsonify({"error": "invalid_video_id"}), 400
    summary_md = VISUAL_LLM_DATA / video_id / "summary.md"
    if not summary_md.is_file():
        return jsonify({"error": "not_found", "video_id": video_id}), 404
    try:
        body = summary_md.read_text(encoding="utf-8")
    except Exception as exc:
        return jsonify({"error": "read_failed",
                         "detail": f"{type(exc).__name__}: {exc}"}), 500
    from flask import Response
    return Response(body, mimetype="text/markdown; charset=utf-8")


if __name__ == "__main__":
    if not DISTILL.exists():
        print(f"FATAL: distill.py not found at {DISTILL}", file=sys.stderr)
        sys.exit(1)
    print(f"distill_service on {BIND}:{PORT}  token={'set' if TOKEN else 'UNSET'}  distill={DISTILL}")
    print(f"  visual_llm_data={VISUAL_LLM_DATA}  list_ttl={DISTILL_LIST_TTL_S}s")
    app.run(host=BIND, port=PORT, threaded=True)
