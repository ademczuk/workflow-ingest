# workflow-ingest v0.2 — Handover for Codex & Claude Code CLI

> **Last updated:** 2026-05-25  
> **Commit:** `539e2c2` on `main`  
> **Repo:** `https://github.com/ademczuk/workflow-ingest`  
> **Tests:** 93 passing  
> **Python:** 3.12+  

---

## What this project does

`workflow-ingest` is the **decision + routing layer** that sits between the `visual-llm` youtube summarizer and the operator's actual workflow tools (Obsidian vault, Linear tracker).

**The canonical flow:**

```
YouTube URL → youtube-summarizer → summary.md
                                    ↓
                              workflow-ingest
                                    ↓
                         ┌──────────┼──────────┐
                      integrate  wiki-only  conflict
                            ↓        ↓          ↓
                      tracker    obsidian   conflicts/
```

It does NOT summarize videos. It reads an existing `summary.md`, extracts patterns, matches them against the live subsystem inventory, audits them, decides what to do with each, and writes the outputs.

---

## Architecture

```
workflow-ingest/
├── automations/           ← NEW in v0.2
│   ├── queue_processor.py      # summary.md → digest + conflicts + tracker
│   ├── inventory_refresh.py    # git scan → atomic snapshot update
│   ├── queue-processor.md      # spec (human-readable)
│   ├── inventory-refresh.md    # spec
│   ├── inbox-drainer.md        # spec (not yet implemented)
│   └── weekly-synthesis.md     # spec (not yet implemented)
├── extract/
│   └── digest_to_patterns.py   # Parses summary.md → Pattern[]
├── route/
│   ├── matcher.py              # Keyword matcher (fast, coarse)
│   ├── bm25_matcher.py         # BM25 matcher (richer, default)
│   ├── audit.py                # 5-point audit before write
│   ├── decision.py             # 5-way classifier
│   └── confidence.py           # Stub (returns 0.6 always)
├── sink/
│   ├── obsidian_writer.py      # Renders digest markdown
│   ├── conflict_log.py         # Appends to conflicts/YYYY-MM.md
│   └── tracker_writer.py       # Builds Linear payload + verify contract
├── inventory/
│   ├── snapshot.yaml           # Live subsystem inventory (audit authority)
│   ├── corpus_map_v1.yaml      # Richer entries (keywords, concepts, write_target)
│   ├── corpus_map.py           # Loader + validator
│   ├── model.py                # Snapshot dataclasses
│   └── operator_bullets.yaml   # Operator-curated subsystem list
├── pattern/
│   └── model.py                # Pattern + Routing dataclasses
├── tools/
│   └── decide.py               # CLI report generator
├── tests/
│   ├── test_decision.py
│   ├── test_matcher_audit.py
│   ├── test_three_known_videos.py
│   ├── test_queue_processor.py      # NEW v0.2
│   └── test_inventory_refresh.py    # NEW v0.2
└── README.md
```

---

## Key concepts

### Pattern
A `Pattern` is one insight extracted from a video summary. It has:
- `pattern_id`: SHA-256 hash of the idea text (first 12 chars)
- `idea`: One-sentence insight (≤280 chars)
- `evidence`: Full bullet text from the summary
- `type`: `anti-pattern`, `architecture-pattern`, `feature-add`, `external-tool`, `tooling-swap`, `observability-gap`
- `confidence`: 0.0–1.0 (stub returns 0.6)
- `routing`: `Routing` object after matching (subsystem, tier, match_state, write_target)

### Routing
```python
Routing(
    subsystem_slug="visual-llm",
    match_state="green",          # green | yellow | red
    tier="high",                  # high | medium | low | conflict
    snapshot_version="1.0",
    canonical_path=None,          # Obsidian canonical note path
    write_target="02-Knowledge/Wiki/AI/",  # Where to write a stub
    target_kind="subsystem",      # subsystem | topical_page
)
```

### Decision (5-way classifier)
| Decision | When | Action |
|---|---|---|
| `integrate` | Audit passes, tier high/medium, `write_target` defined | Create tracker item + include in digest |
| `already-covered` | `pattern_id` already in `existing_ids` | Skip (dedupe) |
| `wiki-only` | Topical-page target, low tier, or no `write_target` | Include in digest only |
| `conflict` | `match_state=red` or `tier=conflict` | Conflict log (weekly batch triage) |
| `discard` | Audit failed or zero evidence | Skip entirely |

### Audit (5 checks)
1. `pattern_id` is valid hex
2. `idea` is non-empty and ≤280 chars
3. `evidence` is non-empty
4. `subsystem_slug` exists in snapshot
5. `confidence` is in [0.0, 1.0]

