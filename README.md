# workflow-ingest

Workflow-aware idea router. Drops a YouTube URL anywhere, gets a routed tracker
idea (or skip / conflict log) tagged against the live workflow inventory.

The router is the layer ABOVE the existing visual-llm stack
(`C:\Projects\visual-llm`) and the `youtube-summarizer` MCP server. It does not
modify either; missing primitives become new MCP tools, never workarounds.

## Layers

| Layer | Module | Job |
|---|---|---|
| Capture | `ingest/inbox_drainer.py` | Read Obsidian inbox, create tracker items, truncate inbox |
| Queue | Tracker (Nimbalyst MCP) | Single source of truth for queue state |
| Process | `ingest/youtube_adapter.py` | Calls `youtube_status` then `youtube_summarize`, emits `summary.md` path |
| Extract | `extract/digest_to_patterns.py` | Parses summary.md into `Pattern[]` (source-agnostic) |
| Route | `route/matcher.py` + `route/audit.py` | Match Pattern x Inventory, run 5pt audit, decide tier |
| Sink | `sink/{tracker,obsidian,conflict_log}_writer.py` | Write outputs, verify-after-write |
| Feedback | `feedback/learn.py` | Closed-implemented boosts, skipped/dup demotes (v0 stub) |

## Anti-patterns guarded against

1. No silent failures. `youtube_status` red creates a tracker bug.
2. Audit at write-time, not after. 5 checks before any tracker_create lands.
3. Schema mirror. Pattern v1 + Inventory snapshot v1 are versioned together.
4. Don't conflate topic with agreement. "Mentions trident" != "agrees with trident".
5. Verify end-state. After tracker_create, re-query before marking digest routed.

## Schemas

- `pattern/schema_v1.yaml` - source-agnostic pattern shape
- `inventory/schema_v1.yaml` - subsystem snapshot shape

Both are v1.0. Any change requires a migration script and bumps both versions
together; never one without the other.

## Confidence scoring

v0 stub returns 0.6 for everything. Tier thresholds and the actual scoring
function are deferred until there is real feedback signal to fit weights to.

## /solve-room throttle

Conflict-state ideas log + tag only. They never auto-convene a cross-floor
meeting. Weekly synthesis batches them; only items that survive batch review
escalate to /solve-room.

## Inventory sources (v1)

1. Recent commits across `C:\Projects\*` (git log last 30 days)
2. Tracker items in this workspace
3. Obsidian notes tagged `#subsystem` under `developer/**`
4. Memory files under `~/.claude/projects/.../memory/*.md` (NEW in v1)
5. Operator-curated bullets at `inventory/operator_bullets.yaml`

## Decision layer

After routing, patterns pass through a 5-way decision classifier:

| Decision | When | Write target |
|---|---|---|
| `integrate` | Audit passes, tier high/medium, `write_target` defined | Tracker item or subsystem ticket |
| `already-covered` | `pattern_id` already seen (dedupe gate) | No-op |
| `wiki-only` | Topical-page target, low tier, or no `write_target` | Wiki append or canonical note |
| `conflict` | `match_state=red` or `tier=conflict` | Conflict log (weekly batch triage) |
| `discard` | Audit failed or zero evidence | No-op |

CLI:

```bash
# Keyword matcher (fast, uses snapshot subsystem keywords)
python tools/decide.py --summary tests/fixtures/kQu5pWKS8GA_summary.md

# BM25 matcher (uses corpus_map + topical pages)
python tools/decide.py --summary tests/fixtures/kQu5pWKS8GA_summary.md --matcher bm25

# JSON output for downstream automation
python tools/decide.py --summary <path> --json
```

## Integration with distill-youtube

The canonical flow:

```
youtube URL → youtube-summarizer → summary.md
                                    ↓
                              workflow-ingest decide
                                    ↓
                         ┌──────────┼──────────┐
                      integrate  wiki-only  conflict
                            ↓        ↓          ↓
                      tracker    obsidian   conflicts/
```

`distill-youtube` handles step 1 (absorption + wiki). `workflow-ingest`
handles step 2 (decision + routing). The bridge is the `summary.md` file.

## Backtest

`tests/test_three_known_videos.py` exercises the matcher on three already-
digested runs. All three argue for bespoke agentic pipelines. The test asks:
are these three the same idea routed N times, or N variations of one idea?
That validates dedup + match-state logic on real data, day one.
