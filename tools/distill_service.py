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
PY = sys.executable
JOB_TTL_S = 3600
DISTILL_TIMEOUT_S = int(os.environ.get("KCS_DISTILL_TIMEOUT_S", "2000"))

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
        r = subprocess.run(
            [PY, "-u", str(DISTILL), url, "--json"],
            capture_output=True, text=True, timeout=DISTILL_TIMEOUT_S,
            encoding="utf-8", errors="replace",
        )
        try:
            verdict = json.loads(r.stdout)
        except Exception:
            verdict = {"ok": False, "error": "parse_failed",
                       "detail": (r.stdout or r.stderr or "")[-400:]}
        if isinstance(verdict, dict) and verdict.get("ok"):
            take = _clawfish_take(verdict)
            if take:
                verdict["take"] = take
        with _lock:
            _jobs[job_id].update(status="done", verdict=verdict)
    except subprocess.TimeoutExpired:
        with _lock:
            _jobs[job_id].update(status="error", error="distill timed out")
    except Exception as exc:
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc)[:300])


@app.route("/healthz")
def healthz():
    with _lock:
        n = len(_jobs)
    return jsonify({"ok": True, "jobs": n, "token": bool(TOKEN)})


@app.route("/distill", methods=["POST"])
def distill():
    _check_token()
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
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


if __name__ == "__main__":
    if not DISTILL.exists():
        print(f"FATAL: distill.py not found at {DISTILL}", file=sys.stderr)
        sys.exit(1)
    print(f"distill_service on {BIND}:{PORT}  token={'set' if TOKEN else 'UNSET'}  distill={DISTILL}")
    app.run(host=BIND, port=PORT, threaded=True)