---

## CRITICAL BUG FIXED IN v0.2

**The keyword matcher did not set `write_target`.** This meant every single pattern routed by keyword matcher got `wiki-only` with reason "no write_target defined" — even when the corpus map had a `write_target_when_novel`.

**Fix:** `route/matcher.py` now accepts an optional `corpus` parameter and pulls `write_target_when_novel` from the corpus map.

**Default changed:** `tools/decide.py` now defaults to `--matcher bm25` because BM25 produces dramatically better routing. Keyword is still available as `--matcher keyword`.

**Real data from `efRIrLXoOVA`:**
- Keyword (before fix): 6 patterns → 0 integrate, 6 wiki-only
- Keyword (after fix): 6 patterns → 2 integrate, 4 wiki-only
- BM25 (default): 6 patterns → 6 integrate

---

## How to run things

### Decision report (CLI)
```bash
# Default BM25 matcher
python tools/decide.py --summary <path/to/summary.md>

# Keyword matcher (faster, coarser)
python tools/decide.py --summary <path> --matcher keyword

# JSON output
python tools/decide.py --summary <path> --json
```

### Queue processor (end-to-end)
```bash
# Dry-run: see what would happen
python automations/queue_processor.py --summary <path> --dry-run

# Real run: writes digest + conflict log
python automations/queue_processor.py --summary <path>

# Also create Linear issues for integrate decisions
# Requires LINEAR_API_KEY env var (same key the MCP server uses)
set LINEAR_API_KEY=lin_api_...
python automations/queue_processor.py --summary <path> --create-linear-issues
```

Outputs:
- `data/digests/<video_id>.md` — Obsidian digest with routed patterns
- `data/conflicts/<YYYY-MM>.md` — conflict-tier patterns for weekly synthesis
- Linear issues (optional) — one per `integrate` decision

### Inventory refresh
```bash
# Scans C:\Projects\* recursively for git repos
python automations/inventory_refresh.py

# With custom paths
python automations/inventory_refresh.py --snapshot inventory/snapshot.yaml --repos-root D:\Projects

# Dry-run
python automations/inventory_refresh.py --dry-run
```

Safe to run daily. Writes atomically (`.yaml.tmp` then rename). Leaves old snapshot on error.

---

## Testing

```bash
# Run everything
python -m pytest tests/ -q

# Just the new automation tests
python -m pytest tests/test_queue_processor.py tests/test_inventory_refresh.py -v
```

**Test fixtures:** `tests/fixtures/*_summary.md` — real summary files from previously digested videos.

---

## Inventory system

### snapshot.yaml vs corpus_map_v1.yaml

| File | Purpose | Updated by |
|---|---|---|
| `inventory/snapshot.yaml` | **Audit authority.** Lists every subsystem + sources + last_seen. Used by matcher and audit. | `inventory_refresh.py` |
| `inventory/corpus_map_v1.yaml` | **Routing intelligence.** Keywords, concepts, known_gaps, write_target_when_novel, topical pages. Used by BM25 matcher. | Human curation |

**Rule:** `validate_against_snapshot()` enforces that every slug in `corpus_map_v1.yaml` exists in `snapshot.yaml`. If you add a subsystem to one, add it to the other.

### Adding a new subsystem
1. Add to `inventory/snapshot.yaml` with at least one source
2. Add to `inventory/corpus_map_v1.yaml` with keywords, concepts, and `write_target_when_novel`
3. If it's a topical page (not a real subsystem), add to `corpus_map_v1.yaml` under `topical_pages`
4. Run tests: `python -m pytest tests/`

### Adding a write target
Edit `inventory/corpus_map_v1.yaml`:
```yaml
subsystems:
  - slug: my-new-subsystem
    write_target_when_novel: 02-Knowledge/Wiki/My-Area/
```

Without a `write_target_when_novel`, patterns routed to that subsystem will always be `wiki-only` (even at high tier).

---

## MCP integration

`workflow-ingest` does NOT call MCP tools directly. The queue processor works on existing `summary.md` files.

**The Kimi-side orchestration** (not yet automated) is:
1. `youtube_status` — check GPU stack health
2. `youtube_summarize <url>` — produces `summary.md`
3. `python automations/queue_processor.py --summary <path>` — runs the pipeline

In v0.3, a Kimi skill or shell wrapper could glue steps 1–3 together.

### Linear MCP
- The queue processor can create Linear issues directly via GraphQL API (not through MCP).
- Requires `LINEAR_API_KEY` env var.
- Uses team `CLA` (Clawdify).
- The `tracker_writer.py` module defines the payload shape and verify contract for testing.

---

## Known issues & gotchas

1. **Stale MCP sessions:** After changing `visual-llm` MCP server code, Kimi CLI sessions hold stale server references. Fix: exit and restart Kimi.
2. **Long narration timeouts:** Kimi's default `tool_call_timeout_ms` is 60000ms. Narration for long videos may exceed this. The `kokoro_client.py` chunked synthesis fixes the root cause, but the Kimi config may also need raising to 300000ms.
3. **Keyword matcher is coarse:** It only matches on slug + display_name + notes. BM25 uses keywords + concepts + roles. Always prefer BM25.
4. **Confidence scoring is a stub:** `route/confidence.py` returns 0.6 for everything. Real scoring requires feedback signal (deferred to v0.3+).
5. **No scheduler:** Automations are scripts, not cron jobs. The operator runs them manually or via external scheduler.
6. **Tracker verify-after-write is partial:** The `tracker_writer.py` Protocol defines `verify_passed`, but the actual Linear client in `queue_processor.py` only verifies that the API call succeeded. It does not re-query the issue to confirm the `pattern-{id}` tag exists (Linear GraphQL query is straightforward to add).
7. **Conflict handling is append-only:** Conflict-tier patterns go to `data/conflicts/<YYYY-MM>.md`. Weekly synthesis (not yet implemented) batches them. They NEVER auto-convene `/solve-room`.
8. **Playlist processing blocked:** CLA-134. Playlist URL `PLx-045m7by1k3ZaD6P8xpxwP0DvoPVGTJ` does not exist on YouTube. Needs correct URL from operator.

---

## Deferred to v0.3

| Item | Why deferred |
|---|---|
| `inbox-drainer.py` implementation | Needs Obsidian vault MCP or direct file access to `inbox/youtube-queue.md` |
| `weekly-synthesis.py` | Needs scheduler + Nimbalyst session API + scoring weights |
| Full tracker verify-after-write | Needs Linear read-by-tag GraphQL query |
| Confidence scoring | Needs feedback corpus to fit weights |
| Multi-video batch / playlist | Blocked on CLA-134 |
| Scheduler integration (cron/Windows Task Scheduler) | Out of scope for v0.2 |

---

## Quick reference: files you will edit most

| Task | File |
|---|---|
| Add subsystem | `inventory/snapshot.yaml` + `inventory/corpus_map_v1.yaml` |
| Change routing logic | `route/bm25_matcher.py` or `route/matcher.py` |
| Change decision rules | `route/decision.py` |
| Change audit checks | `route/audit.py` |
| Change digest format | `sink/obsidian_writer.py` |
| Change tracker payload | `sink/tracker_writer.py` |
| Add automation | `automations/<name>.py` + `tests/test_<name>.py` |

---

## Environment

- **OS:** Windows 11
- **Python:** 3.12.10
- **Dependencies:** `PyYAML>=6.0`, `rank-bm25>=0.2.1`, `requests` (optional, for Linear)
- **Dev deps:** `pytest>=7.4`
- **Git repo:** `C:\Projects\workflow-ingest`
- **Remote:** `https://github.com/ademczuk/workflow-ingest`
- **Linear team:** Clawdify (`CLA`)
- **Linear API key:** In `~/.kimi/mcp.json` → `linear` server env

---

## End-to-end smoke test

```bash
# 1. Summarize a video (or use existing summary)
# 2. Run decision report
python tools/decide.py --summary C:/Projects/visual-llm/data/efRIrLXoOVA/summary.md

# 3. Run queue processor (dry-run)
python automations/queue_processor.py --summary C:/Projects/visual-llm/data/efRIrLXoOVA/summary.md --dry-run

# 4. Run queue processor (for real)
python automations/queue_processor.py --summary C:/Projects/visual-llm/data/efRIrLXoOVA/summary.md

# 5. Check outputs
ls data/digests/
ls data/conflicts/

# 6. Refresh inventory
python automations/inventory_refresh.py --dry-run
python automations/inventory_refresh.py

# 7. Run tests
python -m pytest tests/ -q
```

Expected: 93 tests pass, digest file created, no errors.

---

## Who to ask

- **andrew.demczuk** (operator) — for subsystem definitions, write targets, priority order, and whether a pattern should integrate or wiki-only.
- **visual-llm stack** — for summary quality, transcription issues, or GPU health.
- **Linear tracker** — for issue state, backlog prioritization, or bug triage.
